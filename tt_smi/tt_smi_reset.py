# SPDX-FileCopyrightText: © 2026 Tenstorrent Inc.
# SPDX-License-Identifier: Apache-2.0

"""
Reset-related functions for tt-smi: PCI board reset, galaxy 6U tray reset, and helpers.
"""

import os
import re
import sys
import time
from dataclasses import dataclass
from enum import Enum
from typing import List, Optional, Tuple, Union, Dict

from tt_tools_common.ui_common.themes import CMD_LINE_COLOR
from tt_tools_common.reset_common.wh_reset import WHChipReset
from tt_tools_common.reset_common.bh_reset import BHChipReset
from pyluwen import (
    PciChip,
    run_wh_ubb_ipmi_reset,
    run_ubb_wait_for_driver_load,
)
from tt_umd import (
    WarmReset,
    PCIDevice,
    TopologyDiscovery,
    TopologyDiscoveryOptions,
)
from tt_tools_common.utils_common.tools_utils import (
    detect_chips_with_callback,
)

# Reset -r target formats: UMD logical ID (int), PCI BDF (aa:00.0 or 0000:aa:00.0), /dev/tenstorrent/<int>
DEV_TENSTORRENT_PREFIX = "/dev/tenstorrent/"
PCI_BDF_FULL_RE = re.compile(r"^[0-9a-fA-F]{4}:[0-9a-fA-F]{2}:[0-9a-fA-F]{2}\.[0-9a-fA-F]$")
PCI_BDF_SHORT_RE = re.compile(r"^[0-9a-fA-F]{2}:[0-9a-fA-F]{2}\.[0-9a-fA-F]$")

"""
tt-smi -r <UMD logical ID>   e.g. tt-smi -r 0  or  tt-smi -r 0,1,2
tt-smi -r <PCI BDF>          e.g. tt-smi -r 0000:0a:00.0  or  tt-smi -r 0a:00.0, 0b:00.0
tt-smi -r /dev/tenstorrent/<ID>   e.g. tt-smi -r /dev/tenstorrent/0  or  tt-smi -r /dev/tenstorrent/0, /dev/tenstorrent/1
Mixing types is allowed; parse_reset_input returns a list of three ResetInput (BDF, UMD logical ID, dev ID).
"""


class ResetType(Enum):
    ALL = 1
    UMD_LOGICAL_ID = 2
    PCI_BDF = 3
    DEV_TENSTORRENT_ID = 4


@dataclass
class ResetInput:
    type: ResetType
    value: Optional[Union[List[int], List[str]]] = None


def _classify_reset_token(token: str) -> Tuple[str, Union[int, str]]:
    """Classify a single token as 'int' (UMD logical ID), 'bdf', or 'dev_path'. Returns (kind, value). For dev_path, value is the integer ID only."""
    token = token.strip()
    if not token:
        raise ValueError("Empty token")
    if token.lower() == "all":
        raise ValueError("Use 'all' as the only argument to reset all devices")
    if token.startswith(DEV_TENSTORRENT_PREFIX):
        suffix = token[len(DEV_TENSTORRENT_PREFIX) :].strip()
        if suffix.isdigit():
            return ("dev_path", int(suffix))
        raise ValueError(f"Invalid path: {token} (expected /dev/tenstorrent/<integer>)")
    if PCI_BDF_FULL_RE.match(token) or PCI_BDF_SHORT_RE.match(token):
        return ("bdf", token)
    if token.lstrip("-").isdigit():
        return ("int", int(token))
    raise ValueError(
        f"Invalid reset target: '{token}'. "
        "Use UMD logical ID (integer), PCI BDF (e.g. 0000:0a:00.0 or 0a:00.0), or /dev/tenstorrent/<integer>."
    )


