import sys
import copy
import signal
import argparse
import constants
import time
import asyncio
import threading
from rich.text import Text
from sys import path
from pathlib import Path
from datetime import datetime
from typing import List, Tuple, Dict
from collections import OrderedDict
from textual.css.query import NoMatches
from textual.app import App, ComposeResult
from ui.common_themes import CMD_LINE_COLOR
# from version import VERSION_STR, APP_SIGNATURE
from textual.widgets import DataTable, Footer, TabbedContent
from utils_common import get_host_info, system_compatibility 
from textual.containers import Container, Vertical
from ui.common_widgets import TTHeader, TTDataTable, TTMenu, TTCompatibilityMenu
from ui.common_themes import create_tt_tools_theme
from utils import TTSMIBackend
from pyluwen import PciChip
from pyluwen import detect_chips

TextualKeyBindings = List[Tuple[str, str, str]]
interrupt_received = False
telem_threads = []

def interrupt_handler(sig, action) -> None:
    """Handle interrupts to exit processes gracefully"""
    global interrupt_received
    interrupt_received = True

class TTSMI(App):
    """A Textual app example to test all tt_textual widgets for TT-Tools."""

    BINDINGS = [("q, Q", "quit", "Quit"),
                ("h, H", "help", "Help"),
                ("d, D", "toggle_dark", "Toggle dark mode"),
                ("t, T", "toggle_default", "Toggle default"),
                ("1", "tab_one", "Device info tab"),
                ("2", "tab_two", "Telemetry tab"),
                ("3", "tab_three", "Firmware tab")
                ]

    CSS_PATH = ["../ui/common_style.css", "tt_smi_style.css"]

    def __init__(self,
                 result_filename: str = None,
                 app_name: str = "TT-SMI",
                 app_version: str = "N/A",
                 key_bindings: TextualKeyBindings = [],
                 backend: TTSMIBackend = None,
                 no_log: bool = False) -> None:
        """Initialize the textual app."""
        super().__init__()
        self.app_name = app_name
        self.app_version = app_version
        self.backend = backend
        self.no_log = no_log
        self.theme = create_tt_tools_theme()
        
        if key_bindings:
            self.BINDINGS += key_bindings

    def compose(self) -> ComposeResult:
        """Create child widgets for the app."""

        yield TTHeader(self.app_name, self.app_version)        
        with Container(id="app_grid"):
            with Vertical(id="left_col"):
                yield TTMenu(id="host_info",
                             title="Host Info",
                             data=get_host_info())
                yield TTCompatibilityMenu(id="compatibility_menu",
                             title="Compatibility Check",
                             data=system_compatibility())
            with TabbedContent("Information (1)", "Telemetry(2)", "Firmwares(3)", id="tab_container"):
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
        # smi_table.dt.add_rows([[f"{n}", f"Status {n}", f"This is description {n}", datetime.now().strftime("%b %d %Y %I:%M:%S %p")] for n in range(42)])
        smi_table.dt.add_rows(self.format_device_info_rows())
        
        telem_table = self.get_widget_by_id(id="tt_smi_telem")
        telem_table.dt.cursor_type = "none"
        telem_table.dt.add_rows(self.format_telemetry_rows())
        
        firmware_table = self.get_widget_by_id(id="tt_smi_firmware")
        firmware_table.dt.cursor_type = "none"
        firmware_table.dt.add_rows(self.format_firmware_rows())
           
    def update_telem_table(self) -> None:
        telem_table = self.get_widget_by_id(id="tt_smi_telem")
        self.backend.update_telem()
        rows = self.format_telemetry_rows()
        telem_table.update_data(rows)

    def format_firmware_rows(self):
        all_rows = []
        for i, _ in enumerate(self.backend.devices):
            rows = [Text(f"{i}", style=self.theme["yellow_bold"], justify="center")]
            for fw in constants.FW_LIST:
                val = self.backend.firmware_infos[i][fw]
                rows.append(Text(f"{val}", style=self.theme["text_green"], justify="center"))
            all_rows.append(rows)
        return all_rows

    def format_telemetry_rows(self):
        all_rows = []
        for i, _ in enumerate(self.backend.devices):
            rows = [Text(f"{i}", style=self.theme["yellow_bold"], justify="center")]
            for telem in constants.TELEM_LIST:
                val = self.backend.device_telemetrys[i][telem]   
                if telem == "voltage":
                    vdd_max = self.backend.chip_limits[i]["vdd_max"]
                    rows.append(Text(f"{val}", style=self.theme["text_green"], justify="center") + Text(f"/ {vdd_max}", style=self.theme["yellow_bold"], justify="center"))
                elif telem == "current":
                    max_current = self.backend.chip_limits[i]["tdc_limit"]
                    rows.append(Text(f"{val}", style=self.theme["text_green"], justify="center") + Text(f"/ {max_current}", style=self.theme["yellow_bold"], justify="center"))
                elif telem == "power":
                    max_power = self.backend.chip_limits[i]["tdp_limit"]
                    rows.append(Text(f"{val}", style=self.theme["text_green"], justify="center") + Text(f"/ {max_power}", style=self.theme["yellow_bold"], justify="center"))
                elif telem == "aiclk":
                    asic_fmax = self.backend.chip_limits[i]["asic_fmax"]
                    rows.append(Text(f"{val}", style=self.theme["text_green"], justify="center") + Text(f"/ {asic_fmax}", style=self.theme["yellow_bold"], justify="center"))
                elif telem == "asic_temperature":
                    max_temp = self.backend.chip_limits[i]["thm_limit"]
                    rows.append(Text(f"{val}", style=self.theme["text_green"], justify="center") + Text(f"/ {max_temp}", style=self.theme["yellow_bold"], justify="center"))
                else:
                    rows.append(Text(f"{val}", style=self.theme["text_green"], justify="center"))
            all_rows.append(rows)
        return all_rows

    def format_device_info_rows(self):
        all_rows = []
        for i, _ in enumerate(self.backend.devices):
            rows = [Text(f"{i}", style=self.theme["yellow_bold"], justify="center")]
            for info in constants.DEV_INFO_LIST:
                val = self.backend.device_infos[i][info]
                if info == "pcie_width":
                    if val < constants.MAX_PCIE_WIDTH:
                        rows.append(Text(f"x{val}", style=self.theme["attention"], justify="center") + Text(f" / x16", style=self.theme["yellow_bold"], justify="center"))
                    else:
                        rows.append(Text(f"x{val}", style=self.theme["text_green"], justify="center") + Text(f" / x16", style=self.theme["yellow_bold"], justify="center"))
                elif info == "pcie_speed":
                    if val < constants.MAX_PCIE_SPEED:
                        rows.append(Text(f"Gen{val}", style=self.theme["attention"], justify="center") + Text(f" / Gen4", style=self.theme["yellow_bold"], justify="center"))
                    else:
                        rows.append(Text(f"Gen{val}", style=self.theme["text_green"], justify="center") + Text(f" / Gen4", style=self.theme["yellow_bold"], justify="center"))
                elif info == "dram_status":
                    if val == True:
                        rows.append(Text(f"Y", style=self.theme["text_green"], justify="center"))
                    else:
                        rows.append(Text(f"N", style=self.theme["text_green"], justify="center"))
                elif info == "dram_speed":
                    if val: 
                        if val < 12:
                            rows.append(Text(f"{val}G", style=self.theme["attention"], justify="center"))
                        else:
                            rows.append(Text(f"{val}G", style=self.theme["text_green"], justify="center"))
                    else:
                        rows.append(Text(f"N/A", style=self.theme["red_bold"], justify="center"))
                else:
                    if val == "N/A":
                        rows.append(Text(f"{val}", style=self.theme["gray"], justify="center"))
                    else:
                        rows.append(Text(f"{val}", style=self.theme["text_green"], justify="center"))
            all_rows.append(rows)
        return all_rows
    
    def action_toggle_dark(self) -> None:
        """An action to toggle dark mode."""
        self.dark = not self.dark

    async def action_quit(self) -> None:
        """An [action](/guide/actions) to quit the app as soon as possible."""
        global interrupt_received, telem_threads
        exit_message = Text(f"Exiting TT-SMI.", style="yellow")
        interrupt_received = True
        for thread in telem_threads:
            thread.join(timeout=0.1)
        self.exit(message=exit_message)

    def action_tab_one(self) -> None:
        """Switch to read-write tab"""
        self.query_one(TabbedContent).active = "tab-1"

    async def action_tab_two(self) -> None:
        """Switch to read-only tab"""
        global interrupt_received, telem_threads
        self.query_one(TabbedContent).active = "tab-2"
        signal.signal(signal.SIGTERM, interrupt_handler)
        signal.signal(signal.SIGINT, interrupt_handler)
        
        def update_telem():
            global interrupt_received
            while not interrupt_received:
                self.update_telem_table()
                time.sleep(constants.GUI_INTERVAL_TIME)
        thread = threading.Thread(target=update_telem, name="telem_thread")
        thread.setDaemon(True)
        thread.start()
        telem_threads.append(thread)

    def action_tab_three(self) -> None:
        """Switch to read-only tab"""
        self.query_one(TabbedContent).active = "tab-3"

