# SPDX-FileCopyrightText: © 2023 Tenstorrent Inc.
# SPDX-License-Identifier: Apache-2.0

########################################
#          BACKEND CONSTANTS
########################################

SMBUS_TELEMETRY_LIST = [
"BOARD_ID",
"SMBUS_TX_ENUM_VERSION",
"SMBUS_TX_DEVICE_ID",
"SMBUS_TX_ASIC_RO",
"SMBUS_TX_ASIC_IDD",
"SMBUS_TX_BOARD_ID_HIGH",
"SMBUS_TX_BOARD_ID_LOW",
"SMBUS_TX_ARC0_FW_VERSION",
"SMBUS_TX_ARC1_FW_VERSION",
"SMBUS_TX_ARC2_FW_VERSION",
"SMBUS_TX_ARC3_FW_VERSION",
"SMBUS_TX_SPIBOOTROM_FW_VERSION",
"SMBUS_TX_ETH_FW_VERSION",
"SMBUS_TX_M3_BL_FW_VERSION",
"SMBUS_TX_M3_APP_FW_VERSION",
"SMBUS_TX_DDR_SPEED",
"SMBUS_TX_DDR_STATUS",
"SMBUS_TX_ETH_STATUS0",
"SMBUS_TX_ETH_STATUS1",
"SMBUS_TX_PCIE_STATUS",
"SMBUS_TX_FAULTS",
"SMBUS_TX_ARC0_HEALTH",
"SMBUS_TX_ARC1_HEALTH",
"SMBUS_TX_ARC2_HEALTH",
"SMBUS_TX_ARC3_HEALTH",
"SMBUS_TX_FAN_SPEED",
"SMBUS_TX_AICLK",
"SMBUS_TX_AXICLK",
"SMBUS_TX_ARCCLK",
"SMBUS_TX_THROTTLER",
"SMBUS_TX_VCORE",
"SMBUS_TX_ASIC_TEMPERATURE",
"SMBUS_TX_VREG_TEMPERATURE",
"SMBUS_TX_BOARD_TEMPERATURE",
"SMBUS_TX_TDP",
"SMBUS_TX_TDC",
"SMBUS_TX_VDD_LIMITS",
"SMBUS_TX_THM_LIMITS",
"SMBUS_TX_WH_FW_DATE",
"SMBUS_TX_ASIC_TMON0",
"SMBUS_TX_ASIC_TMON1",
"SMBUS_TX_MVDDQ_POWER",
"SMBUS_TX_GDDR_TRAIN_TEMP0",
"SMBUS_TX_GDDR_TRAIN_TEMP1",
"SMBUS_TX_BOOT_DATE",
"SMBUS_TX_RT_SECONDS",
"SMBUS_TX_AUX_STATUS",
"SMBUS_TX_ETH_DEBUG_STATUS0",
"SMBUS_TX_ETH_DEBUG_STATUS1",
"SMBUS_TX_TT_FLASH_VERSION",
]

TELEM_LIST = [
    "voltage",
    "current",
    "aiclk",
    "power",
    # "arcclk", 
    # "axiclk", 
    "asic_temperature",
    # "vreg_temperature",
    # "board_temperature_2",
    # "board_temperature_0",
    # "board_temperature_1",
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
    "arc_fw",
    "arc_fw_date",
    "eth_fw",
    "m3_bl_fw",
    "m3_app_fw",
    "tt_flash_version",
]

DEV_INFO_LIST = [ 
    "bus_id", 
    "board_type", 
    "board_id", 
    "coords", 
    "dram_status", 
    "dram_speed", 
    "pcie_speed", 
    "pcie_width"
]

PCI_PROPERTIES = [
    'current_link_speed',
    'max_link_speed',
    'current_link_width',
    'max_link_width'
]

MAX_PCIE_WIDTH = 16
MAX_PCIE_SPEED = 4
GUI_INTERVAL_TIME = 0.1
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
    "Link Width"
]

TELEMETRY_TABLE_HEADER = [
    "#",
    "Core Voltage (V)",
    "Core Current (A)",
    "AICLK (MHz)",
    "Core Power (W)",
    "Core Temp (°C)",
]

TELEMETRY_TABLE_HEADER_INTERNAL = [
    "#",
    "Core Voltage (V)",
    "Core Current (A)",
    "Core Power (W)",
    "AICLK (MHz)",
    "ARCCLK (MHz)",
    "AXICLK (MHz)",
    "Core Temp (°C)",
    "VREG Temp (°C)",
    "Outlet Temp 1 (°C)",
    "Outlet Temp 2 (°C)",
    "Inlet Temp (°C)",
    "12V Bus Current (A)",
]

FIRMWARES_TABLE_HEADER = [
    "#",
    "FW Version",
    "FW Date",
    "ETH FW Version",
    "M3 BL Version",
    "M3 App Version",
    "TT-Flash Version",
]

# HELP MARKDOWN DOCUMENT

HELP_MENU_MARKDOWN = """\
# TT-MOD HELP MENU

TT-Mod is a command-line utility for updating the contents of the SPI flash memory on Tenstorrent WH Boards. 
It operates through a text user interface that the user can interact with via cursor or keyboard input.

## Features

Use cursor or keyboard arrow keys to navigate the table. The following table details the keyboard keys that can be used and their functions

|            Action            |    Key           |                     Detailed Description                     |
| :--------------------------: | :--------------: | :----------------------------------------------------------: |
|             Quit             |   q  |        Exit the program and get a SPI dump yaml file        |
|             Help             |   h   |                   Opens up this help menu                   |
|   Go to device(s) info tab  |        1        |          Switch to tab with device info         |
|   Go to device(s) telemetry tab     |        2        |          Switch to tab with telemetry info that is updated every 100ms           |
|   Go to device(s) telemetry tab     |        3        |          Switch to tab with telemetry info that is updated every 100ms           |

"""
