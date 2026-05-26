# SPDX-FileCopyrightText: © 2023 Tenstorrent Inc.
# SPDX-License-Identifier: Apache-2.0

"""
This is the backend of tt-smi.
    - Keeps track of chip objects and tasks related to fetching device info and telemetry
    - Sanitizes input for frontend
"""

import os
import re
import sys
import json
import time
import datetime
from tt_smi import log
from pathlib import Path
from rich.table import Table
from rich.text import Text
from tt_smi import constants
from rich import get_console
from rich.syntax import Syntax
from typing import Any, Dict, List, Optional, Tuple, Union
from rich.progress import track
from tt_tools_common.ui_common.themes import CMD_LINE_COLOR
from pyluwen import PciChip
from tt_smi.utils import (
    LOG_FOLDER,
    hex_to_date,
    hex_to_semver_eth,
    hex_to_semver_m3_fw,
    hex_to_semver_gddr_fw,
    hex_to_semver_eth_wh,
    get_board_type,
    convert_signed_16_16_to_float,
    dict_from_public_attrs,
    get_host_software_versions,
    get_fw_bundle_version
)
from tt_umd import (
    TTDevice,
    wormhole,
    TelemetryTag,
    ClusterDescriptor,
    SmBusArcTelemetryReader,
    ARCH,
)
from tt_tools_common.utils_common.system_utils import (
    get_host_info,
)
from tt_tools_common.utils_common.tools_utils import init_logging

