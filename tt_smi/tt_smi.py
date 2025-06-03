# SPDX-FileCopyrightText: © 2023 Tenstorrent Inc.
# SPDX-License-Identifier: Apache-2.0

"""
Tenstorrent System Management Interface (TT-SMI) is a command line utility
to interact with all Tenstorrent devices on host.

The main objective of TT-SMI is to provide a simple and easy-to-use interface
to display devices, device telemetry, and system information.

TT-SMI is also used to issue board-level resets.
"""
import os
import sys
import time
import signal
import argparse
import threading
from rich.text import Text
from tt_smi import constants
from typing import List, Tuple, Union
from importlib.resources import files
from importlib.metadata import version
from pyluwen import pci_scan
from textual.app import App, ComposeResult
from textual.css.query import NoMatches
from textual.widgets import Footer, TabbedContent
from textual.containers import Container, Vertical
from tt_tools_common.ui_common.themes import CMD_LINE_COLOR, create_tt_tools_theme
from tt_tools_common.reset_common.reset_utils import (
    ResetType,
    parse_reset_input,
)
from tt_smi.tt_smi_backend import (
    TTSMIBackend,
    pci_board_reset,
    glx_6u_trays_reset
)
from tt_tools_common.utils_common.tools_utils import (
    hex_to_semver_m3_fw,
    detect_chips_with_callback,
)
from tt_tools_common.utils_common.system_utils import (
    get_driver_version,
    get_host_compatibility_info,
)
from tt_tools_common.ui_common.widgets import (
    TTHeader,
    TTDataTable,
    TTHostCompatibilityMenu,
    TTHelperMenuBox,
)

# Global variables
TextualKeyBindings = List[Tuple[str, str, str]]
INTERRUPT_RECEIVED = False
TELEM_THREADS = []
RUNNING_TELEM_THREAD = False


def interrupt_handler(sig, action) -> None:
    """Handle interrupts to exit processes gracefully"""
    global INTERRUPT_RECEIVED
    INTERRUPT_RECEIVED = True


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
    except:
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
        except NoMatches as e:
            pass

    def format_firmware_rows(self):
        """Format firmware rows"""
        all_rows = []
        for i, _ in enumerate(self.backend.devices):
            rows = [Text(f"{i}", style=self.text_theme["yellow_bold"], justify="center")]
            for fw in constants.FW_LIST:
                val = self.backend.firmware_infos[i][fw]
                if val == "N/A":
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
        for board_num, _ in enumerate(self.backend.devices):
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
        for i, device in enumerate(self.backend.devices):
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
        global INTERRUPT_RECEIVED, TELEM_THREADS
        exit_message = Text(f"Exiting TT-SMI.", style="yellow")
        if self.snapshot:
            log_name = self.backend.save_logs(self.result_filename)
            exit_message = exit_message + Text(
                f"\nSaved tt-smi log to: {log_name}", style="purple"
            )
        INTERRUPT_RECEIVED = True
        for thread in TELEM_THREADS:
            thread.join(timeout=0.1)
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

    async def dispatch_telem_thread(self) -> None:
        global INTERRUPT_RECEIVED, TELEM_THREADS, RUNNING_TELEM_THREAD
        if not RUNNING_TELEM_THREAD:
            signal.signal(signal.SIGTERM, interrupt_handler)
            signal.signal(signal.SIGINT, interrupt_handler)

            def update_telem():
                global INTERRUPT_RECEIVED
                while not INTERRUPT_RECEIVED:
                    self.update_telem_table()
                    time.sleep(constants.GUI_INTERVAL_TIME)

            thread = threading.Thread(target=update_telem, name="telem_thread")
            thread.setDaemon(True)
            thread.start()
            TELEM_THREADS.append(thread)
            RUNNING_TELEM_THREAD = True

    async def on_tabbed_content_tab_activated(self, event: TabbedContent.TabActivated) -> None:
        """This function runs every time a tab is activated"""
        tab_id = self.query_one(TabbedContent).active

        if tab_id == "tab-2":  # Telemetry tab
            # Dispatch the telemetry thread
            await self.dispatch_telem_thread()