def parse_reset_input(value: list) -> Union[ResetInput, List[ResetInput]]:
    """
    Parse -r / --reset arguments. Accepts mixed types; returns either:
    - A single ResetInput(type=ALL, value=None) for no input or "all".
    - A list of ResetInput items for each kind that had at least one value (BDF, UMD logical ID, dev ID).
      Empty kinds are omitted. Dev IDs are stored as integers (no /dev/tenstorrent/ prefix).
    """
    if value is None or len(value) == 0:
        return ResetInput(type=ResetType.ALL, value=None)
    tokens = [t.strip() for raw in value for t in raw.split(",") if t.strip()]
    if not tokens or (len(tokens) == 1 and tokens[0].lower() == "all"):
        return ResetInput(type=ResetType.ALL, value=None)

    bdf_values: List[str] = []
    int_values: List[int] = []
    dev_values: List[int] = []
    seen_bdf: set = set()
    seen_int: set = set()
    seen_dev: set = set()

    for token in tokens:
        try:
            kind, val = _classify_reset_token(token)
            if kind == "bdf":
                if val not in seen_bdf:
                    seen_bdf.add(val)
                    bdf_values.append(val)
            elif kind == "int":
                if val not in seen_int:
                    seen_int.add(val)
                    int_values.append(val)
            else:
                assert kind == "dev_path"
                if val not in seen_dev:
                    seen_dev.add(val)
                    dev_values.append(val)
        except ValueError as e:
            print(CMD_LINE_COLOR.RED, str(e), CMD_LINE_COLOR.ENDC)
            sys.exit(1)

    result: List[ResetInput] = []
    if bdf_values:
        result.append(ResetInput(type=ResetType.PCI_BDF, value=bdf_values))
    if int_values:
        result.append(ResetInput(type=ResetType.UMD_LOGICAL_ID, value=sorted(set(int_values))))
    if dev_values:
        result.append(ResetInput(type=ResetType.DEV_TENSTORRENT_ID, value=sorted(set(dev_values))))
    return result

def umd_bdf_to_indices(bdf_list: List[str], chips) -> List[int]:
    """Convert a list of PCI BDF strings to a list of PCI index's using UMD."""
    bdf_to_idx: Dict[str, int] = {}
    for idx, info in chips.items():
        bdf = info.pci_bdf
        bdf_to_idx[bdf] = idx
        parts = bdf.split(":", 1)
        if len(parts) == 2:
            bdf_to_idx[parts[1]] = idx
    print(bdf_to_idx)
    indices: List[int] = []
    for bdf in bdf_list:
        s = bdf.strip()
        if s not in bdf_to_idx:
            s = "0000:" + s if s.count(":") == 1 and "." in s else s
        if s not in bdf_to_idx:
            raise ValueError(f"No device with PCI BDF '{bdf}'. Use tt-smi -ls to list devices.")
        indices.append(bdf_to_idx[s])
    print(indices)
    return sorted(set(indices))

def umd_pci_reset(
    reset_input: List[ResetInput],
    secondary_bus_reset: bool = False,
):
    """Reset the PCI devices using UMD. Handles ALL, UMD_LOGICAL_ID, PCI_BDF, and DEV_TENSTORRENT_ID."""
    # Convert the reset_input to a list of PCI index's with helper functions
    chips = PCIDevice.enumerate_devices_info()
    indices = set()
    # If ResetInput(type=<ResetType.ALL: 1>, value=None) exists, reset all devices
    if ResetInput(type=ResetType.ALL, value=None) in reset_input:
        indices = list(chips.keys())
        WarmReset.warm_reset(indices, secondary_bus_reset=secondary_bus_reset)
        return
    # Otherwise, reset the devices in the reset_input list
    for reset_input in reset_input:
        if reset_input.type == ResetType.DEV_TENSTORRENT_ID:
            # These are already in the pci index's format, so we can use them directly
            indices.update(reset_input.value)
        if reset_input.type == ResetType.PCI_BDF:
            pci_indices = umd_bdf_to_indices(reset_input.value, chips)
            indices.update(pci_indices)
    
    print(indices)
    
    if indices:
        # WarmReset.warm_reset(indices, secondary_bus_reset=secondary_bus_reset)
        return
    else:
        print(CMD_LINE_COLOR.RED, "No valid devices to reset. Use tt-smi -ls to list devices.", CMD_LINE_COLOR.ENDC)
        sys.exit(1)

