# SPDX-FileCopyrightText: © 2026 Tenstorrent Inc.
# SPDX-License-Identifier: Apache-2.0
"""Unit tests for the branchy parts of tt-smi -glx_reset (no hardware)."""

import os
import subprocess
from unittest.mock import patch

import pytest

from tt_smi import reset

# BH Galaxy: tray 1 = bus 0x0X, tray 2 = 0x4X, tray 3 = 0xcX, tray 4 = 0x8X.
GALAXY_BDFS = [
    f"0000:{base + i:02x}:00.0"
    for base in (0x00, 0x40, 0x80, 0xC0)
    for i in range(1, 9)
]


def add_chip(sysfs, bdf):
    dev = sysfs / "devices" / bdf
    dev.mkdir(parents=True)
    (dev / "vendor").write_text("0x1e52\n")
    (dev / "device").write_text("0xb140\n")
    (dev / "config").write_bytes(b"\x52\x1e\x40\xb1")
    os.symlink(sysfs / "drivers" / "tenstorrent", dev / "driver")


@pytest.fixture
def galaxy(tmp_path, monkeypatch):
    """Fake sysfs with 32 chips and every hardware step stubbed out."""
    (tmp_path / "drivers" / "tenstorrent").mkdir(parents=True)
    for bdf in GALAXY_BDFS:
        add_chip(tmp_path, bdf)
    monkeypatch.setattr(reset, "SYSFS_PCI", str(tmp_path))
    monkeypatch.setattr(reset, "_bmc_problem", lambda: "")
    monkeypatch.setattr(reset, "pci_scan", lambda: [])
    monkeypatch.setattr(reset, "parallel_reset_device_ioctl", lambda ids, flag: [])
    monkeypatch.setattr(reset, "run_galaxy_ipmi_reset", lambda *a: None)
    monkeypatch.setattr(reset, "timed_wait", lambda s: None)
    monkeypatch.setattr(reset.time, "sleep", lambda s: None)
    return tmp_path


def run_reset(**kwargs):
    with pytest.raises(SystemExit) as exc:
        reset.glx_6u_trays_reset(print_status=False, **kwargs)
    return exc.value.code


def test_silent_bmc_resets_nothing(monkeypatch):
    def no_answer(*args, **kwargs):
        raise subprocess.TimeoutExpired("ipmitool", 15)

    monkeypatch.setattr(reset.subprocess, "run", no_answer)
    ioctls = []
    monkeypatch.setattr(
        reset, "parallel_reset_device_ioctl", lambda *a: ioctls.append(a)
    )
    assert run_reset() == 1
    assert ioctls == []


def test_chip_back_after_rescan_exits_0(galaxy, monkeypatch):
    lost = "0000:47:00.0"
    (galaxy / "devices" / lost / "driver").unlink()
    monkeypatch.setattr(
        reset,
        "_pci_rescan",
        lambda: (
            (galaxy / "devices" / lost / "driver").symlink_to(
                galaxy / "drivers" / "tenstorrent"
            )
        ),
    )
    assert run_reset(reinit=False) == 0


def test_chip_still_missing_is_named_by_bdf_and_tray(galaxy, monkeypatch, capsys):
    (galaxy / "devices" / "0000:47:00.0" / "config").write_bytes(b"\xff\xff\xff\xff")
    monkeypatch.setattr(reset, "GLX_RECOUNT_TIMEOUT_S", 0)
    monkeypatch.setattr(reset, "_pci_rescan", lambda: None)
    assert run_reset(reinit=False) == reset.GLX_EXIT_CHIPS_MISSING
    out = capsys.readouterr().out
    assert "0000:47:00.0 (tray 2)" in out
    assert "31/32" in out


def test_stuck_heartbeat_is_named(galaxy, monkeypatch, capsys):
    stuck = "0000:c3:00.0"
    beats = {}

    def read_heartbeat(bdf, use_umd):
        beats[bdf] = beats.get(bdf, 0) + (bdf != stuck)
        return beats[bdf]

    monkeypatch.setattr(reset, "_read_heartbeat", read_heartbeat)
    monkeypatch.setattr(
        reset,
        "_reinit_and_check_heartbeat",
        lambda use_umd, print_status: reset._alive_chips(
            {b: b for b in GALAXY_BDFS}, use_umd
        ),
    )
    assert run_reset() == reset.GLX_EXIT_CHIPS_MISSING
    assert f"{stuck} (tray 3)" in capsys.readouterr().out


def test_ioctl_that_raises_does_not_stop_the_others():
    def ioctl(interface_id, flag):
        if interface_id == 3:
            raise OSError("No such device")
        return True

    with patch("tt_smi.reset.reset_device_ioctl", side_effect=ioctl):
        assert reset.parallel_reset_device_ioctl([0, 1, 2, 3, 4], 0) == [3]
