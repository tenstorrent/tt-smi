# SPDX-FileCopyrightText: © 2025 Tenstorrent Inc.
# SPDX-License-Identifier: Apache-2.0

import pytest

from typing import List

from pyluwen import pci_scan
from tt_umd import PCIDevice
from tt_smi.tt_smi_backend import pci_board_reset, glx_6u_trays_reset

NUM_RESETS_STRESS_TEST = 10


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
        pci_board_reset(pci_indices, reinit=True, use_umd=use_umd)
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
            pci_board_reset(pci_indices, reinit=True, use_umd=use_umd)
            post_reset_devices = redetect_devices(use_umd)
            assert len(post_reset_devices) == len(pci_indices)


@pytest.mark.requires_hardware
@pytest.mark.requires_galaxy
class TestGalaxyReset:
    def test_glx_reset(self, reset_test_config):
        """
        Test galaxy 6U trays reset (invoked by tt-smi -glx_reset).

        Passes if the reset is successful and the same number of devices are
        detected before and after.
        """
        pci_indices, use_umd = reset_test_config
        # Expect SystemExit with return code 0 on successful reset
        with pytest.raises(SystemExit) as exc_info:
            glx_6u_trays_reset(use_umd=use_umd)

        assert exc_info.value.code == 0

        post_reset_devices = redetect_devices(use_umd)
        assert len(post_reset_devices) == len(pci_indices)

    def test_glx_reset_stress(self, reset_test_config):
        """
        Test galaxy 6U trays reset NUM_RESETS_STRESS_TEST times in a row.

        Passes if the resets are successful and the same number of devices are
        detected before and after each reset.
        """
        pci_indices, use_umd = reset_test_config
        for _ in range(NUM_RESETS_STRESS_TEST):
            # Expect SystemExit with return code 0 on successful reset
            with pytest.raises(SystemExit) as exc_info:
                glx_6u_trays_reset(use_umd=use_umd)

            assert exc_info.value.code == 0

            post_reset_devices = redetect_devices(use_umd)
            assert len(post_reset_devices) == len(pci_indices)
