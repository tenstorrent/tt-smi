# SPDX-FileCopyrightText: © 2026 Tenstorrent Inc.
# SPDX-License-Identifier: Apache-2.0

"""
Experimental tt-smi backend using ttdal (Tenstorrent Device Access Library).

ttdal is not yet on PyPI.  Install it with: uv sync --extra ttdal

Select this backend at runtime with:
    tt-smi --use-dal
"""

import os
import re
from typing import Dict, Optional

from rich.table import Table
from rich import get_console

import ttdal
import ttdal.telem

from tt_smi import constants
from tt_smi.backend import TTSMIBackend
from tt_smi.utils import (
    get_board_type,
    hex_to_semver_eth,
    hex_to_semver_eth_wh,
    hex_to_semver_m3_fw,
    hex_to_semver_gddr_fw,
    get_fw_bundle_version,
)

_WH_DEVICE_ID = 0x401E
_BH_DEVICE_ID = 0xB140


def dal_pci_reset(reset_input, reinit: bool = True, print_status: bool = True) -> None:
    """Reset devices via tt-dal session.reset().

    tt_reset() issues the reset ioctl and blocks until the device reappears,
    so no separate reinit wait is needed. UMD logical ID targets are not
    supported; use a PCI BDF or /dev/tenstorrent/<n> instead.
    """
    import sys
    from tt_smi.device_input import SmiDeviceTargetKind
    from tt_tools_common.ui_common.themes import CMD_LINE_COLOR

    if reset_input.type == SmiDeviceTargetKind.UMD_LOGICAL_ID:
        print(
            CMD_LINE_COLOR.RED,
            "--use_dal does not support UMD logical ID reset targets; "
            "use a PCI BDF (e.g. 0000:01:00.0) or /dev/tenstorrent/<n>.",
            CMD_LINE_COLOR.ENDC,
        )
        sys.exit(1)

    if reset_input.type == SmiDeviceTargetKind.ALL:
        devs = ttdal.scan()
        if print_status:
            print(f"Resetting all PCI devices: {[d.id for d in devs]}")
    elif reset_input.type == SmiDeviceTargetKind.PCI_BDF:
        devs = [ttdal.Device.from_bdf(bdf) for bdf in reset_input.value]
        if print_status:
            print(f"Resetting PCI BDFs: {reset_input.value}")
    else:  # DEV_TENSTORRENT_ID
        devs = [ttdal.Device.from_path(f"/dev/tenstorrent/{i}") for i in reset_input.value]
        if print_status:
            print(f"Resetting /dev/tenstorrent IDs: {reset_input.value}")

    for dev in devs:
        dev.open().reset()  # blocks until device is back up

    if reinit and print_status:
        recovered = ttdal.scan()
        print(
            CMD_LINE_COLOR.PURPLE,
            f"Detected {len(recovered)} device(s) after reset.",
            CMD_LINE_COLOR.ENDC,
        )


class _DalDevice:
    """
    Thin compatibility shim around ttdal.Device.

    The tt-smi frontend calls device.is_remote() on entries from
    backend.devices.  ttdal.Device has no such method (it only exposes
    local hardware).  This wrapper returns False for is_remote() and
    forwards every other attribute access to the underlying device.
    """

    def __init__(self, dev) -> None:
        self._dev = dev

    def is_remote(self) -> bool:
        return False

    def __getattr__(self, name: str):
        return getattr(self._dev, name)