def pci_board_reset(
    list_of_boards: List[int],
    reinit: bool = False,
    print_status: bool = True,
    use_umd: bool = False,
    reset_input: List[ResetInput] = None,
):
    """Given a list of PCI index's init the PCI chip and call reset on it"""
    reset_wh_pci_idx = []
    reset_bh_pci_idx = []
    board_types = set()
    if use_umd:
        chips = PCIDevice.enumerate_devices_info()
    for pci_idx in list_of_boards:
        try:
            if not use_umd:
                chip = PciChip(pci_interface=pci_idx)
        except Exception as e:
            print(e)
            print(
                CMD_LINE_COLOR.RED,
                f"Error accessing board at PCI index {pci_idx}! Use -ls to see all devices available to reset",
                CMD_LINE_COLOR.ENDC,
            )
            continue

        if use_umd:
            board_types.add(chips[pci_idx].subsystem_id)
        else:
            if chip.as_wh():
                reset_wh_pci_idx.append(pci_idx)
                board_types.add(chip.as_wh().pci_board_type())
            elif chip.as_bh():
                reset_bh_pci_idx.append(pci_idx)
                board_types.add(chip.as_bh().pci_board_type())
            else:
                print(
                    CMD_LINE_COLOR.RED,
                    "Unknown chip type detected. Exiting...",
                    CMD_LINE_COLOR.ENDC,
                )
                del chip
                sys.exit(1)
            del chip

    is_galaxy = board_types <= {0x35, 0x47}
    if is_galaxy:
        print(
            CMD_LINE_COLOR.YELLOW,
            "CPLD FW v1.16 or higher is required to use tt-smi -r on Galaxy systems.",
            "If tt-smi -r fails, please continue to use tt-smi -glx_reset instead and contact your system administrator to request a CPLD update.",
            CMD_LINE_COLOR.ENDC,
        )

    secondary_bus_reset = not is_galaxy

    if use_umd:
        umd_pci_reset(reset_input, secondary_bus_reset=secondary_bus_reset)
        # WarmReset.warm_reset(list_of_boards, secondary_bus_reset=secondary_bus_reset)
    else:
        if reset_wh_pci_idx:
            WHChipReset().full_lds_reset(
                pci_interfaces=reset_wh_pci_idx, secondary_bus_reset=secondary_bus_reset
            )
        if reset_bh_pci_idx:
            BHChipReset().full_lds_reset(
                pci_interfaces=reset_bh_pci_idx, secondary_bus_reset=secondary_bus_reset
            )

    if reinit:
        os.environ["RUST_BACKTRACE"] = "full"
        print(
            CMD_LINE_COLOR.PURPLE,
            "Re-initializing boards after reset....",
            CMD_LINE_COLOR.ENDC,
        )
        try:
            if use_umd:
                TopologyDiscovery.discover()
            else:
                detect_chips_with_callback(print_status=print_status)
        except Exception as e:
            print(
                CMD_LINE_COLOR.RED,
                f"Error when re-initializing chips!\n {e}",
                CMD_LINE_COLOR.ENDC,
            )
            sys.exit(1)

def timed_wait(seconds):
    print("\033[93mWaiting for {} seconds: 0\033[0m".format(seconds), end='')
    sys.stdout.flush()

    for i in range(1, seconds + 1):
        time.sleep(1)
        print("\r\033[93mWaiting for {} seconds: {}\033[0m".format(seconds, i), end='')
        sys.stdout.flush()
    print()


def check_wh_galaxy_eth_link_status(devices):
    """
    Check the WH Galaxy Ethernet link status.
    Returns True if the link is up, False otherwise.
    """
    noc_id = 0
    DEBUG_BUF_ADDR = 0x12c0  # For eth fw 5.0.0 and above
    eth_locations_noc_0 = [
        (9, 0), (1, 0), (8, 0), (2, 0), (7, 0), (3, 0), (6, 0), (4, 0),
        (9, 6), (1, 6), (8, 6), (2, 6), (7, 6), (3, 6), (6, 6), (4, 6),
    ]
    LINK_INACTIVE_FAIL_DUMMY_PACKET = 10
    if len(devices) != 32:
        print(
            CMD_LINE_COLOR.RED,
            f"Error: Expected 32 devices for WH Galaxy Ethernet link status check, seeing {len(devices)}, please try reset again or cold boot the system.",
            CMD_LINE_COLOR.ENDC,
        )
        sys.exit(1)

    link_errors = {}
    for i, device in enumerate(devices):
        for eth in range(16):
            eth_x, eth_y = eth_locations_noc_0[eth]
            link_error = device.noc_read32(noc_id, eth_x, eth_y, DEBUG_BUF_ADDR + 0x4 * 96)
            if link_error == LINK_INACTIVE_FAIL_DUMMY_PACKET:
                link_errors[i] = eth

    if link_errors:
        for board_idx, eth in link_errors.items():
            print(
                CMD_LINE_COLOR.RED,
                f"Board {board_idx} has link error on eth port {eth}",
                CMD_LINE_COLOR.ENDC,
            )
        raise Exception("WH Galaxy Ethernet link errors detected!")


