# SPDX-FileCopyrightText: © 2026 Tenstorrent Inc.
# SPDX-License-Identifier: Apache-2.0

import json
import pytest
import subprocess


@pytest.fixture(scope="session")
def snapshot(backend) -> dict:
    """Return snapshot data from TTSMIBackend."""
    log_str = backend.get_logs_json()
    log_json = json.loads(log_str)
    return log_json


@pytest.mark.requires_hardware
class TestSnapshot:
    """Test the tt-smi -s (snapshot) interface."""

    def test_snapshot_is_valid_json(self, snapshot):
        """Test if log string is valid json"""
        assert isinstance(snapshot, dict)

    def test_snapshot_fields_present(self, snapshot):
        """Test if fields are present in snapshot"""
        assert "time" in snapshot
        assert "host_info" in snapshot
        assert "host_sw_vers" in snapshot
        assert "device_info" in snapshot

    def test_host_info_fields_present(self, snapshot):
        """Test if fields are present in host_info"""
        host_info = snapshot["host_info"]
        assert "OS" in host_info
        assert "Distro" in host_info
        assert "Kernel" in host_info
        assert "Hostname" in host_info
        assert "Platform" in host_info
        assert "Python" in host_info
        assert "Memory" in host_info
        assert "Driver" in host_info

    def test_device_info_fields_present(self, snapshot):
        """Test if fields are present in device_info"""
        device_infos = snapshot["device_info"]
        for device_info in device_infos:
            assert "smbus_telem" in device_info
            assert "board_info" in device_info
            assert "telemetry" in device_info
            assert "firmwares" in device_info
            assert "limits" in device_info

    def test_smbus_telem_fields_present(self, snapshot):
        """Test if fields are present in smbus_telem."""
        smbus_telem_list = [
            "BOARD_ID_HIGH",
            "BOARD_ID_LOW",
            "ETH_FW_VERSION",
            "DDR_STATUS",
            "FAN_SPEED",
            # "FAN_RPM",
            "AICLK",
            "AXICLK",
            "ARCCLK",
            "VCORE",
            "ASIC_TEMPERATURE",
            "VREG_TEMPERATURE",
            "BOARD_TEMPERATURE",
            "TDP",
            "TDC",
            "VDD_LIMITS",
            "THM_LIMITS",
            "TT_FLASH_VERSION",
        ]
        device_infos = snapshot["device_info"]
        for device_info in device_infos:
            smbus_telem = device_info["smbus_telem"]
            for field in smbus_telem_list:
                assert field in smbus_telem

    def test_board_info_fields_present(self, snapshot):
        """Test if fields are present in board_info."""
        device_infos = snapshot["device_info"]
        for device_info in device_infos:
            board_info = device_info["board_info"]
            assert "bus_id" in board_info
            assert "board_type" in board_info
            assert "board_id" in board_info
            assert "coords" in board_info
            assert "dram_status" in board_info
            assert "dram_speed" in board_info
            assert "pcie_speed" in board_info
            assert "pcie_width" in board_info

    def test_telemetry_fields_present(self, snapshot):
        """Test if fields are present in telemetry."""
        device_infos = snapshot["device_info"]
        for device_info in device_infos:
            telemetry = device_info["telemetry"]
            assert "voltage" in telemetry
            assert "current" in telemetry
            assert "power" in telemetry
            assert "aiclk" in telemetry
            assert "asic_temperature" in telemetry
            assert "fan_speed" in telemetry
            assert "heartbeat" in telemetry

    def test_firmwares_fields_present(self, snapshot):
        """Test if fields are present in firmwares."""
        device_infos = snapshot["device_info"]
        for device_info in device_infos:
            firmwares = device_info["firmwares"]
            assert "fw_bundle_version" in firmwares
            assert "tt_flash_version" in firmwares
            assert "cm_fw" in firmwares
            assert "cm_fw_date" in firmwares
            assert "eth_fw" in firmwares
            assert "dm_bl_fw" in firmwares
            assert "dm_app_fw" in firmwares

    def test_limits_fields(self, snapshot):
        """Test if fields are present in limits."""
        device_infos = snapshot["device_info"]
        for device_info in device_infos:
            limits = device_info["limits"]
            assert "vdd_min" in limits
            assert "vdd_max" in limits
            assert "tdp_limit" in limits
            assert "tdc_limit" in limits
            assert "asic_fmax" in limits
            assert "therm_trip_l1_limit" in limits
            assert "thm_limit" in limits
            assert "bus_peak_limit" in limits

    def test_snapshot_no_tty(self):
        """Test if the output from tt-smi -s --snapshot_no_tty can be parsed as json"""
        result = subprocess.run(
            ["tt-smi", "-s", "--snapshot_no_tty"],
            capture_output=True,
            text=True,
        )
        log_json = json.loads(result.stdout)
        assert isinstance(log_json, dict)
