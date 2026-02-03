# SPDX-FileCopyrightText: © 2025 Tenstorrent Inc.
# SPDX-License-Identifier: Apache-2.0

import pytest

from pyluwen import pci_scan
from tt_smi.tt_smi_backend import pci_board_reset, glx_6u_trays_reset

NUM_RESETS_STRESS_TEST = 10


@pytest.mark.requires_hardware
class TestPciDriverReset:
    def test_pci_reset_all_devices(self, pci_indices):
        """
        Test resetting all PCI devices (invoked by tt-smi -r).

        Passes if the reset is successful and the same number of devices are
        detected before and after.
        """
        pci_board_reset(pci_indices, reinit=True)
        post_reset_devices = pci_scan()
        assert len(post_reset_devices) == len(pci_indices)


    def test_pci_reset_all_devices_stress(self, pci_indices):
        """
        Test resetting all PCI devices NUM_RESETS_STRESS_TEST times in a row.

        Passes if the resets are successful and the same number of devices are
        detected before and after each reset.
        """
        for _ in range(NUM_RESETS_STRESS_TEST):
            pci_board_reset(pci_indices, reinit=True)
            post_reset_devices = pci_scan()
            assert len(post_reset_devices) == len(pci_indices)


@pytest.mark.requires_hardware
@pytest.mark.requires_galaxy
class TestGalaxyReset:
    def test_glx_reset(pci_indices):
        """
        Test galaxy 6U trays reset (invoked by tt-smi -glx_reset).

        Passes if the reset is successful and the same number of devices are
        detected before and after.
        """
        # Expect SystemExit with return code 0 on successful reset
        with pytest.raises(SystemExit) as exc_info:
            glx_6u_trays_reset()

        assert exc_info.value.code == 0

        post_reset_devices = pci_scan()
        assert len(post_reset_devices) == len(pci_indices)


    def test_glx_reset_stress(pci_indices):
        """
        Test galaxy 6U trays reset NUM_RESETS_STRESS_TEST times in a row.

        Passes if the resets are successful and the same number of devices are
        detected before and after each reset.
        """
        for _ in range(NUM_RESETS_STRESS_TEST):
            # Expect SystemExit with return code 0 on successful reset
            with pytest.raises(SystemExit) as exc_info:
                glx_6u_trays_reset()

            assert exc_info.value.code == 0

            post_reset_devices = pci_scan()
            assert len(post_reset_devices) == len(pci_indices)
