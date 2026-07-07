# SPDX-FileCopyrightText: © 2026 Tenstorrent Inc.
# SPDX-License-Identifier: Apache-2.0

"""Textual TUI: layout, tables, telemetry worker, and Rich formatting for TT-SMI."""

import time
from importlib.resources import files
from importlib.metadata import version
from typing import Dict, List, Optional, Tuple, Union

from rich.style import Style
from rich.text import Text
from textual.app import App, ComposeResult
from textual.css.query import NoMatches
from textual.widgets import Footer, ProgressBar, Static, TabbedContent
from textual.containers import Container, Vertical
from textual.worker import get_current_worker, Worker, WorkerState

from tt_tools_common.ui_common.themes import create_tt_tools_theme
from tt_tools_common.ui_common.widgets import (
    TTHeader,
    TTDataTable,
    TTHostCompatibilityMenu,
    TTHelperMenuBox,
)
from tt_tools_common.utils_common.system_utils import get_host_compatibility_info

from . import constants
from .backend import TTSMIBackend
from .latest_releases import (
    DEFAULT_MAX_ATTEMPTS,
    RELEASE_SPECS,
    ReleaseSpec,
    fetch_all,
    get_installed_all,
    version_tuple,
)
from .utils import hex_to_semver_eth, hex_to_semver_m3_fw


class LatestReleasesBox(Container):
    """Sidebar box showing the latest published version of each tt-stack package.

    Renders a ProgressBar while fetches are in-flight, then swaps to a list of
    versions. If every fetch fails (e.g. no internet), the list stays empty
    and the box just shows its border + title.
    """

    def __init__(self, id: str, title: str) -> None:
        super().__init__(id=id)
        self._base_title = title
        self.border_title = title
        self._versions: Dict[str, Optional[str]] = {}
        # Specs in this dict are "checkable" (we know how to ask the host);
        # a None value means "not installed". A status glyph only renders
        # when an installed version is known.
        self._installed: Dict[str, Optional[str]] = {}
        self._key_width = max(len(s.name) for s in RELEASE_SPECS) + 1

    def compose(self) -> ComposeResult:
        yield ProgressBar(
            total=len(RELEASE_SPECS), show_eta=False, id="latest_releases_progress"
        )
        yield Static("", id="latest_releases_list")

    def on_mount(self) -> None:
        self.query_one("#latest_releases_list", Static).display = False

    def record(self, spec: ReleaseSpec, version: Optional[str]) -> None:
        """One fetch completed. Stash the result and advance the progress bar."""
        self._versions[spec.name] = version
        self.query_one("#latest_releases_progress", ProgressBar).advance(1)

    def set_installed(self, installed: Dict[str, Optional[str]]) -> None:
        """Record installed-version lookups for the checkable specs."""
        self._installed = installed

    def set_attempt(self, attempt: int, max_attempts: int) -> None:
        """Surface a retry indicator in the border title (e.g. 'attempt 2/3')."""
        self.border_title = f"{self._base_title} (attempt {attempt}/{max_attempts})"
        self.styles.border_title_color = "yellow"
        # border_title is reactive and calls refresh(), but the Container
        # chrome doesn't always redraw on its own — force it.
        self.refresh()

    def finalize(self) -> None:
        """All fetches done. Swap the progress bar out for the version list."""
        self.border_title = self._base_title
        self.styles.border_title_color = None
        self.query_one("#latest_releases_progress", ProgressBar).display = False
        list_widget = self.query_one("#latest_releases_list", Static)
        if any(v is not None for v in self._versions.values()):
            list_widget.update(self._render_versions())
        else:
            list_widget.update(self._render_error())
        list_widget.display = True
        self.refresh()

    def _row_status(
        self, spec_name: str, latest: Optional[str]
    ) -> Tuple[str, Optional[Style], str, Optional[Style]]:
        """Return (glyph, glyph_style, value_text, value_style) for one row.

        The value column shows "installed → latest" for the outdated case so
        users see exactly what bump is available; everything else just shows
        the latest version (or "—" if it's unknown).
        """
        muted = Style(color="grey50")
        installed = self._installed.get(spec_name)
        if installed is None:
            # Not checkable on this host, or not installed: no status glyph.
            if latest is None:
                return ("", None, "—", muted)
            return ("", None, latest, None)

        if latest is None:
            # We have installed but couldn't fetch latest.
            return ("", None, "—", muted)

        if version_tuple(installed) >= version_tuple(latest):
            return ("✓", Style(color="green"), latest, None)

        return ("↑", Style(color="yellow"), f"{installed} → {latest}", Style(color="yellow"))

    def _render_versions(self) -> Text:
        text = Text()
        for spec in RELEASE_SPECS:
            ver = self._versions.get(spec.name)
            glyph, glyph_style, val_text, val_style = self._row_status(spec.name, ver)
            leader = Text(f"{glyph} " if glyph else "  ", style=glyph_style)
            key = Text(
                f"{spec.name.ljust(self._key_width)}: ",
                style=Style(color="#ffd10a", bold=True),
            )
            val = Text(f"{val_text}\n", style=val_style)
            text.append_text(leader).append_text(key).append_text(val)
        text.rstrip()
        return text

    def _render_error(self) -> Text:
        return Text(
            "Failed to fetch latest releases.\nCheck your network connection.",
            style=Style(color="red"),
        )

