# SPDX-FileCopyrightText: © 2023 Tenstorrent Inc.
# SPDX-License-Identifier: Apache-2.0

"""
This is the backend of tt-smi.
    - Keeps track of chip objects and tasks related to fetching device info and telemetry
    - Sanitizes input for frontend
"""

import os
import re
import sys
import jsons
import datetime
from tt_smi import log
from pathlib import Path
from rich.text import Text
from pyluwen import PciChip
from rich.table import Table
from tt_smi import constants
from rich import get_console
from typing import Dict, List
from rich.progress import track
from tt_tools_common.ui_common.themes import CMD_LINE_COLOR
from tt_tools_common.reset_common.wh_reset import WHChipReset
from tt_tools_common.reset_common.bh_reset import BHChipReset
from tt_tools_common.reset_common.gs_tensix_reset import GSTensixReset
from tt_tools_common.reset_common.galaxy_reset import GalaxyReset
from tt_tools_common.utils_common.system_utils import (
    get_host_info,
)
from tt_tools_common.utils_common.tools_utils import (
    get_board_type,
    hex_to_semver_m3_fw,
    hex_to_date,
    hex_to_semver_eth,
    init_logging,
    detect_chips_with_callback,
)

LOG_FOLDER = os.path.expanduser("~/tt_smi_logs/")


