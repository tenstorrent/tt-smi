# SPDX-FileCopyrightText: © 2026 Tenstorrent Inc.
# SPDX-License-Identifier: Apache-2.0

"""
Reset-related functions for tt-smi: PCI board reset, galaxy 6U tray reset, and helpers.

Device-selection parsing for ``-r`` / ``--reset`` lives in
:mod:`tt_smi.device_input`; this module focuses on the reset mechanism itself.
"""

import glob
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from typing import List

from tt_tools_common.ui_common.themes import CMD_LINE_COLOR
from tt_tools_common.reset_common.wh_reset import WHChipReset
from tt_tools_common.reset_common.bh_reset import BHChipReset
from tt_smi.utils import (
    discover,
    get_dev_id_from_bdf,
    get_driver_version,
    is_driver_version_at_least,
    IoctlResetFlags,
    reset_device_ioctl,
)
from tt_smi import constants
from tt_smi.constants import get_default_discovery_options
from tt_smi.device_input import SmiDeviceInput, SmiDeviceTargetKind
from pyluwen import (
    PciChip,
    pci_scan,
)
from tt_umd import (
    WarmReset,
    PCIDevice,
    ARCH,
    SmBusArcTelemetryReader,
    TelemetryTag,
    wormhole,
)
from tt_tools_common.utils_common.tools_utils import (
    detect_chips_with_callback,
)

MINIMUM_DRIVER_VERSION_GALAXY_SECONDARY_BUS_RESET = "2.7.0"
# BMC v0.05.22 adds op_mode 0x3: UBB reset without RST_RETIMER_PERST_N.
# op_mode 0x0 (legacy, also asserts RST_RETIMER_PERST_N and resets NVMe retimers) is not used.
MINIMUM_BMC_VERSION_NO_RETIMER_RESET = "0.05.22"
GLX_IPMI_OP_MODE_NO_RETIMER = "0x3"
GLX_EXPECTED_CHIPS = 32
GLX_EXIT_CHIPS_MISSING = 4
GLX_RECOUNT_TIMEOUT_S = 120
SYSFS_PCI = "/sys/bus/pci"


def _ipmi_output_is_param_out_of_range(output: str) -> bool:
    text = output.lower()
    return "rsp=0xc9" in text or "parameter out of range" in text


def _galaxy_ipmi_cmd(
    ubb_num: str, dev_num: str, op_mode: str, reset_time: str
) -> List[str]:
    return [
        "sudo",
        "ipmitool",
        "raw",
        "0x30",
        "0x8B",
        ubb_num,
        dev_num,
        op_mode,
        reset_time,
    ]


def _run_ipmitool(cmd: List[str]) -> subprocess.CompletedProcess:
    print(f"Executing command: {' '.join(cmd)}")
    try:
        return subprocess.run(cmd, capture_output=True, text=True)
    except FileNotFoundError as e:
        raise RuntimeError(
            "Failed to execute ipmitool. Ensure ipmitool is installed and sudo is available."
        ) from e


def _ipmi_failure_detail(result: subprocess.CompletedProcess) -> str:
    if result.returncode == 0:
        return ""
    return f"{result.stdout}{result.stderr}".strip() or f"exit code {result.returncode}"


def run_galaxy_ipmi_reset(
    ubb_num: str = "0xF",
    dev_num: str = "0xFF",
    op_mode: str = GLX_IPMI_OP_MODE_NO_RETIMER,
    reset_time: str = "0xF",
) -> None:
    """
    Issue the Galaxy UBB IPMI reset with no-retimer op_mode 0x3.

    BMC firmware v0.05.22 or later accepts 0x3. Older BMCs reject it with
    IPMI completion code 0xc9 (Parameter out of range): that raises with an
    upgrade hint. There is no fallback to legacy op_mode 0x0, which also
    resets the NVMe retimers and can drop the host's NVMe drives.
    """
    result = _run_ipmitool(
        _galaxy_ipmi_cmd(ubb_num, dev_num, op_mode, reset_time)
    )
    failure = _ipmi_failure_detail(result)
    if not failure:
        return
    if _ipmi_output_is_param_out_of_range(failure):
        failure += (
            f" (BMC < v{MINIMUM_BMC_VERSION_NO_RETIMER_RESET} does not support "
            "op_mode 0x3; upgrade BMC firmware)"
        )
    raise RuntimeError(f"IPMI command failed: {failure}")


