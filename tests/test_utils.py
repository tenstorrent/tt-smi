# SPDX-FileCopyrightText: © 2026 Tenstorrent Inc.
# SPDX-License-Identifier: Apache-2.0

import pytest

from tt_smi.utils import (
    get_board_type,
    convert_signed_16_16_to_float,
    hex_to_semver_gddr_fw,
    is_driver_version_at_least,
    p100_dram_training_passed,
    bh_dram_training_passed,
    decode_gddr_pair_temps,
    decode_gddr_pair_corr_errs,
    decode_gddr_uncorr_errs,
    decode_bh_gddr_channel_status,
    decode_wh_gddr_channel_training,
    build_gddr_telemetry,
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
            (0x202, "tt-galaxy-bh"),
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


class TestGddrTelemetryDecode:
    """Packed BH GDDR telemetry tags (temps, EDC errors, DDR_STATUS)."""

    def test_decode_gddr_pair_temps(self):
        # hello.log device 0 GDDR_0_1_TEMP = 0x1e1e2c2e
        (x_bottom, x_top), (y_bottom, y_top) = decode_gddr_pair_temps(0x1E1E2C2E)
        assert (x_bottom, x_top) == (0x2E, 0x2C)
        assert (y_bottom, y_top) == (0x1E, 0x1E)

    def test_decode_gddr_pair_corr_errs(self):
        packed = (7 << 24) | (6 << 16) | (5 << 8) | 4
        (x_rd, x_wr), (y_rd, y_wr) = decode_gddr_pair_corr_errs(packed)
        assert (x_rd, x_wr) == (4, 5)
        assert (y_rd, y_wr) == (6, 7)

    def test_decode_gddr_uncorr_errs(self):
        # Channel 3 write + channel 0 read
        packed = (1 << 0) | (1 << 7)
        assert decode_gddr_uncorr_errs(packed, 0) == (1, 0)
        assert decode_gddr_uncorr_errs(packed, 3) == (0, 1)
        assert decode_gddr_uncorr_errs(packed, 1) == (0, 0)

    def test_decode_bh_gddr_channel_status_all_pass(self):
        training, bist = decode_bh_gddr_channel_status(0x55555555, 0, has_bist=True)
        assert training == "pass"
        assert bist == "pass"

    def test_decode_bh_gddr_channel_status_fail(self):
        # Channel 1 training error (bit 3) and BIST failed (bit 19)
        status = (1 << 3) | (1 << 19)
        training, bist = decode_bh_gddr_channel_status(status, 1, has_bist=True)
        assert training == "fail"
        assert bist == "fail"

    def test_decode_bh_gddr_channel_status_no_bist(self):
        training, bist = decode_bh_gddr_channel_status(0x5555, 0, has_bist=False)
        assert training == "pass"
        assert bist == "n/a"

    def test_decode_wh_gddr_channel_training(self):
        # 6 channels all pass: 0x222222
        assert decode_wh_gddr_channel_training(0x222222, 0) == "pass"
        assert decode_wh_gddr_channel_training(0x000001, 0) == "fail"
        assert decode_wh_gddr_channel_training(0x0, 0) == "n/a"

    def test_build_gddr_telemetry_blackhole(self):
        smbus = {
            "DDR_STATUS": "0x55555555",
            "DDR_SPEED": "0x3e80",
            "ENABLED_GDDR": "0xff",
            "GDDR_0_1_TEMP": "0x1e1e2c2e",
            "GDDR_2_3_TEMP": "0x2c2a2c2e",
            "GDDR_4_5_TEMP": "0x2a2a2a2a",
            "GDDR_6_7_TEMP": "0x2a2a1e1c",
            "GDDR_0_1_CORR_ERRS": "0x0",
            "GDDR_2_3_CORR_ERRS": "0x0",
            "GDDR_4_5_CORR_ERRS": "0x0",
            "GDDR_6_7_CORR_ERRS": "0x0",
            "GDDR_UNCORR_ERRS": "0x0",
            "MAX_GDDR_TEMP": "0x2e",
        }
        gddr = build_gddr_telemetry(
            smbus,
            dram_speed="16G",
            is_blackhole=True,
            is_wormhole=False,
            has_bist=True,
        )
        assert gddr["speed"] == "16G"
        assert gddr["max_temp"] == "46"
        assert gddr["enabled_mask"] == "0xff"
        assert len(gddr["channels"]) == 8
        ch0 = gddr["channels"][0]
        assert ch0["channel"] == 0
        assert ch0["enabled"] is True
        assert ch0["training"] == "pass"
        assert ch0["bist"] == "pass"
        assert ch0["temp_bottom"] == "46"
        assert ch0["temp_top"] == "44"
        assert ch0["corr_rd"] == "0"
        assert ch0["uncorr_wr"] == "0"

    def test_build_gddr_telemetry_harvested_channel(self):
        smbus = {
            "DDR_STATUS": "0x55555555",
            "ENABLED_GDDR": "0xfe",  # channel 0 harvested
            "GDDR_0_1_TEMP": "0x1e1e2c2e",
            "GDDR_2_3_TEMP": "0x0",
            "GDDR_4_5_TEMP": "0x0",
            "GDDR_6_7_TEMP": "0x0",
            "GDDR_0_1_CORR_ERRS": "0x0",
            "GDDR_2_3_CORR_ERRS": "0x0",
            "GDDR_4_5_CORR_ERRS": "0x0",
            "GDDR_6_7_CORR_ERRS": "0x0",
            "GDDR_UNCORR_ERRS": "0x0",
            "MAX_GDDR_TEMP": "0x2e",
        }
        gddr = build_gddr_telemetry(
            smbus,
            dram_speed="16G",
            is_blackhole=True,
            is_wormhole=False,
            has_bist=True,
        )
        ch0 = gddr["channels"][0]
        assert ch0["harvested"] is True
        assert ch0["enabled"] is False
        assert ch0["temp_top"] == "N/A"
        assert gddr["channels"][1]["enabled"] is True
        assert gddr["channels"][1]["temp_top"] == "30"

    def test_build_gddr_telemetry_harvested_via_ddr_status(self):
        """Harvested slot is 0b00 in DDR_STATUS even when ENABLED_GDDR is 0xff."""
        smbus = {
            "DDR_STATUS": hex(_p100_dram_status(harvested=2)),
            "ENABLED_GDDR": "0xff",
            "GDDR_0_1_TEMP": "0x1e1e2c2e",
            "GDDR_2_3_TEMP": "0x2c2a2c2e",
            "GDDR_4_5_TEMP": "0x0",
            "GDDR_6_7_TEMP": "0x0",
            "GDDR_0_1_CORR_ERRS": "0x0",
            "GDDR_2_3_CORR_ERRS": "0x0",
            "GDDR_4_5_CORR_ERRS": "0x0",
            "GDDR_6_7_CORR_ERRS": "0x0",
            "GDDR_UNCORR_ERRS": "0x0",
            "MAX_GDDR_TEMP": "0x2e",
        }
        gddr = build_gddr_telemetry(
            smbus,
            dram_speed="16G",
            is_blackhole=True,
            is_wormhole=False,
            has_bist=True,
        )
        ch2 = gddr["channels"][2]
        assert ch2["harvested"] is True
        assert ch2["enabled"] is False
        assert ch2["training"] == "n/a"
        assert ch2["bist"] == "n/a"
        assert ch2["temp_top"] == "N/A"
        assert gddr["channels"][0]["enabled"] is True
        assert gddr["channels"][0]["training"] == "pass"

    def test_build_gddr_telemetry_wormhole(self):
        smbus = {"DDR_STATUS": "0x02222222"}
        gddr = build_gddr_telemetry(
            smbus,
            dram_speed="16G",
            is_blackhole=False,
            is_wormhole=True,
            has_bist=False,
        )
        assert gddr["max_temp"] == "N/A"
        assert len(gddr["channels"]) == 6
        assert all(ch["training"] == "pass" for ch in gddr["channels"])
        assert all(ch["temp_top"] == "N/A" for ch in gddr["channels"])


def _p100_dram_status(*, harvested: int = None, fail_training: int = None, fail_bist: int = None) -> int:
    """Build DDR_STATUS for P100 tests: all channels pass except optional overrides."""
    status = 0x55555555
    if harvested is not None:
        status &= ~((1 << (2 * harvested)) | (1 << (16 + 2 * harvested)))
    if fail_training is not None:
        status |= 1 << (2 * fail_training + 1)
        status &= ~(1 << (2 * fail_training))
    if fail_bist is not None:
        status |= 1 << (17 + 2 * fail_bist)
        status &= ~(1 << (16 + 2 * fail_bist))
    return status


class TestBhDramTrainingPassed:
    @pytest.mark.parametrize(
        "dram_status,expected",
        [
            # All 8 channels trained + BIST
            (0x55555555, True),
            # 7 active channels + GDDR 2 harvested (real P100 example)
            (0x55455545, True),
            # Harvested slot can be any of the 8 channels
            (_p100_dram_status(harvested=0), True),
            (_p100_dram_status(harvested=7), True),
            # Two harvested channels
            (0x54455545, False),
            (_p100_dram_status(harvested=0, fail_training=1), False),
            # Training error on an active channel
            (_p100_dram_status(harvested=2, fail_training=0), False),
            # BIST failure on an active channel
            (_p100_dram_status(harvested=2, fail_bist=5), False),
            # Partial pass: training complete but BIST never ran
            (0x55455555, False),
            # No harvested channel (incomplete training on one slot)
            (0x55455540, False),
        ],
    )
    def test_bh_dram_training_passed(self, dram_status, expected):
        assert bh_dram_training_passed(dram_status) is expected
        # Back-compat alias used by older P100-only call sites.
        assert p100_dram_training_passed(dram_status) is expected
