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
from typing import Dict, List
from rich.progress import track
from tt_tools_common.ui_common.themes import CMD_LINE_COLOR
from tt_tools_common.reset_common.wh_reset import WHChipReset
from tt_tools_common.reset_common.bh_reset import BHChipReset
from tt_tools_common.reset_common.galaxy_reset import GalaxyReset
from tt_umd import (
    TTDevice,
    wormhole,
    blackhole,
    create_remote_wormhole_tt_device,
    RemoteWormholeTTDevice,
    ClusterDescriptor,
    ARCH,
    PCIDevice,
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
        umd_cluster_descriptor: ClusterDescriptor,
        fully_init: bool = True,
        pretty_output: bool = True,
    ):
        # During transitioning period to UMD, the tool will hold both luwen and UMD devices.
        self.construct_umd_devices(umd_cluster_descriptor)
        self.umd_cluster_descriptor = umd_cluster_descriptor
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
                for device in self.umd_device_dict
            ],
        )
        # print("device info ", self.log.device_info)
        self.smbus_telem_info = []
        self.firmware_infos = []
        self.device_infos = []
        self.device_telemetrys = []
        self.chip_limits = []
        self.pci_properties = []

        if fully_init:
            for i, _ in track(
                sorted(self.umd_device_dict.items()),
                total=len(self.umd_device_dict),
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

    def construct_umd_devices(self, umd_cluster_descriptor):
        # Note that mmio chips will always be first since they will have lower logical chip id.
        chips_to_construct = sorted(umd_cluster_descriptor.get_all_chips())
        self.umd_device_dict = {}
        # We need to keep these because the remote devices won't take ownership
        self.umd_local_chips = {}
        chip_to_mmio_map = umd_cluster_descriptor.get_chips_with_mmio()
        chip_eth_coords = umd_cluster_descriptor.get_chip_locations()
        for chip in chips_to_construct:
            if umd_cluster_descriptor.is_chip_mmio_capable(chip):
                self.umd_device_dict[chip] = TTDevice.create(chip_to_mmio_map[chip])
            else:
                closest_mmio = umd_cluster_descriptor.get_closest_mmio_capable_chip(chip)
                self.umd_device_dict[chip] = create_remote_wormhole_tt_device(self.umd_device_dict[closest_mmio], umd_cluster_descriptor, chip)

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
        for i, device in self.umd_device_dict.items():
            self.log.device_info[i].smbus_telem = self.smbus_telem_info[i]
            self.log.device_info[i].board_info = self.device_infos[i]
            # Add L/R for nb300 to separate local and remote asics
            if device.get_arch() == ARCH.WORMHOLE_B0:
                board_type = self.device_infos[i]["board_type"]
                suffix = " R" if self.umd_cluster_descriptor.is_chip_remote(i) else " L"
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
        for i, device in self.umd_device_dict.items():
            board_id = self.device_infos[i]["board_id"]
            board_type = self.device_infos[i]["board_type"]
            pci_dev_id = (
                self.umd_cluster_descriptor.get_chips_with_mmio()[i] if self.umd_cluster_descriptor.is_chip_mmio_capable(i) else "N/A"
            )
            if device.get_arch() == ARCH.WORMHOLE_B0:
                suffix = " R" if self.umd_cluster_descriptor.is_chip_remote(i) else " L"
                board_type = board_type + suffix

            table_1.add_row(
                f"{pci_dev_id}",
                f"{device.get_arch()}",
                f"{board_type}",
                f"{board_id}",
            )
        console.print(table_1)
        table_2 = Table(title="Boards that can be reset:")
        table_2.add_column("PCI Dev ID")
        table_2.add_column("Board Type")
        table_2.add_column("Device Series")
        table_2.add_column("Board Number")
        for i, device in self.umd_device_dict.items():
            if (
                self.umd_cluster_descriptor.is_chip_mmio_capable(i)
                and self.device_infos[i]["board_type"] != "wh_4u"
            ):
                board_id = self.device_infos[i]["board_id"]
                board_type = self.device_infos[i]["board_type"]
                pci_dev_id = self.umd_cluster_descriptor.get_chips_with_mmio()[i]
                if device.get_arch() == ARCH.WORMHOLE_B0:
                    suffix = " R" if self.umd_cluster_descriptor.is_chip_remote(i) else " L"
                    board_type = board_type + suffix
                table_2.add_row(
                    f"{pci_dev_id}",
                    f"{device.get_arch()}",
                    f"{board_type}",
                    f"{board_id}",
                )
        console.print(table_2)

    def get_smbus_board_info(self, board_num: int) -> Dict:
        """Update board info by reading SMBUS_TELEMETRY"""
        smbus_telem_dict = {}
        # print("get_smbus_board_info for board_num: ", board_num)
        if self.umd_device_dict[board_num].get_arch() == ARCH.BLACKHOLE:
            telem_reader = self.umd_device_dict[board_num].get_arc_telemetry_reader()
            for telem_key in blackhole.TelemetryTag:
                telem_value = hex(telem_reader.read_entry(telem_key.value)) if telem_reader.is_entry_available(telem_key.value) else None
                smbus_telem_dict[telem_key.name] = telem_value
                    
            # print ("Got bh smbus telem from umd: ", smbus_telem_dict)
        elif self.umd_device_dict[board_num].get_arch() == ARCH.WORMHOLE_B0:
            telem_reader = self.umd_device_dict[board_num].get_arc_telemetry_reader()
            for telem_key in wormhole.TelemetryTag:
                telem_value = hex(telem_reader.read_entry(telem_key.value)) if telem_reader.is_entry_available(telem_key.value) else None
                smbus_telem_dict[telem_key.name] = telem_value
                    
            # print ("Got wh smbus telem from umd: ", smbus_telem_dict)
        return smbus_telem_dict

    def update_telem(self):
        """Update telemetry in a given interval"""
        for i in self.umd_device_dict:
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
        # TODO: double check this one.
        # Seems like DDR_STATUS for WH gives speed, but for bh there's DDR_STATUS and DDR_SPEED ??
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
        if self.umd_cluster_descriptor.is_chip_remote(board_num):
            return {prop: "N/A" for prop in constants.PCI_PROPERTIES}

        try:
            pcie_bdf = self.umd_device_dict[board_num].get_pci_device().get_device_info().get_pci_bdf()
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
        
        # TODO: Were just adding this interface to ttdevice, so add it both for WH and BH
        if self.umd_device_dict[board_num].get_arch() == ARCH.WORMHOLE_B0:
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

    def get_device_info(self, board_num) -> dict:
        dev_info = {}
        for field in constants.DEV_INFO_LIST:
            if field == "bus_id":
                try:
                    if self.umd_cluster_descriptor.is_chip_mmio_capable(board_num):
                        dev_info[field] = self.umd_device_dict[board_num].get_pci_device().get_device_info().get_pci_bdf()
                    else:
                        dev_info[field] = "N/A"
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
                if self.umd_device_dict[board_num].get_arch() == ARCH.WORMHOLE_B0:
                    eth_coord = self.umd_cluster_descriptor.get_chip_locations()[board_num]
                    dev_info[
                        field
                    ] = f"({eth_coord.x}, {eth_coord.y}, {eth_coord.rack}, {eth_coord.shelf})"
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

    def get_chip_telemetry(self, board_num) -> Dict:
        """Return the correct chip telemetry for a given board"""
        arch = self.umd_device_dict[board_num].get_arch()
        if arch == ARCH.BLACKHOLE:
            return self.get_bh_chip_telemetry(board_num)
        elif arch == ARCH.WORMHOLE_B0:
            return self.get_wh_chip_telemetry(board_num)
        else:
            print(
                CMD_LINE_COLOR.RED,
                f"Could not fetch telemetry for board {arch}: Unrecognized board type!",
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
                # The tag has different value for WH and BH
                if "M3_BL_FW_VERSION" in self.smbus_telem_info[board_num]:
                    val = self.smbus_telem_info[board_num]["M3_BL_FW_VERSION"]
                if "BM_BL_FW_VERSION" in self.smbus_telem_info[board_num]:
                    val = self.smbus_telem_info[board_num]["BM_BL_FW_VERSION"]
                if val is None:
                    fw_versions[field] = "N/A"
                else:
                    fw_versions[field] = hex_to_semver_m3_fw(int(val, 16))
            elif field == "bm_app_fw":
                # The tag has different value for WH and BH
                if "M3_APP_FW_VERSION" in self.smbus_telem_info[board_num]:
                    val = self.smbus_telem_info[board_num]["M3_APP_FW_VERSION"]
                if "BM_APP_FW_VERSION" in self.smbus_telem_info[board_num]:
                    val = self.smbus_telem_info[board_num]["BM_APP_FW_VERSION"]
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
                # The tag has different value for WH and BH
                # print("looking for fw_bundle_version for board_num: ", board_num)
                # print("smbus_telem info keys: ", len(self.smbus_telem_info))
                if "FW_BUNDLE_VERSION" in self.smbus_telem_info[board_num]:
                    val = self.smbus_telem_info[board_num]["FW_BUNDLE_VERSION"]
                elif "FLASH_BUNDLE_VERSION" in self.smbus_telem_info[board_num]:
                    val = self.smbus_telem_info[board_num]["FLASH_BUNDLE_VERSION"]
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
        "tt_umd": pkg_resources.get_distribution("tt_umd").version,
    }


# Reset specific functions


def pci_indices_from_json(json_dict):
    """Parse pci_list from reset json"""
    pci_indices = []
    reinit = False
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
    reset_bh_pci_idx = []
    device_infos = PCIDevice.enumerate_devices_info()
    for pci_idx in list_of_boards:
        arch = device_infos[pci_idx].get_arch()
        if arch == ARCH.WORMHOLE_B0:
            reset_wh_pci_idx.append(pci_idx)
        elif arch == ARCH.BLACKHOLE:
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
    for i, device in self.umd_device_dict.items():
        for eth in range(16):
            eth_x, eth_y = eth_locations_noc_0[eth]
            link_error = device.noc_read32(eth_x, eth_y, DEBUG_BUF_ADDR + 0x4*96)
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

def glx_6u_trays_reset(reinit=True):
    """
    Reset the WH asics on the galaxy systems with the following steps:
    1. Reset the trays with ipmi command
    2. Wait for 30s
    3. Reinit all chips
    """
    ubb_num = "0xF"
    dev_num = "0xFF"
    op_mode = "0x0"
    reset_time = "0xF"
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
            f"Exiting after galoaxy reset without re-initializing chips.",
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

    # after re-init check eth status
    check_wh_galaxy_eth_link_status(chips)
    # All went well - exit with success
    print(
        CMD_LINE_COLOR.GREEN,
        f"Re-initialized {len(chips)} boards after reset. Exiting...",
        CMD_LINE_COLOR.ENDC,
    )
    sys.exit(0)