def parse_args():
    """Parse user args"""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "-l",
        "--local",
        default=False,
        action="store_true",
        help="Run on local chips (Wormhole only)",
    )
    parser.add_argument(
        "-v",
        "--version",
        action="version",
        version=version("tt_smi"),
    )
    parser.add_argument(
        "-s",
        "--snapshot",
        default=False,
        action="store_true",
        help="Dump snapshot of current tt-smi information to STDOUT",
    )
    parser.add_argument(
        "-ls",
        "--list",
        default=False,
        action="store_true",
        help="List boards that are available on host and quit",
    )
    parser.add_argument(
        "-f",
        "--filename",
        metavar="snapshot filename",
        nargs="?",
        const=None,  # If -f is set with no filename
        default=False,  # If -f is not set
        help="Write snapshot to a file. Default: ~/tt_smi/<timestamp>_snapshot.json",
        dest="filename",
    )
    parser.add_argument(
        "-c",
        "--compact",
        default=False,
        action="store_true",
        help="Run in compact mode, hiding the sidebar and other static elements",
    )
    parser.add_argument(
        "-r",
        "--reset",
        metavar="0,1",
        default=None,
        nargs="*",
        help=(
            "Provide a list of PCI indices. "
            "Find PCI index of board using the -ls option. "
            "If no indices are provided, all devices will be reset"
        ),
        dest="reset",
    )
    parser.add_argument(
        "--snapshot_no_tty",
        default=False,
        action="store_true",
        help="Force no-tty behavior in the snapshot to stdout",
    )
    parser.add_argument(
        "-glx_reset",
        "--galaxy_6u_trays_reset",
        default=False,
        action="store_true",
        help="Reset all the ASICs on the galaxy host",
        dest="glx_reset",
    )
    parser.add_argument(
        "-glx_reset_auto",
        "--galaxy_6u_trays_reset_auto",
        default=False,
        action="store_true",
        help="Reset all ASICs on the galaxy host, but do auto retries up to 3 times if reset fails",
        dest="glx_reset_auto",
    )
    parser.add_argument(
        "-glx_reset_tray",
        "--galaxy_6u_reset_tray",
        choices=["1", "2", "3", "4",],
        default=None,
        help="Reset a specific tray on the galaxy",
        dest="glx_reset_tray",
    )
    parser.add_argument(
        "-glx_list_tray_to_device",
        "--galaxy_6u_list_tray_to_device",
        default=False,
        action="store_true",
        help="List the mapping of devices to trays on the galaxy",
        dest="glx_list_tray_to_device",
    )
    parser.add_argument(
        "--no_reinit",
        default=False,
        action="store_true",
        help="Don't detect devices post reset",
    )
    args = parser.parse_args()
    return args


def tt_smi_main(backend: TTSMIBackend, args):
    """
    Given a backend, handle all user args and run TT-SMI frontend.

    Args:
        backend (): Can be overloaded if using older fw version.
    Returns:
        None: None
    """
    global INTERRUPT_RECEIVED
    INTERRUPT_RECEIVED = False

    signal.signal(signal.SIGINT, interrupt_handler)
    signal.signal(signal.SIGTERM, interrupt_handler)

    if args.list:
        backend.print_all_available_devices()
        sys.exit(0)
    if args.glx_list_tray_to_device:
        check_is_galaxy(backend, "-glx_list_tray_to_device")
        if is_vm():
            print(
                CMD_LINE_COLOR.RED,
                "This is in a VM, so we cannot list the tray and device mapping.",
                CMD_LINE_COLOR.ENDC
            )
            sys.exit(1)
        backend.print_tray_and_device_mapping()
        sys.exit(0)
    if args.snapshot or args.filename == "-":  # If we pass '-s' or '-f -"
        backend.print_logs_to_stdout(pretty=backend.pretty_output)
        sys.exit(0)
    if args.filename is not False:  # The default is None, which is falsy
        file = backend.save_logs_to_file(args.filename)
        print(
            CMD_LINE_COLOR.PURPLE,
            f"Saved tt-smi log to: {file}",
            CMD_LINE_COLOR.ENDC,
        )
        sys.exit(0)
    tt_smi_app = TTSMI(
        backend=backend,
        snapshot=args.snapshot,
        result_filename=args.filename,
        show_sidebar=not args.compact,
    )
    tt_smi_app.run()

def check_is_galaxy(backend: TTSMIBackend, user_arg: str):
    """Check if the board is a Galaxy board"""
    if len(backend.device_infos) < 1:
        print(
            CMD_LINE_COLOR.RED,
            f"No devices detected.",
            CMD_LINE_COLOR.ENDC
        )
        sys.exit(1)

    if backend.device_infos[0]["board_type"] not in constants.GLX_BOARD_TYPES:
        print(
            CMD_LINE_COLOR.RED,
            f"This is not a Galaxy board, `{user_arg}` is only supported on Galaxy.",
            CMD_LINE_COLOR.ENDC
        )
        sys.exit(1)
    return


def is_vm():
    try:
        with open("/proc/cpuinfo", "r") as f:
            if "hypervisor" in f.read():
                return True
    except (FileNotFoundError, PermissionError):
        print(
            CMD_LINE_COLOR.YELLOW,
            f"Cannot access /proc/cpuinfo: {e}",
            CMD_LINE_COLOR.ENDC
        )
        pass
    return False


