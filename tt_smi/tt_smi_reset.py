# SPDX-FileCopyrightText: © 2026 Tenstorrent Inc.
# SPDX-License-Identifier: Apache-2.0

"""
Reset-related functions for tt-smi: PCI board reset, galaxy 6U tray reset, and helpers.
"""

import os
import re
import sys
import time
from enum import Enum
from dataclasses import dataclass
from typing import List, Optional, Tuple, Union, Dict

from tt_tools_common.ui_common.themes import CMD_LINE_COLOR
from tt_tools_common.reset_common.wh_reset import WHChipReset
from tt_tools_common.reset_common.bh_reset import BHChipReset
from tt_smi.tt_smi_utils import get_dev_id_from_bdf
from pyluwen import (
    PciChip,
    pci_scan,
    run_wh_ubb_ipmi_reset,
    run_ubb_wait_for_driver_load,
)
from tt_umd import (
    WarmReset,
    PCIDevice,
    TopologyDiscovery,
)
from tt_tools_common.utils_common.tools_utils import (
    detect_chips_with_callback,
)

"""
Reset -r target formats: UMD logical ID (int), PCI BDF (0000:aa:00.0), /dev/tenstorrent/<int>

tt-smi -r <UMD logical ID>   e.g. tt-smi -r 0  or  tt-smi -r 0,1,2
tt-smi -r <PCI BDF>          e.g. tt-smi -r 0000:0a:00.0
tt-smi -r /dev/tenstorrent/<ID>   e.g. tt-smi -r /dev/tenstorrent/0  or  tt-smi -r /dev/tenstorrent/0, /dev/tenstorrent/1
Mixing types is not allowed; parse_reset_input returns a single ResetInput with one ResetType.
"""

DEV_TENSTORRENT_PREFIX = "/dev/tenstorrent/"
PCI_BDF_FULL_RE = re.compile(r"^[0-9a-fA-F]{4}:[0-9a-fA-F]{2}:[0-9a-fA-F]{2}\.[0-9a-fA-F]$")

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
    if PCI_BDF_FULL_RE.match(token):
        return ("bdf", token)
    if token.lstrip("-").isdigit():
        return ("int", int(token))
    raise ValueError(
        f"Invalid reset target: '{token}'. "
        "Use UMD logical ID (integer), PCI BDF (e.g. 0000:0a:00.0), or /dev/tenstorrent/<integer>."
    )

def parse_reset_input(value: list) -> ResetInput:
    """
    Parse -r / --reset arguments. All tokens must be the same type (no mixing).
    Returns a single ResetInput:
    - ResetInput(type=ALL) for no input or "all".
    - ResetInput(type=PCI_BDF, value=[...]) for PCI BDFs.
    - ResetInput(type=UMD_LOGICAL_ID, value=[...]) for UMD logical IDs.
    - ResetInput(type=DEV_TENSTORRENT_ID, value=[...]) for /dev/tenstorrent/<int> paths.
    Mixing types is an error to prevent accidentally resetting the same device more than once.
    """
    if value is None or len(value) == 0:
        return ResetInput(type=ResetType.ALL, value=None)
    tokens = [t.strip() for raw in value for t in raw.split(",") if t.strip()]
    if not tokens or (len(tokens) == 1 and tokens[0].lower() == "all"):
        return ResetInput(type=ResetType.ALL, value=None)

    KIND_TO_RESET_TYPE = {
        "bdf": ResetType.PCI_BDF,
        "int": ResetType.UMD_LOGICAL_ID,
        "dev_path": ResetType.DEV_TENSTORRENT_ID,
    }

    seen: set = set()
    values: list = []
    reset_type: Optional[ResetType] = None

    for token in tokens:
        try:
            kind, val = _classify_reset_token(token)
            token_type = KIND_TO_RESET_TYPE[kind]
            if reset_type is not None and token_type != reset_type:
                raise ValueError(
                    f"Mixed reset types are not allowed. "
                    f"Got '{token}' which is a {token_type.name}, but earlier tokens were {reset_type.name}. "
                    "Use only one type per invocation."
                )
            reset_type = token_type
            if val not in seen:
                seen.add(val)
                values.append(val)
        except ValueError as e:
            print(CMD_LINE_COLOR.RED, str(e), CMD_LINE_COLOR.ENDC)
            sys.exit(1)

    if reset_type in (ResetType.UMD_LOGICAL_ID, ResetType.DEV_TENSTORRENT_ID):
        values = sorted(values)

    return ResetInput(type=reset_type, value=values)

def timed_wait(seconds):
    print("\033[93mWaiting for {} seconds: 0\033[0m".format(seconds), end='')
    sys.stdout.flush()

    for i in range(1, seconds + 1):
        time.sleep(1)
        print("\r\033[93mWaiting for {} seconds: {}\033[0m".format(seconds, i), end='')
        sys.stdout.flush()
    print()

# Keep this function for now, but it's not used anywhere in the codebase. 
# It is the check that is used in Funtest for Galaxy systems and we might need to reference it later.
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