def parse_args():
    """Parse user args"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--local',
                        default=False,
                        action="store_true",
                        help='Run only on local chips')
    parser.add_argument(
        "-v",
        "--version",
        action="version",
        version="FIX",
    )
    parser.add_argument(
        "-nl",
        "--no_log",
        default=False,
        action="store_true",
        help='Runs tt-mod without generating the end SPI log',
    )
    parser.add_argument(
        "-ls",
        "--list",
        default=False,
        action="store_true",
        help="List boards that are available to modify SPI on and quits"
    )
    parser.add_argument(
        "-f",
        "--filename",
        metavar="filename",
        nargs="?",
        const=None,
        default=None,
        help="Change filename for test log. Default: ~/tt_mod_logs/<timestamp>_results.yaml",
        dest="filename",
    )
    args = parser.parse_args()
    return args

def main():
    global interrupt_received
    interrupt_received = False

    signal.signal(signal.SIGINT, interrupt_handler)
    signal.signal(signal.SIGTERM, interrupt_handler)
    
    args = parse_args()
    if args.filename:
        if Path(args.filename).suffix != ".json":
            print(CMD_LINE_COLOR.RED, f"Please use the .json extension on your filename!", CMD_LINE_COLOR.ENDC)
            return
    try:
        devices = detect_chips()
        # print(dir(devices[0]))
    except Exception as e:
        print(e)
        print("Exiting...")
        return -1
    backend = TTSMIBackend(devices=devices)
    
    tt_smi_app = TTSMI(backend=backend, no_log=args.no_log, result_filename=args.filename)
    tt_smi_app.run()

if __name__ == "__main__":
    main()
    

