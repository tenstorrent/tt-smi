# SPDX-FileCopyrightText: © 2026 Tenstorrent Inc.
# SPDX-License-Identifier: Apache-2.0

"""
TT-SMI UI helpers: terminal colors, Textual theme, widgets, and packaged CSS.
"""

from datetime import datetime
from importlib.resources import files
from typing import Dict, List, Tuple, Union

from rich.panel import Panel
from rich.style import Style
from rich.table import Table
from rich.text import Text
from textual.app import ComposeResult, RenderResult
from textual.containers import Container, ScrollableContainer
from textual.coordinate import Coordinate
from textual.screen import ModalScreen
from textual.widget import Widget
from textual.widgets import DataTable, Markdown
from textual.widgets.data_table import CellDoesNotExist


def create_color_scheme(color_system: str):
    if color_system == "truecolor":
        return {
            "red": "#f04f5e",
            "green": "#1eb57d",
            "yellow": "#ffd10a",
            "purple": "#786bb0",
            "orange": "dark_orange",
            "white": "white",
            "grey": "bright_black",
        }
    if color_system == "256":
        return {
            "red": "red1",
            "green": "dark_cyan",
            "yellow": "gold1",
            "purple": "medium_purple3",
            "orange": "dark_orange",
            "white": "white",
            "grey": "bright_black",
        }
    if color_system == "standard":
        return {
            "red": "red",
            "green": "green",
            "yellow": "bright_yellow",
            "purple": "magenta",
            "orange": "bright_red",
            "white": "white",
            "grey": "bright_black",
        }
    return None


class CMD_LINE_COLOR:
    PURPLE = "\033[95m"
    BLUE = "\033[94m"
    GREEN = "\033[92m"
    RED = "\033[91m"
    ENDC = "\033[0m"
    YELLOW = "\033[93m"


def create_tt_tools_theme():
    """Return tt-smi Textual Rich theme styles."""
    tt_colors = create_color_scheme("truecolor")

    return {
        "yellow_bold": Style(color=tt_colors["yellow"], bold=True),
        "orange_italic": Style(color=tt_colors["orange"], italic=True),
        "yellow_italic": Style(color=tt_colors["yellow"], italic=True),
        "light_green_bold": Style(color="#B0FC38", bold=True),
        "red_bold": Style(color=tt_colors["red"], bold=True),
        "gray": Style(color=tt_colors["grey"]),
        "text_green": Style(color=tt_colors["green"]),
        "warning": Style(color=tt_colors["red"]),
        "attention": Style(color=tt_colors["orange"]),
        "green_bold": Style(color=tt_colors["green"], bold=True),
    }


def get_common_style_css_path():
    """Return path to packaged common_style.css for Textual CSS_PATH."""
    return files("tt_smi").joinpath("common_style.css")


class TTHeader(Widget):
    """A custom header widget for TT-SMI."""

    def __init__(self, app_name: str, app_version: str) -> None:
        super().__init__()
        self.app_name = app_name
        self.app_version = app_version

    def on_mount(self) -> None:
        self.set_interval(1, callback=self.refresh)

    def render(self) -> RenderResult:
        grid = Table.grid(expand=True, padding=(0, 1), pad_edge=True)
        grid.add_column(justify="left", ratio=0.25)
        grid.add_column(justify="center", ratio=0.5)
        grid.add_column(justify="right", ratio=0.25)

        version = f"Version {self.app_version}"
        app_name = self.app_name
        date = datetime.now().strftime("%b %d %Y %I:%M:%S %p")

        grid.add_row(version, app_name, date)

        return Panel(grid)


class TTDataTable(ScrollableContainer):
    """A custom container with a DataTable for TT-SMI."""

    def __init__(
        self, title: str, header: List[str] = None, id: str = None, **kwargs
    ) -> None:
        super().__init__(id=id)
        self.border_title = title
        self._title = title
        self.header = header
        self.dt = self.config_dt(**kwargs)

    def style_header(self):
        for i, header_text in enumerate(self.header):
            self.header[i] = Text(header_text, justify="center", style="underline")

    def config_dt(self, **kwargs) -> DataTable:
        dt = DataTable(id=self.id + "_table", **kwargs)
        dt.zebra_stripes = False
        dt.cursor_type = "cell"
        dt.border_title = self._title

        if self.header:
            self.style_header()
            dt.add_columns(*self.header)

        return dt

    def update_data(self, rows: List[str]) -> None:
        for i, row in enumerate(rows):
            for j, val in enumerate(row):
                try:
                    self.dt.update_cell_at(
                        coordinate=Coordinate(column=j, row=i), value=val
                    )
                except CellDoesNotExist:
                    self.dt.clear()
                    self.dt.add_rows(rows)

    def compose(self) -> ComposeResult:
        container = ScrollableContainer(self.dt, id=self.id)
        yield container


class TTHostCompatibilityMenu(Container):
    """
    Host info menu with compatibility notes (str = OK, tuple = warning + recommendation).
    """

    def __init__(self, id: str, title: str, data: Dict[str, Union[str, Tuple]]) -> None:
        super().__init__(id=id)
        self.data = data
        self.justify_width = max([len(k) for k in self.data.keys()]) + 1

        fully_compatible = all(isinstance(value, str) for value in self.data.values())
        if fully_compatible:
            self.border_title = title + " (Fully Compatible)"
        else:
            self.border_title = title + " (Config Warning!)"
            self.styles.border_title_color = "red"
            self.border_subtitle = "* Recommended Config"
            self.styles.border_subtitle_color = "red"

    def render(self) -> RenderResult:
        text = Text()
        for key, value in self.data.items():
            line_leader = Text("* ")
            if isinstance(value, str):
                k = Text(
                    f"{key.ljust(self.justify_width)}" + ": ",
                    style=Style(color="#ffd10a", bold=True),
                )
                v = Text(f"{value}\n")
                text.append_text(line_leader).append_text(k).append_text(v)
            elif isinstance(value, tuple):
                k = Text(
                    f"{key.ljust(self.justify_width)}" + ": ",
                    style=Style(color="#ffd10a", bold=True),
                )
                v_1 = Text(f"{value[0]}\n")
                v_2 = Text(
                    f"{' ' * (self.justify_width)}  * {value[1]}\n",
                    style=Style(color="dark_orange"),
                )
                text.append_text(line_leader).append_text(k).append_text(
                    v_1
                ).append_text(v_2)
        text.rstrip()

        return text


class TTHelperMenuBox(ModalScreen):
    """Modal help screen with markdown content."""

    BINDINGS = [
        ("q", "quit", "Quit"),
        ("escape", "esc_screen", "Escape the open screen"),
        ("h", "help", "Quit the help box"),
    ]

    def __init__(self, text: str, theme: dict = None) -> None:
        super().__init__()
        self.text = text
        self.theme = theme or {}

    def compose(self) -> ComposeResult:
        yield Markdown(self.text, id="help_menu_box")

    async def action_quit(self) -> None:
        self.app.pop_screen()

    async def action_esc_screen(self) -> None:
        self.app.pop_screen()

    async def action_help(self) -> None:
        self.app.pop_screen()
