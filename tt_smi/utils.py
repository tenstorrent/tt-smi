# SPDX-FileCopyrightText: © 2026 Tenstorrent Inc.
# SPDX-License-Identifier: Apache-2.0

"""
Utility functions and constants for tt-smi (hex conversion, board type, logging path, etc.).
"""

import os
import sys
import glob
import fcntl
import struct
from enum import IntEnum
from importlib.metadata import version
from typing import Any, Dict, List, Optional, Tuple, Union

from tt_tools_common.ui_common.themes import CMD_LINE_COLOR

from tt_smi import constants


LOG_FOLDER = os.path.expanduser("~/tt_smi_logs/")

TENSTORRENT_IOCTL_MAGIC = 0xFA
TENSTORRENT_IOCTL_RESET_DEVICE = (TENSTORRENT_IOCTL_MAGIC << 8) | 6


class IoctlResetFlags(IntEnum):
    RESTORE_STATE = 0
    RESET_PCIE_LINK = 1
    CONFIG_WRITE = 2
    USER_RESET = 3
    ASIC_RESET = 4
    ASIC_DMC_RESET = 5
    POST_RESET = 6


def reset_device_ioctl(interface_id: int, flags: int) -> bool:
    """
    Issue TENSTORRENT_IOCTL_RESET_DEVICE on /dev/tenstorrent/{interface_id}.

    Returns True if the driver reports success (result == 0).
    """
    dev_path = f"/dev/tenstorrent/{interface_id}"
    # O_APPEND signals to KMD 2.6.0+ that we are power-aware, skipping power state
    # initialization that could worsen a hung device.
    dev_fd = os.open(dev_path, os.O_RDWR | os.O_CLOEXEC | os.O_APPEND)
    try:
        reset_device_in_struct = "II"
        reset_device_out_struct = "II"
        reset_device_struct = reset_device_in_struct + reset_device_out_struct

        input_size_bytes = struct.calcsize(reset_device_in_struct)
        output_size_bytes = struct.calcsize(reset_device_out_struct)

        reset_device_buf = bytearray(
            struct.pack(reset_device_struct, output_size_bytes, flags, 0, 0)
        )
        fcntl.ioctl(dev_fd, TENSTORRENT_IOCTL_RESET_DEVICE, reset_device_buf)

        output_buf = reset_device_buf[input_size_bytes:]
        _, result = struct.unpack(reset_device_out_struct, output_buf)
        return result == 0
    finally:
        os.close(dev_fd)


def get_driver_version() -> Union[str, None]:
    """Return the installed Tenstorrent KMD version from sysfs, or None if unavailable."""
    try:
        with open("/sys/module/tenstorrent/version", "r", encoding="utf-8") as f:
            return f.readline().rstrip()
    except OSError:
        return None


def _parse_version_string(version_str: str) -> Tuple[int, int, int]:
    """
    Parse a version string into (major, minor, patch).

    Handles SemVer-like formats including pre-release identifiers and build metadata,
    e.g. "1.34", "1.34.0", "1.34.1-alpha", "1.2.3+build456", "1.4.0-rc1+build42".
    """
    if not version_str:
        raise ValueError("Version string cannot be empty")

    core_version_str = version_str.split("+")[0]
    main_version_part = core_version_str.split("-")[0]
    parts = main_version_part.split(".")

    if not parts or not parts[0]:
        raise ValueError(f"Invalid version format: {version_str}")

    try:
        major = int(parts[0])
        minor = int(parts[1]) if len(parts) > 1 else 0
        patch = int(parts[2]) if len(parts) > 2 else 0
    except ValueError as e:
        raise ValueError(f"Version parts must be integers: {version_str}") from e

    return major, minor, patch


def is_driver_version_at_least(current_version: str, minimum_version: str) -> bool:
    """Return True if current_version >= minimum_version."""
    if current_version is None:
        raise ValueError(
            "No Tenstorrent driver detected! Please install the driver using tt-kmd: "
            "https://github.com/tenstorrent/tt-kmd"
        )

    return _parse_version_string(current_version) >= _parse_version_string(
        minimum_version
    )


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


def hex_to_semver_gddr_fw(raw: int) -> str:
    """
    Blackhole GDDR/MRISC FW telemetry (TAG_GDDR_FW_VERSION; UMD key GDDR_FW_VERSION): upper 16 bits = major,
    lower 16 bits = minor (same decoding as UMD for BH). Display as major.minor only.
    Example: 0x2000f -> 2.15
    """
    if raw == 0 or raw == 0xFFFFFFFF:
        return "N/A"
    major = (raw >> 16) & 0xFFFF
    minor = raw & 0xFFFF
    return f"{major}.{minor}"


BH_GDDR_CHANNELS = 8
WH_GDDR_CHANNELS = 6