def umd_ubb_wait_for_driver_load():
    """
    Wait for the driver to reload for UMD, try 100 times.
    Similar to luwen's ubb_wait_for_driver_load but uses PCIDevice.enumerate_devices.
    """
    attempts = 0
    expected_chip_count = 32

    while attempts < 100:
        device_count = 0
        try:
            devices = PCIDevice.enumerate_devices()
            device_count = len(devices)
            if device_count == expected_chip_count:
                print(f"Driver loaded with {device_count} devices")
                return
        except Exception:
            pass

        print(f"Waiting for driver load ... {attempts} seconds (found {device_count} devices)")
        time.sleep(1)
        attempts += 1

    raise Exception(
        f"Driver not loaded with {expected_chip_count} devices after 100 seconds... giving up"
    )

def glx_6u_trays_reset(
    reinit: bool = True,
    ubb_num: str = "0xF",
    dev_num: str = "0xFF",
    op_mode: str = "0x0",
    reset_time: str = "0xF",
    print_status: bool = True,
    use_umd: bool = False,
):
    """
    Reset the WH asics on the galaxy systems with the following steps:
    1. Reset the trays with ipmi command (or UMD warm reset)
    2. Wait for 30s
    3. Reinit all chips

    Args:
        reinit: Whether to reinitialize the chips after reset.
        ubb_num: The UBB number to reset. 0x0~0xF (bit map)
        dev_num: The device number to reset. 0x0~0xFF(bit map)
        op_mode: The operation mode to use.
        reset_time: The reset time to use. resolution 10ms (ex. 0xF => 15 => 150ms)
        print_status: Whether to print out animations while detecting chips.
        use_umd: Whether to use UMD (WarmReset.ubb_warm_reset) or pyluwen (run_wh_ubb_ipmi_reset)
    """
    print(
        CMD_LINE_COLOR.PURPLE,
        "Resetting WH Galaxy trays with reset command...",
        CMD_LINE_COLOR.ENDC,
    )

    if use_umd:
        if ubb_num != "0xF" or dev_num != "0xFF" or op_mode != "0x0" or reset_time != "0xF":
            print(
                CMD_LINE_COLOR.RED,
                "Error: UMD warm reset only supports full galaxy reset (ubb_num=0xF, dev_num=0xFF, op_mode=0x0, reset_time=0xF)",
                CMD_LINE_COLOR.ENDC,
            )
            sys.exit(1)
        WarmReset.ubb_warm_reset(timeout_s=100.0)
        timed_wait(30)
        umd_ubb_wait_for_driver_load()
    else:
        run_wh_ubb_ipmi_reset(ubb_num, dev_num, op_mode, reset_time)
        timed_wait(30)
        run_ubb_wait_for_driver_load()

    print(
        CMD_LINE_COLOR.PURPLE,
        "Re-initializing boards after reset....",
        CMD_LINE_COLOR.ENDC,
    )
    if not reinit:
        print(
            CMD_LINE_COLOR.GREEN,
            "Exiting after galaxy reset without re-initializing chips.",
            CMD_LINE_COLOR.ENDC,
        )
        sys.exit(0)
    try:
        chips = detect_chips_with_callback(
            local_only=True, ignore_ethernet=True, print_status=print_status
        )
    except Exception as e:
        print(
            CMD_LINE_COLOR.RED,
            f"Error when re-initializing chips!\n {e}",
            CMD_LINE_COLOR.ENDC,
        )
        sys.exit(1)

    if ubb_num == "0xF":
        check_wh_galaxy_eth_link_status(chips)
    print(
        CMD_LINE_COLOR.GREEN,
        f"Re-initialized {len(chips)} boards after reset. Exiting...",
        CMD_LINE_COLOR.ENDC,
    )
    sys.exit(0)
