# SPDX-FileCopyrightText: © 2026 Tenstorrent Inc.
# SPDX-License-Identifier: Apache-2.0

import pytest

from typing import List, Dict

from pyluwen import pci_scan, PciChip
from tt_smi.tt_smi_backend import TTSMIBackend
from tt_tools_common.utils_common.tools_utils import detect_chips_with_callback


@pytest.fixture(scope="session")
def pci_indices() -> List[int]:
    """
    Return a list of PCI indices of Tenstorrent devices.
    """
    # TODO: Test using the UMD function that provides pci indices
    return pci_scan()


@pytest.fixture(scope="session")
def devices() -> Dict[int, PciChip]:
    """Return a list of Tenstorrent PciChips."""
    # TODO: Test using the UMD function to detect chips
    return dict(enumerate(detect_chips_with_callback()))


@pytest.fixture(scope="session")
def backend(devices) -> TTSMIBackend:
    """Return a TTSMIBackend instance created from devices."""
    return TTSMIBackend(devices)
