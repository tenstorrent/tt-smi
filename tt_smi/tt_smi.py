# SPDX-FileCopyrightText: © 2023 Tenstorrent Inc.
# SPDX-License-Identifier: Apache-2.0

"""
Tenstorrent System Management Interface (TT-SMI) is a command line utility
to interact with all Tenstorrent devices on host.

Main objective of TT-SMI is to provide a simple and easy to use interface
to collect and display device, telemetry and firmware information.

In addition user can issue Grayskull and Wormhole board level resets.
"""
import os
import sys
import time
import signal
import argparse
import threading
import pkg_resources
from rich.text import Text
from tt_smi import constants
from typing import List, Tuple
from textual.reactive import reactive
from importlib_resources import files
from textual.app import App, ComposeResult
from textual.widgets import Footer, TabbedContent
from textual.containers import Container, Vertical
from tt_tools_common.ui_common.themes import CMD_LINE_COLOR, create_tt_tools_theme
from tt_tools_common.reset_common.reset_utils import (
    generate_reset_logs,
    parse_reset_input,
)
from tt_smi.tt_smi_backend import (
    TTSMIBackend,
    pci_board_reset,
    pci_indices_from_json,
    mobo_reset_from_json,
)
from tt_tools_common.utils_common.tools_utils import (
    hex_to_semver_m3_fw,
    detect_chips_with_callback,
)
from tt_tools_common.utils_common.system_utils import (
    get_driver_version,
    get_host_info,
    system_compatibility,
    get_sw_ver_info,
)
from tt_tools_common.ui_common.widgets import (
    TTHeader,
    TTDataTable,
    TTMenu,
    TTCompatibilityMenu,
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
        ("d, D", "toggle_dark", "Toggle dark mode"),
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

    # Added sw_version as a reactive field for live updating on the GUI
    get_latest_sw_vers = reactive(True)

    def __init__(
        self,
        result_filename: str = None,
        app_name: str = "TT-SMI",
        app_version: str = pkg_resources.get_distribution("tt_smi").version,
        key_bindings: TextualKeyBindings = [],
        backend: TTSMIBackend = None,
        snapshot: bool = False,
    ) -> None:
        """Initialize the textual app."""
        super().__init__()
        self.app_name = app_name
        self.app_version = app_version
        self.backend = backend
        self.snapshot = snapshot
        self.result_filename = result_filename
        self.theme = create_tt_tools_theme()

        if key_bindings:
            self.BINDINGS += key_bindings

    def compose(self) -> ComposeResult:
        """Create child widgets for the app."""

        board_ids = [info["board_id"] for info in self.backend.device_infos]

        yield TTHeader(self.app_name, self.app_version)
        with Container(id="app_grid"):
            with Vertical(id="left_col"):
                yield TTMenu(id="host_info", title="Host Info", data=get_host_info())
                yield TTCompatibilityMenu(
                    id="compatibility_menu",
                    title="Compatibility Check",
                    data=system_compatibility(),
                )
                yield TTMenu(
                    id="sw_ver_menu",
                    title="Latest SW Versions",
                    data=get_sw_ver_info(self.get_latest_sw_vers, board_ids),
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

        sw_ver_table = self.get_widget_by_id(id="sw_ver_menu")
        sw_ver_table.set_interval(0.1, callback=sw_ver_table.refresh)

    def update_telem_table(self) -> None:
        """Update telemetry table"""
        telem_table = self.get_widget_by_id(id="tt_smi_telem")
        self.backend.update_telem()
        rows = self.format_telemetry_rows()
        telem_table.update_data(rows)

    def format_firmware_rows(self):
        """Format firmware rows"""
        all_rows = []
        for i, _ in enumerate(self.backend.devices):
            rows = [Text(f"{i}", style=self.theme["yellow_bold"], justify="center")]
            for fw in constants.FW_LIST:
                val = self.backend.firmware_infos[i][fw]
                if val == "N/A":
                    rows.append(
                        Text(f"{val}", style=self.theme["gray"], justify="center")
                    )
                else:
                    rows.append(
                        Text(f"{val}", style=self.theme["text_green"], justify="center")
                    )
            all_rows.append(rows)
        return all_rows

    def format_bh_telemetry_rows(self, board_num: int) -> List[Text]:
        """BH spefic telemetry rows - subject to change post qual"""
        bh_row = [
            Text(f"{board_num}", style=self.theme["yellow_bold"], justify="center")
        ]
        for telem in constants.TELEM_LIST:
            val = self.backend.device_telemetrys[board_num][telem]
            bh_row.append(
                Text(
                    f"{val}",
                    style=self.theme["attention"],
                    justify="center",
                )
                + Text(
                    f"/ --- ",
                    style=self.theme["gray"],
                    justify="center",
                )
            )
        return bh_row

    def format_telemetry_rows(self):
        """Format telemetry rows"""
        all_rows = []
        for i, chip in enumerate(self.backend.devices):
            if chip.as_bh():
                all_rows.append(self.format_bh_telemetry_rows(i))
                continue
            rows = [Text(f"{i}", style=self.theme["yellow_bold"], justify="center")]
            for telem in constants.TELEM_LIST:
                val = self.backend.device_telemetrys[i][telem]
                if telem == "voltage":
                    vdd_max = self.backend.chip_limits[i]["vdd_max"]
                    if float(val) < float(vdd_max):
                        rows.append(
                            Text(
                                f"{val}",
                                style=self.theme["text_green"],
                                justify="center",
                            )
                            + Text(
                                f"/ {vdd_max}",
                                style=self.theme["yellow_bold"],
                                justify="center",
                            )
                        )
                    else:
                        rows.append(
                            Text(
                                f"{val}",
                                style=self.theme["attention"],
                                justify="center",
                            )
                            + Text(
                                f"/ {vdd_max}",
                                style=self.theme["yellow_bold"],
                                justify="center",
                            )
                        )
                elif telem == "current":
                    max_current = self.backend.chip_limits[i]["tdc_limit"]
                    if float(val) < float(max_current):
                        rows.append(
                            Text(
                                f"{val}",
                                style=self.theme["text_green"],
                                justify="center",
                            )
                            + Text(
                                f"/ {max_current}",
                                style=self.theme["yellow_bold"],
                                justify="center",
                            )
                        )
                    else:
                        rows.append(
                            Text(
                                f"{val}",
                                style=self.theme["attention"],
                                justify="center",
                            )
                            + Text(
                                f"/ {max_current}",
                                style=self.theme["yellow_bold"],
                                justify="center",
                            )
                        )
                elif telem == "power":
                    max_power = self.backend.chip_limits[i]["tdp_limit"]
                    if float(val) < float(max_power):
                        rows.append(
                            Text(
                                f"{val}",
                                style=self.theme["text_green"],
                                justify="center",
                            )
                            + Text(
                                f"/ {max_power}",
                                style=self.theme["yellow_bold"],
                                justify="center",
                            )
                        )
                    else:
                        rows.append(
                            Text(
                                f"{val}",
                                style=self.theme["attention"],
                                justify="center",
                            )
                            + Text(
                                f"/ {max_power}",
                                style=self.theme["yellow_bold"],
                                justify="center",
                            )
                        )
                elif telem == "aiclk":
                    asic_fmax = self.backend.chip_limits[i]["asic_fmax"]
                    if float(val) < float(asic_fmax):
                        rows.append(
                            Text(
                                f"{val}",
                                style=self.theme["text_green"],
                                justify="center",
                            )
                            + Text(
                                f"/ {asic_fmax}",
                                style=self.theme["yellow_bold"],
                                justify="center",
                            )
                        )
                    else:
                        rows.append(
                            Text(
                                f"{val}",
                                style=self.theme["attention"],
                                justify="center",
                            )
                            + Text(
                                f"/ {asic_fmax}",
                                style=self.theme["yellow_bold"],
                                justify="center",
                            )
                        )
                elif telem == "asic_temperature":
                    max_temp = self.backend.chip_limits[i]["thm_limit"]
                    if float(val) < float(max_temp):
                        rows.append(
                            Text(
                                f"{val}",
                                style=self.theme["text_green"],
                                justify="center",
                            )
                            + Text(
                                f"/ {max_temp}",
                                style=self.theme["yellow_bold"],
                                justify="center",
                            )
                        )
                    else:
                        rows.append(
                            Text(
                                f"{val}",
                                style=self.theme["attention"],
                                justify="center",
                            )
                            + Text(
                                f"/ {max_temp}",
                                style=self.theme["yellow_bold"],
                                justify="center",
                            )
                        )
                else:
                    rows.append(
                        Text(f"{val}", style=self.theme["text_green"], justify="center")
                    )
            all_rows.append(rows)
        return all_rows

    def format_device_info_rows(self):
        """Format device info rows"""
        all_rows = []
        for i, device in enumerate(self.backend.devices):
            rows = [Text(f"{i}", style=self.theme["yellow_bold"], justify="center")]
            for info in constants.DEV_INFO_LIST:
                val = self.backend.device_infos[i][info]
                if info == "board_type":
                    if val == "n300":
                        if device.is_remote():
                            rows.append(
                                Text(
                                    f"{val}",
                                    style=self.theme["text_green"],
                                    justify="center",
                                )
                                + Text(
                                    " R",
                                    style=self.theme["yellow_bold"],
                                    justify="center",
                                )
                            )
                        else:
                            rows.append(
                                Text(
                                    f"{val}",
                                    style=self.theme["text_green"],
                                    justify="center",
                                )
                                + Text(
                                    f" L",
                                    style=self.theme["yellow_bold"],
                                    justify="center",
                                )
                            )
                    else:
                        rows.append(
                            Text(
                                f"{val}",
                                style=self.theme["text_green"],
                                justify="center",
                            )
                        )
                elif info == "pcie_width":
                    max_link_width = self.backend.pci_properties[i]["max_link_width"]
                    if device.is_remote():
                        rows.append(
                            Text(
                                f"N/A",
                                style=self.theme["gray"],
                                justify="center",
                            )
                        )
                    else:
                        if val < max_link_width:
                            rows.append(
                                Text(
                                    f"x{val}",
                                    style=self.theme["attention"],
                                    justify="center",
                                )
                                + Text(
                                    f" / x{max_link_width}",
                                    style=self.theme["yellow_bold"],
                                    justify="center",
                                )
                            )
                        else:
                            rows.append(
                                Text(
                                    f"x{val}",
                                    style=self.theme["text_green"],
                                    justify="center",
                                )
                                + Text(
                                    f" / x{max_link_width}",
                                    style=self.theme["yellow_bold"],
                                    justify="center",
                                )
                            )
                elif info == "pcie_speed":
                    max_link_speed = self.backend.pci_properties[i]["max_link_speed"]
                    if device.is_remote():
                        rows.append(
                            Text(
                                f"N/A",
                                style=self.theme["gray"],
                                justify="center",
                            )
                        )
                    else:
                        if max_link_speed == "N/A":
                            rows.append(
                                Text(
                                    f"Gen{val}",
                                    style=self.theme["attention"],
                                    justify="center",
                                )
                                + Text(
                                    f" / N/A",
                                    style=self.theme["gray"],
                                    justify="center",
                                )
                            )
                        elif val == "N/A":
                            rows.append(
                                Text(
                                    f"N/A",
                                    style=self.theme["red_bold"],
                                    justify="center",
                                )
                                + Text(
                                    f" / Gen{max_link_speed}",
                                    style=self.theme["yellow_bold"],
                                    justify="center",
                                )
                            )
                        elif float(val) < float(max_link_speed):
                            rows.append(
                                Text(
                                    f"Gen{val}",
                                    style=self.theme["attention"],
                                    justify="center",
                                )
                                + Text(
                                    f" / Gen{max_link_speed}",
                                    style=self.theme["yellow_bold"],
                                    justify="center",
                                )
                            )
                        else:
                            rows.append(
                                Text(
                                    f"Gen{val}",
                                    style=self.theme["text_green"],
                                    justify="center",
                                )
                                + Text(
                                    f" / Gen{max_link_speed}",
                                    style=self.theme["yellow_bold"],
                                    justify="center",
                                )
                            )
                elif info == "dram_status":
                    # TODO: Update once DRAM status becomes availible
                    if device.as_bh():
                        rows.append(
                            Text("N/A", style=self.theme["gray"], justify="center")
                        )
                    else:
                        if val:
                            rows.append(
                                Text(
                                    "Y",
                                    style=self.theme["text_green"],
                                    justify="center",
                                )
                            )
                        else:
                            rows.append(
                                Text(
                                    "N", style=self.theme["attention"], justify="center"
                                )
                            )
                elif info == "dram_speed":
                    # TODO: Update once DRAM status becomes availible
                    if device.as_bh():
                        rows.append(
                            Text("N/A", style=self.theme["gray"], justify="center")
                        )
                    else:
                        if val:
                            rows.append(
                                Text(
                                    f"{val}",
                                    style=self.theme["text_green"],
                                    justify="center",
                                )
                            )
                        else:
                            rows.append(
                                Text(
                                    "N/A",
                                    style=self.theme["red_bold"],
                                    justify="center",
                                )
                            )
                else:
                    if val == "N/A":
                        rows.append(
                            Text(f"{val}", style=self.theme["gray"], justify="center")
                        )
                    else:
                        rows.append(
                            Text(
                                f"{val}",
                                style=self.theme["text_green"],
                                justify="center",
                            )
                        )
            all_rows.append(rows)
        return all_rows

    def action_toggle_dark(self) -> None:
        """An action to toggle dark mode."""
        self.dark = not self.dark

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

    async def action_tab_two(self) -> None:
        """Switch to read-only tab"""
        global INTERRUPT_RECEIVED, TELEM_THREADS, RUNNING_TELEM_THREAD
        self.query_one(TabbedContent).active = "tab-2"

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

    def action_tab_three(self) -> None:
        """Switch to read-only tab"""
        self.query_one(TabbedContent).active = "tab-3"

    def action_help(self) -> None:
        """Pop up the help menu"""
        tt_confirm_box = TTHelperMenuBox(
            text=constants.HELP_MENU_MARKDOWN, theme=self.theme
        )
        self.push_screen(tt_confirm_box)


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
        version=pkg_resources.get_distribution("tt_smi").version,
    )
    parser.add_argument(
        "-s",
        "--snapshot",
        default=False,
        action="store_true",
        help=(
            "Dump snapshot of current TT-SMI information to .json log."
            "Default: ~/tt_smi/<timestamp>_snapshot.json\nUser can use -f to change filename"
        ),
    )
    parser.add_argument(
        "-ls",
        "--list",
        default=False,
        action="store_true",
        help="List boards that are available on host and quits",
    )
    parser.add_argument(
        "-f",
        "--filename",
        metavar="filename",
        nargs="?",
        const=None,
        default=None,
        help="Change filename for test log. Default: ~/tt_smi/<timestamp>_snapshot.json",
        dest="filename",
    )
    parser.add_argument(
        "-g",
        "--generate_reset_json",
        nargs="?",
        const=True,
        default=False,
        help=(
            "Generate default reset json file that reset consumes. Default stored at ~/.config/tenstorrent/reset_config.json.\n"
            "Update the generated file and use it as an input for the --reset option"
        ),
    )

    parser.add_argument(
        "-r",
        "--reset",
        type=parse_reset_input,
        metavar="0,1 ... or config.json",
        default=None,
        help=(
            "Provide list of PCI index or a json file with reset configs. "
            "Find PCI index of board using the -ls option. "
            "Generate a default reset json file with the -g option."
        ),
        dest="reset",
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
    if args.snapshot:
        file = backend.save_logs(args.filename)
        print(
            CMD_LINE_COLOR.PURPLE,
            f"Saved tt-smi log to: {file}",
            CMD_LINE_COLOR.ENDC,
        )
        sys.exit(0)
    tt_smi_app = TTSMI(
        backend=backend, snapshot=args.snapshot, result_filename=args.filename
    )
    tt_smi_app.run()


def check_fw_version(pyluwen_chip, board_num):
    """
    Check firmware version before running tt_smi and exit gracefully if not supported
    For Grayskull, we only support fw version >= 1.3.0.0
    """
    if pyluwen_chip.as_gs():
        fw_version, exit_code = pyluwen_chip.arc_msg(
            constants.MSG_TYPE_FW_VERSION, arg0=0, arg1=0
        )
        if fw_version < constants.MAGIC_FW_VERSION:
            print(
                CMD_LINE_COLOR.RED,
                f"Unsupported FW version {hex_to_semver_m3_fw(fw_version)} detected on Grayskull device {board_num}.",
                f"\n Require FW version >= {hex_to_semver_m3_fw(constants.MAGIC_FW_VERSION)} to run tt-smi",
                CMD_LINE_COLOR.ENDC,
            )
            print(
                CMD_LINE_COLOR.PURPLE,
                "Please update FW on device using tt-flash: https://github.com/tenstorrent/tt-flash",
                CMD_LINE_COLOR.ENDC,
            )
            sys.exit(1)
    return


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

    # Handle reset first, without setting up backend or
    if args.reset is not None:
        if isinstance(args.reset, list) and all(
            isinstance(item, int) for item in args.reset
        ):
            # If input is just reset board
            pci_board_reset(args.reset, reinit=True)
        else:
            # If mobo reset, perform it first
            parsed_dict = mobo_reset_from_json(args.reset)
            pci_indices, reinit = pci_indices_from_json(parsed_dict)
            if pci_indices:
                pci_board_reset(pci_indices, reinit)

        # All went well - exit
        sys.exit(0)
    if args.generate_reset_json:
        # Use filename if provided, else use default
        try:
            devices = detect_chips_with_callback(local_only=True)
        except Exception as e:
            print(
                CMD_LINE_COLOR.RED,
                f"Error in detecting devices!\n{e}\n Exiting...",
                CMD_LINE_COLOR.ENDC,
            )
            sys.exit(1)

        file = (
            generate_reset_logs(devices)
            if isinstance(args.generate_reset_json, bool)
            else generate_reset_logs(devices, args.generate_reset_json)
        )
        print(
            CMD_LINE_COLOR.PURPLE,
            f"Generated sample reset config file for this host: {file}",
            CMD_LINE_COLOR.ENDC,
        )
        print(
            CMD_LINE_COLOR.YELLOW,
            f"Update the generated file and use it as an input for the -r/--reset option.",
            CMD_LINE_COLOR.ENDC,
        )
        sys.exit(0)

    try:
        devices = detect_chips_with_callback(
            local_only=args.local, ignore_ethernet=args.local
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
    backend = TTSMIBackend(devices)
    # Check firmware version before running tt_smi to avoid crashes
    for i, device in enumerate(backend.devices):
        check_fw_version(device, i)

    tt_smi_main(backend, args)


if __name__ == "__main__":
    main()
