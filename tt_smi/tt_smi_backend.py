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
import pkg_resources
from tt_smi import log
from pathlib import Path
from rich.table import Table
from tt_smi import constants
from rich import get_console
from rich.syntax import Syntax
from typing import Any, Dict, List, Optional
from rich.progress import track
from tt_tools_common.ui_common.themes import CMD_LINE_COLOR
from tt_tools_common.reset_common.wh_reset import WHChipReset
from tt_tools_common.reset_common.bh_reset import BHChipReset
from tt_tools_common.reset_common.gs_tensix_reset import GSTensixReset
from tt_tools_common.reset_common.galaxy_reset import GalaxyReset
from pyluwen import (
    PciChip,
    run_wh_ubb_ipmi_reset,
    run_ubb_wait_for_driver_load
)
from tt_umd import (
    TTDevice,
    wormhole,
    TelemetryTag,
    create_remote_wormhole_tt_device,
    ClusterDescriptor,
    ARCH,
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
        devices: List[PciChip] = None,
        umd_cluster_descriptor: Optional[ClusterDescriptor] = None,
        fully_init: bool = True,
        pretty_output: bool = True,
    ):
        self.devices = devices
        self.use_umd = umd_cluster_descriptor is not None
        if (self.use_umd):
            self.umd_cluster_descriptor = umd_cluster_descriptor
            self.construct_umd_devices()
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
                for device in self.get_devices()
            ],
        )
        self.smbus_telem_info = []
        self.firmware_infos = []
        self.device_infos = []
        self.device_telemetrys = []
        self.chip_limits = []
        self.pci_properties = []

        if fully_init:
            for i, _ in track(
                self.get_devices().items(),
                total=len(self.get_devices()),
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

    # The following set of functions is temporary and should be revised once we switch to using a single driver.
    # This function returns a dictionary of currently used devices. It can return either the pyluwen devices or the umd devices.
    def get_devices(self) -> Dict[int, Any]:
        """Get devices"""
        return (dict(enumerate(self.devices)) if not self.use_umd
                else self.umd_device_dict)
    
    def is_blackhole(self, device_idx) -> bool:
        return (self.devices[device_idx].as_bh() if not self.use_umd
                else self.umd_device_dict[device_idx].get_arch() == ARCH.BLACKHOLE)
    
    def is_wormhole(self, device_idx) -> bool:
        return (self.devices[device_idx].as_wh() if not self.use_umd
                else self.umd_device_dict[device_idx].get_arch() == ARCH.WORMHOLE_B0)

    def is_grayskull(self, device_idx) -> bool:
        return (self.devices[device_idx].as_gs() if not self.use_umd
                else False)
    
    def is_remote(self, device_idx) -> bool:
        return (self.devices[device_idx].is_remote() if not self.use_umd
                else self.umd_cluster_descriptor.is_chip_remote(device_idx))
    
    def get_pci_device_id(self, device_idx) -> str:
        if self.is_remote(device_idx):
            return "N/A"
        return (self.devices[device_idx].get_pci_interface_id() if not self.use_umd
                else self.umd_device_dict[device_idx].get_pci_interface_id())
        
    def get_pci_bdf(self, device_idx) -> str:
        if self.is_remote(device_idx):
            return "N/A"
        return (self.devices[device_idx].get_pci_bdf() if not self.use_umd
                else self.umd_device_dict[device_idx].get_pci_device().get_device_info().pci_bdf)
    
    def get_device_name(self, device_idx):
        """Get device name from chip object"""
        if self.is_grayskull(device_idx):
            return "Grayskull"
        elif self.is_wormhole(device_idx):
            return "Wormhole"
        elif self.is_blackhole(device_idx):
            return "Blackhole"
        else:
            assert False, "Unknown chip name, FIX!"

    def construct_umd_devices(self):
        # Note that we have to create mmio chips first, since they are passed to the construction of the remote chips.
        chips_to_construct = self.umd_cluster_descriptor.get_chips_local_first(self.umd_cluster_descriptor.get_all_chips())
        self.umd_device_dict = {}
        for chip in chips_to_construct:
            if self.umd_cluster_descriptor.is_chip_mmio_capable(chip):
                pci_device_num = self.umd_cluster_descriptor.get_chips_with_mmio()[chip]
                self.umd_device_dict[chip] = TTDevice.create(pci_device_num)
            else:
                closest_mmio = self.umd_cluster_descriptor.get_closest_mmio_capable_chip(chip)
                self.umd_device_dict[chip] = create_remote_wormhole_tt_device(self.umd_device_dict[closest_mmio], self.umd_cluster_descriptor, chip)
            self.umd_device_dict[chip].init_tt_device()

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
        for i in self.get_devices():
            self.log.device_info[i].smbus_telem = self.smbus_telem_info[i]
            self.log.device_info[i].board_info = self.device_infos[i]
            # Add L/R for nb300 to separate local and remote asics
            if self.is_wormhole(i):
                board_type = self.device_infos[i]["board_type"]
                suffix = " R" if self.is_remote(i) else " L"
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
        for i in self.get_devices():
            board_id = self.device_infos[i]["board_id"]
            board_type = self.device_infos[i]["board_type"]
            pci_dev_id = self.get_pci_device_id(i)
            if self.is_wormhole(i):
                suffix = " R" if self.is_remote(i) else " L"
                board_type = board_type + suffix

            table_1.add_row(
                f"{pci_dev_id}",
                f"{self.get_device_name(i)}",
                f"{board_type}",
                f"{board_id}",
            )
        console.print(table_1)
        table_2 = Table(title="Boards that can be reset:")
        table_2.add_column("PCI Dev ID")
        table_2.add_column("Board Type")
        table_2.add_column("Device Series")
        table_2.add_column("Board Number")
        for i in self.get_devices():
            if (
                not self.is_remote(i)
                and self.device_infos[i]["board_type"] != "wh_4u"
            ):
                board_id = self.device_infos[i]["board_id"]
                board_type = self.device_infos[i]["board_type"]
                pci_dev_id = self.get_pci_device_id(i)
                if self.is_wormhole(i):
                    suffix = " R" if self.is_remote(i) else " L"
                    board_type = board_type + suffix
                table_2.add_row(
                    f"{pci_dev_id}",
                    f"{self.get_device_name(i)}",
                    f"{board_type}",
                    f"{board_id}",
                )
        console.print(table_2)

    def get_smbus_board_info(self, board_num: int) -> Dict:
        """Update board info by reading SMBUS_TELEMETRY"""
        if self.use_umd:
            smbus_telem_dict = {}
            tag_collection = {
                # Yeah this is to be refactored
                ARCH.BLACKHOLE: TelemetryTag,
                ARCH.WORMHOLE_B0: wormhole.TelemetryTag,
            }.get(self.umd_device_dict[board_num].get_arch())

            telem_reader = self.umd_device_dict[board_num].get_arc_telemetry_reader()
            for telem_key in tag_collection:
                telem_value = hex(telem_reader.read_entry(telem_key.value)) if telem_reader.is_entry_available(telem_key.value) else None
                smbus_telem_dict[telem_key.name] = telem_value
            return smbus_telem_dict
        
        pyluwen_chip = self.devices[board_num]
        if pyluwen_chip.as_bh():
            telem_struct = pyluwen_chip.as_bh().get_telemetry()
        elif pyluwen_chip.as_wh():
            telem_struct = pyluwen_chip.as_wh().get_telemetry()
        else:
            telem_struct = pyluwen_chip.as_gs().get_telemetry()
        json_map = dict_from_public_attrs(telem_struct)
        smbus_telem_dict = dict.fromkeys(constants.SMBUS_TELEMETRY_LIST)

        for key, value in json_map.items():
            if value:
                smbus_telem_dict[key.upper()] = hex(value)
        return smbus_telem_dict

    def update_telem(self):
        """Update telemetry in a given interval"""
        for i in self.get_devices():
            self.smbus_telem_info[i] = self.get_smbus_board_info(i)
            self.device_telemetrys[i] = self.get_chip_telemetry(i)

    def get_board_id(self, board_num) -> str:
        """Read board id from CSM or SPI if FW is not loaded"""
        if "BOARD_ID" in self.smbus_telem_info[board_num] and self.smbus_telem_info[board_num]["BOARD_ID"]:
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
        if self.is_grayskull(board_num):
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
        if self.is_remote(board_num):
            return {prop: "N/A" for prop in constants.PCI_PROPERTIES}

        try:
            pcie_bdf = self.get_pci_device_id(board_num)
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
        if self.is_wormhole(board_num):
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
        elif self.is_grayskull(board_num):
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
                    dev_info[field] = self.get_pci_bdf(board_num)
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
                if self.is_wormhole(board_num):
                    if self.use_umd:
                        eth_coord = self.umd_cluster_descriptor.get_chip_locations()[board_num]
                        dev_info[
                            field
                        ] = f"({eth_coord.x}, {eth_coord.y}, {eth_coord.rack}, {eth_coord.shelf})"
                    else:
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

        chip_telemetry = {
            "voltage": f"{voltage:4.2f}",
            "current": f"{current:5.1f}",
            "power": f"{power:5.1f}",
            "aiclk": f"{aiclk:4.0f}",
            "asic_temperature": f"{asic_temperature:4.1f}",
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

        chip_telemetry = {
            "voltage": f"{voltage:4.2f}",
            "current": f"{current:5.1f}",
            "power": f"{power:5.1f}",
            "aiclk": f"{aiclk:4.0f}",
            "asic_temperature": f"{asic_temperature:4.1f}",
            "heartbeat": f"{arc3_heartbeat}"
        }

        return chip_telemetry

    def get_gs_chip_telemetry(self, board_num) -> Dict:
        """Get telemetry data for GS chip. None if ARC FW not running"""
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
        arc0_heartbeat = int(self.smbus_telem_info[board_num]["ARC0_HEALTH"], 16) // 1000 # Watchdog heartbeat, ~2 per second

        chip_telemetry = {
            "voltage": f"{voltage:4.2f}",
            "current": f"{current:5.1f}",
            "power": f"{power:5.1f}",
            "aiclk": f"{aiclk:4.0f}",
            "asic_temperature": f"{asic_temperature:4.1f}",
            "heartbeat": f"{arc0_heartbeat}"
        }

        return chip_telemetry

    def get_chip_telemetry(self, board_num) -> Dict:
        """Return the correct chip telemetry for a given board"""
        if self.is_blackhole(board_num):
            return self.get_bh_chip_telemetry(board_num)
        elif self.is_grayskull(board_num):
            return self.get_gs_chip_telemetry(board_num)
        elif self.is_wormhole(board_num):
            return self.get_wh_chip_telemetry(board_num)
        else:
            print(
                CMD_LINE_COLOR.RED,
                f"Could not fetch telemetry for board {e}: Unrecognized board type!",
                CMD_LINE_COLOR.ENDC,
            )
            return {}

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
                if "ARC0_FW_VERSION" in self.smbus_telem_info[board_num]:
                    val = self.smbus_telem_info[board_num]["ARC0_FW_VERSION"]
                if val is None:
                    fw_versions[field] = "N/A"
                else:
                    fw_versions[field] = hex_to_semver_m3_fw(int(val, 16))

            elif field == "cm_fw_date":
                if "WH_FW_DATE" in self.smbus_telem_info[board_num]:
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
                if self.use_umd:
                    # The tag has different value for old WH telemetry and new telemetry.
                    if "M3_BL_FW_VERSION" in self.smbus_telem_info[board_num]:
                        val = self.smbus_telem_info[board_num]["M3_BL_FW_VERSION"]
                    if "BM_BL_FW_VERSION" in self.smbus_telem_info[board_num]:
                        val = self.smbus_telem_info[board_num]["BM_BL_FW_VERSION"]
                else:
                    val = self.smbus_telem_info[board_num]["M3_BL_FW_VERSION"]
                if val is None:
                    fw_versions[field] = "N/A"
                else:
                    fw_versions[field] = hex_to_semver_m3_fw(int(val, 16))
            elif field == "bm_app_fw":
                if self.use_umd:
                    # The tag has different value for WH and BH
                    if "M3_APP_FW_VERSION" in self.smbus_telem_info[board_num]:
                        val = self.smbus_telem_info[board_num]["M3_APP_FW_VERSION"]
                    if "BM_APP_FW_VERSION" in self.smbus_telem_info[board_num]:
                        val = self.smbus_telem_info[board_num]["BM_APP_FW_VERSION"]
                else:
                    val = self.smbus_telem_info[board_num]["M3_APP_FW_VERSION"]
                if val is None:
                    fw_versions[field] = "N/A"
                else:
                    fw_versions[field] = hex_to_semver_m3_fw(int(val, 16))
            elif field == "tt_flash_version":
                if "TT_FLASH_VERSION" in self.smbus_telem_info[board_num]:
                    val = self.smbus_telem_info[board_num]["TT_FLASH_VERSION"]
                if val is None:
                    fw_versions[field] = "N/A"
                # See below- Galaxy systems manually get an N/A tt_flash_version
                elif get_board_type(self.get_board_id(board_num)) == "wh_4u":
                    fw_versions[field] = "N/A"
                else:
                    fw_versions[field] = hex_to_semver_m3_fw(int(val, 16))
            elif field == "fw_bundle_version":
                if self.use_umd:
                    # The tag has different value for WH and BH
                    if "FW_BUNDLE_VERSION" in self.smbus_telem_info[board_num]:
                        val = self.smbus_telem_info[board_num]["FW_BUNDLE_VERSION"]
                    elif "FLASH_BUNDLE_VERSION" in self.smbus_telem_info[board_num]:
                        val = self.smbus_telem_info[board_num]["FLASH_BUNDLE_VERSION"]
                else:
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
        "tt_smi": pkg_resources.get_distribution("tt_smi").version,
        "pyluwen": pkg_resources.get_distribution("pyluwen").version,
        "tt_umd": pkg_resources.get_distribution("tt_umd").version,
    }


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

def glx_6u_trays_reset(reinit=True, ubb_num="0xF", dev_num="0xFF", op_mode="0x0", reset_time="0xF"):
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
        chips = detect_chips_with_callback(local_only=True, ignore_ethernet=True)
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
