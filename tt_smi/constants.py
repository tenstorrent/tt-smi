# SPDX-FileCopyrightText: © 2023 Tenstorrent Inc.
# SPDX-License-Identifier: Apache-2.0

########################################
#          BACKEND CONSTANTS
########################################

SMBUS_TELEMETRY_LIST = [
    "BOARD_ID",
    "ENUM_VERSION",
    "DEVICE_ID",
    "ASIC_RO",
    "ASIC_IDD",
    "BOARD_ID_HIGH",
    "BOARD_ID_LOW",
    "ARC0_FW_VERSION",
    "ARC1_FW_VERSION",
    "ARC2_FW_VERSION",
    "ARC3_FW_VERSION",
    "SPIBOOTROM_FW_VERSION",
    "ETH_FW_VERSION",
    "M3_BL_FW_VERSION",
    "M3_APP_FW_VERSION",
    "DDR_SPEED",
    "DDR_STATUS",
    "ETH_STATUS0",
    "ETH_STATUS1",
    "PCIE_STATUS",
    "FAULTS",
    "ARC0_HEALTH",
    "ARC1_HEALTH",
    "ARC2_HEALTH",
    "ARC3_HEALTH",
    "FAN_SPEED",
    "FAN_RPM",
    "AICLK",
    "AXICLK",
    "ARCCLK",
    "THROTTLER",
    "VCORE",
    "ASIC_TEMPERATURE",
    "VREG_TEMPERATURE",
    "BOARD_TEMPERATURE",
    "TDP",
    "TDC",
    "VDD_LIMITS",
    "THM_LIMITS",
    "WH_FW_DATE",
    "ASIC_TMON0",
    "ASIC_TMON1",
    "MVDDQ_POWER",
    "GDDR_TRAIN_TEMP0",
    "GDDR_TRAIN_TEMP1",
    "BOOT_DATE",
    "RT_SECONDS",
    "AUX_STATUS",
    "ETH_DEBUG_STATUS0",
    "ETH_DEBUG_STATUS1",
    "TT_FLASH_VERSION",
    "FW_BUNDLE_VERSION",
    "THERM_TRIP_COUNT",
    "INPUT_POWER",
    "BOARD_POWER_LIMIT",
]

BH_TELEMETRY_LIST = [
    "TAG_BOARD_ID_HIGH",
    "TAG_BOARD_ID_HIGH",
    "TAG_ASIC_ID",
    "TAG_UPDATE_TELEM_SPEED",
    "TAG_VCORE",
    "TAG_TDP",
    "TAG_TDC",
    "TAG_VDD_LIMITS",
    "TAG_THM_LIMITS",
    "TAG_ASIC_TEMPERATURE",
    "TAG_VREG_TEMPERATURE",
    "TAG_BOARD_TEMPERATURE",
    "TAG_AICLK",
    "TAG_AXICLK",
    "TAG_ARCCLK",
    "TAG_L2CPUCLK0",
    "TAG_L2CPUCLK1",
    "TAG_L2CPUCLK2",
    "TAG_L2CPUCLK3",
    "TAG_ETH_LIVE_STATUS",
    "TAG_DDR_STATUS",
    "TAG_DDR_SPEED",
    "TAG_ETH_FW_VERSION",
    "TAG_DDR_FW_VERSION",
    "TAG_BM_APP_FW_VERSION",
    "TAG_BM_BL_FW_VERSION",
    "TAG_FLASH_BUNDLE_VERSION",
    "TAG_CM_FW_VERSION",
    "TAG_L2CPU_FW_VERSION",
    "TAG_FAN_SPEED",
    "TAG_FAN_RPM",
    "TAG_TIMER_HEARTBEAT",
    "TAG_TELEM_ENUM_COUNT",
]

TELEM_LIST = [
    "voltage",
    "current",
    "aiclk",
    "power",
    "asic_temperature",
    "fan_speed",
    "heartbeat",
]

LIMITS = [
    "vdd_min",
    "vdd_max",
    "tdp_limit",
    "tdc_limit",
    "asic_fmax",
    "therm_trip_l1_limit",
    "thm_limit",
    "bus_peak_limit",
]

FW_LIST = [
    "fw_bundle_version",
    "tt_flash_version",
    "cm_fw",
    "cm_fw_date",
    "eth_fw",
    "bm_bl_fw",
    "bm_app_fw",
]

DEV_INFO_LIST = [
    "bus_id",
    "board_type",
    "board_id",
    "coords",
    "dram_status",
    "dram_speed",
    "pcie_speed",
    "pcie_width",
]

PCI_PROPERTIES = [
    "current_link_speed",
    "max_link_speed",
    "current_link_width",
    "max_link_width",
]

GLX_BOARD_TYPES = ["tt-galaxy-wh", "tt-galaxy-bh"]

# Galaxy tray number and UBB bus IDs
WH_UBB_BUS_IDS = {1: 0xC0, 2: 0x80, 3: 0x00, 4: 0x40}
BH_UBB_BUS_IDS = {1: 0x00, 2: 0x40, 3: 0xC0, 4: 0x80}

MAX_PCIE_WIDTH = 16
MAX_PCIE_SPEED = 4
GUI_INTERVAL_TIME = 0.1
MAGIC_FW_VERSION = 0x01030000
MSG_TYPE_FW_VERSION = 0xB9
########################################
#          GUI CONSTANTS
########################################

INFO_TABLE_HEADER = [
    "#",
    "Bus ID",
    "Board Type",
    "Board ID",
    "Coords",
    "DRAM Trained",
    "DRAM Speed",
    "Link Speed",
    "Link Width",
]

TELEMETRY_TABLE_HEADER = [
    "#",
    "Core Voltage (V)",
    "Core Current (A)",
    "AICLK (MHz)",
    "Core Power (W)",
    "Core Temp (°C)",
    "Fan Speed (%)",
    "Heartbeat",
]

FIRMWARES_TABLE_HEADER = [
    "#",
    "FW Bundle Version",
    "TT-Flash Version",
    "CM FW Version",
    "CM FW Date",
    "ETH FW Version",
    "BM BL Version",
    "BM App Version",
]

PCI_PROPERTIES = [
    "current_link_speed",
    "max_link_speed",
    "current_link_width",
    "max_link_width",
]

# HELP MARKDOWN DOCUMENT

HELP_MENU_MARKDOWN = """\
# TT-SMI HELP MENU

TT-SMI is a command-line utility that allows users to look at the telemetry and device information of Tenstorrent devices.

## KEYBOARD SHORTCUTS

Use cursor or keyboard keys to navigate the app. The following table details the keyboard keys that can be used and their functions

|            Action            |    Key           |                     Detailed Description                     |
| :--------------------------: | :--------------: | :----------------------------------------------------------: |
|             Quit             |   q  |        Exit the program       |
|             Help             |   h   |                   Opens up this help menu                   |
|   Go to device(s) info tab  |        1        |          Switch to tab with device info         |
|   Go to device(s) telemetry tab     |        2        |          Switch to tab with telemetry info that is updated every 100ms           |
|   Go to device(s) firmware tab     |        3        |          Switch to tab with all the fw versions on the board(s)          |

"""
