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
import time
import datetime
from tt_smi import log
from pathlib import Path
from rich.table import Table
from tt_smi import constants
from rich import get_console
from rich.syntax import Syntax
from typing import Dict, List
from rich.progress import track
from importlib.metadata import version
from tt_tools_common.ui_common.themes import CMD_LINE_COLOR
from tt_tools_common.reset_common.wh_reset import WHChipReset
from tt_tools_common.reset_common.bh_reset import BHChipReset
from tt_tools_common.reset_common.galaxy_reset import GalaxyReset
from pyluwen import (
    PciChip,
    run_wh_ubb_ipmi_reset,
    run_ubb_wait_for_driver_load
)
from tt_tools_common.utils_common.system_utils import (
    get_host_info,
)
from tt_tools_common.utils_common.tools_utils import (
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

    def __init__(
        self,
        devices: List[PciChip],
        fully_init: bool = True,
        pretty_output: bool = True,
    ):
        self.devices = devices
        self.pretty_output = pretty_output
        self.log: log.TTSMILog = log.TTSMILog(
            time=datetime.datetime.now(),
            host_info=get_host_info(),
            host_sw_vers=get_host_software_versions(),
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
                disable=not self.pretty_output,
            ):
                self.smbus_telem_info.append(self.get_smbus_board_info(i))
                self.firmware_infos.append(self.get_firmware_versions(i))
                self.pci_properties.append(self.get_pci_properties(i))
                self.device_infos.append(self.get_device_info(i))
                self.device_telemetrys.append(self.get_chip_telemetry(i))
                self.chip_limits.append(self.get_chip_limits(i))

    def get_device_name(self, device):
        """Get device name from chip object"""
        if device.as_wh():
            return "Wormhole"
        elif device.as_bh():
            return "Blackhole"
        else:
            assert False, "Unknown chip name, FIX!"

    def save_logs_to_file(self, result_filename: str = ""):
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

        clean_json = self.get_logs_json()
        with open(log_filename, "w") as f:
            f.write(clean_json)

        return log_filename

    def print_logs_to_stdout(self, pretty: bool = True):
        """Pretty-print (or just print) logs to stdout"""
        clean_json = self.get_logs_json()

        if not pretty:
            print(clean_json)
            return

        formatted = Syntax(clean_json, "json", background_color="default")
        console = get_console()
        console.print(formatted)

    def get_logs_json(self) -> str:
        """Get logs as JSON"""
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

        return self.log.get_clean_json_string()

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
                and self.device_infos[i]["board_type"] != "wh_4u"
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

    def print_tray_and_device_mapping(self):
        """Print the mapping of trays to devices on the galaxy"""

        ubb_bus_ids = (
            constants.BH_UBB_BUS_IDS
            if self.devices[0].as_bh()
            else constants.WH_UBB_BUS_IDS
        )

        def _get_mapping_tray_to_device():
            bus_id_to_tray = {
                bus_id: tray_num for tray_num, bus_id in ubb_bus_ids.items()
            }
            tray_to_devices = {}
            for i, device_info in enumerate(self.device_infos):
                # Extract bus id from "domain:bus:device.function" format
                bus_id = int(device_info["bus_id"].split(":")[1], 16)
                tray_bus_id = bus_id & 0xF0
                if tray_bus_id in bus_id_to_tray:
                    tray_num = bus_id_to_tray[tray_bus_id]
                    tray_to_devices.setdefault(tray_num, []).append(i)
            return tray_to_devices

        tray_to_devices = _get_mapping_tray_to_device()

        console = get_console()
        table = Table(title="Mapping of trays to devices on the galaxy:")
        table.add_column("Tray Number")
        table.add_column("Tray Bus ID")
        table.add_column("PCI Dev ID")
        for tray_num in sorted(tray_to_devices):
            table.add_row(
                f"{tray_num}",
                f"0x{ubb_bus_ids[tray_num]:02x}",
                f"{','.join(map(str, tray_to_devices[tray_num]))}",
            )
        console.print(table)

    def get_smbus_board_info(self, board_num: int) -> Dict:
        """Update board info by reading SMBUS_TELEMETRY"""
        pyluwen_chip = self.devices[board_num]
        if pyluwen_chip.as_bh():
            telem_struct = pyluwen_chip.as_bh().get_telemetry()
        elif pyluwen_chip.as_wh():
            telem_struct = pyluwen_chip.as_wh().get_telemetry()
        else:
            raise ValueError(f"Unknown chip type for device {board_num}")
        json_map = dict_from_public_attrs(telem_struct)
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
            board_id = int(self.smbus_telem_info[board_num]["BOARD_ID"], base=16)
            return f"{board_id:016x}"
        else:
            board_info_0 = int(self.smbus_telem_info[board_num]["BOARD_ID_LOW"], base=16)
            board_info_1 = int(self.smbus_telem_info[board_num]["BOARD_ID_HIGH"], base=16)

            if board_info_0 is None or board_info_1 is None:
                return "N/A"
            return f"{board_info_1:08x}{board_info_0:08x}"

    def get_dram_speed(self, board_num) -> int:
        """Read DRAM Frequency from CSM and alternatively from SPI if FW not loaded on chip"""
        if self.devices[board_num].as_bh():
            if self.smbus_telem_info[board_num]["DDR_SPEED"] is None:
                return "N/A"
            dram_speed = int(self.smbus_telem_info[board_num]["DDR_SPEED"], 16)
            # check if its div by 1000 and then return num GHz else MHz
            if dram_speed % 1000 == 0:
                dram_speed = dram_speed // 1000
                return f"{dram_speed}G"
            else:
                return f"{dram_speed}"
        elif self.devices[board_num].as_wh():
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
        else:
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
        """
        Get DRAM Training Status
        True means it passed training, False means it failed or did not train at all
        """
        if self.devices[board_num].as_wh():
            # DDR_STATUS field in WH:
            # (31:24) dram speed
            # (23:0) per channel dram status (6 channels)
            # DRAM_TRAINING_FAIL = 0x1
            # DRAM_TRAINING_PASS = 0x2
            if self.smbus_telem_info[board_num]["DDR_STATUS"] is None:
                return False
            dram_status = (
                int(self.smbus_telem_info[board_num]["DDR_STATUS"], 16)) & 0xFFFFFF
            if dram_status == 0x222222:
                return True
            return False
        elif self.devices[board_num].as_bh():
            # DDR Status in BH is a 16-bit field with the following layout:
			#  [0] - Training complete GDDR 0
			#  [1] - Error GDDR 0
			#  [2] - Training complete GDDR 1
			#  [3] - Error GDDR 1
			#  ...
			#  [14] - Training Complete GDDR 7
			#  [15] - Error GDDR 7
            dram_status = int(self.smbus_telem_info[board_num]["DDR_STATUS"], 16)
            # 0x5555 = 0b0101010101010101 means all 8 channels trained successfully
            if dram_status == 0x5555:
                return True
            return False
        else:
            return False

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
                else:
                    dev_info[field] = get_board_type(self.get_board_id(board_num))
            elif field == "board_id":
                dev_info[field] = self.get_board_id(board_num)
            elif field == "coords":
                if self.devices[board_num].as_wh():
                    dev_info[
                        field
                    ] = f"({self.devices[board_num].as_wh().get_local_coord().shelf_x}, {self.devices[board_num].as_wh().get_local_coord().shelf_y}, {self.devices[board_num].as_wh().get_local_coord().rack_x}, {self.devices[board_num].as_wh().get_local_coord().rack_y})"
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
        timer_heartbeat = int(self.smbus_telem_info[board_num]["TIMER_HEARTBEAT"], 16) // 6 # Watchdog heartbeat, ~2 per second
        fan_speed = (
            min(int(self.smbus_telem_info[board_num]["FAN_RPM"], 16), 5000) / 50 # RPM to percent conversion
            if self.smbus_telem_info[board_num]["FAN_RPM"] is not None
            else 0
        )

        chip_telemetry = {
            "voltage": f"{voltage:4.2f}",
            "current": f"{current:5.1f}",
            "power": f"{power:5.1f}",
            "aiclk": f"{aiclk:4.0f}",
            "asic_temperature": f"{asic_temperature:4.1f}",
            "fan_speed": f"{fan_speed:3.0f}",
            "heartbeat": f"{timer_heartbeat}",
        }
        return chip_telemetry

    def get_wh_chip_telemetry(self, board_num) -> Dict:
        """Get telemetry data for WH chip. None if ARC FW not running"""
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
        arc3_heartbeat = int(self.smbus_telem_info[board_num]["ARC3_HEALTH"], 16) // 5 # Watchdog heartbeat, ~2 per second
        if self.smbus_telem_info[board_num]["FAN_SPEED"] is not None:
            fan_speed = int(self.smbus_telem_info[board_num]["FAN_SPEED"], 16)
        else:
            fan_speed = 0

        chip_telemetry = {
            "voltage": f"{voltage:4.2f}",
            "current": f"{current:5.1f}",
            "power": f"{power:5.1f}",
            "aiclk": f"{aiclk:4.0f}",
            "asic_temperature": f"{asic_temperature:4.1f}",
            "fan_speed": f"{fan_speed:3.0f}",
            "heartbeat": f"{arc3_heartbeat}"
        }

        return chip_telemetry

    def get_chip_telemetry(self, board_num) -> Dict:
        """Return the correct chip telemetry for a given board"""
        if self.devices[board_num].as_bh():
            return self.get_bh_chip_telemetry(board_num)
        elif self.devices[board_num].as_wh():
            return self.get_wh_chip_telemetry(board_num)
        else:
            print(
                CMD_LINE_COLOR.RED,
                f"Could not fetch telemetry for board {e}: Unrecognized board type!",
                CMD_LINE_COLOR.ENDC,
            )
            return {}

    def get_chip_limits(self, board_num: int) -> Dict[str, str]:
        if self.devices[board_num].as_bh():
            return self.get_bh_chip_limits(board_num)
        elif self.devices[board_num].as_wh():
            return self.get_wh_chip_limits(board_num)
        else:
            print(
                CMD_LINE_COLOR.RED,
                f"Could not fetch chip limits for board {board_num}: Unrecognized board type!",
                CMD_LINE_COLOR.ENDC,
            )
            return {}

    def get_wh_chip_limits(self, board_num: int) -> Dict[str, str]:
        """Get chip limits from the CSM. None if ARC FW not running"""

        chip_limits = {}
        for field in constants.LIMITS:
            if field == "vdd_min":
                vdd_limits = self.smbus_telem_info[board_num].get("VDD_LIMITS")
                chip_limits[field] = f"{(int(vdd_limits, 16) & 0xFFFF) / 1000:4.2f}" if vdd_limits else 0
            elif field == "vdd_max":
                vdd_limits = self.smbus_telem_info[board_num].get("VDD_LIMITS")
                chip_limits[field] = f"{(int(vdd_limits, 16) >> 16) / 1000:4.2f}" if vdd_limits else 0
            elif field == "tdp_limit":
                tdp = self.smbus_telem_info[board_num].get("TDP")
                chip_limits[field] = f"{int(tdp, 16) >> 16:3.0f}" if tdp else 0
            elif field == "tdc_limit":
                tdc = self.smbus_telem_info[board_num].get("TDC")
                chip_limits[field] = f"{int(tdc, 16) >> 16:3.0f}" if tdc else 0
            elif field == "asic_fmax":
                aiclk = self.smbus_telem_info[board_num].get("AICLK")
                chip_limits[field] = f"{int(aiclk, 16) >> 16:4.0f}" if aiclk else 0
            elif field == "therm_trip_l1_limit":
                thm_limits = self.smbus_telem_info[board_num].get("THM_LIMITS")
                chip_limits[field] = f"{int(thm_limits, 16) >> 16:2.0f}" if thm_limits else 0
            elif field == "thm_limit":
                thm_limits = self.smbus_telem_info[board_num].get("THM_LIMITS")
                chip_limits[field] = f"{int(thm_limits, 16) & 0xFFFF:2.0f}" if thm_limits else 0
            else:
                chip_limits[field] = 0
        return chip_limits

    def get_bh_chip_limits(self, board_num: int) -> Dict[str, str]:
        """Get chip limits from BH telemetry. None if ARC FW not running"""

        chip_limits = {}
        for field in constants.LIMITS:
            if field == "vdd_min":
                vdd_limits = self.smbus_telem_info[board_num].get("VDD_LIMITS")
                chip_limits[field] = f"{(int(vdd_limits, 16) & 0xFFFF) / 1000:4.2f}" if vdd_limits else 0
            elif field == "vdd_max":
                vdd_limits = self.smbus_telem_info[board_num].get("VDD_LIMITS")
                chip_limits[field] = f"{(int(vdd_limits, 16) >> 16) / 1000:4.2f}" if vdd_limits else 0
            elif field == "tdp_limit":
                tdp_limit = self.smbus_telem_info[board_num].get("TDP_LIMIT_MAX")
                chip_limits[field] = f"{int(tdp_limit, 16):3.0f}" if tdp_limit else 0
            elif field == "tdc_limit":
                tdc_limit = self.smbus_telem_info[board_num].get("TDC_LIMIT_MAX")
                chip_limits[field] = f"{int(tdc_limit, 16):3.0f}" if tdc_limit else 0
            elif field == "asic_fmax":
                asic_fmax = self.smbus_telem_info[board_num].get("AICLK_LIMIT_MAX")
                chip_limits[field] = f"{int(asic_fmax, 16):4.0f}" if asic_fmax else 0
            elif field == "therm_trip_l1_limit":
                therm_trip_l1_limit = self.smbus_telem_info[board_num].get("THM_LIMIT_THROTTLE")
                chip_limits[field] = f"{int(therm_trip_l1_limit, 16):2.0f}" if therm_trip_l1_limit else 0
            elif field == "thm_limit":
                thm_limits = self.smbus_telem_info[board_num].get("THM_LIMITS")
                chip_limits[field] = f"{int(thm_limits, 16):2.0f}" if thm_limits else 0
            else:
                chip_limits[field] = 0
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
                # See below- Galaxy systems manually get an N/A tt_flash_version
                elif get_board_type(self.get_board_id(board_num)) == "wh_4u":
                    fw_versions[field] = "N/A"
                else:
                    fw_versions[field] = hex_to_semver_m3_fw(int(val, 16))
            elif field == "fw_bundle_version":
                val = self.smbus_telem_info[board_num]["FW_BUNDLE_VERSION"]
                if (
                    get_board_type(self.get_board_id(board_num)) == "wh_4u"
                    and val == "0xffffffff"
                ):
                    # WARNING: Dirty dirty hack!
                    # See Issue #72 https://github.com/tenstorrent/tt-smi/issues/72
                    # Due to a FW flashing bug, Galaxy systems do not have the FW_BUNDLE_VERSION
                    # field set. The value ends up in TT_FLASH_VERSION, so we need to go get it.
                    val = self.smbus_telem_info[board_num]["TT_FLASH_VERSION"]
                    fw_versions[field] = hex_to_semver_m3_fw(int(val, 16))
                if val is None:
                    fw_versions[field] = "N/A"
                else:
                    fw_versions[field] = hex_to_semver_m3_fw(int(val, 16))
        return fw_versions


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

def dict_from_public_attrs(obj) -> dict:
    """Parse an object's public attributes into a dictionary"""
    all_attrs = obj.__dir__()
    # Filter private attrs
    public = [attr for attr in all_attrs if not attr.startswith("_")]
    ret = {}
    for attr in public:
        ret[attr] = getattr(obj, attr)
    return ret


def get_host_software_versions() -> dict:
    return {
        "tt_smi": version("tt_smi"),
        "pyluwen": version("pyluwen"),
    }


# Reset specific functions

def pci_board_reset(list_of_boards: List[int], reinit: bool = False, print_status: bool = True):
    """Given a list of PCI index's init the PCI chip and call reset on it"""

    reset_wh_pci_idx = []
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
        elif chip.as_bh():
            reset_bh_pci_idx.append(pci_idx)
        else:
            print(
                CMD_LINE_COLOR.RED,
                "Unkown chip!!",
                CMD_LINE_COLOR.ENDC,
            )
            # Close the chip  before exiting- needed for docker resets to work
            del chip
            sys.exit(1)
        # Close the chip - needed for docker resets to work
        del chip

    # reset wh devices with pci indices
    if reset_wh_pci_idx:
        WHChipReset().full_lds_reset(pci_interfaces=reset_wh_pci_idx)

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
        # Move cursor back and overwrite the number
        print("\r\033[93mWaiting for {} seconds: {}\033[0m".format(seconds, i), end='')
        sys.stdout.flush()
    print()

def check_wh_galaxy_eth_link_status(devices):
    """
    Check the WH Galaxy Ethernet link status.
    Returns True if the link is up, False otherwise.
    """
    noc_id = 0
    DEBUG_BUF_ADDR = 0x12c0 # For eth fw 5.0.0 and above
    eth_locations_noc_0 = [ (9, 0), (1, 0), (8, 0), (2, 0), (7, 0), (3, 0), (6, 0), (4, 0),
                        (9, 6), (1, 6), (8, 6), (2, 6), (7, 6), (3, 6), (6, 6), (4, 6) ]
    LINK_INACTIVE_FAIL_DUMMY_PACKET = 10
    # Check that we have 32 devices
    if len(devices) != 32:
        print(
            CMD_LINE_COLOR.RED,
            f"Error: Expected 32 devices for WH Galaxy Ethernet link status check, seeing f{len(devices)}, please try reset again or cold boot the system.",
            CMD_LINE_COLOR.ENDC,
        )
        sys.exit(1)

    # Collect all the link errors in a dictionary
    link_errors = {}
    # Check all 16 eth links for all devices
    for i, device in enumerate(devices):
        for eth in range(16):
            eth_x, eth_y = eth_locations_noc_0[eth]
            link_error = device.noc_read32(noc_id, eth_x, eth_y, DEBUG_BUF_ADDR + 0x4*96)
            if link_error == LINK_INACTIVE_FAIL_DUMMY_PACKET:
                link_errors[i] = eth

    if link_errors:
        for board_idx, eth in link_errors.items():
            print(
                CMD_LINE_COLOR.RED,
                f"Board {board_idx} has link error on eth port {eth}",
                CMD_LINE_COLOR.ENDC,
            )
        raise Exception(
            "WH Galaxy Ethernet link errors detected!"
        )
        # sys.exit(1)

def glx_6u_trays_reset(
        reinit: bool = True,
        ubb_num: str = "0xF",
        dev_num: str = "0xFF",
        op_mode: str = "0x0",
        reset_time: str = "0xF",
        print_status: bool = True):
    """
    Reset the WH asics on the galaxy systems with the following steps:
    1. Reset the trays with ipmi command
    2. Wait for 30s
    3. Reinit all chips

    Args:
        reinit (bool): Whether to reinitialize the chips after reset.
        ubb_num (str): The UBB number to reset. 0x0~0xF (bit map)
        dev_num (str): The device number to reset. 0x0~0xFF(bit map)
        op_mode (str): The operation mode to use.
                        0x0 - Asserted/Deassert reset with a reset period (reset_time)
                        0x1 - Asserted reset
                        0x2 - Deasserted reset
        reset_time (str): The reset time to use. resolution 10ms (ex. 0xF => 15 => 150ms)
        print_status (bool): Whether to print out animations while detecting chips.
    """
    print(
        CMD_LINE_COLOR.PURPLE,
        f"Resetting WH Galaxy trays with reset command...",
        CMD_LINE_COLOR.ENDC,
    )
    run_wh_ubb_ipmi_reset(ubb_num, dev_num, op_mode, reset_time)
    timed_wait(30)
    run_ubb_wait_for_driver_load()
    print(
        CMD_LINE_COLOR.PURPLE,
        f"Re-initializing boards after reset....",
        CMD_LINE_COLOR.ENDC,
    )
    if not reinit:
        print(
            CMD_LINE_COLOR.GREEN,
            f"Exiting after galaxy reset without re-initializing chips.",
            CMD_LINE_COLOR.ENDC,
        )
        sys.exit(0)
    try:
        # eth status 2 has been reused to denote "connected", leading to false hangs when detecting chips
        # discover local only to fix that
        chips = detect_chips_with_callback(local_only=True, ignore_ethernet=True, print_status=print_status)
        # Check the eth link status for WH Galaxy
    except Exception as e:
        print(
            CMD_LINE_COLOR.RED,
            f"Error when re-initializing chips!\n {e}",
            CMD_LINE_COLOR.ENDC,
        )
        # Error out if chips don't initalize
        sys.exit(1)

    # after re-init check eth status - only if doing a full galaxy reset.
    # If doing a partial reset, eth connections will be broken because eth training will go out of sync
    if ubb_num == 0xF:
        check_wh_galaxy_eth_link_status(chips)
    # All went well - exit with success
    print(
        CMD_LINE_COLOR.GREEN,
        f"Re-initialized {len(chips)} boards after reset. Exiting...",
        CMD_LINE_COLOR.ENDC,
    )
    sys.exit(0)