# Packed GDDR_x_y_TEMP / GDDR_x_y_CORR_ERRS tags, in channel-pair order.
BH_GDDR_TEMP_TAGS = (
    "GDDR_0_1_TEMP",
    "GDDR_2_3_TEMP",
    "GDDR_4_5_TEMP",
    "GDDR_6_7_TEMP",
)
BH_GDDR_CORR_ERR_TAGS = (
    "GDDR_0_1_CORR_ERRS",
    "GDDR_2_3_CORR_ERRS",
    "GDDR_4_5_CORR_ERRS",
    "GDDR_6_7_CORR_ERRS",
)

# FW 19.7.0.0+ includes per-channel BIST bits in DDR_STATUS [31:16].
BH_GDDR_BIST_FW_VERSION = 0x13070000


def parse_telem_hex(smbus_telem: dict, key: str) -> Optional[int]:
    """Parse a hex telemetry tag from smbus_telem, or None if missing."""
    val = smbus_telem.get(key)
    if val is None:
        return None
    return int(val, 16)


def decode_gddr_pair_temps(packed: int) -> Tuple[Tuple[int, int], Tuple[int, int]]:
    """Decode a GDDR_x_y_TEMP word into ((x_bottom, x_top), (y_bottom, y_top)).

    Firmware packs each pair as:
      [7:0]   x bottom die °C
      [15:8]  x top die °C
      [23:16] y bottom die °C
      [31:24] y top die °C
    """
    x_bottom = packed & 0xFF
    x_top = (packed >> 8) & 0xFF
    y_bottom = (packed >> 16) & 0xFF
    y_top = (packed >> 24) & 0xFF
    return (x_bottom, x_top), (y_bottom, y_top)


def decode_gddr_pair_corr_errs(packed: int) -> Tuple[Tuple[int, int], Tuple[int, int]]:
    """Decode a GDDR_x_y_CORR_ERRS word into ((x_rd, x_wr), (y_rd, y_wr)).

    Firmware packs each pair as:
      [7:0]   x corrected read EDC (saturates at 255)
      [15:8]  x corrected write EDC
      [23:16] y corrected read EDC
      [31:24] y corrected write EDC
    """
    x_rd = packed & 0xFF
    x_wr = (packed >> 8) & 0xFF
    y_rd = (packed >> 16) & 0xFF
    y_wr = (packed >> 24) & 0xFF
    return (x_rd, x_wr), (y_rd, y_wr)


def decode_gddr_uncorr_errs(packed: int, channel: int) -> Tuple[int, int]:
    """Return (uncorr_rd, uncorr_wr) flags for one channel from GDDR_UNCORR_ERRS."""
    rd = (packed >> (channel * 2)) & 1
    wr = (packed >> (channel * 2 + 1)) & 1
    return rd, wr


def decode_bh_gddr_channel_status(
    dram_status: int, channel: int, has_bist: bool
) -> Tuple[str, str]:
    """Decode BH DDR_STATUS training/BIST for one GDDR channel.

    Per channel (FW 19.7+):
      bits [2i+1:2i]     training (01 complete, 10 error, 00 not run)
      bits [17+2i:16+2i] BIST     (01 complete, 10 failed, 00 not run)
    """
    training_complete = bool(dram_status & (1 << (2 * channel)))
    training_error = bool(dram_status & (1 << (2 * channel + 1)))
    if training_error:
        training = "fail"
    elif training_complete:
        training = "pass"
    else:
        training = "n/a"

    if not has_bist:
        return training, "n/a"

    bist_complete = bool(dram_status & (1 << (16 + 2 * channel)))
    bist_failed = bool(dram_status & (1 << (17 + 2 * channel)))
    if bist_failed:
        bist = "fail"
    elif bist_complete:
        bist = "pass"
    else:
        bist = "n/a"
    return training, bist


def decode_wh_gddr_channel_training(dram_status: int, channel: int) -> str:
    """Decode Wormhole DDR_STATUS nibble for one DRAM channel (0x2 pass, 0x1 fail)."""
    nibble = (dram_status >> (channel * 4)) & 0xF
    if nibble == 0x2:
        return "pass"
    if nibble == 0x1:
        return "fail"
    return "n/a"


def _gddr_channel_na(channel: int, enabled: bool = False, harvested: bool = False) -> Dict[str, Any]:
    return {
        "channel": channel,
        "harvested": harvested,
        "enabled": enabled,
        "training": "n/a",
        "bist": "n/a",
        "temp_top": "N/A",
        "temp_bottom": "N/A",
        "corr_rd": "N/A",
        "corr_wr": "N/A",
        "uncorr_rd": "N/A",
        "uncorr_wr": "N/A",
    }