def umd_pci_warm_reset(
    reset_input: ResetInput,
):
    """
    Reset the PCI devices using UMD warm reset.
    """
    chips = PCIDevice.enumerate_devices_info()
    # check if any of the chips are galaxy
    is_galaxy = False
    for info in chips.values():
        if info.subsystem_id in {0x35, 0x47}:
            is_galaxy = True
            break
    if is_galaxy:
        print(
            CMD_LINE_COLOR.YELLOW,
            "CPLD FW v1.16 or higher is required to use tt-smi -r on Galaxy systems.",
            "If tt-smi -r fails, please continue to use tt-smi -glx_reset instead and contact your system administrator to request a CPLD update.",
            CMD_LINE_COLOR.ENDC,
        )
    secondary_bus_reset = not is_galaxy
    
    reset_indices = reset_input.value
    if reset_input.type == ResetType.ALL:
        reset_indices = list(chips.keys())
        print(f"Resetting all PCI devices: {reset_indices}")
        WarmReset.warm_reset(reset_indices, secondary_bus_reset=secondary_bus_reset)
        return
    if reset_input.type == ResetType.UMD_LOGICAL_ID:
        print(f"Resetting UMD logical IDs: {reset_input.value}")
        WarmReset.warm_reset_chip_id(reset_indices, secondary_bus_reset=secondary_bus_reset)
        return
    if reset_input.type == ResetType.PCI_BDF:
        print(f"Resetting PCI BDFs: {reset_input.value}")
        WarmReset.warm_reset_pci_bdfs(reset_indices, secondary_bus_reset=secondary_bus_reset)
        return
    if reset_input.type == ResetType.DEV_TENSTORRENT_ID:
        print(f"Resetting /dev/tenstorrent IDs: {reset_input.value}")
        WarmReset.warm_reset(reset_indices, secondary_bus_reset=secondary_bus_reset)
        return
    raise ValueError(f"Invalid reset type: {reset_input.type}")

def luwen_pci_reset(
    reset_input: ResetInput,
):
    """
    Reset the PCI devices using luwen (pyluwen): discover board type per device
    and call WHChipReset or BHChipReset as appropriate.
    """
    if reset_input.type == ResetType.ALL:
        reset_indices = pci_scan()
    elif reset_input.type == ResetType.UMD_LOGICAL_ID:
        print(
            CMD_LINE_COLOR.RED,
            "UMD ID reset not supported for luwen. Please use tt-smi -r /dev/tenstorrent/<id> or tt-smi -r <PCI BDF> instead.",
            CMD_LINE_COLOR.ENDC,
        )
        sys.exit(1)
    elif reset_input.type == ResetType.PCI_BDF:
        print(f"Resetting PCI BDFs: {reset_input.value}")
        reset_indices = [get_dev_id_from_bdf(bdf) for bdf in reset_input.value]
    elif reset_input.type == ResetType.DEV_TENSTORRENT_ID:
        print(f"Resetting /dev/tenstorrent IDs: {reset_input.value}")
        reset_indices = list(reset_input.value)
    else:
        raise ValueError(f"Invalid reset type: {reset_input.type}")

    reset_wh_pci_idx: List[int] = []
    reset_bh_pci_idx: List[int] = []
    board_types = set()
    for pci_idx in reset_indices:
        try:
            chip = PciChip(pci_interface=pci_idx)
        except Exception as e:
            print(e, file=sys.stderr)
            print(
                CMD_LINE_COLOR.RED,
                f"Error accessing board at PCI index {pci_idx}! Use -ls to see all devices available to reset",
                CMD_LINE_COLOR.ENDC,
            )
            continue
        try:
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
                sys.exit(1)
        finally:
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
    if reset_wh_pci_idx:
        WHChipReset().full_lds_reset(
            pci_interfaces=reset_wh_pci_idx, secondary_bus_reset=secondary_bus_reset
        )
    if reset_bh_pci_idx:
        BHChipReset().full_lds_reset(
            pci_interfaces=reset_bh_pci_idx, secondary_bus_reset=secondary_bus_reset
        )


def pci_board_reset(
    reset_input: ResetInput,
    reinit: bool = False,
    print_status: bool = True,
    use_umd: bool = False,
):
    """Given a list of ResetInput "reset_input", reset the PCI devices using UMD warm reset or luwen (pyluwen)."""

    if use_umd:
        umd_pci_warm_reset(reset_input)
    else:
        luwen_pci_reset(reset_input)

    if reinit:
        print(
            CMD_LINE_COLOR.PURPLE,
            "Re-initializing boards after reset....",
            CMD_LINE_COLOR.ENDC,
        )
        try:
            if use_umd:
                TopologyDiscovery.discover()
            else:
                os.environ["RUST_BACKTRACE"] = "full"
                detect_chips_with_callback(print_status=print_status)
        except Exception as e:
            print(
                CMD_LINE_COLOR.RED,
                f"Error when re-initializing chips!\n {e}",
                CMD_LINE_COLOR.ENDC,
            )
            sys.exit(1)


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

    print(
        CMD_LINE_COLOR.GREEN,
        f"Re-initialized {len(chips)} boards after reset. Exiting...",
        CMD_LINE_COLOR.ENDC,
    )
    sys.exit(0)
