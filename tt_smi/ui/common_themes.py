# SPDX-FileCopyrightText: © 2023 Tenstorrent Inc.
# SPDX-License-Identifier: Apache-2.0

from rich.text import Text
from rich.style import Style


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
    elif color_system == "256":
        return {
            "red": "red1",
            "green": "dark_cyan",
            "yellow": "gold1",
            "purple": "medium_purple3",
            "orange": "dark_orange",
            "white": "white",
            "grey": "bright_black",
        }
    elif color_system == "standard":
        return {
            "red": "red",
            "green": "green",
            "yellow": "bright_yellow",
            "purple": "magenta",
            "orange": "bright_red",
            "white": "white",
            "grey": "bright_black",
        }


class CMD_LINE_COLOR:
    PURPLE = "\033[95m"
    BLUE = "\033[94m"
    GREEN = "\033[92m"
    RED = "\033[91m"
    ENDC = "\033[0m"
    YELLOW = "\033[93m"


def create_tt_tools_theme():
    """Return tt-health theme"""
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