def should_use_secondary_bus_reset(is_galaxy: bool) -> bool:
    """
    Whether to issue secondary bus reset (RESET_PCIE_LINK IOCTL) before ASIC reset.

    Non-Galaxy systems always use secondary bus reset. Galaxy systems use it only
    when KMD is >= 2.7.0.
    """
    if not is_galaxy:
        return True
    driver_version = get_driver_version()
    if driver_version is None:
        return False
    return is_driver_version_at_least(
        driver_version, MINIMUM_DRIVER_VERSION_GALAXY_SECONDARY_BUS_RESET
    )


def parallel_reset_device_ioctl(device_ids: List[int], flag: int) -> List[int]:
    """
    Issue reset ioctl on each device in parallel.

    Returns interface IDs where the ioctl did not complete successfully.
    """
    if not device_ids:
        return []

    def ioctl_one(interface_id: int) -> tuple[int, bool]:
        # A chip that cannot be opened counts as failed; the others go on.
        try:
            return interface_id, reset_device_ioctl(interface_id, flag)
        except OSError as e:
            print(f"/dev/tenstorrent/{interface_id}: {e}")
            return interface_id, False

    failed: List[int] = []
    with ThreadPoolExecutor(max_workers=len(device_ids)) as pool:
        for interface_id, ok in pool.map(ioctl_one, device_ids):
            if not ok:
                failed.append(interface_id)
    return failed


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
    reset_input: SmiDeviceInput,
):
    """
    Reset the PCI devices using UMD warm reset.
    """
    chips = PCIDevice.enumerate_devices_info()
    # check if any of the chips are galaxy
    is_galaxy = False
    for info in chips.values():
        if info.subsystem_id in {0x35, 0x47, 0x202}:
            is_galaxy = True
            break
    if is_galaxy:
        print(
            CMD_LINE_COLOR.YELLOW,
            "CPLD FW v1.16 or higher is required to use tt-smi -r on Galaxy systems.",
            "If tt-smi -r fails, please continue to use tt-smi -glx_reset instead and contact your system administrator to request a CPLD update.",
            CMD_LINE_COLOR.ENDC,
        )
    secondary_bus_reset = should_use_secondary_bus_reset(is_galaxy)

    reset_indices = reset_input.value
    if reset_input.type == SmiDeviceTargetKind.ALL:
        reset_indices = list(chips.keys())
        print(f"Resetting all PCI devices: {reset_indices}")
        WarmReset.warm_reset(reset_indices, secondary_bus_reset=secondary_bus_reset)
        return
    if reset_input.type == SmiDeviceTargetKind.UMD_LOGICAL_ID:
        print(f"Resetting UMD logical IDs: {reset_input.value}")
        WarmReset.warm_reset_chip_id(reset_indices, secondary_bus_reset=secondary_bus_reset)
        return
    if reset_input.type == SmiDeviceTargetKind.PCI_BDF:
        print(f"Resetting PCI BDFs: {reset_input.value}")
        WarmReset.warm_reset_pci_bdfs(reset_indices, secondary_bus_reset=secondary_bus_reset)
        return
    if reset_input.type == SmiDeviceTargetKind.DEV_TENSTORRENT_ID:
        print(f"Resetting /dev/tenstorrent IDs: {reset_input.value}")
        WarmReset.warm_reset(reset_indices, secondary_bus_reset=secondary_bus_reset)
        return
    raise ValueError(f"Invalid reset type: {reset_input.type}")

def luwen_pci_reset(
    reset_input: SmiDeviceInput,
):
    """
    Reset the PCI devices using luwen (pyluwen): discover board type per device
    and call WHChipReset or BHChipReset as appropriate.
    """
    if reset_input.type == SmiDeviceTargetKind.ALL:
        reset_indices = pci_scan()
    elif reset_input.type == SmiDeviceTargetKind.UMD_LOGICAL_ID:
        print(
            CMD_LINE_COLOR.RED,
            "UMD ID reset not supported for luwen. Please use tt-smi -r /dev/tenstorrent/<id> or tt-smi -r <PCI BDF> instead.",
            CMD_LINE_COLOR.ENDC,
        )
        sys.exit(1)
    elif reset_input.type == SmiDeviceTargetKind.PCI_BDF:
        print(f"Resetting PCI BDFs: {reset_input.value}")
        reset_indices = [get_dev_id_from_bdf(bdf) for bdf in reset_input.value]
    elif reset_input.type == SmiDeviceTargetKind.DEV_TENSTORRENT_ID:
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

    is_galaxy = board_types <= {0x35, 0x47, 0x202}
    if is_galaxy:
        print(
            CMD_LINE_COLOR.YELLOW,
            "CPLD FW v1.16 or higher is required to use tt-smi -r on Galaxy systems.",
            "If tt-smi -r fails, please continue to use tt-smi -glx_reset instead and contact your system administrator to request a CPLD update.",
            CMD_LINE_COLOR.ENDC,
        )

    secondary_bus_reset = should_use_secondary_bus_reset(is_galaxy)
    if reset_wh_pci_idx:
        WHChipReset().full_lds_reset(
            pci_interfaces=reset_wh_pci_idx, secondary_bus_reset=secondary_bus_reset
        )
    if reset_bh_pci_idx:
        BHChipReset().full_lds_reset(
            pci_interfaces=reset_bh_pci_idx, secondary_bus_reset=secondary_bus_reset
        )


