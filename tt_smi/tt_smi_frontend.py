# SPDX-FileCopyrightText: © 2026 Tenstorrent Inc.
# SPDX-License-Identifier: Apache-2.0

"""Textual TUI: layout, tables, telemetry worker, and Rich formatting for TT-SMI."""

import time
from importlib.resources import files
from importlib.metadata import version
from typing import List, Tuple, Union

from rich.text import Text
from textual.app import App, ComposeResult
from textual.css.query import NoMatches
from textual.widgets import Footer, TabbedContent
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
from .tt_smi_backend import TTSMIBackend
from .tt_smi_utils import hex_to_semver_eth, hex_to_semver_m3_fw

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
    ) -> None:
        """Initialize the textual app."""
        super().__init__()
        self.app_name = app_name
        self.app_version = app_version
        self.backend = backend
        self.snapshot = snapshot
        self.show_sidebar = show_sidebar
        self.result_filename = result_filename
        self.text_theme = create_tt_tools_theme()
        self.telem_worker = None

        if key_bindings:
            self.BINDINGS += key_bindings

    def compose(self) -> ComposeResult:
        """Create child widgets for the app."""

        yield TTHeader(self.app_name, self.app_version)
        with Container(id="app_grid"):
            with Vertical(id="left_col"):
                yield TTHostCompatibilityMenu(
                    id="host_info",
                    title="Host Info",
                    data=get_host_compatibility_info(),
                )
            with TabbedContent(
                "Information (1)", "Telemetry (2)", "FW Version (3)", id="tab_container"
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

        left_sidebar = self.query_one("#left_col")
        left_sidebar.display = self.show_sidebar

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
                    device_row.append(
                        Text(
                            f"{val}" if 0 < float(val) <= 100 else "N/A",
                            style=self.text_theme["text_green"] if 0 < float(val) <= 100 else self.text_theme["gray"],
                            justify="center",
                        )
                        + Text(
                            f"/ 100",
                            style=self.text_theme["yellow_bold"],
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

    def on_tabbed_content_tab_activated(self, event: TabbedContent.TabActivated) -> None:
        """This function runs every time a tab is activated"""
        tab_id = self.query_one(TabbedContent).active

        if tab_id == "tab-2":  # Telemetry tab
            # Dispatch the telemetry thread
            self.dispatch_telem_thread()

    def on_worker_state_changed(self, event: Worker.StateChanged) -> None:
        """Handle worker state change. Here we just use it to catch telem_thread errors."""
        if event.worker.name == "telem_thread" and event.state == WorkerState.ERROR:
            error = event.worker.error
            exit_message = Text(f"Error when attempting to fetch telemetry: {error}", style="red")
            self.exit(message=exit_message)