class TTSMIBackend:
    """
    TT-SMI backend class that encompasses all chip objects on host.
    It handles all device related tasks like fetching device info, telemetry and toggling resets
    """

    def __init__(self, devices: List[PciChip], fully_init: bool = True):
        self.devices = devices
        self.log: log.TTSMILog = log.TTSMILog(
            time=datetime.datetime.now(),
            host_info=get_host_info(),
            device_info=[
                log.TTSMIDeviceLog(
                    smbus_telem=log.SmbusTelem(),
                    board_info=log.BoardInfo(),
                    telemetry=log.Telemetry(),
                    firmwares=log.Firmwares(),
                    limits=log.Limits(),
                )
                for device in self.devices
            ],
        )
        self.smbus_telem_info = []
        self.firmware_infos = []
        self.device_infos = []
        self.device_telemetrys = []
        self.chip_limits = []
        self.pci_properties = []

        if fully_init:
            for i, device in track(
                enumerate(self.devices),
                total=len(self.devices),
                description="Gathering Information",
                update_period=0.01,
            ):
                self.smbus_telem_info.append(self.get_smbus_board_info(i))
                self.firmware_infos.append(self.get_firmware_versions(i))
                self.pci_properties.append(self.get_pci_properties(i))
                self.device_infos.append(self.get_device_info(i))
                self.device_telemetrys.append(self.get_chip_telemetry(i))
                self.chip_limits.append(self.get_chip_limits(i))

    def get_device_name(self, device):
        """Get device name from chip object"""
        if device.as_gs():
            return "Grayskull"
        elif device.as_wh():
            return "Wormhole"
        elif device.as_bh():
            return "Blackhole"
        else:
            assert False, "Unknown chip name, FIX!"

    def save_logs(self, result_filename: str = None):
        """Save log for smi snapshots"""
        time_now = datetime.datetime.now()
        date_string = time_now.strftime("%m-%d-%Y_%H:%M:%S")
        if not os.path.exists(LOG_FOLDER):
            init_logging(LOG_FOLDER)
        log_filename = f"{LOG_FOLDER}{date_string}_results.json"
        if result_filename:
            dir_path = os.path.dirname(os.path.realpath(result_filename))
            Path(dir_path).mkdir(parents=True, exist_ok=True)
            log_filename = result_filename
        for i, device in enumerate(self.devices):
            self.log.device_info[i].smbus_telem = self.smbus_telem_info[i]
            self.log.device_info[i].board_info = self.device_infos[i]
            # Add L/R for nb300 to separate local and remote asics
            if device.as_wh():
                board_type = self.device_infos[i]["board_type"]
                suffix = " R" if device.is_remote() else " L"
                board_type = board_type + suffix
                self.log.device_info[i].board_info["board_type"] = board_type
            self.log.device_info[i].telemetry = self.device_telemetrys[i]
            self.log.device_info[i].firmwares = self.firmware_infos[i]
            self.log.device_info[i].limits = self.chip_limits[i]
        self.log.save_as_json(log_filename)
        return log_filename

    def print_all_available_devices(self):
        """Print all available boards on host"""
        console = get_console()
        table_1 = Table(title="All available boards on host:")
        table_1.add_column("PCI Dev ID")
        table_1.add_column("Board Type")
        table_1.add_column("Device Series")
        table_1.add_column("Board Number")
        for i, device in enumerate(self.devices):
            board_id = self.device_infos[i]["board_id"]
            board_type = self.device_infos[i]["board_type"]
            pci_dev_id = (
                device.get_pci_interface_id() if not device.is_remote() else "N/A"
            )
            if device.as_wh():
                suffix = " R" if device.is_remote() else " L"
                board_type = board_type + suffix

            table_1.add_row(
                f"{pci_dev_id}",
                f"{self.get_device_name(device)}",
                f"{board_type}",
                f"{board_id}",
            )
        console.print(table_1)
        table_2 = Table(title="Boards that can be reset:")
        table_2.add_column("PCI Dev ID")
        table_2.add_column("Board Type")
        table_2.add_column("Device Series")
        table_2.add_column("Board Number")
        for i, device in enumerate(self.devices):
            if (
                not device.is_remote()
                and self.device_infos[i]["board_type"] != "GALAXY"
            ):
                board_id = self.device_infos[i]["board_id"]
                board_type = self.device_infos[i]["board_type"]
                pci_dev_id = device.get_pci_interface_id()
                if device.as_wh():
                    suffix = " R" if device.is_remote() else " L"
                    board_type = board_type + suffix
                table_2.add_row(
                    f"{pci_dev_id}",
                    f"{self.get_device_name(device)}",
                    f"{board_type}",
                    f"{board_id}",
                )
        console.print(table_2)

    def get_smbus_board_info(self, board_num: int) -> Dict:
        """Update board info by reading SMBUS_TELEMETRY"""
        pyluwen_chip = self.devices[board_num]
        if pyluwen_chip.as_bh():
            telem_struct = pyluwen_chip.as_bh().get_telemetry()
        elif pyluwen_chip.as_wh():
            telem_struct = pyluwen_chip.as_wh().get_telemetry()
        else:
            telem_struct = pyluwen_chip.as_gs().get_telemetry()
        json_map = jsons.dump(telem_struct)
        smbus_telem_dict = dict.fromkeys(constants.SMBUS_TELEMETRY_LIST)

        for key, value in json_map.items():
            if value:
                smbus_telem_dict[key.upper()] = hex(value)
        return smbus_telem_dict

    def update_telem(self):
        """Update telemetry in a given interval"""
        for i, _ in enumerate(self.devices):
            self.smbus_telem_info[i] = self.get_smbus_board_info(i)
            self.device_telemetrys[i] = self.get_chip_telemetry(i)

    def get_board_id(self, board_num) -> str:
        """Read board id from CSM or SPI if FW is not loaded"""
        if self.smbus_telem_info[board_num]["BOARD_ID"]:
            board_id = self.smbus_telem_info[board_num]["BOARD_ID"]
            return (f"{board_id}").replace("0x", "")
        else:
            board_info_0 = self.smbus_telem_info[board_num]["BOARD_ID_LOW"]
            board_info_1 = self.smbus_telem_info[board_num]["BOARD_ID_HIGH"]

            if board_info_0 is None or board_info_1 is None:
                return "N/A"
            board_info_0 = (f"{board_info_0}").replace("0x", "")
            board_info_1 = (f"{board_info_1}").replace("x", "")
            return f"{board_info_1}{board_info_0}"

    def get_dram_speed(self, board_num) -> int:
        """Read DRAM Frequency from CSM and alternatively from SPI if FW not loaded on chip"""
        if self.devices[board_num].as_gs():
            val = int(self.smbus_telem_info[board_num]["DDR_SPEED"], 16)
            return f"{val}"
        if self.smbus_telem_info[board_num]["DDR_STATUS"] is not None:
            dram_speed_raw = (
                int(self.smbus_telem_info[board_num]["DDR_STATUS"], 16) >> 24
            )
            if dram_speed_raw == 0:
                return "16G"
            elif dram_speed_raw == 1:
                return "14G"
            elif dram_speed_raw == 2:
                return "12G"
            elif dram_speed_raw == 3:
                return "10G"
            elif dram_speed_raw == 4:
                return "8G"
            else:
                return None
        return "N/A"

    def get_pci_properties(self, board_num):
        """Get the PCI link speed and link width details from sysfs files"""
        if self.devices[board_num].is_remote():
            return {prop: "N/A" for prop in constants.PCI_PROPERTIES}

        try:
            pcie_bdf = self.devices[board_num].get_pci_bdf()
            pci_bus_path = os.path.realpath(f"/sys/bus/pci/devices/{pcie_bdf}")
        except:
            return {prop: "N/A" for prop in constants.PCI_PROPERTIES}

        def get_pcie_gen(link_speed: str) -> int:
            if link_speed == "32.0":
                return 5
            if link_speed == "16.0":
                return 4
            elif link_speed == "8.0":
                return 3
            elif link_speed == "5.0":
                return 2
            elif link_speed == "2.5":
                return 1
            else:
                assert False, f"Invalid link speed {link_speed}"

        properties = {}

        for prop in constants.PCI_PROPERTIES:
            try:
                with open(os.path.join(pci_bus_path, prop), "r", encoding="utf-8") as f:
                    output = f.readline().rstrip()
                    value = re.findall(r"\d+\.\d+|\d+", output)[0]
                    if prop == "current_link_speed" or prop == "max_link_speed":
                        value = get_pcie_gen(value)
            except Exception:
                value = "N/A"
            properties[prop] = value
        return properties

    def get_dram_training_status(self, board_num) -> bool:
        """Get DRAM Training Status
        True means it passed training, False means it failed or did not train at all"""
        if self.devices[board_num].as_wh():
            num_channels = 8
            for i in range(num_channels):
                if self.smbus_telem_info[board_num]["DDR_STATUS"] is None:
                    return False
                dram_status = (
                    int(self.smbus_telem_info[board_num]["DDR_STATUS"], 16) >> (4 * i)
                ) & 0xF
                if dram_status != 2:
                    return False
                return True
        elif self.devices[board_num].as_gs():
            num_channels = 6
            for i in range(num_channels):
                if self.smbus_telem_info[board_num]["DDR_STATUS"] is None:
                    return False
                dram_status = (
                    int(self.smbus_telem_info[board_num]["DDR_STATUS"], 16) >> (4 * i)
                ) & 0xF
                if dram_status != 1:
                    return False
                return True

    def get_device_info(self, board_num) -> dict:
        dev_info = {}
        for field in constants.DEV_INFO_LIST:
            if field == "bus_id":
                try:
                    dev_info[field] = self.devices[board_num].get_pci_bdf()
                except:
                    dev_info[field] = "N/A"
            elif field == "board_type":
                if self.get_board_id(board_num) == "N/A":
                    dev_info[field] = "N/A"
                # TODO: Update when we have BH type identifiers
                elif self.devices[board_num].as_bh():
                    dev_info[field] = "bh"
                else:
                    dev_info[field] = get_board_type(self.get_board_id(board_num))
            elif field == "board_id":
                dev_info[field] = self.get_board_id(board_num)
            elif field == "coords":
                if self.devices[board_num].as_wh():
                    dev_info[field] = (
                        f"({self.devices[board_num].as_wh().get_local_coord().shelf_x}, {self.devices[board_num].as_wh().get_local_coord().shelf_y}, {self.devices[board_num].as_wh().get_local_coord().rack_x}, {self.devices[board_num].as_wh().get_local_coord().rack_y})"
                    )
                else:
                    dev_info[field] = "N/A"
            elif field == "dram_status":
                dev_info[field] = self.get_dram_training_status(board_num)
            elif field == "dram_speed":
                dev_info[field] = self.get_dram_speed(board_num)
            elif field == "pcie_speed":
                dev_info[field] = self.pci_properties[board_num]["current_link_speed"]
            elif field == "pcie_width":
                dev_info[field] = self.pci_properties[board_num]["current_link_width"]

        return dev_info

    def convert_signed_16_16_to_float(self, value):
        """Convert signed 16.16 to float"""
        return (value >> 16) + (value & 0xFFFF) / 65536.0

    def get_bh_chip_telemetry(self, board_num) -> Dict:
        """Get telemetry data for bh chip. None if ARC FW not running"""
        current = (
            int(self.smbus_telem_info[board_num]["TDC"], 16) & 0xFFFF
            if self.smbus_telem_info[board_num]["TDC"] is not None
            else 0
        )
        if self.smbus_telem_info[board_num]["VCORE"] is not None:
            voltage = int(self.smbus_telem_info[board_num]["VCORE"], 16) / 1000
        else:
            voltage = 10000
        power = (
            int(self.smbus_telem_info[board_num]["TDP"], 16) & 0xFFFF
            if self.smbus_telem_info[board_num]["TDP"] is not None
            else 0
        )
        asic_temperature = (
            (
                self.convert_signed_16_16_to_float(
                    int(self.smbus_telem_info[board_num]["ASIC_TEMPERATURE"], 16)
                )
            )
            if self.smbus_telem_info[board_num]["ASIC_TEMPERATURE"] is not None
            else 0
        )
        aiclk = (
            int(self.smbus_telem_info[board_num]["AICLK"], 16) & 0xFFFF
            if self.smbus_telem_info[board_num]["AICLK"] is not None
            else 0
        )

        chip_telemetry = {
            "voltage": f"{voltage:4.2f}",
            "current": f"{current:5.1f}",
            "power": f"{power:5.1f}",
            "aiclk": f"{aiclk:4.0f}",
            "asic_temperature": f"{asic_temperature:4.1f}",
        }
        return chip_telemetry

    def get_wh_gs_chip_telemetry(self, board_num) -> Dict:
        """Get telemetry data for GS and WH chip. None if ARC FW not running"""
        current = int(self.smbus_telem_info[board_num]["TDC"], 16) & 0xFFFF
        if self.smbus_telem_info[board_num]["VCORE"] is not None:
            voltage = int(self.smbus_telem_info[board_num]["VCORE"], 16) / 1000
        else:
            voltage = 10000
        power = int(self.smbus_telem_info[board_num]["TDP"], 16) & 0xFFFF
        asic_temperature = (
            int(self.smbus_telem_info[board_num]["ASIC_TEMPERATURE"], 16) & 0xFFFF
        ) / 16
        aiclk = int(self.smbus_telem_info[board_num]["AICLK"], 16) & 0xFFFF

        chip_telemetry = {
            "voltage": f"{voltage:4.2f}",
            "current": f"{current:5.1f}",
            "power": f"{power:5.1f}",
            "aiclk": f"{aiclk:4.0f}",
            "asic_temperature": f"{asic_temperature:4.1f}",
        }

        return chip_telemetry

    def get_chip_telemetry(self, board_num) -> Dict:
        """Return the correct chip telemetry for a given board"""
        if self.devices[board_num].as_bh():
            return self.get_bh_chip_telemetry(board_num)
        else:
            return self.get_wh_gs_chip_telemetry(board_num)

    def get_chip_limits(self, board_num):
        """Get chip limits from the CSM. None if ARC FW not running"""

        chip_limits = {}
        for field in constants.LIMITS:
            if field == "vdd_min":
                value = (
                    int(self.smbus_telem_info[board_num]["VDD_LIMITS"], 16) & 0xFFFF
                    if self.smbus_telem_info[board_num]["VDD_LIMITS"] is not None
                    else 0
                )
                chip_limits[field] = f"{value/1000:4.2f}" if value is not None else None
            elif field == "vdd_max":
                value = (
                    int(self.smbus_telem_info[board_num]["VDD_LIMITS"], 16) >> 16
                    if self.smbus_telem_info[board_num]["VDD_LIMITS"] is not None
                    else 0
                )
                chip_limits[field] = f"{value/1000:4.2f}" if value is not None else None
            elif field == "tdp_limit":
                value = (
                    int(self.smbus_telem_info[board_num]["TDP"], 16) >> 16
                    if self.smbus_telem_info[board_num]["TDP"] is not None
                    else 0
                )
                chip_limits[field] = f"{value:3.0f}" if value is not None else None
            elif field == "tdc_limit":
                value = (
                    int(self.smbus_telem_info[board_num]["TDC"], 16) >> 16
                    if self.smbus_telem_info[board_num]["TDC"] is not None
                    else 0
                )
                chip_limits[field] = f"{value:3.0f}" if value is not None else None
            elif field == "asic_fmax":
                value = (
                    int(self.smbus_telem_info[board_num]["AICLK"], 16) >> 16
                    if self.smbus_telem_info[board_num]["AICLK"] is not None
                    else 0
                )
                chip_limits[field] = f"{value:4.0f}" if value is not None else None
            elif field == "therm_trip_l1_limit":
                value = (
                    int(self.smbus_telem_info[board_num]["THM_LIMITS"], 16) >> 16
                    if self.smbus_telem_info[board_num]["THM_LIMITS"] is not None
                    else 0
                )
                chip_limits[field] = f"{value:2.0f}" if value is not None else None
            elif field == "thm_limit":
                value = (
                    int(self.smbus_telem_info[board_num]["THM_LIMITS"], 16) & 0xFFFF
                    if self.smbus_telem_info[board_num]["THM_LIMITS"] is not None
                    else 0
                )
                chip_limits[field] = f"{value:2.0f}" if value is not None else 0
            else:
                chip_limits[field] = None
        return chip_limits

    def get_firmware_versions(self, board_num):
        """Translate the telem struct semver for gui"""
        fw_versions = {}
        for field in constants.FW_LIST:
            if field == "cm_fw":
                val = self.smbus_telem_info[board_num]["ARC0_FW_VERSION"]
                if val is None:
                    fw_versions[field] = "N/A"
                else:
                    fw_versions[field] = hex_to_semver_m3_fw(int(val, 16))

            elif field == "cm_fw_date":
                val = self.smbus_telem_info[board_num]["WH_FW_DATE"]
                if val is None:
                    fw_versions[field] = "N/A"
                else:
                    fw_versions[field] = hex_to_date(int(val, 16), include_time=False)

            elif field == "eth_fw":
                val = self.smbus_telem_info[board_num]["ETH_FW_VERSION"]
                if val is None:
                    fw_versions[field] = "N/A"
                else:
                    fw_versions[field] = hex_to_semver_eth(int(val, 16))
            elif field == "bm_bl_fw":
                val = self.smbus_telem_info[board_num]["M3_BL_FW_VERSION"]
                if val is None:
                    fw_versions[field] = "N/A"
                else:
                    fw_versions[field] = hex_to_semver_m3_fw(int(val, 16))
            elif field == "bm_app_fw":
                val = self.smbus_telem_info[board_num]["M3_APP_FW_VERSION"]
                if val is None:
                    fw_versions[field] = "N/A"
                else:
                    fw_versions[field] = hex_to_semver_m3_fw(int(val, 16))
            elif field == "tt_flash_version":
                val = self.smbus_telem_info[board_num]["TT_FLASH_VERSION"]
                if val is None:
                    fw_versions[field] = "N/A"
                else:
                    fw_versions[field] = hex_to_semver_m3_fw(int(val, 16))
            elif field == "fw_bundle_version":
                val = self.smbus_telem_info[board_num]["FW_BUNDLE_VERSION"]
                if val is None:
                    fw_versions[field] = "N/A"
                else:
                    fw_versions[field] = hex_to_semver_m3_fw(int(val, 16))
        return fw_versions

    def gs_tensix_reset(self, board_num) -> None:
        """Reset the Tensix cores on a GS chip"""
        print(
            CMD_LINE_COLOR.BLUE,
            f"Starting Tensix reset on GS board at PCI index {board_num}",
            CMD_LINE_COLOR.ENDC,
        )
        device = self.devices[board_num]
        # Init reset object and call reset
        GSTensixReset(device).tensix_reset()

        print(
            CMD_LINE_COLOR.GREEN,
            f"Finished Tensix reset on GS board at PCI index {board_num}\n",
            CMD_LINE_COLOR.ENDC,
        )


