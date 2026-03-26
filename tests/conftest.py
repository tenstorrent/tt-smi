# SPDX-FileCopyrightText: © 2026 Tenstorrent Inc.
# SPDX-License-Identifier: Apache-2.0

import pytest

from typing import List, Dict, Tuple, Union

from pyluwen import pci_scan, PciChip
from tt_umd import PCIDevice, TopologyDiscovery, TTDevice, TopologyDiscoveryOptions
from tt_smi.tt_smi_backend import TTSMIBackend
from tt_smi.constants import SMBUS_TELEMETRY_OPTIONS
from tt_tools_common.utils_common.tools_utils import detect_chips_with_callback

# Luwen fixtures


@pytest.fixture(scope="function")
def luwen_reset_test_config() -> Tuple[List[int], bool]:
    """Return a tuple of a list of PCI indices using Luwen backend, and False, which represents the use_umd param."""
    return (pci_scan(), False)


@pytest.fixture(scope="function")
def luwen_devices() -> Dict[int, PciChip]:
    """Return fresh Tenstorrent PciChips for each test."""
    chips = dict(enumerate(detect_chips_with_callback()))
    try:
        yield chips
    finally:
        # We need to make sure the chips are deleted after the test is done to avoid memory leaks.
        del chips


@pytest.fixture(scope="function")
def luwen_backend(luwen_devices) -> TTSMIBackend:
    """Return a TTSMIBackend instance created from devices (must match luwen_devices scope)."""
    return TTSMIBackend(luwen_devices)


# UMD fixtures


@pytest.fixture(scope="function")
def umd_reset_test_config() -> Tuple[List[int], bool]:
    """Return a tuple of a list of PCI indices using UMD backend, and True, which represents the use_umd param."""
    return (list(PCIDevice.enumerate_devices_info()), True)


@pytest.fixture(scope="function")
def umd_devices() -> dict[int, TTDevice]:
    """Return fresh Tenstorrent TTDevices for each test."""
    # Ignore eth heartbeat failures for now. Not relevant to tests.
    _, devices = TopologyDiscovery.discover(options=SMBUS_TELEMETRY_OPTIONS)
    try:
        yield devices
    finally:
        del devices


@pytest.fixture(scope="function")
def umd_backend() -> TTSMIBackend:
    """Return a TTSMIBackend instance using UMD backend."""
    cluster_descriptor, devices = TopologyDiscovery.discover(options=SMBUS_TELEMETRY_OPTIONS)
    return TTSMIBackend(devices=devices, umd_cluster_descriptor=cluster_descriptor)


# Parametrized fixtures to run tests with both backends


@pytest.fixture(scope="function", params=["luwen", "umd"])
def reset_test_config(
    request, luwen_reset_test_config, umd_reset_test_config
) -> Tuple[List[int], bool]:
    if request.param == "luwen":
        return luwen_reset_test_config
    return umd_reset_test_config


@pytest.fixture(scope="function", params=["luwen", "umd"])
def devices(request, luwen_devices, umd_devices) -> List[Union[PciChip, TTDevice]]:
    if request.param == "luwen":
        return luwen_devices
    return umd_devices


@pytest.fixture(scope="function", params=["luwen", "umd"])
def backend(request, luwen_backend, umd_backend) -> TTSMIBackend:
    if request.param == "luwen":
        return luwen_backend
    return umd_backend


@pytest.fixture(scope="function", params=["luwen", "umd"])
def reset_test_config_with_backend(
    request,
    luwen_reset_test_config,
    umd_reset_test_config,
    luwen_backend,
    umd_backend,
) -> Tuple[Tuple[List[int], bool], TTSMIBackend]:
    """Same variant as reset_test_config, paired with the matching TTSMIBackend (one param axis)."""
    if request.param == "luwen":
        return luwen_reset_test_config, luwen_backend
    return umd_reset_test_config, umd_backend
