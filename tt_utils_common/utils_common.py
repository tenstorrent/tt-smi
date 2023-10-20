# SPDX-FileCopyrightText: © 2023 Tenstorrent Inc.
# SPDX-License-Identifier: Apache-2.0

import os
import sys
import time
import psutil
import distro
import signal
import platform
import datetime
import traceback
import subprocess
from pathlib import Path
from collections import OrderedDict
from typing import List, OrderedDict, Tuple, Union

def get_size(size_bytes: int, suffix: str = "B") -> str:
    """
    Scale bytes to its proper format
    e.g:
        1253656 => '1.20MB'
        1253656678 => '1.17GB'
    """
    factor = 1024
    for unit in ["", "K", "M", "G", "T", "P"]:
        if size_bytes < factor:
            return f"{size_bytes:.2f} {unit}{suffix}"
        size_bytes /= factor
    return "N/A"

def get_driver_version() -> Union[str, None]:
    try:
        with open("/sys/module/tenstorrent/version", "r", encoding="utf-8") as f:
            driver = f.readline().rstrip()
    except Exception:
        driver = None

    return driver

def get_host_info() -> OrderedDict:
    """
        Reads and organizes host info
    Returns:
        OrderedDict: with host info
    """
    uname = platform.uname()
    svmem = psutil.virtual_memory()

    os: str = uname.system
    distro_name: str = distro.name(pretty=True)
    kernel: str = uname.release
    hostname: str = uname.node

    return OrderedDict([("OS", os), ("Distro", distro_name),
                        ("Kernel", kernel), ("Hostname", hostname),
                        ("Platform", uname.machine),
                        ("Python", platform.python_version()),
                        ("Memory", get_size(svmem.total)),
                        ("Driver", "TTKMD " + get_driver_version())])

def system_compatibility() -> OrderedDict:
        host_info = get_host_info()
        checklist = {}
        if host_info["OS"] == "Linux":
            if distro.id() == "ubuntu":
                distro_version = float(".".join(distro.version_parts()[:2]))
                print(distro_version)
                if distro_version >= 20.04:
                    checklist["OS"] = (True, "Pass")
                else:
                    checklist["OS"] = (False, "Fail, not Ubuntu 20.04+")
            else:
                checklist["OS"] = (False, "Fail, not Ubuntu 20.04+")
        else:
            checklist["OS"] = (False, "Fail, not Ubuntu 20.04+")

        if host_info["Driver"]:
            checklist["Driver"] = (True, "Pass")
        else:
            checklist["Driver"] = (False, "Fail, no driver")
        if psutil.virtual_memory().total >= 32 * 1E9:
            checklist["Memory"] = (True, "Pass")
        else:
            checklist["Memory"] = (False, "Fail, not 32GB+")
        print(checklist)
        return checklist

def init_logging(log_folder: str):
    """Create tt-mod log folders if they don't exist"""
    if not os.path.isdir(log_folder):
        os.mkdir(log_folder)
        
def semver_to_hex(semver: str):
    """Converts a semantic version string from format 10.15.1 to hex 0x0A0F0100"""
    major, minor, patch = semver.split('.')
    byte_array = bytearray([0, int(major), int(minor), int(patch)])
    return f"{int.from_bytes(byte_array, byteorder='big'):08x}"

def date_to_hex(date: int):
    """Converts a given date string from format YYYYMMDDHHMM to hex 0xYMDDHHMM"""
    year = int(date[0:4]) - 2020
    month = int(date[4:6])
    day = int(date[6:8])
    hour = int(date[8:10])
    minute = int(date[10:12])
    byte_array = bytearray([year*16+month, day, hour, minute])
    return f"{int.from_bytes(byte_array, byteorder='big'):08x}"

def hex_to_semver(hexsemver: int):
    """Converts a semantic version string from format 0x0A0F0100 to 10.15.1"""
    if hexsemver == 0 or hexsemver == 0xFFFFFFFF:
        raise ValueError("hexsemver is invalid!")

    major = hexsemver >> 16 & 0xFF
    minor = hexsemver >>  8 & 0xFF
    patch = hexsemver >>  0 & 0xFF

    return f"{major}.{minor}.{patch}"

def hex_to_semver_eth(hexsemver: int):
    """Converts a semantic version string from format 0x061000 to 6.1.0"""
    if hexsemver == 0 or hexsemver == 0xFFFFFF:
        return "N/A"

    major = hexsemver >> 16 & 0xFF
    minor = hexsemver >>  12 & 0xF
    patch = hexsemver & 0xFFF

    return f"{major}.{minor}.{patch}"

def hex_to_semver_m3_fw(hexsemver: int):
    """Converts a semantic version string from format 0x0A0F0100 to 10.15.1"""
    if hexsemver == 0 or hexsemver == 0xFFFFFFFF:
        return "N/A"
    
    major = hexsemver >> 24 & 0xFF
    minor = hexsemver >> 16 & 0xFF
    patch = hexsemver >>  8 & 0xFF
    ver = hexsemver >>  0 & 0xFF

    return f"{major}.{minor}.{patch}.{ver}"

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

def get_board_type(board_id: str) -> str:
    """
    Get board type from board ID string.
    Ex:
        Board ID: AA-BBBBB-C-D-EE-FF-XXX
        BBBBB = Unique Part Identifier (UPI)
        C = Revision
    """
    serial_num = int(f"0x{board_id}", base=16)
    upi = (serial_num >> 36) & 0xFFFFF
    rev = (serial_num >> 32) & 0xF

    if upi == 0x1:
        if rev == 0x2:
            return "E300_R2"
        elif rev in (0x3, 0x4):
            return "E300_R3"
        else:
            return "N/A"
    elif upi == 0x3:
        return "E300_105"
    elif upi == 0x7:
        return "E75"
    elif upi == 0x8:
        return "NEBULA_CB"
    elif upi == 0xA:
        return "E300_X2"
    elif upi == 0xB:
        return "GALAXY"
    elif upi == 0x14:
        return "NEBULA_X2"
    elif upi == 0x18:
        return "NEBULA_X1"
    else:
        return "N/A"
