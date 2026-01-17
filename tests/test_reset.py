# SPDX-FileCopyrightText: © 2025 Tenstorrent Inc.
# SPDX-License-Identifier: Apache-2.0

import pytest
from typing import List

from pyluwen import pci_scan
from tt_smi.tt_smi_backend import pci_board_reset, glx_6u_trays_reset, get_board_type
from tt_tools_common.utils_common.tools_utils import detect_chips_with_callback

NUM_RESETS_STRESS_TEST = 10

@pytest.fixture(scope="session")
def devices() -> List[int]:
    """
    Return a list of PCI indices of Tenstorrent devices.
    """
    return pci_scan()


@pytest.fixture(scope="session")
def is_galaxy_6u() -> bool:
    """
    Return True if the system is a Galaxy 6U (32 devices, all Galaxy board type).
    """
    devices = detect_chips_with_callback()
    if len(devices) != 32:
        return False

    board_types = {get_board_type(f"{device.board_id():x}") for device in devices}
    return board_types <= {"tt-galaxy-wh", "tt-galaxy-bh"}


def test_pci_reset_all_devices(devices, is_galaxy_6u):
    """
    Test resetting all PCI devices (invoked by tt-smi -r).

    Passes if the reset is successful and the same number of devices are
    detected before and after.
    """
    if is_galaxy_6u:
        pytest.skip("Skipping PCI reset test on Galaxy")

    pci_board_reset(devices, reinit=True)
    post_reset_devices = pci_scan()
    assert len(post_reset_devices) == len(devices)


def test_pci_reset_all_devices_stress(devices, is_galaxy_6u):
    """
    Test resetting all PCI devices NUM_RESETS_STRESS_TEST times in a row.

    Passes if the resets are successful and the same number of devices are
    detected before and after each reset.
    """
    if is_galaxy_6u:
        pytest.skip("Skipping PCI reset stress test on Galaxy")

    for _ in range(NUM_RESETS_STRESS_TEST):
        pci_board_reset(devices, reinit=True)
        post_reset_devices = pci_scan()
        assert len(post_reset_devices) == len(devices)


def test_glx_reset(devices, is_galaxy_6u):
    """
    Test galaxy 6U trays reset (invoked by tt-smi -glx_reset).

    Passes if the reset is successful and the same number of devices are
    detected before and after.
    """
    if not is_galaxy_6u:
        pytest.skip("Skipping Galaxy reset test on non-Galaxy system")

    # Expect SystemExit with return code 0 on successful reset
    with pytest.raises(SystemExit) as exc_info:
        glx_6u_trays_reset()

    assert exc_info.value.code == 0

    post_reset_devices = pci_scan()
    assert len(post_reset_devices) == len(devices)


def test_glx_reset_stress(devices, is_galaxy_6u):
    """
    Test galaxy 6U trays reset NUM_RESETS_STRESS_TEST times in a row.

    Passes if the resets are successful and the same number of devices are
    detected before and after each reset.
    """
    if not is_galaxy_6u:
        pytest.skip("Skipping Galaxy reset test on non-Galaxy system")

    for _ in range(NUM_RESETS_STRESS_TEST):
        # Expect SystemExit with return code 0 on successful reset
        with pytest.raises(SystemExit) as exc_info:
            glx_6u_trays_reset()

        assert exc_info.value.code == 0

        post_reset_devices = pci_scan()
        assert len(post_reset_devices) == len(devices)
