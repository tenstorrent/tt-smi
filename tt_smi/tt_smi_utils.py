# SPDX-FileCopyrightText: © 2026 Tenstorrent Inc.
# SPDX-License-Identifier: Apache-2.0

"""
Utility functions and constants for tt-smi (hex conversion, board type, logging path, etc.).
"""

import os
import sys
import glob
from importlib.metadata import version

from tt_tools_common.ui_common.themes import CMD_LINE_COLOR

from tt_smi import constants


LOG_FOLDER = os.path.expanduser("~/tt_smi_logs/")

def hex_to_date(hexdate: int, include_time=True):
    """Converts a date given in hex from format 0xYMDDHHMM to string YYYY-MM-DD HH:MM"""
    if hexdate == 0 or hexdate == 0xFFFFFFFF:
        return "N/A"

    year = (hexdate >> 28 & 0xF) + 2020
    month = hexdate >> 24 & 0xF
    day = hexdate >> 16 & 0xFF
    hour = hexdate >> 8 & 0xFF
    minute = hexdate & 0xFF

    date = f"{year:04}-{month:02}-{day:02}"

    if include_time:
        date += f" {hour:02}:{minute:02}"

    return date

def hex_to_semver_eth_wh(hexsemver: int):
    """
    Converts a semantic version string from format 0x061000 to 6.1.0
    Used in WH firmware only.
    """
    major = hexsemver >> 16 & 0xFF
    minor = hexsemver >> 12 & 0xF
    patch = hexsemver & 0xFFF

    return f"{major}.{minor}.{patch}"

def hex_to_semver_eth(hexsemver: int):
    """
    Converts a semantic version string from format 0x060100 to 6.1.0
    """
    major = hexsemver >> 16 & 0xFF
    minor = hexsemver >> 8 & 0xFF
    patch = hexsemver & 0xFF

    return f"{major}.{minor}.{patch}"

def hex_to_semver_m3_fw(hexsemver: int):
    """Converts a semantic version string from format 0x0A0F0100 to 10.15.1"""
    major = hexsemver >> 24 & 0xFF
    minor = hexsemver >> 16 & 0xFF
    patch = hexsemver >> 8 & 0xFF
    ver = hexsemver >> 0 & 0xFF

    return f"{major}.{minor}.{patch}.{ver}"


def get_board_type(board_id: str) -> str:
    """
    Get board type from board ID string.
    Ex:
        Board ID: AA-BBBBB-C-D-EE-FF-XXX
                   ^     ^ ^ ^  ^  ^   ^
                   |     | | |  |  |   +- XXX
                   |     | | |  |  +----- FF
                   |     | | |  +-------- EE
                   |     | | +----------- D
                   |     | +------------- C = Revision
                   |     +--------------- BBBBB = Unique Part Identifier (UPI)
                   +--------------------- AA
    """
    if board_id == "N/A":
        return "N/A"
    serial_num = int(f"0x{board_id}", base=16)
    upi = (serial_num >> 36) & 0xFFFFF

    # Grayskull cards
    if upi == 0x3:
        return "e150"
    elif upi == 0xA:
        return "e300"
    elif upi == 0x7:
        return "e75"

    # Wormhole cards
    elif upi == 0x8:
        return "nb_cb"
    elif upi == 0xB:
        return "wh_4u"
    elif upi == 0x14:
        return "n300"
    elif upi == 0x18:
        return "n150"
    elif upi == 0x35:
        return "tt-galaxy-wh"

    # Blackhole cards
    elif upi == 0x36:
        return "bh-scrappy"
    elif upi == 0x43:
        return "p100a"
    elif upi == 0x40:
        return "p150a"
    elif upi == 0x41:
        return "p150b"
    elif upi == 0x42:
        return "p150c"
    elif upi == 0x44:
        return "p300b"
    elif upi == 0x45:
        return "p300a"
    elif upi == 0x46:
        return "p300c"
    elif upi == 0x47:
        return "tt-galaxy-bh"
    else:
        return "N/A"


def convert_signed_16_16_to_float(value: int) -> float:
    """Convert signed 16.16 to float"""
    if value & (1 << (32 - 1)):  # if the value is negative (two's complement)
        value -= 1 << 32  # convert to negative value
    return value / 65536.0


