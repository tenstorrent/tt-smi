# SPDX-FileCopyrightText: © 2023 Tenstorrent Inc.
# SPDX-License-Identifier: Apache-2.0

"""
This is the backend of tt-smi.
    - Keeps track of chip objects and tasks related to fetching device info and telemetry 
    - Sanitizes input for frontend
"""

import os
import jsons
import datetime
import tt_utils_common
from pathlib import Path
from pyluwen import PciChip
from rich.progress import track
from tt_smi import log, constants
from tt_smi.registers import Registers
from tt_smi.gs_tensix_reset import GSTensixReset
from tt_smi.ui.common_themes import CMD_LINE_COLOR
from tt_utils_common import get_host_info, init_logging
from typing import Dict, List, OrderedDict, Optional, Callable

LOG_FOLDER = os.path.expanduser("~/tt_smi_logs/")


class TTSMIBackend:
    """
    TT-SMI backend class that encompasses all chip objects on host.
    It handles all device related tasks like fetching device info, telemetry and toggling resets
    """

    def __init__(
        self,
        devices: List[PciChip],
        telem_struct_override: Optional[Callable[[PciChip], dict]] = None,
        set_safe_clock_override: Optional[Callable[[PciChip, int], None]] = None,
    ):
        self.devices = devices
        self.telem_struct_override = telem_struct_override
        self.safe_clock_override = set_safe_clock_override
        self.log: log.TTSMILog = log.TTSMILog(
            time=datetime.datetime.now(),
            host_info=get_host_info(),
            device_info=[
                log.TTSMIDeviceLog(
                    board_info=log.BoardInfo(),
                    telemetry=log.Telemetry(),
                    firmwares=log.Firmwares(),
                    limits=log.Limits(),
                    # smbus_telem=log.SmbusTelem(),
                )
                for device in self.devices
            ],
        )
        self.smbus_telem_info = []
        self.firmware_infos = []
        self.device_infos = []
        self.device_telemetrys = []
        self.chip_limits = []
        self.registers = []

        for i, device in track(
            enumerate(self.devices),
            total=len(self.devices),
            description="Gathering Information",
            update_period=0.01,
        ):
            self.smbus_telem_info.append(self.get_smbus_board_info(i))
            self.firmware_infos.append(self.get_firmware_versions(i))
            self.device_infos.append(self.get_device_info(i))
            self.device_telemetrys.append(self.get_chip_telemetry(i))
            self.chip_limits.append(self.get_chip_limits(i))
            self.registers.append(self.get_register_object(device))

    def get_device_name(self, device):
        """Get device name from chip object"""
        if device.as_gs():
            return "grayskull"
        elif device.as_wh():
            return "wormhole"
        else:
            assert False, "Unkown chip name, FIX!"

    def save_logs(self, result_filename: str = None):
        time_now = datetime.datetime.now()
        date_string = time_now.strftime("%m-%d-%Y_%H:%M:%S")
        if not os.path.exists(LOG_FOLDER):
            init_logging(LOG_FOLDER)
        log_filename = f"{LOG_FOLDER}{date_string}_results.json"
        if result_filename:
            dir_path = os.path.dirname(os.path.realpath(result_filename))
            Path(dir_path).mkdir(parents=True, exist_ok=True)
            log_filename = result_filename
        for i in range(0, len(self.devices)):
            self.log.device_info[i].board_info = self.device_infos[i]
            self.log.device_info[i].telemetry = self.device_telemetrys[i]
            self.log.device_info[i].firmwares = self.firmware_infos[i]
            self.log.device_info[i].limits = self.chip_limits[i]
            # self.log.device_info[i].smbus_telem = self.smbus_telem_info[i]
        self.log.save_as_json(log_filename)
        return log_filename

    def print_all_available_devices(self):
        """Print all available boards on host"""
        print(
            CMD_LINE_COLOR.YELLOW, "All available boards on host:", CMD_LINE_COLOR.ENDC
        )
        for i, device in enumerate(self.devices):
            board_id = self.device_infos[i]["board_id"]
            board_type = self.device_infos[i]["board_type"]

            print(
                CMD_LINE_COLOR.BLUE,
                f"{i}: {board_id} ({self.get_device_name(device)} - {board_type})",
                CMD_LINE_COLOR.ENDC,
            )

    def get_smbus_board_info(self, board_num: int) -> Dict:
        """Update board info by reading SMBUS_TELEMETRY"""
        pylewen_chip = self.devices[board_num]
        if self.telem_struct_override is not None:
            telem_struct = self.telem_struct_override(pylewen_chip)
        else:
            telem_struct = pylewen_chip.get_telemetry()

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

        board_info_0 = self.smbus_telem_info[board_num]["SMBUS_TX_BOARD_ID_LOW"]
        board_info_1 = self.smbus_telem_info[board_num]["SMBUS_TX_BOARD_ID_HIGH"]
        if board_info_0 is None or board_info_1 is None:
            return "N/A"
        board_info_0 = (f"{board_info_0}").replace("0x", "")
        board_info_1 = (f"{board_info_1}").replace("x", "")
        return f"{board_info_1}{board_info_0}"

    def get_dram_speed(self, board_num) -> int:
        """Read DRAM Frequency from CSM and alternatively from SPI if FW not loaded on chip"""
        if self.devices[board_num].as_gs():
            val = int(self.smbus_telem_info[board_num]["SMBUS_TX_DDR_SPEED"], 16)
            return f"{val}"
        dram_speed_raw = (
            int(self.smbus_telem_info[board_num]["SMBUS_TX_DDR_STATUS"], 16) >> 24
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

    def get_pci_speed_width(self, board_num):
        """Get PCI Speed and Width from telemetry struct"""
        pci_val = (
            int(self.smbus_telem_info[board_num]["SMBUS_TX_PCIE_STATUS"], 16)
            if self.smbus_telem_info[board_num]["SMBUS_TX_PCIE_STATUS"] is not None
            else None
        )

        if pci_val is None:
            return 0, 0
        width = (pci_val >> 16) & 0xF
        speed = (pci_val >> 20) & 0x3F

        return width, speed

    def get_dram_training_status(self, board_num) -> bool:
        """Get DRAM Training Status
        True means it passed training, False means it failed or did not train at all"""
        if self.devices[board_num].as_wh():
            num_channels = 8
            for i in range(num_channels):
                if self.smbus_telem_info[board_num]["SMBUS_TX_DDR_STATUS"] is None:
                    return False
                dram_status = (
                    int(self.smbus_telem_info[board_num]["SMBUS_TX_DDR_STATUS"], 16)
                    >> (4 * i)
                ) & 0xF
                if dram_status != 2:
                    return False
                return True
        elif self.devices[board_num].as_gs():
            num_channels = 6
            for i in range(num_channels):
                if self.smbus_telem_info[board_num]["SMBUS_TX_DDR_STATUS"] is None:
                    return False
                dram_status = (
                    int(self.smbus_telem_info[board_num]["SMBUS_TX_DDR_STATUS"], 16)
                    >> (4 * i)
                ) & 0xF
                if dram_status != 1:
                    return False
                return True

    def get_device_info(self, board_num) -> OrderedDict:
        dev_info = OrderedDict()
        for field in constants.DEV_INFO_LIST:
            if field == "bus_id":
                try:
                    dev_info[field] = self.devices[board_num].get_pci_bdf()
                except:
                    dev_info[field] = "N/A"
            elif field == "board_type":
                dev_info[field] = tt_utils_common.get_board_type(
                    self.get_board_id(board_num)
                )
            elif field == "board_id":
                dev_info[field] = self.get_board_id(board_num)
            elif field == "coords":
                dev_info[field] = "N/A"
            elif field == "dram_status":
                dev_info[field] = self.get_dram_training_status(board_num)
            elif field == "dram_speed":
                dev_info[field] = self.get_dram_speed(board_num)
            elif field == "pcie_speed":
                dev_info[field], _ = self.get_pci_speed_width(board_num)
            elif field == "pcie_width":
                _, dev_info[field] = self.get_pci_speed_width(board_num)

        return dev_info

    def get_chip_telemetry(self, board_num) -> Dict:
        """Get telemetry data for chip. None if ARC FW not running"""

        current = int(self.smbus_telem_info[board_num]["SMBUS_TX_TDC"], 16) & 0xFFFF
        voltage = int(self.smbus_telem_info[board_num]["SMBUS_TX_VCORE"], 16) / 1000
        power = int(self.smbus_telem_info[board_num]["SMBUS_TX_TDP"], 16) & 0xFFFF
        asic_temperature = (
            int(self.smbus_telem_info[board_num]["SMBUS_TX_ASIC_TEMPERATURE"], 16)
            & 0xFFFF
        ) / 16
        aiclk = int(self.smbus_telem_info[board_num]["SMBUS_TX_AICLK"], 16) & 0xFFFF

        chip_telemetry = {
            "voltage": f"{voltage:4.2f}",
            "current": f"{current:5.1f}",
            "power": f"{power:5.1f}",
            "aiclk": f"{aiclk:4.0f}",
            "asic_temperature": f"{asic_temperature:4.1f}",
        }

        return chip_telemetry

    def get_chip_limits(self, board_num):
        """Get chip limits from the CSM. None if ARC FW not running"""

        chip_limits = {}
        for field in constants.LIMITS:
            if field == "vdd_min":
                value = (
                    int(self.smbus_telem_info[board_num]["SMBUS_TX_VDD_LIMITS"], 16)
                    & 0xFFFF
                    if self.smbus_telem_info[board_num]["SMBUS_TX_VDD_LIMITS"]
                    is not None
                    else None
                )
                chip_limits[field] = f"{value/1000:4.2f}" if value is not None else None
            elif field == "vdd_max":
                value = (
                    int(self.smbus_telem_info[board_num]["SMBUS_TX_VDD_LIMITS"], 16)
                    >> 16
                    if self.smbus_telem_info[board_num]["SMBUS_TX_VDD_LIMITS"]
                    is not None
                    else None
                )
                chip_limits[field] = f"{value/1000:4.2f}" if value is not None else None
            elif field == "tdp_limit":
                value = (
                    int(self.smbus_telem_info[board_num]["SMBUS_TX_TDP"], 16) >> 16
                    if self.smbus_telem_info[board_num]["SMBUS_TX_TDP"] is not None
                    else None
                )
                chip_limits[field] = f"{value:3.0f}" if value is not None else None
            elif field == "tdc_limit":
                value = (
                    int(self.smbus_telem_info[board_num]["SMBUS_TX_TDC"], 16) >> 16
                    if self.smbus_telem_info[board_num]["SMBUS_TX_TDC"] is not None
                    else None
                )
                chip_limits[field] = f"{value:3.0f}" if value is not None else None
            elif field == "asic_fmax":
                value = (
                    int(self.smbus_telem_info[board_num]["SMBUS_TX_AICLK"], 16) >> 16
                    if self.smbus_telem_info[board_num]["SMBUS_TX_AICLK"] is not None
                    else None
                )
                chip_limits[field] = f"{value:4.0f}" if value is not None else None
            elif field == "therm_trip_l1_limit":
                value = (
                    int(self.smbus_telem_info[board_num]["SMBUS_TX_THM_LIMITS"], 16)
                    >> 16
                    if self.smbus_telem_info[board_num]["SMBUS_TX_THM_LIMITS"]
                    is not None
                    else None
                )
                chip_limits[field] = f"{value:2.0f}" if value is not None else None
            elif field == "thm_limit":
                value = (
                    int(self.smbus_telem_info[board_num]["SMBUS_TX_THM_LIMITS"], 16)
                    & 0xFFFF
                    if self.smbus_telem_info[board_num]["SMBUS_TX_THM_LIMITS"]
                    is not None
                    else None
                )
                chip_limits[field] = f"{value:2.0f}" if value is not None else None
            else:
                chip_limits[field] = None
        return chip_limits

    def get_firmware_versions(self, board_num):
        """Translate the telem struct semver for gui"""
        fw_versions = OrderedDict()
        for field in constants.FW_LIST:
            if field == "arc_fw":
                val = self.smbus_telem_info[board_num]["SMBUS_TX_ARC0_FW_VERSION"]
                if val is None:
                    fw_versions[field] = "N/A"
                else:
                    fw_versions[field] = tt_utils_common.hex_to_semver_m3_fw(
                        int(val, 16)
                    )
            elif field == "arc_fw_date":
                val = self.smbus_telem_info[board_num]["SMBUS_TX_WH_FW_DATE"]
                if val is None:
                    fw_versions[field] = "N/A"
                else:
                    fw_versions[field] = tt_utils_common.hex_to_date(
                        int(val, 16), include_time=False
                    )
            elif field == "eth_fw":
                val = self.smbus_telem_info[board_num]["SMBUS_TX_ETH_FW_VERSION"]
                if val is None:
                    fw_versions[field] = "N/A"
                else:
                    fw_versions[field] = tt_utils_common.hex_to_semver_eth(int(val, 16))
            elif field == "m3_bl_fw":
                val = self.smbus_telem_info[board_num]["SMBUS_TX_M3_BL_FW_VERSION"]
                if val is None:
                    fw_versions[field] = "N/A"
                else:
                    fw_versions[field] = tt_utils_common.hex_to_semver_m3_fw(
                        int(val, 16)
                    )

            elif field == "m3_app_fw":
                val = self.smbus_telem_info[board_num]["SMBUS_TX_M3_APP_FW_VERSION"]
                if val is None:
                    fw_versions[field] = "N/A"
                else:
                    fw_versions[field] = tt_utils_common.hex_to_semver_m3_fw(
                        int(val, 16)
                    )
            elif field == "tt_flash_version":
                val = self.smbus_telem_info[board_num]["SMBUS_TX_TT_FLASH_VERSION"]
                if val is None:
                    fw_versions[field] = "N/A"
                else:
                    fw_versions[field] = tt_utils_common.hex_to_semver_m3_fw(
                        int(val, 16)
                    )
        return fw_versions

    def get_register_object(self, device) -> Registers:
        """
        Generate register object for a given device.
        This is used to read/write registers
        """

        def reader_function(byte_addr: int, path) -> int:
            return device.axi_read32(byte_addr)

        def writer_function(byte_addr: int, value: int, path) -> None:
            device.axi_write32(byte_addr, value)

        if self.get_device_name(device) == "wormhole":
            return None

        root_yaml_path = f"data/{self.get_device_name(device)}/axi-pci.yaml"
        return Registers(root_yaml_path, reader_function, writer_function)

    def gs_tensix_reset(self, board_num) -> None:
        """Reset the tensix's on a GS chip"""
        device = self.devices[board_num]
        axi_registers = self.registers[board_num]
        gs_tensix_reset_obj = GSTensixReset(device, axi_registers)
        print(
            CMD_LINE_COLOR.YELLOW, "Lowering clks to safe value...", CMD_LINE_COLOR.ENDC
        )
        if self.safe_clock_override is not None:
            # Force clks to safe value - if user has older fw
            self.safe_clock_override(device, True)
        else:
            gs_tensix_reset_obj.enter_safe_clks()
        try:
            print(
                CMD_LINE_COLOR.YELLOW,
                "Beginning reset sequence...",
                CMD_LINE_COLOR.ENDC,
            )
            gs_tensix_reset_obj.all_riscs_assert_reset()
            gs_tensix_reset_obj.msg_tensix_toggle_reset()
            gs_tensix_reset_obj.get_harvesting()
            gs_tensix_reset_obj.setup_interface()
            gs_tensix_reset_obj.assert_all_riscv_soft_reset()
            gs_tensix_reset_obj.all_riscs_deassert_reset()
            print(
                CMD_LINE_COLOR.YELLOW,
                "Finishing reset sequence...",
                CMD_LINE_COLOR.ENDC,
            )
        finally:
            print(
                CMD_LINE_COLOR.YELLOW,
                "Returning clks to original values...",
                CMD_LINE_COLOR.ENDC,
            )
            if self.safe_clock_override is not None:
                # Set clks back to original value - if user has older fw
                self.safe_clock_override(device, False)  
            else:              
                gs_tensix_reset_obj.exit_safe_clks()