class TTSMIBackend:
    """
    TT-SMI backend class that encompasses all chip objects on host.
    It handles all device related tasks like fetching device info, telemetry and toggling resets
    """

    def __init__(
        self,
        devices: Dict[int, Union[PciChip, TTDevice]] = None,
        umd_cluster_descriptor: Optional[ClusterDescriptor] = None,
        fully_init: bool = True,
        pretty_output: bool = True,
    ):
        self.devices = devices
        self.use_umd = umd_cluster_descriptor is not None
        if (self.use_umd):
            self.umd_cluster_descriptor = umd_cluster_descriptor
        self.pretty_output = pretty_output
        self.log: log.TTSMILog = log.TTSMILog(
            time=datetime.datetime.now(),
            host_info=get_host_info(),
            host_sw_vers=get_host_software_versions(),
            device_info=[
                log.TTSMIDeviceLog(
                    smbus_telem=log.SmbusTelem(),
                    board_info=log.BoardInfo(),
                    telemetry=log.Telemetry(),
                    firmwares=log.Firmwares(),
                    limits=log.Limits(),
                )
                for device in self.devices
            ],
        )
        self.smbus_telem_info = []
        self.firmware_infos = []
        self.device_infos = []
        self.device_telemetrys = []
        self.chip_limits = []
        self.pci_properties = []

        if fully_init:
            for i, _ in track(
                self.devices.items(),
                total=len(self.devices),
                description="Gathering Information",
                update_period=0.01,
                disable=not self.pretty_output,
            ):
                self.smbus_telem_info.append(self.get_smbus_board_info(i))
                self.firmware_infos.append(self.get_firmware_versions(i))
                self.pci_properties.append(self.get_pci_properties(i))
                self.device_infos.append(self.get_device_info(i))
                self.device_telemetrys.append(self.get_chip_telemetry(i))
                self.chip_limits.append(self.get_chip_limits(i))

    def get_topology_info(self, device_idx):
        """Per-device interconnect topology from the UMD ClusterDescriptor.

        Returns {} when running without UMD (--use_luwen), since the cluster
        descriptor (and thus chip-to-chip ethernet link data) is only produced
        by TopologyDiscovery. The full map is built once and cached.
        """
        if not self.use_umd:
            return {}
        if not hasattr(self, "_topology_infos"):
            cd = self.umd_cluster_descriptor
            eth = cd.get_ethernet_connections()
            mmio = cd.get_chips_with_mmio()
            self._topology_infos = {}
            for i in self.devices:
                try:
                    active = set(cd.get_active_eth_channels(i))
                    links = []
                    for chan, (rchip, rchan) in eth.get(i, {}).items():
                        links.append({
                            "eth_ch": chan,
                            "rchip": rchip,
                            "rchan": rchan,
                            "active": chan in active,
                        })
                    self._topology_infos[i] = {
                        "board": cd.get_board_type(i),
                        "attach": "PCIe" if cd.is_chip_mmio_capable(i) else "ETH",
                        "is_remote": cd.is_chip_remote(i),
                        "mmio_via": mmio.get(i),
                        "links": sorted(links, key=lambda d: d["eth_ch"]),
                    }
                except Exception as e:
                    self._topology_infos[i] = {"error": str(e), "links": []}
        return self._topology_infos.get(device_idx, {})

    def is_blackhole(self, device_idx) -> bool:
        return (self.devices[device_idx].as_bh() if not self.use_umd
                else self.devices[device_idx].get_arch() == ARCH.BLACKHOLE)
    
    def is_wormhole(self, device_idx) -> bool:
        return (self.devices[device_idx].as_wh() if not self.use_umd
                else self.devices[device_idx].get_arch() == ARCH.WORMHOLE_B0)

    def is_grayskull(self, device_idx) -> bool:
        return (self.devices[device_idx].as_gs() if not self.use_umd
                else False)
    
    def get_pci_device_id(self, device_idx) -> str:
        if self.devices[device_idx].is_remote():
            return "N/A"
        return (self.devices[device_idx].get_pci_interface_id() if not self.use_umd
                else self.devices[device_idx].get_pci_device().get_device_num())
        
    def get_pci_bdf(self, device_idx) -> str:
        if self.devices[device_idx].is_remote():
            return "N/A"
        return (self.devices[device_idx].get_pci_bdf() if not self.use_umd
                else self.devices[device_idx].get_pci_device().get_device_info().pci_bdf)
    
    def get_device_name(self, device_idx):
        """Get device name from chip object"""
        if self.is_wormhole(device_idx):
            return "Wormhole"
        elif self.is_blackhole(device_idx):
            return "Blackhole"
        else:
            assert False, "Unknown chip name, FIX!"

    def save_logs_to_file(self, result_filename: str = ""):
        """Save log for smi snapshots"""
        time_now = datetime.datetime.now()
        date_string = time_now.strftime("%m-%d-%Y_%H:%M:%S")
        if not os.path.exists(LOG_FOLDER):
            init_logging(LOG_FOLDER)
        log_filename = f"{LOG_FOLDER}{date_string}_results.json"
        if result_filename:
            dir_path = os.path.dirname(os.path.realpath(result_filename))
            Path(dir_path).mkdir(parents=True, exist_ok=True)
            log_filename = result_filename

        clean_json = self.get_logs_json()
        with open(log_filename, "w") as f:
            f.write(clean_json)

        return log_filename

    def print_logs_to_stdout(self, pretty: bool = True):
        """Pretty-print (or just print) logs to stdout"""
        clean_json = self.get_logs_json()

        if not pretty:
            print(clean_json)
            return

        formatted = Syntax(clean_json, "json", background_color="default")
        console = get_console()
        console.print(formatted)

    def get_logs_json(self) -> str:
        """Get logs as JSON"""
        for i in self.devices:
            self.log.device_info[i].smbus_telem = self.smbus_telem_info[i]
            self.log.device_info[i].board_info = self.device_infos[i]
            # Add L/R for nb300 to separate local and remote asics
            if self.is_wormhole(i):
                board_type = self.device_infos[i]["board_type"]
                suffix = " R" if self.devices[i].is_remote() else " L"
                board_type = board_type + suffix
                self.log.device_info[i].board_info["board_type"] = board_type
            self.log.device_info[i].telemetry = self.device_telemetrys[i]
            self.log.device_info[i].firmwares = self.firmware_infos[i]
            self.log.device_info[i].limits = self.chip_limits[i]

        return self.log.get_clean_json_string()

    def print_all_available_devices_luwen(self):
        """Print all available boards on host (Luwen path). Shows both PCI BDF and PCI Dev ID. Doesn't show UMD Chip ID."""
        console = get_console()
        table_1 = Table(title="All available boards on host:")
        table_1.add_column("PCI BDF")
        table_1.add_column("PCI Dev ID")
        table_1.add_column("Board Type")
        table_1.add_column("Device Series")
        table_1.add_column("Board Number")
        for i in self.devices:
            board_id = self.device_infos[i]["board_id"]
            board_type = self.device_infos[i]["board_type"]
            pci_dev_id = self.get_pci_device_id(i)
            if self.is_wormhole(i):
                suffix = " R" if self.devices[i].is_remote() else " L"
                board_type = board_type + suffix

            table_1.add_row(
                f"{self.get_pci_bdf(i)}",
                f"/dev/tenstorrent/{i}",
                f"{self.get_device_name(i)}",
                f"{board_type}",
                f"{board_id}",
            )
        console.print(table_1)
        table_2 = Table(title="Boards that can be reset:")
        table_2.add_column("PCI BDF")
        table_2.add_column("PCI Dev ID")
        table_2.add_column("Board Type")
        table_2.add_column("Device Series")
        table_2.add_column("Board Number")
        for i in self.devices:
            if (
                not self.devices[i].is_remote()
                and self.device_infos[i]["board_type"] != "wh_4u"
            ):
                board_id = self.device_infos[i]["board_id"]
                board_type = self.device_infos[i]["board_type"]
                pci_dev_id = self.get_pci_device_id(i)
                if self.is_wormhole(i):
                    suffix = " R" if self.devices[i].is_remote() else " L"
                    board_type = board_type + suffix
                table_2.add_row(
                    f"{self.get_pci_bdf(i)}",
                    f"/dev/tenstorrent/{i}",
                    f"{self.get_device_name(i)}",
                    f"{board_type}",
                    f"{board_id}",
                )
        console.print(table_2)

    def print_all_available_devices_umd(self):
        """Print all available boards on host (UMD path). Shows UMD Chip ID, PCI BDF, and PCI Dev ID."""
        if not self.use_umd:
            raise RuntimeError("print_all_available_devices_umd requires UMD backend (umd_cluster_descriptor set)")
        console = get_console()
        table_1 = Table(title="All available boards on host (UMD):")
        table_1.add_column("UMD Chip ID")
        table_1.add_column("PCI BDF")
        table_1.add_column("PCI Dev ID")
        table_1.add_column("Board Type")
        table_1.add_column("Device Series")
        table_1.add_column("Board Number")
        for i in self.devices:
            pci_dev_num = self.get_pci_device_id(i)
            board_id = self.device_infos[i]["board_id"]
            board_type = self.device_infos[i]["board_type"]
            if self.is_wormhole(i):
                suffix = " R" if self.devices[i].is_remote() else " L"
                board_type = board_type + suffix
            table_1.add_row(
                f"{i}",
                f"{self.get_pci_bdf(i)}",
                f"/dev/tenstorrent/{pci_dev_num}",
                f"{self.get_device_name(i)}",
                f"{board_type}",
                f"{board_id}",
            )
        console.print(table_1)
        table_2 = Table(title="Boards that can be reset (UMD):")
        table_2.add_column("UMD Chip ID")
        table_2.add_column("PCI BDF")
        table_2.add_column("PCI Dev ID")
        table_2.add_column("Board Type")
        table_2.add_column("Device Series")
        table_2.add_column("Board Number")
        for i in self.devices:
            if (
                not self.devices[i].is_remote()
                and self.device_infos[i]["board_type"] != "wh_4u"
            ):
                pci_dev_num = self.get_pci_device_id(i)
                board_id = self.device_infos[i]["board_id"]
                board_type = self.device_infos[i]["board_type"]
                if self.is_wormhole(i):
                    suffix = " R" if self.devices[i].is_remote() else " L"
                    board_type = board_type + suffix
                table_2.add_row(
                    f"{i}",
                    f"{self.get_pci_bdf(i)}",
                    f"/dev/tenstorrent/{pci_dev_num}",
                    f"{self.get_device_name(i)}",
                    f"{board_type}",
                    f"{board_id}",
                )
        console.print(table_2)

    def print_tray_and_device_mapping(self):
        """Print the mapping of trays to devices on the galaxy"""

        ubb_bus_ids = (
            constants.BH_UBB_BUS_IDS
            if self.devices[0].as_bh()
            else constants.WH_UBB_BUS_IDS
        )

        def _get_mapping_tray_to_device():
            bus_id_to_tray = {
                bus_id: tray_num for tray_num, bus_id in ubb_bus_ids.items()
            }
            tray_to_devices = {}
            for i, device_info in enumerate(self.device_infos):
                # Extract bus id from "domain:bus:device.function" format
                bus_id = int(device_info["bus_id"].split(":")[1], 16)
                tray_bus_id = bus_id & 0xF0
                if tray_bus_id in bus_id_to_tray:
                    tray_num = bus_id_to_tray[tray_bus_id]
                    tray_to_devices.setdefault(tray_num, []).append(i)
            return tray_to_devices

        tray_to_devices = _get_mapping_tray_to_device()

        console = get_console()
        table = Table(title="Mapping of trays to devices on the galaxy:")
        table.add_column("Tray Number")
        table.add_column("Tray Bus ID")
        table.add_column("/dev/tenstorrent/<id>")
        for tray_num in sorted(tray_to_devices):
            table.add_row(
                f"{tray_num}",
                f"0x{ubb_bus_ids[tray_num]:02x}",
                f"{','.join(map(str, tray_to_devices[tray_num]))}",
            )
        console.print(table)

    def _aggregate_topology(self) -> Dict[int, Dict[int, Dict[str, Any]]]:
        """Group per-chip links by remote chip. Returns {chip: {neighbor: stats}}.

        Each stats dict has: n_active, n_total, channels (sorted list of eth_ch),
        all_active (bool).
        """
        if not self.use_umd:
            return {}
        adj: Dict[int, Dict[int, Dict[str, Any]]] = {}
        for i in self.devices:
            t = self.get_topology_info(i)
            per_neighbor: Dict[int, Dict[str, Any]] = {}
            for lk in t.get("links", []):
                rchip = lk["rchip"]
                slot = per_neighbor.setdefault(rchip, {"channels": [], "active": []})
                slot["channels"].append(lk["eth_ch"])
                slot["active"].append(bool(lk["active"]))
            adj[i] = {}
            for rchip, slot in per_neighbor.items():
                chans = sorted(slot["channels"])
                n_active = sum(slot["active"])
                n_total = len(chans)
                adj[i][rchip] = {
                    "n_active": n_active,
                    "n_total": n_total,
                    "channels": chans,
                    "all_active": (n_active == n_total),
                }
        return adj

    def _classify_topology(self, adj: Dict[int, Dict[int, Dict[str, Any]]]) -> str:
        """Detect formal topology shape. Returns one of:
        'single', 'pair', 'ring', 'unknown'.
        """
        n = len(adj)
        if n <= 1:
            return "single"
        if n == 2:
            return "pair"
        # Ring detection: every chip has exactly 2 distinct neighbors AND the
        # neighbor graph forms a single cycle covering all chips.
        if not all(len(neighbors) == 2 for neighbors in adj.values()):
            return "unknown"
        start = next(iter(adj))
        visited = [start]
        prev = None
        cur = start
        while True:
            neighbors = list(adj[cur].keys())
            if prev is None:
                nxt = neighbors[0]
            else:
                nexts = [x for x in neighbors if x != prev]
                if len(nexts) != 1:
                    return "unknown"
                nxt = nexts[0]
            if nxt == start:
                break
            if nxt in visited:
                return "unknown"
            visited.append(nxt)
            prev, cur = cur, nxt
            if len(visited) > n:
                return "unknown"
        return "ring" if len(visited) == n else "unknown"

    def _channel_range_str(self, channels: List[int]) -> str:
        """Compress sorted channel list into 'a-b' or 'a,b,c' form."""
        if not channels:
            return ""
        chans = sorted(channels)
        # Detect contiguous run
        if chans[-1] - chans[0] + 1 == len(chans):
            return f"{chans[0]}-{chans[-1]}" if len(chans) > 1 else f"{chans[0]}"
        return ",".join(str(c) for c in chans)

    def _diagram_styles(self, theme: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Resolve diagram color styles. Prefers caller-provided theme keys
        (matching tt_tools_common's create_tt_tools_theme), falls back to
        Rich-native styles so CLI use without a theme stays consistent.
        """
        if theme is None:
            return {
                "chip": "bright_yellow bold",
                "channel": "green",
                "range": "dim",
                "warn": "yellow",
                "frame": "dim",
                "muted": "dim",
            }
        return {
            "chip": theme.get("yellow_bold", "bright_yellow bold"),
            "channel": theme.get("text_green", "green"),
            "range": theme.get("gray", "dim"),
            "warn": theme.get("attention", "yellow"),
            "frame": theme.get("gray", "dim"),
            "muted": theme.get("gray", "dim"),
        }

    def _edge_label_lines(self, adj: Dict[int, Dict[int, Dict[str, Any]]],
                          a: int, b: int, styles: Dict[str, Any]) -> Tuple[Text, Text]:
        """Return (count_line, range_line) for a 2-row edge label.

        Line 1 carries the channel count (e.g. ``4ch``).
        Line 2 carries the channel range (e.g. ``(ch 4-7)``) and an optional
        warn suffix (``3/4 up``) when some channels are down.
        """
        s = adj[a][b]
        ch = self._channel_range_str(s["channels"])
        count_line = Text(f"{s['n_total']}ch", style=styles["channel"])
        range_line = Text(f"(ch {ch})", style=styles["range"])
        if not s["all_active"]:
            range_line.append(f" {s['n_active']}/{s['n_total']} up", style=styles["warn"])
        return (count_line, range_line)

    def _edge_label_text(self, adj: Dict[int, Dict[int, Dict[str, Any]]],
                         a: int, b: int, styles: Dict[str, Any]) -> Text:
        """Build a styled 2-row edge label (count and range on separate lines).

        Renders as::

            4ch
            (ch 4-7)

        or, when some channels are down::

            4ch
            (ch 4-7) 3/4 up

        Used by inline diagram contexts (pair, 5+ ring listing, adjacency
        listing, 3-chip triangle bottom edge). The 4-chip ring renderer uses
        :meth:`_edge_label_lines` directly so each line can be placed
        independently inside the box geometry.
        """
        count_line, range_line = self._edge_label_lines(adj, a, b, styles)
        t = Text()
        t.append(count_line)
        t.append("\n")
        t.append(range_line)
        return t

    def _chip_token(self, i: int, styles: Dict[str, Any]) -> Text:
        """Styled chip marker like '[0]'."""
        t = Text()
        t.append("[", style=styles["frame"])
        t.append(str(i), style=styles["chip"])
        t.append("]", style=styles["frame"])
        return t

    def _ring_walk_order(self, adj: Dict[int, Dict[int, Dict[str, Any]]]) -> List[int]:
        """Deterministic ring traversal starting at min chip ID, smaller neighbor first."""
        start = min(adj.keys())
        order = [start]
        prev = None
        cur = start
        # Safety bound: classify_topology already validated the ring shape, but
        # guard against malformed input so a degenerate adj cannot infinite-loop.
        n_nodes = len(adj)
        while len(order) <= n_nodes:
            neighbors = sorted(adj[cur].keys())
            if prev is None:
                nxt = neighbors[0]
            else:
                nxt = next((x for x in neighbors if x != prev), None)
                if nxt is None:
                    break
            if nxt == start:
                break
            order.append(nxt)
            prev, cur = cur, nxt
        return order

    def _join_lines(self, lines: List[Text]) -> Text:
        out = Text()
        for i, line in enumerate(lines):
            if i:
                out.append("\n")
            out.append(line)
        return out

    def _render_ring(self, adj: Dict[int, Dict[int, Dict[str, Any]]],
                     styles: Dict[str, Any]) -> Text:
        """Render a ring topology. 4-chip uses a box diagram with side labels
        centered on the side ``│`` columns (mirroring how the top label sits
        between ``┌──`` and ``──┐``); 3-chip uses a triangle; 5+ chip rings
        fall back to a compact ring listing.
        """
        order = self._ring_walk_order(adj)
        n = len(order)

        if n == 4:
            a, b, c, d = order
            # 2-line labels (count, range) for each edge
            ab_count, ab_range = self._edge_label_lines(adj, a, b, styles)  # top
            bc_count, bc_range = self._edge_label_lines(adj, b, c, styles)  # right side
            cd_count, cd_range = self._edge_label_lines(adj, c, d, styles)  # bottom
            da_count, da_range = self._edge_label_lines(adj, d, a, styles)  # left side

            # ── Geometry ────────────────────────────────────────────────
            # Top/bottom labels live INSIDE the box → drive ``internal_w``.
            top_label_w = max(
                len(ab_count.plain), len(ab_range.plain),
                len(cd_count.plain), len(cd_range.plain),
            )
            # Side labels live OUTSIDE the box, centered on the ``│`` column.
            # They no longer constrain box width, but they DO constrain the
            # outer indent (left labels extend left of bar_left) and may force
            # the box to widen so left/right labels don't collide in the middle.
            side_l_w = max(len(da_count.plain), len(da_range.plain))
            side_r_w = max(len(bc_count.plain), len(bc_range.plain))
            min_side_gap = 2  # min spacing between left side label and right side label

            # ``internal_w`` = column count between the two │ bars (excludes bars).
            # - Top edges: ``── `` + label + `` ──`` = 6 + top_label_w
            # - Side rows: left label is LEFT-leaning (extra char outside box on
            #   the left) and right label is RIGHT-leaning (extra char outside
            #   box on the right). This keeps both labels' OUTSIDE extent equal
            #   so the diagram is left/right mirror-symmetric. Inside extent
            #   from each bar is ``(label_w - 1) // 2`` columns in both cases.
            left_interior = max(0, (side_l_w - 1) // 2)
            right_interior = max(0, (side_r_w - 1) // 2)
            internal_w = max(
                top_label_w + 6,
                left_interior + right_interior + min_side_gap,
            )
            box_w = internal_w + 2  # add the two │ bars

            # Outer indent: must accommodate the leftward extent of left-side
            # labels (``side_l_w // 2`` columns to the left of bar_left).
            indent = max(8, side_l_w // 2 + 2)

            bar_left = indent              # column of the left │ / ┌
            bar_right = indent + box_w - 1 # column of the right │ / ┐

            def _centered(label: Text, area_w: int) -> Tuple[int, int]:
                """Return (left_pad, right_pad) needed to center ``label`` within ``area_w``."""
                pad = area_w - len(label.plain)
                if pad <= 0:
                    return (0, 0)
                left = pad // 2
                return (left, pad - left)

            def corner_row(label_line: Text, corner_l: str, corner_r: str) -> Text:
                """``<indent>┌── label ──┐`` (top) or ``<indent>└── label ──┘`` (bottom)
                with the label centered inside the box.
                """
                # Area available for the label between "── " and " ──":
                # internal_w - "── " (3) - " ──" (3) = internal_w - 6
                area = internal_w - 6
                left_pad, right_pad = _centered(label_line, area)
                row = Text(" " * indent)
                row.append(corner_l, style=styles["frame"])
                row.append("── ", style=styles["frame"])
                if left_pad:
                    row.append(" " * left_pad)
                row.append(label_line)
                if right_pad:
                    row.append(" " * right_pad)
                row.append(" ──", style=styles["frame"])
                row.append(corner_r, style=styles["frame"])
                return row

            def edge_inner_row(label_line: Text) -> Text:
                """``<indent>│   label   │`` — second row of the top/bottom edge label,
                centered inside the box.
                """
                left_pad, right_pad = _centered(label_line, internal_w)
                row = Text(" " * indent)
                row.append("│", style=styles["frame"])
                if left_pad:
                    row.append(" " * left_pad)
                row.append(label_line)
                if right_pad:
                    row.append(" " * right_pad)
                row.append("│", style=styles["frame"])
                return row

            def chip_row(left: int, right: int) -> Text:
                """Place chip tokens so each token's visual centre sits on the
                corresponding │ column. For even-width tokens (e.g. ``[10]``),
                the LEFT token is left-leaning (extra char outside box) and the
                RIGHT token is right-leaning (extra char outside box) for
                mirror symmetry. Odd-width tokens (e.g. ``[0]``) are perfectly
                centered.
                """
                left_token = self._chip_token(left, styles)
                right_token = self._chip_token(right, styles)
                l_w = len(left_token.plain)
                r_w = len(right_token.plain)
                left_start = bar_left - l_w // 2
                right_start = bar_right - (r_w - 1) // 2
                row = Text(" " * max(0, left_start))
                row.append(left_token)
                end_left = left_start + l_w
                gap = right_start - end_left
                row.append(" " * max(1, gap))
                row.append(right_token)
                return row

            def bar_row() -> Text:
                row = Text(" " * bar_left)
                row.append("│", style=styles["frame"])
                row.append(" " * (box_w - 2))
                row.append("│", style=styles["frame"])
                return row

            def side_label_row(left_label: Text, right_label: Text) -> Text:
                """Place ``left_label`` on the left ``│`` column and
                ``right_label`` on the right ``│`` column, breaking the bars
                on this row (mirror of how the top label breaks ``──`` between
                ``┌──`` and ``──┐``). For even-width labels the LEFT label is
                left-leaning (extra char outside box on the left) and the
                RIGHT label is right-leaning (extra char outside box on the
                right) so both sides have equal OUTSIDE extent — keeping the
                diagram left/right mirror-symmetric.
                """
                l_w = len(left_label.plain)
                r_w = len(right_label.plain)
                left_start = bar_left - l_w // 2
                right_start = bar_right - (r_w - 1) // 2
                row = Text(" " * max(0, left_start))
                row.append(left_label)
                end_left = left_start + l_w
                gap = right_start - end_left
                row.append(" " * max(1, gap))
                row.append(right_label)
                return row

            lines = [
                corner_row(ab_count, "┌", "┐"),  # top edge — count
                edge_inner_row(ab_range),         # top edge — range continuation
                chip_row(a, b),
                bar_row(),
                bar_row(),
                side_label_row(da_count, bc_count),   # side count, centered on │ columns
                side_label_row(da_range, bc_range),   # side range, centered on │ columns
                bar_row(),
                bar_row(),
                chip_row(d, c),
                edge_inner_row(cd_range),         # bottom edge — range above corner
                corner_row(cd_count, "└", "┘"),  # bottom edge — count on corner row
            ]
            return self._join_lines(lines)

        if n == 3:
            a, b, c = order
            ab_count, ab_range = self._edge_label_lines(adj, a, b, styles)
            bc_count, bc_range = self._edge_label_lines(adj, b, c, styles)
            ca_count, ca_range = self._edge_label_lines(adj, c, a, styles)
            # Compact 3-chip triangle. Side labels (ca, ab) span two rows each
            # so columns stay aligned; the bottom edge label sits beneath the
            # connecting line.
            ca_w = max(len(ca_count.plain), len(ca_range.plain))
            lines: List[Text] = []
            # Apex chip
            row_apex = Text("        ")
            row_apex.append(self._chip_token(a, styles))
            lines.append(row_apex)
            lines.append(Text("       / \\", style=styles["frame"]))
            # Side labels — count row then range row
            row_sides_count = Text("    ")
            row_sides_count.append(ca_count)
            row_sides_count.append(" " * (ca_w - len(ca_count.plain) + 3))
            row_sides_count.append(ab_count)
            lines.append(row_sides_count)
            row_sides_range = Text("    ")
            row_sides_range.append(ca_range)
            row_sides_range.append(" " * (ca_w - len(ca_range.plain) + 3))
            row_sides_range.append(ab_range)
            lines.append(row_sides_range)
            # Bottom row: chip ── count_label ── chip
            row_bottom = Text()
            row_bottom.append(self._chip_token(b, styles))
            row_bottom.append(Text(" ── ", style=styles["frame"]))
            row_bottom.append(bc_count)
            row_bottom.append(Text(" ── ", style=styles["frame"]))
            row_bottom.append(self._chip_token(c, styles))
            lines.append(row_bottom)
            # Range of bottom edge sits centered under the count label.
            b_token_w = len(self._chip_token(b, styles).plain)
            row_bottom_range = Text(" " * (b_token_w + 4))  # past "[b] ── "
            row_bottom_range.append(bc_range)
            lines.append(row_bottom_range)
            return self._join_lines(lines)

        # n >= 5: compact ring listing
        title = Text(f"Ring of {n} chips:\n", style=styles["muted"])
        chips_text = Text()
        for idx, x in enumerate(order + [order[0]]):
            if idx:
                chips_text.append(" → ", style=styles["frame"])
            chips_text.append(self._chip_token(x, styles))
        body_lines: List[Text] = []
        for a, b in zip(order, order[1:] + [order[0]]):
            count, rng = self._edge_label_lines(adj, a, b, styles)
            a_token = self._chip_token(a, styles)
            # Row 1: ``  [a] ── count ── [b]``
            row_main = Text("  ")
            row_main.append(a_token)
            row_main.append(" ── ", style=styles["frame"])
            row_main.append(count)
            row_main.append(" ── ", style=styles["frame"])
            row_main.append(self._chip_token(b, styles))
            body_lines.append(row_main)
            # Row 2: range, aligned under the count column.
            indent_w = 2 + len(a_token.plain) + 4  # 2 leading + [a] + " ── "
            row_range = Text(" " * indent_w)
            row_range.append(rng)
            body_lines.append(row_range)
        out = Text()
        out.append(title)
        out.append("  ")
        out.append(chips_text)
        for line in body_lines:
            out.append("\n")
            out.append(line)
        return out

    def _render_pair(self, adj: Dict[int, Dict[int, Dict[str, Any]]],
                     styles: Dict[str, Any]) -> Text:
        """Render a 2-chip topology (two rows: chips + count edge, then range)."""
        a, b = sorted(adj.keys())
        s = adj[a].get(b)
        a_token = self._chip_token(a, styles)
        b_token = self._chip_token(b, styles)
        if not s:
            row = Text("   ")
            row.append(a_token)
            row.append("   ")
            row.append(b_token)
            row.append("  (no inter-chip links discovered)", style=styles["muted"])
            return row
        count, rng = self._edge_label_lines(adj, a, b, styles)
        # Row 1: ``   [a] ── count ── [b]``
        row_main = Text("   ")
        row_main.append(a_token)
        row_main.append(" ── ", style=styles["frame"])
        row_main.append(count)
        row_main.append(" ── ", style=styles["frame"])
        row_main.append(b_token)
        # Row 2: range, aligned under the count column.
        indent_w = 3 + len(a_token.plain) + 4  # 3 leading + [a] + " ── "
        row_range = Text(" " * indent_w)
        row_range.append(rng)
        return self._join_lines([row_main, row_range])

    def _render_adjacency_list(self, adj: Dict[int, Dict[int, Dict[str, Any]]],
                               styles: Dict[str, Any]) -> Text:
        """Fallback: per-chip neighbor listing (two rows per neighbor entry)."""
        lines: List[Text] = []
        for i in sorted(adj.keys()):
            neighbors = adj[i]
            header = Text()
            header.append(self._chip_token(i, styles))
            if not neighbors:
                header.append("  (no eth links)", style=styles["muted"])
                lines.append(header)
                continue
            header.append(" neighbors:", style=styles["muted"])
            lines.append(header)
            for rchip in sorted(neighbors.keys()):
                count, rng = self._edge_label_lines(adj, i, rchip, styles)
                rchip_token = self._chip_token(rchip, styles)
                # Row 1: ``  → [rchip] : count``
                row_main = Text("  → ", style=styles["frame"])
                row_main.append(rchip_token)
                row_main.append(" : ", style=styles["muted"])
                row_main.append(count)
                lines.append(row_main)
                # Row 2: range, aligned under count column.
                indent_w = 4 + len(rchip_token.plain) + 3  # "  → " + [rchip] + " : "
                row_range = Text(" " * indent_w)
                row_range.append(rng)
                lines.append(row_range)
        return self._join_lines(lines)

    def render_topology_diagram_rich(self, theme: Optional[Dict[str, Any]] = None) -> Text:
        """Return a styled (Rich) multi-line topology diagram.

        Auto-detects ring / pair / single topologies; falls back to a per-chip
        adjacency listing for unrecognized shapes. Colors follow the supplied
        text theme (tt_tools_common keys) when given, otherwise defaults to
        Rich's standard palette.
        """
        styles = self._diagram_styles(theme)
        if not self.use_umd:
            return Text("(topology diagram requires UMD mode; disabled with --use_luwen)",
                        style=styles["muted"])
        for i in self.devices:
            self.get_topology_info(i)
        adj = self._aggregate_topology()
        if not adj:
            return Text("(no devices detected)", style=styles["muted"])
        kind = self._classify_topology(adj)
        if kind == "single":
            row = Text()
            row.append(self._chip_token(next(iter(adj)), styles))
            row.append("  (single device, no inter-chip links)", style=styles["muted"])
            return row
        if kind == "pair":
            return self._render_pair(adj, styles)
        if kind == "ring":
            return self._render_ring(adj, styles)
        return self._render_adjacency_list(adj, styles)

    def render_topology_diagram(self) -> str:
        """Plain-text version of the topology diagram (no ANSI styling).

        Convenience wrapper for piping/scripts; equivalent to
        `render_topology_diagram_rich().plain`.
        """
        return self.render_topology_diagram_rich().plain

    def topology_kind_label(self) -> str:
        """Short human-readable summary of the current topology, suitable for
        a panel/border title. Reflects device count and detected shape.
        """
        if not self.use_umd:
            return "Topology"
        for i in self.devices:
            self.get_topology_info(i)
        adj = self._aggregate_topology()
        if not adj:
            return "Topology"
        n = len(adj)
        kind = self._classify_topology(adj)
        if kind == "single":
            return "Topology · single chip"
        if kind == "pair":
            return "Topology · pair"
        if kind == "ring":
            return f"Topology · ring of {n}"
        return f"Topology · {n} chips"

    def print_topology_to_stdout(self, fmt: str = "table", pretty: bool = True) -> None:
        """Print chip-to-chip ethernet topology to stdout.

        Args:
            fmt: "table" for a rich.Table render, "json" for a JSON dump,
                 "diagram" for a unicode topology diagram.
            pretty: When fmt=="json", syntax-highlight via rich.Syntax (TTY only).

        Requires UMD backend; caller must guard with `self.use_umd`.
        """
        if not self.use_umd:
            raise RuntimeError(
                "print_topology_to_stdout requires UMD backend (disabled with --use_luwen)"
            )

        # Force-populate the cached topology map for every device
        for i in self.devices:
            self.get_topology_info(i)

        if fmt == "diagram":
            console = get_console()
            console.print(self.render_topology_diagram_rich())
            return

        if fmt == "json":
            payload: Dict[int, Dict[str, Any]] = {}
            for i in self.devices:
                t = self.get_topology_info(i)
                entry: Dict[str, Any] = {
                    "board": str(t.get("board")) if t.get("board") is not None else None,
                    "attach": t.get("attach"),
                    "is_remote": t.get("is_remote"),
                    "mmio_via": t.get("mmio_via"),
                    "links": t.get("links", []),
                }
                if "error" in t:
                    entry["error"] = t["error"]
                payload[i] = entry
            out = json.dumps(payload, indent=2, default=str)
            if pretty:
                console = get_console()
                console.print(Syntax(out, "json", background_color="default"))
            else:
                print(out)
            return

        if fmt != "table":
            raise ValueError(f"Unsupported topology format: {fmt!r} (expected 'table' or 'json')")

        console = get_console()
        table = Table(title="Device Topology (chip-to-chip ethernet)")
        for col in constants.TOPOLOGY_TABLE_HEADER:
            table.add_column(col, justify="center")

        for i in self.devices:
            t = self.get_topology_info(i)
            board = str(t.get("board", "?"))
            attach = t.get("attach", "?") + (" R" if t.get("is_remote") else "")
            links = t.get("links", [])
            if not links:
                note = "error" if t.get("error") else "no eth link"
                table.add_row(f"{i}", board, attach, "-", "-", "-", note)
                continue
            for n, lk in enumerate(links):
                state = "active" if lk["active"] else "down"
                if n == 0:
                    table.add_row(
                        f"{i}", board, attach,
                        str(lk["eth_ch"]), str(lk["rchip"]), str(lk["rchan"]), state,
                    )
                else:
                    table.add_row(
                        "", "", "",
                        str(lk["eth_ch"]), str(lk["rchip"]), str(lk["rchan"]), state,
                    )
        console.print(table)

    def get_smbus_board_info(self, board_num: int) -> Dict:
        """Update board info by reading SMBUS_TELEMETRY"""
        if self.use_umd:
            smbus_telem_dict = {}
            arch = self.devices[board_num].get_arch()
            if arch == ARCH.WORMHOLE_B0:
                telem_reader = SmBusArcTelemetryReader(self.devices[board_num])
                tag_collection = wormhole.TelemetryTag
            elif arch == ARCH.BLACKHOLE:
                telem_reader = self.devices[board_num].get_arc_telemetry_reader()
                tag_collection = TelemetryTag
            else:
                raise ValueError(f"Unknown arch for device {board_num}")
            
            for telem_key in tag_collection:
                telem_value = hex(telem_reader.read_entry(telem_key.value)) if telem_reader.is_entry_available(telem_key.value) else None
                smbus_telem_dict[telem_key.name] = telem_value
            return smbus_telem_dict
        
        pyluwen_chip = self.devices[board_num]
        if pyluwen_chip.as_bh():
            telem_struct = pyluwen_chip.as_bh().get_telemetry()
        elif pyluwen_chip.as_wh():
            telem_struct = pyluwen_chip.as_wh().get_telemetry()
        else:
            raise ValueError(f"Unknown chip type for device {board_num}")
        json_map = dict_from_public_attrs(telem_struct)
        smbus_telem_dict = dict.fromkeys(constants.SMBUS_TELEMETRY_LIST)

        for key, value in json_map.items():
            if value is not None:
                value = hex(value)
            smbus_telem_dict[key.upper()] = value
        return smbus_telem_dict

    def update_telem(self):
        """Update telemetry in a given interval"""
        for i in self.devices:
            self.smbus_telem_info[i] = self.get_smbus_board_info(i)
            self.device_telemetrys[i] = self.get_chip_telemetry(i)

    def get_board_id(self, board_num) -> str:
        """Read board id from CSM or SPI if FW is not loaded"""
        if "BOARD_ID" in self.smbus_telem_info[board_num] and self.smbus_telem_info[board_num]["BOARD_ID"]:
            board_id = int(self.smbus_telem_info[board_num]["BOARD_ID"], base=16)
            return f"{board_id:016x}"
        else:
            board_info_0 = int(self.smbus_telem_info[board_num]["BOARD_ID_LOW"], base=16)
            board_info_1 = int(self.smbus_telem_info[board_num]["BOARD_ID_HIGH"], base=16)

            if board_info_0 is None or board_info_1 is None:
                return "N/A"
            return f"{board_info_1:08x}{board_info_0:08x}"

    def get_dram_speed(self, board_num) -> int:
        """Read DRAM Frequency from CSM and alternatively from SPI if FW not loaded on chip"""
        if self.is_blackhole(board_num):
            if self.smbus_telem_info[board_num]["DDR_SPEED"] is None:
                return "N/A"
            dram_speed = int(self.smbus_telem_info[board_num]["DDR_SPEED"], 16)
            # check if its div by 1000 and then return num GHz else MHz
            if dram_speed % 1000 == 0:
                dram_speed = dram_speed // 1000
                return f"{dram_speed}G"
            else:
                return f"{dram_speed}"
        elif self.is_wormhole(board_num):
            if self.smbus_telem_info[board_num]["DDR_STATUS"] is not None:
                dram_speed_raw = (
                    int(self.smbus_telem_info[board_num]["DDR_STATUS"], 16) >> 24
                )
                if dram_speed_raw == 0:
                    return "16G"
                elif dram_speed_raw == 1:
                    return "14G"
                elif dram_speed_raw == 2:
                    return "12G"
                elif dram_speed_raw == 3:
                    return "10G"
                elif dram_speed_raw == 4:
                    return "8G"
                else:
                    return None
            return "N/A"
        else:
            return "N/A"

    def get_pci_properties(self, board_num):
        """Get the PCI link speed and link width details from sysfs files"""
        if self.devices[board_num].is_remote():
            return {prop: "N/A" for prop in constants.PCI_PROPERTIES}

        try:
            pcie_bdf = self.get_pci_bdf(board_num)
            pci_bus_path = os.path.realpath(f"/sys/bus/pci/devices/{pcie_bdf}")
        except:
            return {prop: "N/A" for prop in constants.PCI_PROPERTIES}

        def get_pcie_gen(link_speed: str) -> int:
            if link_speed == "32.0":
                return 5
            if link_speed == "16.0":
                return 4
            elif link_speed == "8.0":
                return 3
            elif link_speed == "5.0":
                return 2
            elif link_speed == "2.5":
                return 1
            else:
                assert False, f"Invalid link speed {link_speed}"

        properties = {}

        for prop in constants.PCI_PROPERTIES:
            try:
                with open(os.path.join(pci_bus_path, prop), "r", encoding="utf-8") as f:
                    output = f.readline().rstrip()
                    value = re.findall(r"\d+\.\d+|\d+", output)[0]
                    if prop == "current_link_speed" or prop == "max_link_speed":
                        value = get_pcie_gen(value)
            except Exception:
                value = "N/A"
            properties[prop] = value
        return properties

    def get_dram_training_status(self, board_num) -> bool:
        """
        Get DRAM Training Status
        True means it passed training, False means it failed or did not train at all
        """
        if self.is_wormhole(board_num):
            # DDR_STATUS field in WH:
            # (31:24) dram speed
            # (23:0) per channel dram status (6 channels)
            # DRAM_TRAINING_FAIL = 0x1
            # DRAM_TRAINING_PASS = 0x2
            if self.smbus_telem_info[board_num]["DDR_STATUS"] is None:
                return False
            dram_status = (
                int(self.smbus_telem_info[board_num]["DDR_STATUS"], 16)) & 0xFFFFFF
            if dram_status == 0x222222:
                return True
            return False
        elif self.is_blackhole(board_num):
            dram_status = int(self.smbus_telem_info[board_num]["DDR_STATUS"], 16)
            if int(get_fw_bundle_version(self.smbus_telem_info[board_num]), 16) >= 0x13070000:
                # After FW version 19.7.0.3 (hex 0x13070003), the dram status is a 16-bit field with the following layout:
                # DDR Status:
                # [0]  - Training complete GDDR 0
                # [1]  - Error GDDR 0
                # [2]  - Training complete GDDR 1
                # [3]  - Error GDDR 1
                # ...
                # [14] - Training complete GDDR 7
                # [15] - Error GDDR 7
                # [16] - BIST complete GDDR 0
                # [17] - BIST failed GDDR 0
                # [18] - BIST complete GDDR 1
                # [19] - BIST failed GDDR 1
                # ...
                # [30] - BIST complete GDDR 7
                # [31] - BIST failed GDDR 7
                if dram_status == 0x55555555:
                    return True
                return False
            else:
                # Pre-19.7.0.3 DDR Status in BH is a 16-bit field with the following layout:
                #  [0] - Training complete GDDR 0
                #  [1] - Error GDDR 0
                #  [2] - Training complete GDDR 1
                #  [3] - Error GDDR 1
                #  ...
                #  [14] - Training Complete GDDR 7
                #  [15] - Error GDDR 7
                # 0x5555 = 0b0101010101010101 means all 8 channels trained successfully
                if dram_status == 0x5555:
                    return True
                return False
        else:
            return False

    def get_device_info(self, board_num) -> dict:
        dev_info = {}
        for field in constants.DEV_INFO_LIST:
            if field == "bus_id":
                try:
                    dev_info[field] = self.get_pci_bdf(board_num)
                except:
                    dev_info[field] = "N/A"
            elif field == "board_type":
                if self.get_board_id(board_num) == "N/A":
                    dev_info[field] = "N/A"
                else:
                    dev_info[field] = get_board_type(self.get_board_id(board_num))
            elif field == "board_id":
                dev_info[field] = self.get_board_id(board_num)
            elif field == "coords":
                if self.is_wormhole(board_num):
                    if self.use_umd:
                        if board_num in self.umd_cluster_descriptor.get_chip_locations():
                            eth_coord = self.umd_cluster_descriptor.get_chip_locations()[board_num]
                            dev_info[
                                field
                            ] = f"({eth_coord.x}, {eth_coord.y}, {eth_coord.rack}, {eth_coord.shelf})"
                        else:
                            dev_info[field] = "(0, 0, 0, 0)"
                    else:
                        dev_info[
                            field
                        ] = f"({self.devices[board_num].as_wh().get_local_coord().shelf_x}, {self.devices[board_num].as_wh().get_local_coord().shelf_y}, {self.devices[board_num].as_wh().get_local_coord().rack_x}, {self.devices[board_num].as_wh().get_local_coord().rack_y})"
                else:
                    dev_info[field] = "N/A"
            elif field == "dram_status":
                dev_info[field] = self.get_dram_training_status(board_num)
            elif field == "dram_speed":
                dev_info[field] = self.get_dram_speed(board_num)
            elif field == "pcie_speed":
                dev_info[field] = self.pci_properties[board_num]["current_link_speed"]
            elif field == "pcie_width":
                dev_info[field] = self.pci_properties[board_num]["current_link_width"]

        return dev_info

    def get_bh_chip_telemetry(self, board_num) -> Dict:
        """Get telemetry data for bh chip. None if ARC FW not running"""
        current = (
            int(self.smbus_telem_info[board_num]["TDC"], 16) & 0xFFFF
            if self.smbus_telem_info[board_num]["TDC"] is not None
            else 0
        )
        if self.smbus_telem_info[board_num]["VCORE"] is not None:
            voltage = int(self.smbus_telem_info[board_num]["VCORE"], 16) / 1000
        else:
            voltage = 10000
        power = (
            int(self.smbus_telem_info[board_num]["TDP"], 16) & 0xFFFF
            if self.smbus_telem_info[board_num]["TDP"] is not None
            else 0
        )
        asic_temperature = (
            (
                convert_signed_16_16_to_float(
                    int(self.smbus_telem_info[board_num]["ASIC_TEMPERATURE"], 16)
                )
            )
            if self.smbus_telem_info[board_num]["ASIC_TEMPERATURE"] is not None
            else 0
        )
        aiclk = (
            int(self.smbus_telem_info[board_num]["AICLK"], 16) & 0xFFFF
            if self.smbus_telem_info[board_num]["AICLK"] is not None
            else 0
        )
        timer_heartbeat = int(self.smbus_telem_info[board_num]["TIMER_HEARTBEAT"], 16) // 6 # Watchdog heartbeat, ~2 per second
        fan_speed = (
            int(self.smbus_telem_info[board_num]["FAN_RPM"], 16) & 0xFFFF
            if self.smbus_telem_info[board_num]["FAN_RPM"] is not None
            else 0
        )

        chip_telemetry = {
            "voltage": f"{voltage:4.2f}",
            "current": f"{current:5.1f}",
            "power": f"{power:5.1f}",
            "aiclk": f"{aiclk:4.0f}",
            "asic_temperature": f"{asic_temperature:4.1f}",
            "fan_speed": f"{fan_speed}",
            "heartbeat": f"{timer_heartbeat}",
        }
        return chip_telemetry

    def get_wh_chip_telemetry(self, board_num) -> Dict:
        """Get telemetry data for WH chip. None if ARC FW not running"""
        current = int(self.smbus_telem_info[board_num]["TDC"], 16) & 0xFFFF
        if self.smbus_telem_info[board_num]["VCORE"] is not None:
            voltage = int(self.smbus_telem_info[board_num]["VCORE"], 16) / 1000
        else:
            voltage = 10000
        power = int(self.smbus_telem_info[board_num]["TDP"], 16) & 0xFFFF
        asic_temperature = (
            int(self.smbus_telem_info[board_num]["ASIC_TEMPERATURE"], 16) & 0xFFFF
        ) / 16
        aiclk = int(self.smbus_telem_info[board_num]["AICLK"], 16) & 0xFFFF
        arc3_heartbeat = int(self.smbus_telem_info[board_num]["ARC3_HEALTH"], 16) // 5 # Watchdog heartbeat, ~2 per second
        if self.smbus_telem_info[board_num]["FAN_SPEED"] is not None:
            if self.devices[board_num].is_remote():
                fan_speed = (int(self.smbus_telem_info[board_num]["FAN_SPEED"], 16) >> 16) & 0xFFFF
            else:
                fan_speed = int(self.smbus_telem_info[board_num]["FAN_SPEED"], 16) & 0xFFFF
        else:
            fan_speed = 0

        chip_telemetry = {
            "voltage": f"{voltage:4.2f}",
            "current": f"{current:5.1f}",
            "power": f"{power:5.1f}",
            "aiclk": f"{aiclk:4.0f}",
            "asic_temperature": f"{asic_temperature:4.1f}",
            "fan_speed": f"{fan_speed}",
            "heartbeat": f"{arc3_heartbeat}"
        }

        return chip_telemetry

    def get_chip_telemetry(self, board_num) -> Dict:
        """Return the correct chip telemetry for a given board"""
        if self.is_blackhole(board_num):
            return self.get_bh_chip_telemetry(board_num)
        elif self.is_wormhole(board_num):
            return self.get_wh_chip_telemetry(board_num)
        else:
            print(
                CMD_LINE_COLOR.RED,
                f"Could not fetch telemetry for board {e}: Unrecognized board type!",
                CMD_LINE_COLOR.ENDC,
            )
            return {}

    def get_chip_limits(self, board_num: int) -> Dict[str, str]:
        if self.is_blackhole(board_num):
            return self.get_bh_chip_limits(board_num)
        elif self.is_wormhole(board_num):
            return self.get_wh_chip_limits(board_num)
        else:
            print(
                CMD_LINE_COLOR.RED,
                f"Could not fetch chip limits for board {board_num}: Unrecognized board type!",
                CMD_LINE_COLOR.ENDC,
            )
            return {}

    def get_wh_chip_limits(self, board_num: int) -> Dict[str, str]:
        """Get chip limits from the CSM. None if ARC FW not running"""

        chip_limits = {}
        for field in constants.LIMITS:
            if field == "vdd_min":
                vdd_limits = self.smbus_telem_info[board_num].get("VDD_LIMITS")
                chip_limits[field] = f"{(int(vdd_limits, 16) & 0xFFFF) / 1000:4.2f}" if vdd_limits else 0
            elif field == "vdd_max":
                vdd_limits = self.smbus_telem_info[board_num].get("VDD_LIMITS")
                chip_limits[field] = f"{(int(vdd_limits, 16) >> 16) / 1000:4.2f}" if vdd_limits else 0
            elif field == "tdp_limit":
                tdp = self.smbus_telem_info[board_num].get("TDP")
                chip_limits[field] = f"{int(tdp, 16) >> 16:3.0f}" if tdp else 0
            elif field == "tdc_limit":
                tdc = self.smbus_telem_info[board_num].get("TDC")
                chip_limits[field] = f"{int(tdc, 16) >> 16:3.0f}" if tdc else 0
            elif field == "asic_fmax":
                aiclk = self.smbus_telem_info[board_num].get("AICLK")
                chip_limits[field] = f"{int(aiclk, 16) >> 16:4.0f}" if aiclk else 0
            elif field == "therm_trip_l1_limit":
                thm_limits = self.smbus_telem_info[board_num].get("THM_LIMITS")
                chip_limits[field] = f"{int(thm_limits, 16) >> 16:2.0f}" if thm_limits else 0
            elif field == "thm_limit":
                thm_limits = self.smbus_telem_info[board_num].get("THM_LIMITS")
                chip_limits[field] = f"{int(thm_limits, 16) & 0xFFFF:2.0f}" if thm_limits else 0
            else:
                chip_limits[field] = 0
        return chip_limits

    def get_bh_chip_limits(self, board_num: int) -> Dict[str, str]:
        """Get chip limits from BH telemetry. None if ARC FW not running"""

        chip_limits = {}
        for field in constants.LIMITS:
            if field == "vdd_min":
                vdd_limits = self.smbus_telem_info[board_num].get("VDD_LIMITS")
                chip_limits[field] = f"{(int(vdd_limits, 16) & 0xFFFF) / 1000:4.2f}" if vdd_limits else 0
            elif field == "vdd_max":
                vdd_limits = self.smbus_telem_info[board_num].get("VDD_LIMITS")
                chip_limits[field] = f"{(int(vdd_limits, 16) >> 16) / 1000:4.2f}" if vdd_limits else 0
            elif field == "tdp_limit":
                tdp_limit = self.smbus_telem_info[board_num].get("TDP_LIMIT_MAX")
                chip_limits[field] = f"{int(tdp_limit, 16):3.0f}" if tdp_limit else 0
            elif field == "tdc_limit":
                tdc_limit = self.smbus_telem_info[board_num].get("TDC_LIMIT_MAX")
                chip_limits[field] = f"{int(tdc_limit, 16):3.0f}" if tdc_limit else 0
            elif field == "asic_fmax":
                asic_fmax = self.smbus_telem_info[board_num].get("AICLK_LIMIT_MAX")
                chip_limits[field] = f"{int(asic_fmax, 16):4.0f}" if asic_fmax else 0
            elif field == "therm_trip_l1_limit":
                therm_trip_l1_limit = self.smbus_telem_info[board_num].get("THM_LIMIT_THROTTLE")
                chip_limits[field] = f"{int(therm_trip_l1_limit, 16):2.0f}" if therm_trip_l1_limit else 0
            elif field == "thm_limit":
                if "THM_LIMIT_SHUTDOWN" in self.smbus_telem_info[board_num]:
                    thm_limits = self.smbus_telem_info[board_num].get("THM_LIMIT_SHUTDOWN")
                elif "THM_LIMITS" in self.smbus_telem_info[board_num]:
                    thm_limits = self.smbus_telem_info[board_num].get("THM_LIMITS")
                else:
                    thm_limits = 0
                chip_limits[field] = f"{int(thm_limits, 16):2.0f}" if thm_limits else 0
            else:
                chip_limits[field] = 0
        return chip_limits

    def get_firmware_versions(self, board_num):
        """Translate the telem struct semver for gui"""
        fw_versions = {}
        for field in constants.FW_LIST_SNAPSHOT:
            if field == "cm_fw":
                if "ARC0_FW_VERSION" in self.smbus_telem_info[board_num]:
                    val = self.smbus_telem_info[board_num]["ARC0_FW_VERSION"]
                elif "CM_FW_VERSION" in self.smbus_telem_info[board_num]:
                    val = self.smbus_telem_info[board_num]["CM_FW_VERSION"]
                else:
                    val = "N/A"
                if val is None:
                    fw_versions[field] = "N/A"
                else:
                    fw_versions[field] = hex_to_semver_m3_fw(int(val, 16))

            elif field == "cm_fw_date":
                if "WH_FW_DATE" in self.smbus_telem_info[board_num]:
                    val = self.smbus_telem_info[board_num]["WH_FW_DATE"]
                if val is None:
                    fw_versions[field] = "N/A"
                else:
                    fw_versions[field] = hex_to_date(int(val, 16), include_time=False)

            elif field == "eth_fw":
                val = self.smbus_telem_info[board_num]["ETH_FW_VERSION"]
                if val is None:
                    fw_versions[field] = "N/A"
                elif self.is_wormhole(board_num):
                    fw_versions[field] = hex_to_semver_eth_wh(int(val, 16))
                else:
                    fw_versions[field] = hex_to_semver_eth(int(val, 16))
            elif field == "dm_bl_fw":
                if self.use_umd:
                    # The tag has different value for old WH telemetry and new telemetry.
                    if "M3_BL_FW_VERSION" in self.smbus_telem_info[board_num]:
                        val = self.smbus_telem_info[board_num]["M3_BL_FW_VERSION"]
                    if "DM_BL_FW_VERSION" in self.smbus_telem_info[board_num]:
                        val = self.smbus_telem_info[board_num]["DM_BL_FW_VERSION"]
                else:
                    val = self.smbus_telem_info[board_num]["M3_BL_FW_VERSION"]
                if val is None:
                    fw_versions[field] = "N/A"
                else:
                    fw_versions[field] = hex_to_semver_m3_fw(int(val, 16))
            elif field == "dm_app_fw":
                if self.use_umd:
                    # The tag has different value for WH and BH
                    if "M3_APP_FW_VERSION" in self.smbus_telem_info[board_num]:
                        val = self.smbus_telem_info[board_num]["M3_APP_FW_VERSION"]
                    if "DM_APP_FW_VERSION" in self.smbus_telem_info[board_num]:
                        val = self.smbus_telem_info[board_num]["DM_APP_FW_VERSION"]
                else:
                    val = self.smbus_telem_info[board_num]["M3_APP_FW_VERSION"]
                if val is None:
                    fw_versions[field] = "N/A"
                else:
                    fw_versions[field] = hex_to_semver_m3_fw(int(val, 16))
            elif field == "tt_flash_version":
                if "TT_FLASH_VERSION" in self.smbus_telem_info[board_num]:
                    val = self.smbus_telem_info[board_num]["TT_FLASH_VERSION"]
                if val is None:
                    fw_versions[field] = "N/A"
                # See below- Galaxy systems manually get an N/A tt_flash_version
                elif get_board_type(self.get_board_id(board_num)) == "wh_4u":
                    fw_versions[field] = "N/A"
                else:
                    fw_versions[field] = hex_to_semver_m3_fw(int(val, 16))
            elif field == "fw_bundle_version":
                val = get_fw_bundle_version(self.smbus_telem_info[board_num])
                if (
                    get_board_type(self.get_board_id(board_num)) == "wh_4u"
                    and val == "0xffffffff"
                ):
                    # WARNING: Dirty dirty hack!
                    # See Issue #72 https://github.com/tenstorrent/tt-smi/issues/72
                    # Due to a FW flashing bug, Galaxy systems do not have the FW_BUNDLE_VERSION
                    # field set. The value ends up in TT_FLASH_VERSION, so we need to go get it.
                    val = self.smbus_telem_info[board_num]["TT_FLASH_VERSION"]
                    fw_versions[field] = hex_to_semver_m3_fw(int(val, 16))
                if val is None:
                    fw_versions[field] = "N/A"
                else:
                    fw_versions[field] = hex_to_semver_m3_fw(int(val, 16))
            elif field == "gddr_fw":
                if self.use_umd:
                    val = self.smbus_telem_info[board_num].get("GDDR_FW_VERSION")
                else:
                    # TODO: Need to add GDDR FW tag into luwen
                    val = None
                if val is None or not self.is_blackhole(board_num):
                    fw_versions[field] = "N/A"
                else:
                    fw_versions[field] = hex_to_semver_gddr_fw(int(val, 16))
        return fw_versions
