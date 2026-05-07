# SPDX-FileCopyrightText: © 2026 Tenstorrent Inc.
# SPDX-License-Identifier: Apache-2.0

import pytest

from tt_smi.tt_smi_utils import (
    get_board_type,
    convert_signed_16_16_to_float,
    check_blackhole_dram_training_status,
)

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


class TestBlackholeDramTrainingStatus:
    """
    Tests for check_blackhole_dram_training_status.

    DDR_STATUS packs 2 bits per GDDR channel for training status (bits 0..15)
    and, on FW >= 19.7.0.3, another 2 bits per channel for BIST status
    (bits 16..31): bit 2i = complete, bit 2i + 1 = error/failed.

    A fully-trained 8-channel Blackhole reports DDR_STATUS == 0x55555555
    (modern) or 0x5555 (legacy). p100a ships with channel 3 harvested
    (ENABLED_GDDR = 0xF7) and the channel 3 bits read back zero.
    """

    # --- FW >= 19.7.0.3 (32-bit DDR_STATUS with BIST) ---

    def test_full_8ch_pass(self):
        assert check_blackhole_dram_training_status(0x55555555, 0xFF, has_bist=True)

    def test_p100a_7ch_pass(self):
        # Real observed value on a healthy p100a (channel 3 harvested).
        assert check_blackhole_dram_training_status(0x55155515, 0xF7, has_bist=True)

    def test_enabled_channel_missing_train_complete(self):
        # Channel 2 (bits 4-5) cleared.
        assert not check_blackhole_dram_training_status(
            0x55155505, 0xF7, has_bist=True
        )

    def test_enabled_channel_train_error_set(self):
        # Channel 0 error bit (bit 1) set.
        assert not check_blackhole_dram_training_status(
            0x55155517, 0xF7, has_bist=True
        )

    def test_enabled_channel_bist_failed(self):
        # Channel 0 BIST-failed bit (bit 17) set.
        assert not check_blackhole_dram_training_status(
            0x55175515, 0xF7, has_bist=True
        )

    def test_disabled_channel_bits_ignored(self):
        # Channel 3 bits deliberately dirty but channel 3 is disabled -> still pass.
        assert check_blackhole_dram_training_status(0x55D555D5, 0xF7, has_bist=True)

    # --- FW < 19.7.0.3 (16-bit DDR_STATUS, no BIST bits) ---

    def test_legacy_full_8ch_pass(self):
        assert check_blackhole_dram_training_status(0x5555, 0xFF, has_bist=False)

    def test_legacy_p100a_7ch_pass(self):
        assert check_blackhole_dram_training_status(0x5515, 0xF7, has_bist=False)

    def test_legacy_ignores_upper_bits(self):
        # Upper bits are undefined on legacy FW and must not affect the decision.
        assert check_blackhole_dram_training_status(0xFFFF5555, 0xFF, has_bist=False)

    def test_legacy_error_bit_set(self):
        # Channel 0 error bit set.
        assert not check_blackhole_dram_training_status(0x5557, 0xFF, has_bist=False)