# Reset specific functions


def pci_indices_from_json(json_dict):
    """Parse pci_list from reset json"""
    pci_indices = []
    reinit = False
    if "gs_tensix_reset" in json_dict.keys():
        pci_indices.extend(json_dict["gs_tensix_reset"]["pci_index"])
    if "wh_link_reset" in json_dict.keys():
        pci_indices.extend(json_dict["wh_link_reset"]["pci_index"])
    if "re_init_devices" in json_dict.keys():
        reinit = json_dict["re_init_devices"]
    return pci_indices, reinit


def mobo_reset_from_json(json_dict) -> dict:
    """Parse pci_list from reset json and init mobo reset"""
    if "wh_mobo_reset" in json_dict.keys():
        mobo_dict_list = []
        for mobo_dict in json_dict["wh_mobo_reset"]:
            # Only add the mobos that have a name
            if "MOBO NAME" not in mobo_dict["mobo"]:
                mobo_dict_list.append(mobo_dict)
        # If any mobos - do the reset
        if mobo_dict_list:
            GalaxyReset().warm_reset_mobo(mobo_dict_list)
            # If there are mobos to reset, remove link reset PCI index's from the json
            try:
                wh_link_pci_indices = json_dict["wh_link_reset"]["pci_index"]
                for entry in mobo_dict_list:
                    if "nb_host_pci_idx" in entry.keys() and entry["nb_host_pci_idx"]:
                        # remove the list of WH PCIe index's from the reset list
                        wh_link_pci_indices = list(
                            set(wh_link_pci_indices) - set(entry["nb_host_pci_idx"])
                        )
                json_dict["wh_link_reset"]["pci_index"] = wh_link_pci_indices
            except Exception as e:
                print(
                    CMD_LINE_COLOR.RED,
                    f"Error! {e}",
                    CMD_LINE_COLOR.ENDC,
                )

    return json_dict


