# SPDX-FileCopyrightText: © 2026 Tenstorrent Inc.
# SPDX-License-Identifier: Apache-2.0

import pytest

from typing import List, Dict, Tuple, Union

from pyluwen import pci_scan, PciChip
from tt_umd import PCIDevice, TopologyDiscovery, TTDevice
from tt_smi.tt_smi_backend import TTSMIBackend
from tt_tools_common.utils_common.tools_utils import detect_chips_with_callback

# Luwen fixtures


@pytest.fixture(scope="session")
def luwen_reset_test_config() -> Tuple[List[int], bool]:
    """Return a tuple of a list of PCI indices using Luwen backend, and False, which represents the use_umd param."""
    return (pci_scan(), False)


@pytest.fixture(scope="session")
def luwen_devices() -> Dict[int, PciChip]:
    """Return a list of Tenstorrent PciChips."""
    return dict(enumerate(detect_chips_with_callback()))


@pytest.fixture(scope="session")
def luwen_backend(luwen_devices) -> TTSMIBackend:
    """Return a TTSMIBackend instance created from devices."""
    return TTSMIBackend(luwen_devices)


# UMD fixtures


@pytest.fixture(scope="session")
def umd_reset_test_config() -> Tuple[List[int], bool]:
    """Return a tuple of a list of PCI indices using UMD backend, and True, which represents the use_umd param."""
    return (list(PCIDevice.enumerate_devices_info()), True)


@pytest.fixture(scope="session")
def umd_devices() -> dict[int, TTDevice]:
    """Return a dict of Tenstorrent TTDevices using UMD backend."""
    _, devices = TopologyDiscovery.discover()
    return devices


@pytest.fixture(scope="session")
def umd_backend() -> TTSMIBackend:
    """Return a TTSMIBackend instance using UMD backend."""
    cluster_descriptor, devices = TopologyDiscovery.discover()
    return TTSMIBackend(devices=devices, umd_cluster_descriptor=cluster_descriptor)


# Parametrized fixtures to run tests with both backends


@pytest.fixture(scope="session", params=["luwen", "umd"])
def reset_test_config(
    request, luwen_reset_test_config, umd_reset_test_config
) -> Tuple[List[int], bool]:
    if request.param == "luwen":
        return luwen_reset_test_config
    return umd_reset_test_config


@pytest.fixture(scope="session", params=["luwen", "umd"])
def devices(request, luwen_devices, umd_devices) -> List[Union[PciChip, TTDevice]]:
    if request.param == "luwen":
        return luwen_devices
    return umd_devices


@pytest.fixture(scope="session", params=["luwen", "umd"])
def backend(request, luwen_backend, umd_backend) -> TTSMIBackend:
    if request.param == "luwen":
        return luwen_backend
    return umd_backend
