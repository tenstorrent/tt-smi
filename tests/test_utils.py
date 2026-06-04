# SPDX-FileCopyrightText: © 2026 Tenstorrent Inc.
# SPDX-License-Identifier: Apache-2.0

import pytest

from tt_smi.utils import (
    get_board_type,
    convert_signed_16_16_to_float,
    get_size,
    get_host_info,
    get_host_compatibility_info,
    init_logging,
    hex_to_semver_gddr_fw,
    is_driver_version_at_least,
)

class TestDriverVersion:
    def test_is_driver_version_at_least(self):
        assert is_driver_version_at_least("1.34.0", "1.34.0")
        assert is_driver_version_at_least("2.0.0", "1.34.0")
        assert not is_driver_version_at_least("1.34.0", "1.35.0")
        assert is_driver_version_at_least("2.7.1-pre", "2.7.0")

    def test_is_driver_version_at_least_no_driver(self):
        with pytest.raises(ValueError, match="No Tenstorrent driver"):
            is_driver_version_at_least(None, "2.0.0")


class TestGetSize:
    def test_get_size_bytes(self):
        assert get_size(500) == "500.00 B"
        assert get_size(1253656) == "1.20 MB"

    def test_get_size_zero(self):
        assert get_size(0) == "0.00 B"


class TestGetHostInfo:
    def test_get_host_info_keys(self):
        info = get_host_info()
        assert set(info.keys()) == {
            "OS",
            "Distro",
            "Kernel",
            "Hostname",
            "Platform",
            "Python",
            "Memory",
            "Driver",
        }
        assert info["Driver"].startswith("TT-KMD ")


class TestGetHostCompatibilityInfo:
    def test_get_host_compatibility_info_keys(self):
        info = get_host_compatibility_info()
        assert set(info.keys()) == {
            "OS",
            "Distro",
            "Kernel",
            "Hostname",
            "Python",
            "Memory",
            "Driver",
        }


class TestInitLogging:
    def test_init_logging_creates_folder(self, tmp_path):
        log_dir = tmp_path / "tt_smi_logs"
        init_logging(str(log_dir))
        assert log_dir.is_dir()


class TestGetBoardType:
    @pytest.mark.parametrize(
        "upi,expected",
        [
            # Grayskull
            (0x3, "e150"),
            (0xA, "e300"),
            (0x7, "e75"),
            # Wormhole
            (0x8, "nb_cb"),
            (0xB, "wh_4u"),
            (0x14, "n300"),
            (0x18, "n150"),
            (0x35, "tt-galaxy-wh"),
            # Blackhole
            (0x36, "bh-scrappy"),
            (0x43, "p100a"),
            (0x40, "p150a"),
            (0x41, "p150b"),
            (0x42, "p150c"),
            (0x44, "p300b"),
            (0x45, "p300a"),
            (0x46, "p300c"),
            (0x47, "tt-galaxy-bh"),
        ],
    )
    def test_all_known_board_types(self, upi, expected):
        """Parametrized test for all known board types."""
        serial_num = upi << 36
        board_id = f"{serial_num:016x}"
        assert get_board_type(board_id) == expected

    @pytest.mark.requires_hardware
    def test_get_board_id_real_device(self, devices):
        """Test get_board_id with a real example."""
        for dev in devices.values():
            result = get_board_type(f"{dev.board_id():x}")
            # This is a real board ID, so just verify it returns a valid type
            assert result in [
                "e150",
                "e300",
                "e75",
                "nb_cb",
                "wh_4u",
                "n300",
                "n150",
                "tt-galaxy-wh",
                "bh-scrappy",
                "p100a",
                "p150a",
                "p150b",
                "p150c",
                "p300a",
                "p300b",
                "p300c",
                "tt-galaxy-bh",
            ]


class TestDataFormatting:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            (0x00000000, 0.0),  # Zero
            (0x003C0000, 60.0),  # A typical ASIC temperature
            (0xFFF60000, -10.0),  # Negative value
            (0x7FFFFFFF, 32768.0),  # Max 16.16 positive value
            (0x80000000, -32768.0),  # Min 16.16 negative value
        ],
    )
    def test_convert_signed_16_16_to_float(self, raw, expected):
        """Test converting signed 16.16 fixed-point number to float."""
        assert convert_signed_16_16_to_float(raw) == pytest.approx(expected)


class TestHexToSemverGddrFw:
    """BH GDDR/MRISC FW: major = high 16 bits, minor = low 16 bits; display major.minor."""

    @pytest.mark.parametrize(
        "raw,expected",
        [
            (0x2000F, "2.15"),
            (0x0002000F, "2.15"),
            (0x0, "N/A"),
            (0xFFFFFFFF, "N/A"),
        ],
    )
    def test_hex_to_semver_gddr_fw(self, raw, expected):
        assert hex_to_semver_gddr_fw(raw) == expected