def pci_board_reset(
    reset_input: SmiDeviceInput,
    reinit: bool = False,
    print_status: bool = True,
    use_umd: bool = False,
    eth_train_skip: bool = False,
):
    """Given a ``SmiDeviceInput`` ``reset_input``, reset the PCI devices using UMD warm reset or luwen (pyluwen)."""

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
                options = get_default_discovery_options()
                if eth_train_skip:
                    options.discover_remote_devices = False
                    options.wait_on_ethernet_link_training = False
                discover(options)
            else:
                os.environ["RUST_BACKTRACE"] = "full"
                detect_chips_with_callback(print_status=print_status, ignore_ethernet=eth_train_skip)
        except Exception as e:
            print(
                CMD_LINE_COLOR.RED,
                f"Error when re-initializing chips!\n {e}",
                CMD_LINE_COLOR.ENDC,
            )
            sys.exit(1)


def _bmc_problem() -> str:
    """Return "" if the BMC answers in-band Get Device ID, else what went wrong."""
    try:
        result = subprocess.run(
            ["sudo", "ipmitool", "mc", "info"], capture_output=True, text=True, timeout=15
        )
    except FileNotFoundError:
        return "ipmitool not found"
    except subprocess.TimeoutExpired:
        return "no answer within 15 s"
    return _ipmi_failure_detail(result)


def _glx_chips() -> tuple:
    """Tenstorrent chips in sysfs: (BDF -> PCI device id of every chip listed,
    set of BDFs that answer config reads (not all ones) with the driver bound)."""
    known, present = {}, set()
    for path in glob.glob(os.path.join(SYSFS_PCI, "devices", "*")):
        bdf = os.path.basename(path)
        try:
            with open(os.path.join(path, "vendor")) as f:
                if f.read().strip() != "0x1e52":
                    continue
            with open(os.path.join(path, "device")) as f:
                known[bdf] = f.read().strip()
            with open(os.path.join(path, "config"), "rb") as f:
                if f.read(2) != b"\x52\x1e":
                    continue
            if os.path.basename(os.readlink(os.path.join(path, "driver"))) == "tenstorrent":
                present.add(bdf)
        except OSError:
            continue
    return known, present


def _glx_tray(bdf: str, is_wh: bool) -> int:
    """Tray 1-4 of a Galaxy chip from its PCI bus number (0 if unknown)."""
    bus = int(bdf.split(":")[-2], 16) & 0xF0
    ubb_bus_ids = constants.WH_UBB_BUS_IDS if is_wh else constants.BH_UBB_BUS_IDS
    return next((tray for tray, base in ubb_bus_ids.items() if base == bus), 0)


def _pci_rescan():
    path = os.path.join(SYSFS_PCI, "rescan")
    try:
        with open(path, "w") as f:
            f.write("1")
        return
    except PermissionError:
        result = subprocess.run(
            ["sudo", "tee", path], input="1", capture_output=True, text=True
        )
        error = _ipmi_failure_detail(result)
    except OSError as e:
        error = str(e)
    if error:
        print(f"{CMD_LINE_COLOR.YELLOW}Warning: PCI rescan failed: {error}{CMD_LINE_COLOR.ENDC}")


def _wait_for_glx_chips(expected: set) -> tuple:
    """Rescan and recount until every chip seen before the reset is back and
    there are at least GLX_EXPECTED_CHIPS, or GLX_RECOUNT_TIMEOUT_S passes."""
    deadline = time.monotonic() + GLX_RECOUNT_TIMEOUT_S
    while True:
        known, present = _glx_chips()
        print(f"Recount after reset: {len(present)}/{GLX_EXPECTED_CHIPS} chips present")
        done = not (expected - present) and len(present) >= GLX_EXPECTED_CHIPS
        if done or time.monotonic() > deadline:
            return known, present
        _pci_rescan()
        time.sleep(10)