def main():
    """
    First entry point for TT-SMI. Detects devices and instantiates backend.
    """
    # Enable backtrace for debugging
    os.environ["RUST_BACKTRACE"] = "full"

    args = parse_args()

    driver = get_driver_version()
    if not driver:
        print(
            CMD_LINE_COLOR.RED,
            "No Tenstorrent driver detected! Please install driver using tt-kmd: https://github.com/tenstorrent/tt-kmd ",
            CMD_LINE_COLOR.ENDC,
        )
        sys.exit(1)

    # Detect non-tty stdout, but allow users to override
    is_tty = sys.stdout.isatty() and not args.snapshot_no_tty

    # Handle reset first, without setting up backend
    if args.reset is not None:
        reset_input = parse_reset_input(args.reset)

        if reset_input.type == ResetType.ALL:
            # Assume user wants all pci devices to be reset
            reset_indices = pci_scan()
            pci_board_reset(reset_indices, reinit=not(args.no_reinit), print_status=is_tty)

        elif reset_input.type == ResetType.ID_LIST:
            reset_indices = reset_input.value
            pci_board_reset(reset_indices, reinit=not(args.no_reinit), print_status=is_tty)

        # All went well - exit
        sys.exit(0)
    # Handle ubb reset without backend
    if args.glx_reset:
        # Galaxy reset, without auto retries
        try:
            # reinit has to be enabled to detect devices post reset

            glx_6u_trays_reset(reinit=not(args.no_reinit), print_status=is_tty)
        except Exception as e:
            print(
                CMD_LINE_COLOR.RED,
                f"Error in resetting galaxy 6u trays!\n{e}\n Exiting...",
                CMD_LINE_COLOR.ENDC,
            )
            sys.exit(1)
    if args.glx_reset_auto:
        # Galaxy reset with upto 3 auto retries
        reset_try_number = 0
        max_reset_try = 3
        print(
            CMD_LINE_COLOR.YELLOW,
            f"This option will auto retry resetting galaxy 6u trays up to {max_reset_try} times if it fails.",
            CMD_LINE_COLOR.ENDC,
        )
        while reset_try_number < max_reset_try:
            print(
                CMD_LINE_COLOR.YELLOW,
                f"Trying reset ({reset_try_number+1}/{max_reset_try})...",
                CMD_LINE_COLOR.ENDC,
            )
            try:
                # Try to reset galaxy 6u trays
                # reinit has to be enabled to detect devices post reset
                glx_6u_trays_reset(reinit=True, print_status=is_tty)
                break  # If reset was successful, break the loop
            except Exception as e:
                reset_try_number += 1
                if reset_try_number < max_reset_try:
                    print(
                        CMD_LINE_COLOR.RED,
                        f"Error in resetting galaxy 6u trays, resetting again...",
                        CMD_LINE_COLOR.ENDC,
                    )
                else:
                    print(
                        CMD_LINE_COLOR.RED,
                        f"Failed on last reset...exiting with error code 1",
                        CMD_LINE_COLOR.ENDC,
                    )
                    sys.exit(1)

        # All went well - exit
        sys.exit(0)
    if args.glx_reset_tray is not None:
        # Reset a specific tray on the galaxy
        try:
            tray_num_bitmask = hex(1 << (int(args.glx_reset_tray) - 1))
            glx_6u_trays_reset(reinit=not(args.no_reinit), ubb_num=tray_num_bitmask, dev_num="0xFF", op_mode="0x0", reset_time="0xF", print_status=is_tty)
        except Exception as e:
            print(
                CMD_LINE_COLOR.RED,
                f"Error in resetting galaxy 6u tray {args.glx_reset_tray}!\n{e}\n Exiting...",
                CMD_LINE_COLOR.ENDC,
            )
            sys.exit(1)

    try:
        devices = detect_chips_with_callback(
            local_only=args.local, ignore_ethernet=args.local, print_status=is_tty
        )
    except Exception as e:
        print(
            CMD_LINE_COLOR.RED,
            f"Error in detecting devices!\n{e}\n Exiting...",
            CMD_LINE_COLOR.ENDC,
        )
        sys.exit(1)
    if not devices:
        print(
            CMD_LINE_COLOR.RED,
            "No Tenstorrent devices detected! Please check your hardware and try again. Exiting...",
            CMD_LINE_COLOR.ENDC,
        )
        sys.exit(1)
    backend = TTSMIBackend(devices, pretty_output=is_tty)

    tt_smi_main(backend, args)


if __name__ == "__main__":
    main()
