# SPDX-FileCopyrightText: © 2023 Tenstorrent Inc.
# SPDX-License-Identifier: Apache-2.0

"""
EXAMPLE APP FOR TT-TOOLS
Tests all basic widgets and themes for TT-Tools
"""
from typing import List, Tuple
from textual.app import App, ComposeResult
from textual.containers import Container, Horizontal, Vertical
from textual.widgets import Footer, Header, Static, DataTable
from textual.css.query import NoMatches

from ui.common_widgets import TTHeader, TTFooter, TTDataTable, TTMenu, TTConfirmBox

from datetime import datetime

TextualKeyBindings = List[Tuple[str, str, str]]


class TTApp(App):
    """A Textual app example to test all tt_textual widgets for TT-Tools."""

    BINDINGS = [
        ("d", "toggle_dark", "Toggle dark mode"),
        ("q", "quit", "Quit"),
        ("b", "test_confirmbox", "Test Confirm Box"),
    ]

    CSS_PATH = "../ui/common_style.css"

    def __init__(
        self,
        app_name: str = "TT-App Example",
        app_version: str = "1.0.0",
        key_bindings: TextualKeyBindings = [],
    ) -> None:
        """Initialize the textual app."""
        super().__init__()
        self.app_name = app_name
        self.app_version = app_version

        if key_bindings:
            self.BINDINGS += key_bindings

    def compose(self) -> ComposeResult:
        """Create child widgets for the app."""
        data = {"Menu 1": 1, "Menu 2": 2, "Menu 3": 3}
        yield TTHeader(self.app_name, self.app_version)
        with Horizontal():
            with Vertical():  # TODO: Specify widget width & height on layout
                yield TTMenu(title="Menu 1", data=data, id="menu_1")
                yield TTMenu(title="Menu 2", data=data, id="menu_2")
            yield TTDataTable(
                title="TT_DATA_TABLE_EXAMPLE",
                header=["Board ID", "Test NOC", "Test PCIE", "Time"],
                id="dt_example",
            )
            yield TTMenu(title="Right Menu", data=data, id="menu_3")
        yield Footer()

    def on_mount(self) -> None:
        """Event handler called when widget is added to the app."""
        tt_dt = self.get_widget_by_id(id="dt_example")
        # initial data rows for TTDataTable
        tt_dt.dt.add_rows(
            [
                [
                    f"{n}",
                    f"Status {n}",
                    f"This is description {n}",
                    datetime.now().strftime("%b %d %Y %I:%M:%S %p"),
                ]
                for n in range(42)
            ]
        )

    def action_toggle_dark(self) -> None:
        """An action to toggle dark mode."""
        self.dark = not self.dark

    def action_test_confirmbox(self) -> None:
        """An action to test the confirm box toggle."""
        self.push_screen(TTConfirmBox(text="Are you sure you want to...?"))


if __name__ == "__main__":
    TTApp().run()