def _read_heartbeat(chip, use_umd: bool) -> int:
    # Same telemetry readers as backend.get_smbus_board_info.
    if use_umd:
        if chip.get_arch() == ARCH.WORMHOLE_B0:
            reader = SmBusArcTelemetryReader(chip)
            return reader.read_entry(wormhole.TelemetryTag.ARC0_HEALTH.value)
        reader = chip.get_arc_telemetry_reader()
        return reader.read_entry(TelemetryTag.TIMER_HEARTBEAT.value)
    if chip.as_bh():
        return chip.as_bh().get_telemetry().timer_heartbeat
    return chip.as_wh().get_telemetry().arc0_health


def _alive_chips(chips_by_bdf: dict, use_umd: bool) -> set:
    """BDFs whose ARC heartbeat advances within about a second."""
    first = {}
    for bdf, chip in chips_by_bdf.items():
        try:
            first[bdf] = _read_heartbeat(chip, use_umd)
        except Exception as e:
            print(f"{bdf}: heartbeat read failed: {e}")
    time.sleep(1.5)
    alive = set()
    for bdf, value in first.items():
        try:
            if _read_heartbeat(chips_by_bdf[bdf], use_umd) != value:
                alive.add(bdf)
        except Exception as e:
            print(f"{bdf}: heartbeat read failed: {e}")
    return alive


def _full_bdf(bdf: str) -> str:
    bdf = bdf.lower()
    return bdf if bdf.count(":") == 2 else f"0000:{bdf}"