TextualKeyBindings = List[Tuple[str, str, str]]


class TTSMI(App):
    """A textual app for TT-SMI"""

    BINDINGS = [
        ("q, Q", "quit", "Quit"),
        ("h, H", "help", "Help"),
        ("d, D", "app.toggle_dark", "Toggle dark mode"),
        ("c, C", "toggle_compact", "Toggle sidebar"),
        ("1", "tab_one", "Device info tab"),
        ("2", "tab_two", "Telemetry tab"),
        ("3", "tab_three", "Firmware tab"),
        ("4", "tab_four", "Processes tab"),
    ]

    try:
        common_style_file_path = files("tt_tools_common.ui_common").joinpath(
            "common_style.css"
        )
    except Exception:
        raise Exception(
            "Cannot find common_style.css file, please make sure tt_tools_common lib is installed correctly."
        )

    CSS_PATH = [f"{common_style_file_path}", "tt_smi_style.css"]

    def __init__(
        self,
        result_filename: str = None,
        app_name: str = "TT-SMI",
        app_version: str = version("tt_smi"),
        key_bindings: TextualKeyBindings = [],
        backend: TTSMIBackend = None,
        snapshot: bool = False,
        show_sidebar: bool = True,
        offline: bool = False,
    ) -> None:
        """Initialize the textual app."""
        super().__init__()
        self.app_name = app_name
        self.app_version = app_version
        self.backend = backend
        self.snapshot = snapshot
        self.show_sidebar = show_sidebar
        self.offline = offline
        self.result_filename = result_filename
        self.text_theme = create_tt_tools_theme()
        self.telem_worker = None
        self.process_worker = None

        if key_bindings:
            self.BINDINGS += key_bindings

    def compose(self) -> ComposeResult:
        """Create child widgets for the app."""

        yield TTHeader(self.app_name, self.app_version)
        with Container(id="app_grid"):
            with Vertical(id="left_col"):
                host_data = get_host_compatibility_info()
                host_info_widget = TTHostCompatibilityMenu(
                    id="host_info",
                    title="Host Info",
                    data=host_data,
                )
                # TTHostCompatibilityMenu is a Container that draws via render()
                # rather than child widgets, so textual's height: auto collapses
                # to 0 and the default height: 1fr stretches it to fill the
                # column. Pin to actual content height: one row per entry (two
                # for tuple-valued entries that show a recommendation), plus 2
                # border + 2 padding.
                content_rows = sum(2 if isinstance(v, tuple) else 1 for v in host_data.values())
                host_info_widget.styles.height = content_rows + 4
                yield host_info_widget
                if not self.offline:
                    yield LatestReleasesBox(
                        id="latest_releases",
                        title="Latest Releases",
                    )
            with TabbedContent(
                "Information (1)", "Telemetry (2)", "FW Version (3)", "Processes (4)", id="tab_container"
            ):
                yield TTDataTable(
                    title="Device Information",
                    id="tt_smi_device_info",
                    header=constants.INFO_TABLE_HEADER,
                    header_height=2,
                )
                yield TTDataTable(
                    title="Device Telemetry",
                    id="tt_smi_telem",
                    header=constants.TELEMETRY_TABLE_HEADER,
                    header_height=2,
                )
                yield TTDataTable(
                    title="Device Firmware Versions",
                    id="tt_smi_firmware",
                    header=constants.FIRMWARES_TABLE_HEADER,
                    header_height=2,
                )
                yield TTDataTable(
                    title="Device Processes",
                    id="tt_smi_processes",
                    header=constants.PROCESSES_TABLE_HEADER,
                    header_height=2,
                )
        yield Footer()

    def on_mount(self) -> None:
        """Event handler called when widget is added to the app."""
        smi_table = self.get_widget_by_id(id="tt_smi_device_info")
        smi_table.dt.cursor_type = "none"
        smi_table.dt.add_rows(self.format_device_info_rows())

        telem_table = self.get_widget_by_id(id="tt_smi_telem")
        telem_table.dt.cursor_type = "none"
        telem_table.dt.add_rows(self.format_telemetry_rows())

        firmware_table = self.get_widget_by_id(id="tt_smi_firmware")
        firmware_table.dt.cursor_type = "none"
        firmware_table.dt.add_rows(self.format_firmware_rows())

        proc_table = self.get_widget_by_id(id="tt_smi_processes")
        proc_table.dt.cursor_type = "none"
        self.backend.update_processes()
        proc_table.dt.add_rows(self.format_process_rows())

        left_sidebar = self.query_one("#left_col")
        left_sidebar.display = self.show_sidebar

        if not self.offline:
            self.run_worker(
                self.fetch_latest_releases,
                thread=True,
                exit_on_error=False,
                name="latest_releases_thread",
            )

    def fetch_latest_releases(self) -> None:
        """Worker: fetch latest GitHub release tags and push them to the box."""
        try:
            box = self.query_one("#latest_releases", LatestReleasesBox)
        except NoMatches:
            return

        def on_done(spec: ReleaseSpec, version: Optional[str]) -> None:
            self.call_from_thread(box.record, spec, version)

        def on_attempt(attempt: int) -> None:
            self.call_from_thread(box.set_attempt, attempt, DEFAULT_MAX_ATTEMPTS)

        fetch_all(timeout=5.0, on_done=on_done, on_attempt=on_attempt)
        installed = get_installed_all()
        self.call_from_thread(box.set_installed, installed)
        self.call_from_thread(box.finalize)

    def update_telem_table(self) -> None:
        """Update telemetry table"""
        try:
            telem_table = self.get_widget_by_id(id="tt_smi_telem")
            self.backend.update_telem()
            rows = self.format_telemetry_rows()
            telem_table.update_data(rows)
        # When we bring up the help menu, the telem table is no longer visible,
        # but the thread keeps running, so we need to ignore that exception.
        except NoMatches:
            pass

    def update_process_table(self) -> None:
        """Update process table"""
        try:
            proc_table = self.get_widget_by_id(id="tt_smi_processes")
            self.backend.update_processes()
            rows = self.format_process_rows()
            # TTDataTable.update_data doesn't shrink the table when rows go
            # away, so exited processes would stick around. Clear and re-add.
            proc_table.dt.clear()
            proc_table.dt.add_rows(rows)
        except NoMatches:
            pass

    def format_process_rows(self) -> List[List[Text]]:
        """Format process rows"""
        all_rows = []
        for proc in self.backend.device_processes:
            row = [
                Text(f"{proc['pid']}", style=self.text_theme["yellow_bold"], justify="center"),
                Text(f"{proc['user']}", style=self.text_theme["text_green"], justify="left"),
                Text(f"{proc['device']}", style=self.text_theme["text_green"], justify="center"),
                Text(f"{proc['cmdline']}", style=self.text_theme["text_green"], justify="left"),
            ]
            all_rows.append(row)
        if not all_rows:
            ncols = len(constants.PROCESSES_TABLE_HEADER)
            empty = [Text("", justify="center") for _ in range(ncols)]
            empty[0] = Text("No processes found", style=self.text_theme["gray"], justify="center")
            all_rows.append(empty)
        return all_rows

    def format_firmware_rows(self):
        """Format firmware rows"""
        all_rows = []
        for i in self.backend.devices:
            rows = [Text(f"{i}", style=self.text_theme["yellow_bold"], justify="center")]
            for fw in constants.FW_LIST_GUI:
                val = self.backend.firmware_infos[i][fw]
                if val == "N/A" or val == hex_to_semver_m3_fw(0) or val == hex_to_semver_eth(0):
                    rows.append(
                        Text(f"{val}", style=self.text_theme["gray"], justify="center")
                    )
                else:
                    rows.append(
                        Text(f"{val}", style=self.text_theme["text_green"], justify="center")
                    )
            all_rows.append(rows)
        return all_rows

    def format_telemetry_rows(self) -> List[List[Text]]:
        """Format telemetry rows"""
        all_rows = []
        for board_num in self.backend.devices:
            device_row = [
                Text(f"{board_num}", style=self.text_theme["yellow_bold"], justify="center")
            ]

            for telem in constants.TELEM_LIST:
                val = self.backend.device_telemetrys[board_num][telem]
                if telem == "heartbeat":
                    device_row.append(
                        Text(
                            f"{self.get_heartbeat_spinner(val)}",
                            style=self.text_theme["text_green"],
                            justify="center",
                        )
                    )
                elif telem == "voltage":
                    vdd_max = self.backend.chip_limits[board_num]["vdd_max"]
                    device_row.append(
                        Text(
                            f"{val}",
                            style=self.text_theme["text_green"] if float(val) < float(vdd_max) else self.text_theme["attention"],
                            justify="center",
                        )
                        + Text(
                            f"/ {vdd_max}" if vdd_max else "/ ---",
                            style=self.text_theme["yellow_bold"] if vdd_max else self.text_theme["gray"],
                            justify="center",
                        )
                    )
                elif telem == "current":
                    max_current = self.backend.chip_limits[board_num]["tdc_limit"]
                    device_row.append(
                        Text(
                            f"{val}",
                            style=self.text_theme["text_green"] if float(val) < float(max_current) else self.text_theme["attention"],
                            justify="center",
                        )
                        + Text(
                            f"/ {max_current}" if max_current else "/ ---",
                            style=self.text_theme["yellow_bold"] if max_current else self.text_theme["gray"],
                            justify="center",
                        )
                    )
                elif telem == "power":
                    max_power = self.backend.chip_limits[board_num]["tdp_limit"]
                    device_row.append(
                        Text(
                            f"{val}",
                            style=self.text_theme["text_green"] if float(val) < float(max_power) else self.text_theme["attention"],
                            justify="center",
                        )
                        + Text(
                            f"/ {max_power}" if max_power else "/ ---",
                            style=self.text_theme["yellow_bold"] if max_power else self.text_theme["gray"],
                            justify="center",
                        )
                    )
                elif telem == "aiclk":
                    asic_fmax = self.backend.chip_limits[board_num]["asic_fmax"]
                    device_row.append(
                        Text(
                            f"{val}",
                            style=self.text_theme["text_green"] if float(val) < float(asic_fmax) else self.text_theme["attention"],
                            justify="center",
                        )
                        + Text(
                            f"/ {asic_fmax}" if asic_fmax else "/ ---",
                            style=self.text_theme["yellow_bold"] if asic_fmax else self.text_theme["gray"],
                            justify="center",
                        )
                    )
                elif telem == "asic_temperature":
                    max_temp = self.backend.chip_limits[board_num]["thm_limit"]
                    device_row.append(
                        Text(
                            f"{val}",
                            style=self.text_theme["text_green"] if float(val) < float(max_temp) else self.text_theme["attention"],
                            justify="center",
                        )
                        + Text(
                            f"/ {max_temp}" if max_temp else "/ ---",
                            style=self.text_theme["yellow_bold"] if max_temp else self.text_theme["gray"],
                            justify="center",
                        )
                    )
                elif telem == "fan_speed":
                    max_rpm = self.backend.chip_limits[board_num]["fan_rpm_limit"]
                    device_row.append(
                        Text(
                            f"{val}" if int(val) > 0 else "N/A",
                            style=self.text_theme["text_green"] if int(val) > 0 else self.text_theme["gray"],
                            justify="center",
                        )
                        + Text(
                            f"/ {max_rpm}" if max_rpm else "/ ---",
                            style=self.text_theme["yellow_bold"] if max_rpm else self.text_theme["gray"],
                            justify="center",
                        )
                    )
                else:
                    device_row.append(
                        Text(f"{val}", style=self.text_theme["text_green"], justify="center")
                    )
            all_rows.append(device_row)
        return all_rows

    def get_heartbeat_spinner(self, input_secs: Union[int, str]) -> str:
        """
        Get a symbol depending on the heartbeat input, which changes every ~.5 seconds,
        so we expect two new symbols every second. Approximates a spinner.
        """
        symbols = [
            "●∙∙",
            "∙●∙",
            "∙∙●",
            "∙●∙",
        ]
        cur_symbol = int(input_secs) % len(symbols)
        return symbols[cur_symbol]

    def format_device_info_rows(self):
        """Format device info rows"""
        all_rows = []
        for i, device in self.backend.devices.items():
            rows = [Text(f"{i}", style=self.text_theme["yellow_bold"], justify="center")]
            for info in constants.DEV_INFO_LIST:
                val = self.backend.device_infos[i][info]
                if info == "board_type":
                    if val == "n300":
                        if device.is_remote():
                            rows.append(
                                Text(
                                    f"{val}",
                                    style=self.text_theme["text_green"],
                                    justify="center",
                                )
                                + Text(
                                    " R",
                                    style=self.text_theme["yellow_bold"],
                                    justify="center",
                                )
                            )
                        else:
                            rows.append(
                                Text(
                                    f"{val}",
                                    style=self.text_theme["text_green"],
                                    justify="center",
                                )
                                + Text(
                                    f" L",
                                    style=self.text_theme["yellow_bold"],
                                    justify="center",
                                )
                            )
                    else:
                        rows.append(
                            Text(
                                f"{val}",
                                style=self.text_theme["text_green"],
                                justify="center",
                            )
                        )
                elif info == "pcie_width":
                    max_link_width = self.backend.pci_properties[i]["max_link_width"]
                    if device.is_remote():
                        rows.append(
                            Text(
                                f"N/A",
                                style=self.text_theme["gray"],
                                justify="center",
                            )
                        )
                    else:
                        if val < max_link_width:
                            rows.append(
                                Text(
                                    f"x{val}",
                                    style=self.text_theme["attention"],
                                    justify="center",
                                )
                                + Text(
                                    f" / x{max_link_width}",
                                    style=self.text_theme["yellow_bold"],
                                    justify="center",
                                )
                            )
                        else:
                            rows.append(
                                Text(
                                    f"x{val}",
                                    style=self.text_theme["text_green"],
                                    justify="center",
                                )
                                + Text(
                                    f" / x{max_link_width}",
                                    style=self.text_theme["yellow_bold"],
                                    justify="center",
                                )
                            )
                elif info == "pcie_speed":
                    max_link_speed = self.backend.pci_properties[i]["max_link_speed"]
                    if device.is_remote():
                        rows.append(
                            Text(
                                f"N/A",
                                style=self.text_theme["gray"],
                                justify="center",
                            )
                        )
                    else:
                        if max_link_speed == "N/A":
                            rows.append(
                                Text(
                                    f"Gen{val}",
                                    style=self.text_theme["attention"],
                                    justify="center",
                                )
                                + Text(
                                    f" / N/A",
                                    style=self.text_theme["gray"],
                                    justify="center",
                                )
                            )
                        elif val == "N/A":
                            rows.append(
                                Text(
                                    f"N/A",
                                    style=self.text_theme["red_bold"],
                                    justify="center",
                                )
                                + Text(
                                    f" / Gen{max_link_speed}",
                                    style=self.text_theme["yellow_bold"],
                                    justify="center",
                                )
                            )
                        elif float(val) < float(max_link_speed):
                            rows.append(
                                Text(
                                    f"Gen{val}",
                                    style=self.text_theme["attention"],
                                    justify="center",
                                )
                                + Text(
                                    f" / Gen{max_link_speed}",
                                    style=self.text_theme["yellow_bold"],
                                    justify="center",
                                )
                            )
                        else:
                            rows.append(
                                Text(
                                    f"Gen{val}",
                                    style=self.text_theme["text_green"],
                                    justify="center",
                                )
                                + Text(
                                    f" / Gen{max_link_speed}",
                                    style=self.text_theme["yellow_bold"],
                                    justify="center",
                                )
                            )
                elif info == "dram_status":
                    if val:
                        rows.append(
                            Text(
                                "Y",
                                style=self.text_theme["text_green"],
                                justify="center",
                            )
                        )
                    else:
                        rows.append(
                            Text(
                                "N", style=self.text_theme["attention"], justify="center"
                            )
                        )
                elif info == "dram_speed":
                    if val:
                        rows.append(
                            Text(
                                f"{val}",
                                style=self.text_theme["text_green"],
                                justify="center",
                            )
                        )
                    else:
                        rows.append(
                            Text(
                                "N/A",
                                style=self.text_theme["red_bold"],
                                justify="center",
                            )
                        )
                else:
                    if val == "N/A":
                        rows.append(
                            Text(f"{val}", style=self.text_theme["gray"], justify="center")
                        )
                    else:
                        rows.append(
                            Text(
                                f"{val}",
                                style=self.text_theme["text_green"],
                                justify="center",
                            )
                        )
            all_rows.append(rows)
        return all_rows

    def action_toggle_compact(self) -> None:
        """An action to toggle compact mode."""
        left_sidebar = self.query_one("#left_col")
        left_sidebar.display = not left_sidebar.display

    async def action_quit(self) -> None:
        """An [action](/guide/actions) to quit the app as soon as possible."""
        exit_message = Text(f"Exiting TT-SMI.", style="yellow")
        if self.snapshot:
            log_name = self.backend.save_logs(self.result_filename)
            exit_message = exit_message + Text(
                f"\nSaved tt-smi log to: {log_name}", style="purple"
            )
        if self.telem_worker is not None:
            self.telem_worker.cancel()
        self.exit(message=exit_message)

    def action_tab_one(self) -> None:
        """Switch to read-write tab"""
        self.query_one(TabbedContent).active = "tab-1"

    def action_tab_two(self) -> None:
        """Switch to read-only tab"""
        # Note that this is set by Textual if we click 2,
        # but not press 2; this function is necessary to synchronize behavior
        # between those two ways of accessing the tab.
        self.query_one(TabbedContent).active = "tab-2"

    def action_tab_three(self) -> None:
        """Switch to read-only tab"""
        self.query_one(TabbedContent).active = "tab-3"

    def action_tab_four(self) -> None:
        """Switch to processes tab"""
        self.query_one(TabbedContent).active = "tab-4"

    def action_help(self) -> None:
        """Pop up the help menu"""
        tt_confirm_box = TTHelperMenuBox(
            text=constants.HELP_MENU_MARKDOWN, theme=self.text_theme
        )
        self.push_screen(tt_confirm_box)

    def update_telem(self) -> None:
        """Worker function that continuously updates telemetry"""
        worker = get_current_worker()
        while not worker.is_cancelled:
            self.call_from_thread(self.update_telem_table)
            time.sleep(constants.GUI_INTERVAL_TIME)

    def dispatch_telem_thread(self) -> None:
        """Start the telemetry update thread if not already running"""
        if self.telem_worker is None or self.telem_worker.is_finished:
            self.telem_worker = self.run_worker(
                self.update_telem,
                thread=True,
                exit_on_error=False, # tt-smi exits on error, but in worker state change handler
                name="telem_thread",
            )

    def update_processes(self) -> None:
        """Worker function that continuously updates the process list"""
        worker = get_current_worker()
        while not worker.is_cancelled:
            self.call_from_thread(self.update_process_table)
            time.sleep(constants.GUI_INTERVAL_TIME)

    def dispatch_process_thread(self) -> None:
        """Start the process update thread if not already running"""
        if self.process_worker is None or self.process_worker.is_finished:
            self.process_worker = self.run_worker(
                self.update_processes,
                thread=True,
                exit_on_error=False,
                name="process_thread",
            )

    def on_tabbed_content_tab_activated(self, event: TabbedContent.TabActivated) -> None:
        """This function runs every time a tab is activated"""
        tab_id = self.query_one(TabbedContent).active

        if tab_id == "tab-2":
            self.dispatch_telem_thread()
        elif tab_id == "tab-4":
            self.dispatch_process_thread()

    def on_worker_state_changed(self, event: Worker.StateChanged) -> None:
        """Handle worker state change. Here we just use it to catch worker errors."""
        if event.state != WorkerState.ERROR:
            return
        if event.worker.name == "telem_thread":
            error = event.worker.error
            exit_message = Text(f"Error when attempting to fetch telemetry: {error}", style="red")
            self.exit(message=exit_message)
        elif event.worker.name == "process_thread":
            error = event.worker.error
            exit_message = Text(f"Error when attempting to fetch processes: {error}", style="red")
            self.exit(message=exit_message)