def build_gddr_telemetry(
    smbus_telem: dict,
    dram_speed: str,
    is_blackhole: bool,
    is_wormhole: bool,
    has_bist: bool,
) -> Dict[str, Any]:
    """Build the decoded GDDR telemetry struct used by the GUI and snapshot."""
    gddr: Dict[str, Any] = {
        "speed": dram_speed if dram_speed else "N/A",
        "max_temp": "N/A",
        "enabled_mask": "N/A",
        "channels": [],
    }

    if is_blackhole:
        enabled_mask = parse_telem_hex(smbus_telem, "ENABLED_GDDR")
        if enabled_mask is not None:
            gddr["enabled_mask"] = hex(enabled_mask)
        else:
            enabled_mask = (1 << BH_GDDR_CHANNELS) - 1

        max_temp = parse_telem_hex(smbus_telem, "MAX_GDDR_TEMP")
        if max_temp is not None:
            gddr["max_temp"] = str(max_temp)

        dram_status = parse_telem_hex(smbus_telem, "DDR_STATUS")
        uncorr_packed = parse_telem_hex(smbus_telem, "GDDR_UNCORR_ERRS")

        pair_temps: List[Optional[Tuple[Tuple[int, int], Tuple[int, int]]]] = []
        pair_corrs: List[Optional[Tuple[Tuple[int, int], Tuple[int, int]]]] = []
        for temp_tag, corr_tag in zip(BH_GDDR_TEMP_TAGS, BH_GDDR_CORR_ERR_TAGS):
            temp_packed = parse_telem_hex(smbus_telem, temp_tag)
            corr_packed = parse_telem_hex(smbus_telem, corr_tag)
            pair_temps.append(
                decode_gddr_pair_temps(temp_packed) if temp_packed is not None else None
            )
            pair_corrs.append(
                decode_gddr_pair_corr_errs(corr_packed)
                if corr_packed is not None
                else None
            )

        for channel in range(BH_GDDR_CHANNELS):
            enabled = bool(enabled_mask & (1 << channel))
            harvested = (not enabled) or (
                dram_status is not None
                and is_bh_gddr_channel_harvested(dram_status, channel, has_bist)
            )
            if harvested:
                enabled = False
            ch = _gddr_channel_na(channel, enabled=enabled, harvested=harvested)
            if dram_status is not None:
                training, bist = decode_bh_gddr_channel_status(
                    dram_status, channel, has_bist
                )
                ch["training"] = training
                ch["bist"] = bist
            if enabled:
                pair_idx = channel // 2
                lane = channel % 2
                if pair_temps[pair_idx] is not None:
                    bottom, top = pair_temps[pair_idx][lane]
                    ch["temp_bottom"] = str(bottom)
                    ch["temp_top"] = str(top)
                if pair_corrs[pair_idx] is not None:
                    corr_rd, corr_wr = pair_corrs[pair_idx][lane]
                    ch["corr_rd"] = str(corr_rd)
                    ch["corr_wr"] = str(corr_wr)
                if uncorr_packed is not None:
                    uncorr_rd, uncorr_wr = decode_gddr_uncorr_errs(
                        uncorr_packed, channel
                    )
                    ch["uncorr_rd"] = str(uncorr_rd)
                    ch["uncorr_wr"] = str(uncorr_wr)
            gddr["channels"].append(ch)
        return gddr

    if is_wormhole:
        dram_status = parse_telem_hex(smbus_telem, "DDR_STATUS")
        if dram_status is not None:
            dram_status &= 0xFFFFFF
        for channel in range(WH_GDDR_CHANNELS):
            ch = _gddr_channel_na(channel, enabled=True)
            if dram_status is not None:
                ch["training"] = decode_wh_gddr_channel_training(dram_status, channel)
            gddr["channels"].append(ch)
        return gddr

    return gddr


def is_bh_gddr_channel_harvested(
    dram_status: int, channel: int, has_bist: bool
) -> bool:
    """True when a BH GDDR channel never ran training/BIST (0b00 / 0b00)."""
    if not has_bist:
        return False
    training, bist = decode_bh_gddr_channel_status(
        dram_status, channel, has_bist=True
    )
    return training == "n/a" and bist == "n/a"


def bh_dram_training_passed(dram_status: int) -> bool:
    """
    Check if DRAM training passed for Blackhole.

    Firmware may harvest up to one GDDR channel on any BH ASIC (PT-505).
    Pass when all 8 channels report training+BIST success (0b01), or when
    exactly 7 channels pass and one slot is all zeros (0b00 — harvested).
    A training/BIST error on any channel, more than one harvested slot, or a
    partial/incomplete state is a fail.
    """
    passing_channels = 0
    harvested_channels = 0

    for channel in range(BH_GDDR_CHANNELS):
        training, bist = decode_bh_gddr_channel_status(
            dram_status, channel, has_bist=True
        )
        if training == "pass" and bist == "pass":
            passing_channels += 1
        elif training == "n/a" and bist == "n/a":
            harvested_channels += 1
            if harvested_channels > 1:
                return False
        else:
            return False

    return passing_channels + harvested_channels == BH_GDDR_CHANNELS and harvested_channels <= 1


def p100_dram_training_passed(dram_status: int) -> bool:
    """Alias for bh_dram_training_passed; P100 uses the same harvest-tolerant check."""
    return bh_dram_training_passed(dram_status)


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