def dict_from_public_attrs(obj: object) -> dict:
    """Parse an object's public attributes into a dictionary"""
    all_attrs = obj.__dir__()
    public = [attr for attr in all_attrs if not attr.startswith("_")]
    return {attr: getattr(obj, attr) for attr in public}


def get_host_software_versions() -> dict:
    """Return dict of tt_smi, pyluwen, tt_umd package versions."""
    return {
        "tt_smi": version("tt_smi"),
        "pyluwen": version("pyluwen"),
        "tt_umd": version("tt_umd"),
    }


def check_is_galaxy(backend, user_arg: str):
    """Check if the board is a Galaxy board."""
    if len(backend.device_infos) < 1:
        print(
            CMD_LINE_COLOR.RED,
            "No devices detected.",
            CMD_LINE_COLOR.ENDC,
        )
        sys.exit(1)

    if backend.device_infos[0]["board_type"] not in constants.GLX_BOARD_TYPES:
        print(
            CMD_LINE_COLOR.RED,
            f"This is not a Galaxy board, `{user_arg}` is only supported on Galaxy.",
            CMD_LINE_COLOR.ENDC,
        )
        sys.exit(1)


def is_vm() -> bool:
    """Return True if running inside a VM (e.g. hypervisor present in /proc/cpuinfo)."""
    try:
        with open("/proc/cpuinfo", "r") as f:
            if "hypervisor" in f.read():
                return True
    except (FileNotFoundError, PermissionError) as e:
        print(
            CMD_LINE_COLOR.YELLOW,
            f"Cannot access /proc/cpuinfo: {e}",
            CMD_LINE_COLOR.ENDC,
        )
    return False

def get_fw_bundle_version(smbus_telem_info) -> int:
    """We have two possible keys for the firmware bundle version: FLASH_BUNDLE_VERSION and FW_BUNDLE_VERSION.
    We need to return the version of the firmware bundle.
    """
    if "FW_BUNDLE_VERSION" in smbus_telem_info:
        return smbus_telem_info["FW_BUNDLE_VERSION"]
    elif "FLASH_BUNDLE_VERSION" in smbus_telem_info:
        return smbus_telem_info["FLASH_BUNDLE_VERSION"]
    else:
        return None

def get_dev_id_from_bdf(bdf: str) -> int:
    """
    Resolve /dev/tenstorrent index N from a PCI BDF. Validates that the device
    exists under sysfs, is a Tenstorrent device (tenstorrent/tenstorrent!N present),
    then returns N. On failure prints error in red and exits with code 1.
    Path: /sys/bus/pci/devices/0000:BB:DD.F/tenstorrent/tenstorrent!N -> N is the dev index.
    """
    dev_sysfs = f"/sys/bus/pci/devices/{bdf}"
    if not os.path.exists(dev_sysfs):
        print(CMD_LINE_COLOR.RED, f"Device does not exist: {dev_sysfs}", CMD_LINE_COLOR.ENDC, file=sys.stderr)
        sys.exit(1)

    tenstorrent_dir = os.path.join(dev_sysfs, "tenstorrent")
    if not os.path.isdir(tenstorrent_dir):
        print(CMD_LINE_COLOR.RED, f"Device exists but is not a Tenstorrent device: {dev_sysfs}", CMD_LINE_COLOR.ENDC, file=sys.stderr)
        sys.exit(1)

    pattern = f"/sys/bus/pci/devices/{bdf}/tenstorrent/tenstorrent!*"
    matches = glob.glob(pattern)
    if not matches:
        print(CMD_LINE_COLOR.RED, f"Tenstorrent dir present but no tenstorrent!N entry under {tenstorrent_dir}", CMD_LINE_COLOR.ENDC, file=sys.stderr)
        sys.exit(1)
    name = os.path.basename(matches[0])
    if not name.startswith("tenstorrent!") or name == "tenstorrent!":
        print(CMD_LINE_COLOR.RED, f"Tenstorrent dir present but no tenstorrent!N entry under {tenstorrent_dir}", CMD_LINE_COLOR.ENDC, file=sys.stderr)
        sys.exit(1)
    try:
        n = int(name.replace("tenstorrent!", "", 1))
    except ValueError:
        print(CMD_LINE_COLOR.RED, f"Tenstorrent dir present but no tenstorrent!N entry under {tenstorrent_dir}", CMD_LINE_COLOR.ENDC, file=sys.stderr)
        sys.exit(1)

    return n