class TTDalBackend(TTSMIBackend):
    """
    Experimental tt-smi backend backed by ttdal Python bindings.

    Inherits TTSMIBackend and overrides device discovery and telemetry
    fetching to use ttdal instead of pyluwen / tt-umd.  All inherited
    telemetry-parsing helpers (chip limits, DRAM status, firmware version
    decoding, …) work unchanged because get_smbus_board_info() maps every
    tt-dal Tag to the expected smbus_telem_info key name.

    Limitations vs. the default backends:
      - No NOC coordinates (reported as "N/A")
      - No remote / NB300 chip support (tt-dal is local-only)
      - WH heartbeat uses TimerHeartbeat (BH formula) as a fallback
      - Galaxy / tray features are not supported
    """

    def __init__(self, pretty_output: bool = True):
        # ttdal.Device is a lightweight descriptor — no fd, no open resources.
        self.devs: list = ttdal.scan()

        # Keep one Session open per device for the lifetime of this backend.
        #
        # tt_device_open() with O_APPEND registers a power-state slot in the
        # kernel driver's aggregation table; tt_device_close() removes it and
        # triggers re-aggregation.  At the 100 ms polling rate we'd churn
        # through that machinery 10× per second per device for no benefit —
        # tt-smi never requests any power features.  A persistent session
        # avoids that driver overhead while keeping the fd count constant.
        self.sess: list = [dev.open() for dev in self.devs]

        # Info (vendor/device ID, BDF, …) is static for the lifetime of the
        # device; cache it once from the already-open sessions.
        self.info: list = [s.info() for s in self.sess]

        # Wrap descriptors so the frontend's device.is_remote() calls work.
        dal_devices = {i: _DalDevice(dev) for i, dev in enumerate(self.devs)}

        # use_umd=False (no cluster_descriptor) keeps the luwen code paths
        # active everywhere except the methods we explicitly override.
        super().__init__(
            devices=dal_devices,
            umd_cluster_descriptor=None,
            pretty_output=pretty_output,
        )

    # ------------------------------------------------------------------
    # Architecture detection
    # ------------------------------------------------------------------

    def is_blackhole(self, device_idx: int) -> bool:
        return self.info[device_idx].device_id == _BH_DEVICE_ID

    def is_wormhole(self, device_idx: int) -> bool:
        return self.info[device_idx].device_id == _WH_DEVICE_ID

    def is_grayskull(self, device_idx: int) -> bool:
        return False  # tt-dal does not support Grayskull

    def get_device_name(self, device_idx: int) -> str:
        if self.is_wormhole(device_idx):
            return "Wormhole"
        if self.is_blackhole(device_idx):
            return "Blackhole"
        return f"Unknown (0x{self.info[device_idx].device_id:04x})"

    # ------------------------------------------------------------------
    # PCI helpers
    # ------------------------------------------------------------------

    def get_pci_bdf(self, device_idx: int) -> str:
        info = self.info[device_idx]
        bdf = info.bus_dev_fn          # u16: [15:8]=bus [7:3]=dev [2:0]=fn
        bus = (bdf >> 8) & 0xFF
        dev = (bdf >> 3) & 0x1F
        fn  = bdf & 0x7
        return f"{info.pci_domain:04x}:{bus:02x}:{dev:02x}.{fn}"

    def get_pci_device_id(self, device_idx: int) -> str:
        return str(self.devs[device_idx].id)

    def _session(self, device_idx: int):
        """Return the open session for device_idx, reopening it if stale.

        Any call on a dead session raises immediately. sess.dev() is a pure
        local struct copy with no ioctl, so it always succeeds and gives us
        a fresh Device to open() from.
        """
        s = self.sess[device_idx]
        try:
            s.info()
        except Exception:
            try:
                s.close()
            except Exception:
                pass
            self.sess[device_idx] = s.dev().open()
        return self.sess[device_idx]

    # ------------------------------------------------------------------
    # Telemetry source
    # ------------------------------------------------------------------

    def get_smbus_board_info(self, board_num: int) -> Dict:
        """
        Read a fresh telemetry snapshot from tt-dal and return a dict whose
        keys match the smbus_telem_info format expected by the inherited
        parsing helpers (get_board_id, get_dram_speed, get_chip_limits, …).
        """
        T = ttdal.telem.Tag
        telem = self._session(board_num).telemetry()

        def h(tag) -> Optional[str]:
            v = telem.get(tag)
            return hex(v) if v is not None else None

        # Synthesise a packed THM_LIMITS word for the WH limits code path:
        # upper 16 bits = throttle limit, lower 16 bits = shutdown limit.
        throttle = telem.get(T.ThmLimitThrottle)
        shutdown  = telem.get(T.ThmLimitShutdown)
        if throttle is not None and shutdown is not None:
            thm_limits_packed: Optional[str] = hex((throttle << 16) | (shutdown & 0xFFFF))
        else:
            thm_limits_packed = None

        return {
            # Board identity
            "BOARD_ID":       None,   # no single-tag equivalent; HIGH+LOW used instead
            "BOARD_ID_HIGH":  h(T.BoardIdHigh),
            "BOARD_ID_LOW":   h(T.BoardIdLow),
            # Power / voltage / current
            "VCORE":          h(T.Vcore),
            "TDP":            h(T.Tdp),
            "TDC":            h(T.Tdc),
            "VDD_LIMITS":     h(T.VddLimits),
            "INPUT_POWER":    h(T.InputPower),
            "BOARD_POWER_LIMIT": h(T.BoardPowerLimit),
            # Temperature
            "ASIC_TEMPERATURE": h(T.AsicTemperature),
            # Clocks
            "AICLK":          h(T.AiClk),
            "AXICLK":         h(T.AxiClk),
            "ARCCLK":         h(T.ArcClk),
            # Fan
            "FAN_SPEED":      h(T.FanSpeed),
            "FAN_RPM":        h(T.FanRpm),
            # Heartbeat
            # WH code reads ARC3_HEALTH; BH code reads TIMER_HEARTBEAT.
            # Both paths divide by a small constant — close enough for display.
            "TIMER_HEARTBEAT": h(T.TimerHeartbeat),
            "ARC3_HEALTH":     h(T.TimerHeartbeat),
            # DRAM
            "DDR_STATUS":     h(T.GddrStatus),
            "DDR_SPEED":      h(T.GddrSpeed),
            # Firmware — provide both luwen (M3_) and UMD (DM_) key names so
            # the inherited get_firmware_versions() finds them on either path.
            "ETH_FW_VERSION":    h(T.EthFwVersion),
            "GDDR_FW_VERSION":   h(T.GddrFwVersion),
            "CM_FW_VERSION":     h(T.CmFwVersion),
            "M3_BL_FW_VERSION":  h(T.DmBlFwVersion),
            "M3_APP_FW_VERSION": h(T.DmAppFwVersion),
            "DM_BL_FW_VERSION":  h(T.DmBlFwVersion),
            "DM_APP_FW_VERSION": h(T.DmAppFwVersion),
            # Provide both key names used by get_fw_bundle_version()
            "FW_BUNDLE_VERSION":   h(T.FlashBundleVersion),
            "FLASH_BUNDLE_VERSION": h(T.FlashBundleVersion),
            "TT_FLASH_VERSION":    h(T.TtFlashVersion),
            # BH chip limits (separate tags)
            "TDP_LIMIT_MAX":        h(T.TdpLimitMax),
            "TDC_LIMIT_MAX":        h(T.TdcLimitMax),
            "AICLK_LIMIT_MAX":      h(T.AiClkLimitMax),
            "THM_LIMIT_SHUTDOWN":   h(T.ThmLimitShutdown),
            "THM_LIMIT_THROTTLE":   h(T.ThmLimitThrottle),
            # WH chip limits (packed register synthesised from separate tags)
            "THM_LIMITS":           thm_limits_packed,
        }

    # ------------------------------------------------------------------
    # Overrides that call device.is_remote() / .as_wh() in the parent
    # ------------------------------------------------------------------

    def get_pci_properties(self, board_num: int) -> Dict:
        """Return PCIe link properties from sysfs. tt-dal only sees local devices."""
        try:
            pcie_bdf = self.get_pci_bdf(board_num)
            pci_bus_path = os.path.realpath(f"/sys/bus/pci/devices/{pcie_bdf}")
        except Exception:
            return {prop: "N/A" for prop in constants.PCI_PROPERTIES}

        _gen_map = {"32.0": 5, "16.0": 4, "8.0": 3, "5.0": 2, "2.5": 1}

        properties: Dict = {}
        for prop in constants.PCI_PROPERTIES:
            try:
                with open(os.path.join(pci_bus_path, prop), "r", encoding="utf-8") as f:
                    output = f.readline().rstrip()
                    value = re.findall(r"\d+\.\d+|\d+", output)[0]
                    if prop in ("current_link_speed", "max_link_speed"):
                        value = _gen_map.get(value, "N/A")
            except Exception:
                value = "N/A"
            properties[prop] = value
        return properties

    def get_device_info(self, board_num: int) -> Dict:
        dev_info: Dict = {}
        for field in constants.DEV_INFO_LIST:
            if field == "bus_id":
                try:
                    dev_info[field] = self.get_pci_bdf(board_num)
                except Exception:
                    dev_info[field] = "N/A"
            elif field == "board_type":
                try:
                    board_id = self.get_board_id(board_num)
                    dev_info[field] = "N/A" if board_id == "N/A" else get_board_type(board_id)
                except Exception:
                    dev_info[field] = "N/A"
            elif field == "board_id":
                try:
                    dev_info[field] = self.get_board_id(board_num)
                except Exception:
                    dev_info[field] = "N/A"
            elif field == "coords":
                dev_info[field] = "N/A"   # not available via tt-dal
            elif field == "dram_status":
                dev_info[field] = self.get_dram_training_status(board_num)
            elif field == "dram_speed":
                dev_info[field] = self.get_dram_speed(board_num)
            elif field == "pcie_speed":
                dev_info[field] = self.pci_properties[board_num]["current_link_speed"]
            elif field == "pcie_width":
                dev_info[field] = self.pci_properties[board_num]["current_link_width"]
        return dev_info

    def get_firmware_versions(self, board_num: int) -> Dict:
        """
        Firmware version strings read from tt-dal telemetry.

        Overrides the parent to fix two issues when use_umd=False:
          1. gddr_fw is gated behind use_umd in the parent; we read it directly.
          2. Provides a single clean code path without the M3_/DM_ key fallbacks.
        """
        smbus = self.smbus_telem_info[board_num]
        fw: Dict = {}

        def semver_m3(key: str) -> str:
            val = smbus.get(key)
            return hex_to_semver_m3_fw(int(val, 16)) if val else "N/A"

        bundle = get_fw_bundle_version(smbus)
        fw["fw_bundle_version"] = (
            hex_to_semver_m3_fw(int(bundle, 16)) if bundle else "N/A"
        )
        fw["tt_flash_version"] = semver_m3("TT_FLASH_VERSION")
        fw["cm_fw"]            = semver_m3("CM_FW_VERSION")
        fw["cm_fw_date"]       = "N/A"   # not available via tt-dal

        eth_raw = smbus.get("ETH_FW_VERSION")
        if eth_raw is None:
            fw["eth_fw"] = "N/A"
        elif self.is_wormhole(board_num):
            fw["eth_fw"] = hex_to_semver_eth_wh(int(eth_raw, 16))
        else:
            fw["eth_fw"] = hex_to_semver_eth(int(eth_raw, 16))

        fw["dm_bl_fw"]  = semver_m3("M3_BL_FW_VERSION")
        fw["dm_app_fw"] = semver_m3("M3_APP_FW_VERSION")

        gddr_raw = smbus.get("GDDR_FW_VERSION")
        fw["gddr_fw"] = (
            hex_to_semver_gddr_fw(int(gddr_raw, 16))
            if gddr_raw and self.is_blackhole(board_num)
            else "N/A"
        )

        return fw

    def update_telem(self):
        """Update telemetry for all devices, silently retrying on dead sessions.

        If a session is dead (e.g. device was reset externally), _session()
        reopens it. If the reopen fails (device still coming up), the exception
        is caught here and stale data is kept; the next poll will retry.
        """
        for i in self.devices:
            try:
                self.smbus_telem_info[i] = self.get_smbus_board_info(i)
                self.device_telemetrys[i] = self.get_chip_telemetry(i)
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Snapshot helpers — drop is_remote() dependency
    # ------------------------------------------------------------------

    def print_all_available_devices_luwen(self):
        """List all tt-dal devices. tt-dal only sees local devices."""
        console = get_console()
        table = Table(title="All available boards on host (tt-dal):")
        table.add_column("Dev ID")
        table.add_column("PCI BDF")
        table.add_column("Architecture")
        table.add_column("Board Type")
        table.add_column("Board ID")
        for i in self.devices:
            table.add_row(
                f"/dev/tenstorrent/{self.get_pci_device_id(i)}",
                self.get_pci_bdf(i),
                self.get_device_name(i),
                str(self.device_infos[i]["board_type"]),
                str(self.device_infos[i]["board_id"]),
            )
        console.print(table)

    def get_logs_json(self) -> str:
        """Snapshot JSON. tt-dal devices are always local — no L/R suffix."""
        for i in self.devices:
            self.log.device_info[i].smbus_telem  = self.smbus_telem_info[i]
            self.log.device_info[i].board_info   = self.device_infos[i]
            self.log.device_info[i].telemetry    = self.device_telemetrys[i]
            self.log.device_info[i].firmwares    = self.firmware_infos[i]
            self.log.device_info[i].limits       = self.chip_limits[i]
        return self.log.get_clean_json_string()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def close(self):
        """Close all persistent tt-dal sessions."""
        for session in self.sess:
            try:
                session.close()
            except Exception:
                pass
