# SPDX-FileCopyrightText: © 2025 Tenstorrent Inc.
# SPDX-License-Identifier: Apache-2.0

import pytest

from typing import List, Tuple

from pyluwen import pci_scan
from tt_umd import PCIDevice
from tt_smi.backend import TTSMIBackend
from tt_smi.utils import get_dev_id_from_bdf
from tt_smi.reset import pci_board_reset
from tt_smi.device_input import (
    classify_single_input,
    parse_smi_device_input,
    SmiDeviceInput,
    SmiDeviceTargetKind,
)

NUM_RESETS_STRESS_TEST = 10


class TestParseResetInput:
    """Unit tests for device-selection parsing used by ``tt-smi -r`` (no hardware)."""

    # --- classify_single_input: UMD logical IDs (ints as strings) ---

    def test_classify_umd_logical_id_single(self):
        assert classify_single_input("0") == (SmiDeviceTargetKind.UMD_LOGICAL_ID, 0)
        assert classify_single_input(" 42 ") == (SmiDeviceTargetKind.UMD_LOGICAL_ID, 42)
        assert classify_single_input("-1") == (SmiDeviceTargetKind.UMD_LOGICAL_ID, -1)

    # --- PCI BDF ---

    def test_classify_pci_bdf(self):
        assert classify_single_input("0000:0a:00.0") == (
            SmiDeviceTargetKind.PCI_BDF,
            "0000:0a:00.0",
        )
        assert classify_single_input("ABCD:EF:01.2") == (
            SmiDeviceTargetKind.PCI_BDF,
            "ABCD:EF:01.2",
        )

    def test_classify_bdf_rejects_short_domain(self):
        with pytest.raises(ValueError, match="Invalid device target"):
            classify_single_input("0a:00.0")

    # --- /dev/tenstorrent/<id> ---

    def test_classify_dev_tenstorrent_path(self):
        assert classify_single_input("/dev/tenstorrent/0") == (
            SmiDeviceTargetKind.DEV_TENSTORRENT_ID,
            0,
        )
        assert classify_single_input("/dev/tenstorrent/12") == (
            SmiDeviceTargetKind.DEV_TENSTORRENT_ID,
            12,
        )

    def test_classify_dev_path_rejects_non_numeric_suffix(self):
        with pytest.raises(ValueError, match="Invalid path"):
            classify_single_input("/dev/tenstorrent/abc")

    def test_classify_dev_path_case_sensitive_prefix(self):
        """Only lowercase /dev/tenstorrent/ is accepted (not a filesystem path check)."""
        with pytest.raises(ValueError, match="Invalid device target"):
            classify_single_input("/dev/Tenstorrent/0")

    # --- invalid / special inputs ---

    def test_classify_empty_input(self):
        with pytest.raises(ValueError, match="Empty input"):
            classify_single_input("")

    def test_classify_all_is_reserved(self):
        with pytest.raises(ValueError, match="only argument"):
            classify_single_input("all")

    def test_classify_garbage(self):
        with pytest.raises(ValueError, match="Invalid device target"):
            classify_single_input("not-a-target")

    # --- parse_smi_device_input: ALL ---

    def test_parse_all_empty_or_none(self):
        assert parse_smi_device_input(None) == SmiDeviceInput(
            type=SmiDeviceTargetKind.ALL, value=None
        )
        assert parse_smi_device_input([]) == SmiDeviceInput(
            type=SmiDeviceTargetKind.ALL, value=None
        )

    def test_parse_all_explicit(self):
        assert parse_smi_device_input(["all"]) == SmiDeviceInput(
            type=SmiDeviceTargetKind.ALL, value=None
        )
        assert parse_smi_device_input([" ALL "]) == SmiDeviceInput(
            type=SmiDeviceTargetKind.ALL, value=None
        )

    # --- parse_smi_device_input: homogeneous types ---

    def test_parse_umd_logical_ids_sorted_deduped(self):
        r = parse_smi_device_input(["2", "0", "1", "0"])
        assert r.type == SmiDeviceTargetKind.UMD_LOGICAL_ID
        assert r.value == [0, 1, 2]

    def test_parse_umd_comma_separated_in_one_arg(self):
        r = parse_smi_device_input(["0, 1 ,2"])
        assert r.type == SmiDeviceTargetKind.UMD_LOGICAL_ID
        assert r.value == [0, 1, 2]

    def test_parse_pci_bdf_list(self):
        r = parse_smi_device_input(["0000:0a:00.0", "0000:0b:00.0"])
        assert r.type == SmiDeviceTargetKind.PCI_BDF
        assert r.value == ["0000:0a:00.0", "0000:0b:00.0"]

    def test_parse_pci_bdf_comma_separated(self):
        r = parse_smi_device_input(["0000:0a:00.0,0000:0b:00.0"])
        assert r.type == SmiDeviceTargetKind.PCI_BDF
        assert r.value == ["0000:0a:00.0", "0000:0b:00.0"]

    def test_parse_dev_tenstorrent_ids_sorted(self):
        r = parse_smi_device_input(["/dev/tenstorrent/2", "/dev/tenstorrent/0"])
        assert r.type == SmiDeviceTargetKind.DEV_TENSTORRENT_ID
        assert r.value == [0, 2]

    def test_parse_dev_tenstorrent_comma_separated(self):
        r = parse_smi_device_input(["/dev/tenstorrent/1,/dev/tenstorrent/0"])
        assert r.type == SmiDeviceTargetKind.DEV_TENSTORRENT_ID
        assert r.value == [0, 1]

    # --- parse_smi_device_input: mixed kinds -> exit 1 ---

    def test_parse_mixed_int_and_bdf_exits(self):
        with pytest.raises(SystemExit) as exc:
            parse_smi_device_input(["0", "0000:0a:00.0"])
        assert exc.value.code == 1

    def test_parse_mixed_int_and_dev_path_exits(self):
        with pytest.raises(SystemExit) as exc:
            parse_smi_device_input(["0", "/dev/tenstorrent/0"])
        assert exc.value.code == 1

    def test_parse_mixed_bdf_and_dev_path_exits(self):
        with pytest.raises(SystemExit) as exc:
            parse_smi_device_input(["0000:0a:00.0", "/dev/tenstorrent/0"])
        assert exc.value.code == 1

    def test_parse_invalid_input_exits(self):
        with pytest.raises(SystemExit) as exc:
            parse_smi_device_input(["bogus"])
        assert exc.value.code == 1