def glx_6u_trays_reset(
    reinit: bool = True,
    ubb_num: str = "0xF",
    dev_num: str = "0xFF",
    op_mode: str = GLX_IPMI_OP_MODE_NO_RETIMER,
    reset_time: str = "0xF",
    print_status: bool = True,
    use_umd: bool = False,
):
    """
    Reset the ASICs on the galaxy systems with the following steps:
    1. Check that the BMC answers; if not, reset nothing
    2. Perform USER_RESET ioctl on all chips
    3. Reset the trays with IPMI (op_mode 0x3, no PCIe retimer)
    4. Wait for 30s, then rescan and recount until all 32 chips are back
    5. Perform POST_RESET ioctl on all chips
    6. Reinit all chips and check that each chip's ARC heartbeat advances
    Exits GLX_EXIT_CHIPS_MISSING (4) naming each chip not back, by BDF and tray.

    Args:
        reinit: Whether to reinitialize the chips after reset.
        ubb_num: The UBB number to reset. 0x0~0xF (bit map)
        dev_num: The device number to reset. 0x0~0xFF(bit map)
        op_mode: IPMI operation mode. 0x3 = reset without PCIe retimer (BMC v0.05.22+).
        reset_time: The reset time to use. resolution 10ms (ex. 0xF => 15 => 150ms)
        print_status: Whether to print out animations while detecting chips.
        use_umd: Whether to enumerate/reinit with UMD or pyluwen.
    """
    # First, check if we're trying to do anything other than a full reset
    if (
        ubb_num != "0xF"
        or dev_num != "0xFF"
        or op_mode != GLX_IPMI_OP_MODE_NO_RETIMER
        or reset_time != "0xF"
    ):
        print(
            CMD_LINE_COLOR.RED,
            "Error: Galaxy 6U IPMI reset only supports full Galaxy reset ",
            f"(ubb_num=0xF, dev_num=0xFF, op_mode={GLX_IPMI_OP_MODE_NO_RETIMER}, reset_time=0xF)",
            CMD_LINE_COLOR.ENDC,
        )
        sys.exit(1)
    bmc_problem = _bmc_problem()
    if bmc_problem:
        print(
            CMD_LINE_COLOR.RED,
            f"Error: BMC does not answer in-band (ipmitool mc info): {bmc_problem}\n"
            "No chip was reset.",
            CMD_LINE_COLOR.ENDC,
        )
        sys.exit(1)
    known_before, _ = _glx_chips()
    print(
        CMD_LINE_COLOR.PURPLE,
        "Resetting WH Galaxy trays with reset command...",
        CMD_LINE_COLOR.ENDC,
    )

    if use_umd:
        device_ids = list(PCIDevice.enumerate_devices())
    else:
        device_ids = pci_scan()

    if should_use_secondary_bus_reset(True):
        print(
            CMD_LINE_COLOR.BLUE,
            f"Issuing RESET_PCIE_LINK on {len(device_ids)} devices before IPMI reset...",
            CMD_LINE_COLOR.ENDC,
        )
        for interface_id in parallel_reset_device_ioctl(
            device_ids, IoctlResetFlags.RESET_PCIE_LINK
        ):
            print(
                CMD_LINE_COLOR.YELLOW,
                f"Warning: Secondary bus reset not completed for device /dev/tenstorrent/{interface_id}. Continuing...",
                CMD_LINE_COLOR.ENDC,
            )

    print(
        CMD_LINE_COLOR.BLUE,
        f"Issuing USER_RESET on {len(device_ids)} devices before IPMI reset...",
        CMD_LINE_COLOR.ENDC,
    )
    for interface_id in parallel_reset_device_ioctl(
        device_ids, IoctlResetFlags.USER_RESET
    ):
        print(
            CMD_LINE_COLOR.YELLOW,
            f"Warning: USER_RESET did not complete for device /dev/tenstorrent/{interface_id}. Continuing...",
            CMD_LINE_COLOR.ENDC,
        )

    # IPMI reset without PCIe retimer (BMC v0.05.22+). Do not fall back to
    # op_mode 0x0 — that asserts RST_RETIMER_PERST_N and can drop NVMe.
    run_galaxy_ipmi_reset(ubb_num, dev_num, op_mode, reset_time)
    timed_wait(30)
    known, present = _wait_for_glx_chips(set(known_before))

    # Issue POST_RESET ioctl on all devices after they reappear
    if use_umd:
        post_reset_ids = list(PCIDevice.enumerate_devices())
    else:
        post_reset_ids = pci_scan()
    print(
        CMD_LINE_COLOR.BLUE,
        f"Issuing POST_RESET on {len(post_reset_ids)} devices after IPMI reset...",
        CMD_LINE_COLOR.ENDC,
    )
    post_reset_failed = parallel_reset_device_ioctl(
        post_reset_ids, IoctlResetFlags.POST_RESET
    )
    for interface_id in post_reset_failed:
        print(
            CMD_LINE_COLOR.RED,
            f"Error: POST_RESET failed for device /dev/tenstorrent/{interface_id}.",
            CMD_LINE_COLOR.ENDC,
        )

    alive = _reinit_and_check_heartbeat(use_umd, print_status) if reinit else present

    known.update(known_before)
    is_wh = "0x401e" in known.values()
    missing = sorted(set(known) - alive)
    for bdf in missing:
        print(
            CMD_LINE_COLOR.RED,
            f"Error: chip {bdf} (tray {_glx_tray(bdf, is_wh)}) did not come back after reset.",
            CMD_LINE_COLOR.ENDC,
        )
    if missing or len(alive) < GLX_EXPECTED_CHIPS:
        trays = [_glx_tray(bdf, is_wh) for bdf in alive]
        print(
            CMD_LINE_COLOR.RED,
            f"Only {len(alive)}/{GLX_EXPECTED_CHIPS} chips back. Chips per tray: "
            + " ".join(f"{tray}:{trays.count(tray)}" for tray in range(1, 5)),
            CMD_LINE_COLOR.ENDC,
        )
        sys.exit(GLX_EXIT_CHIPS_MISSING)
    if post_reset_failed:
        sys.exit(1)
    print(
        CMD_LINE_COLOR.GREEN,
        f"All {len(alive)} chips back after reset. Exiting...",
        CMD_LINE_COLOR.ENDC,
    )
    sys.exit(0)


def _reinit_and_check_heartbeat(use_umd: bool, print_status: bool) -> set:
    """Reinit the local chips and return the BDFs whose ARC heartbeat advances."""
    print(
        CMD_LINE_COLOR.PURPLE,
        "Re-initializing boards after reset....",
        CMD_LINE_COLOR.ENDC,
    )
    try:
        if use_umd:
            options = get_default_discovery_options()
            options.discover_remote_devices = False
            options.wait_on_ethernet_link_training = False
            cluster_descriptor, devices = discover(options)
            bdfs = cluster_descriptor.get_chip_pci_bdfs()
            chips_by_bdf = {_full_bdf(bdfs[i]): chip for i, chip in devices.items()}
        else:
            os.environ["RUST_BACKTRACE"] = "full"
            chips = detect_chips_with_callback(
                local_only=True, ignore_ethernet=True, print_status=print_status
            )
            chips_by_bdf = {_full_bdf(chip.get_pci_bdf()): chip for chip in chips}
    except Exception as e:
        print(
            CMD_LINE_COLOR.RED,
            f"Error when re-initializing chips!\n {e}",
            CMD_LINE_COLOR.ENDC,
        )
        sys.exit(1)
    print(f"Re-initialized {len(chips_by_bdf)} chips; checking ARC heartbeats...")
    return _alive_chips(chips_by_bdf, use_umd)