def pci_board_reset(list_of_boards: List[int], reinit=False):
    """Given a list of PCI index's init the PCI chip and call reset on it"""

    reset_wh_pci_idx = []
    reset_gs_devs = []
    reset_bh_pci_idx = []
    for pci_idx in list_of_boards:
        try:
            chip = PciChip(pci_interface=pci_idx)
        except Exception as e:
            print(
                CMD_LINE_COLOR.RED,
                f"Error accessing board at PCI index {pci_idx}! Use -ls to see all devices available to reset",
                CMD_LINE_COLOR.ENDC,
            )
            # Exit the loop to go to the next chip
            continue
        if chip.as_wh():
            reset_wh_pci_idx.append(pci_idx)
        elif chip.as_gs():
            reset_gs_devs.append(chip)
        elif chip.as_bh():
            reset_bh_pci_idx.append(pci_idx)
        else:
            print(
                CMD_LINE_COLOR.RED,
                "Unkown chip!!",
                CMD_LINE_COLOR.ENDC,
            )
            sys.exit(1)

    # reset wh devices with pci indices
    if reset_wh_pci_idx:
        reset_devices = WHChipReset().full_lds_reset(pci_interfaces=reset_wh_pci_idx)

    # reset gs devices by creating a partially init backend
    if reset_gs_devs:
        backend = TTSMIBackend(devices=reset_gs_devs, fully_init=False)
        for i, _ in enumerate(reset_gs_devs):
            backend.gs_tensix_reset(i)

    if reset_bh_pci_idx:
        BHChipReset().full_lds_reset(pci_interfaces=reset_bh_pci_idx)

    if reinit:
        # Enable backtrace for debugging
        os.environ["RUST_BACKTRACE"] = "full"

        print(
            CMD_LINE_COLOR.PURPLE,
            f"Re-initializing boards after reset....",
            CMD_LINE_COLOR.ENDC,
        )
        try:
            chips = detect_chips_with_callback()
        except Exception as e:
            print(
                CMD_LINE_COLOR.RED,
                f"Error when re-initializing chips!\n {e}",
                CMD_LINE_COLOR.ENDC,
            )
            sys.exit(1)