def redetect_devices(use_umd: bool) -> List[int]:
    if use_umd:
        return list(PCIDevice.enumerate_devices_info())
    return pci_scan()


@pytest.mark.requires_hardware
class TestPciDriverReset:
    def test_pci_reset_all_devices(self, reset_test_config):
        """
        Test resetting all PCI devices (invoked by tt-smi -r).

        Passes if the reset is successful and the same number of devices are
        detected before and after.
        """
        pci_indices, use_umd = reset_test_config
        # Match `tt-smi -r` / `tt-smi -r all`: reset all devices (SmiDeviceInput), not a raw index list.
        pci_board_reset(
            SmiDeviceInput(type=SmiDeviceTargetKind.ALL, value=None),
            reinit=True,
            use_umd=use_umd,
        )
        post_reset_devices = redetect_devices(use_umd)
        assert len(post_reset_devices) == len(pci_indices)

    def test_pci_reset_umd_id(self, reset_test_config):
        """
        Reset UMD logical device 0 (tt-smi -r 0).

        Only runs under the UMD parametrization; skips if logical id 0 is absent.
        Passes if device count is unchanged after reset.
        """
        pci_indices, use_umd = reset_test_config
        if not use_umd:
            pytest.skip("UMD logical ID reset is only supported with UMD backend")
        if 0 not in pci_indices:
            pytest.skip("No device with UMD logical ID 0")

        pci_board_reset(
            SmiDeviceInput(type=SmiDeviceTargetKind.UMD_LOGICAL_ID, value=[0]),
            reinit=True,
            use_umd=True,
        )
        post_reset_devices = redetect_devices(use_umd)
        assert len(post_reset_devices) == len(pci_indices)

    def test_pci_reset_dev_id(
        self, reset_test_config_with_backend: Tuple[Tuple[List[int], bool], TTSMIBackend]
    ):
        """
        Reset the first enumerated device by /dev/tenstorrent/<N> (tt-smi -r /dev/tenstorrent/N).

        Resolves N from sysfs for board index 0; runs for both Luwen and UMD.
        """
        (pci_indices, use_umd), backend = reset_test_config_with_backend
        if not pci_indices:
            pytest.skip("No Tenstorrent devices")
        bdf = backend.get_pci_bdf(0)
        if bdf == "N/A":
            pytest.skip("No local PCI BDF for first device (remote)")
        dev_id = get_dev_id_from_bdf(bdf)
        pci_board_reset(
            SmiDeviceInput(type=SmiDeviceTargetKind.DEV_TENSTORRENT_ID, value=[dev_id]),
            reinit=True,
            use_umd=use_umd,
        )
        post_reset_devices = redetect_devices(use_umd)
        assert len(post_reset_devices) == len(pci_indices)

    def test_pci_reset_pci_bdf(
        self, reset_test_config_with_backend: Tuple[Tuple[List[int], bool], TTSMIBackend]
    ):
        """
        Reset the first enumerated device by /dev/tenstorrent/<N> (tt-smi -r /dev/tenstorrent/N).

        Resolves N from sysfs for board index 0; runs for both Luwen and UMD.
        """
        (pci_indices, use_umd), backend = reset_test_config_with_backend
        if not pci_indices:
            pytest.skip("No Tenstorrent devices")
        bdf = backend.get_pci_bdf(0)
        if bdf == "N/A":
            pytest.skip("No local PCI BDF for first device (remote)")
        pci_board_reset(
            SmiDeviceInput(type=SmiDeviceTargetKind.PCI_BDF, value=[bdf]),
            reinit=True,
            use_umd=use_umd,
        )
        post_reset_devices = redetect_devices(use_umd)
        assert len(post_reset_devices) == len(pci_indices)

    def test_pci_reset_all_devices_stress(self, reset_test_config):
        """
        Test resetting all PCI devices NUM_RESETS_STRESS_TEST times in a row.

        Passes if the resets are successful and the same number of devices are
        detected before and after each reset.
        """
        pci_indices, use_umd = reset_test_config
        for _ in range(NUM_RESETS_STRESS_TEST):
            pci_board_reset(
                SmiDeviceInput(type=SmiDeviceTargetKind.ALL, value=None),
                reinit=True,
                use_umd=use_umd,
            )
            post_reset_devices = redetect_devices(use_umd)
            assert len(post_reset_devices) == len(pci_indices)
