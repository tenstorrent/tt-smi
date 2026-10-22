# SPDX-FileCopyrightText: © 2026 Tenstorrent Inc.
# SPDX-License-Identifier: Apache-2.0

import io

import pytest
from rich.cells import cell_len
from rich.console import Console

from tt_smi.frontend import LatestReleasesBox
from tt_smi.latest_releases import RELEASE_SPECS, oldest_version

# Width inside the sidebar's border and padding
INNER_WIDTH = 36

LONG = "0.78.0-rc12+dev.gabcdef1234"


def render(box: LatestReleasesBox) -> list:
    """Render the box's contents at sidebar width, one string per line."""
    console = Console(width=INNER_WIDTH, file=io.StringIO(), color_system=None)
    console.print(box._render_versions())
    return console.file.getvalue().rstrip("\n").split("\n")


def make_box(versions, installed, mixed=False) -> LatestReleasesBox:
    box = LatestReleasesBox(id="latest_releases", title="Versions")
    box._versions = versions
    box.set_installed(installed, mixed)
    return box


class TestOldestVersion:
    def test_oldest_version(self):
        assert oldest_version(["19.16.0", "19.9.0", "19.15.0"]) == "19.9.0"

    def test_oldest_version_empty(self):
        assert oldest_version([]) is None


class TestVersionsLayout:
    """The box must never wrap, whatever the versions."""

    def test_columns_fit(self):
        widths = (
            LatestReleasesBox.MARK_WIDTH
            + LatestReleasesBox.LABEL_WIDTH
            + 2 * LatestReleasesBox.VERSION_WIDTH
        )
        # One space between each of the four columns
        assert widths + 3 <= INNER_WIDTH

    def test_labels_fit(self):
        for spec in RELEASE_SPECS:
            assert len(spec.label) <= LatestReleasesBox.LABEL_WIDTH

    @pytest.mark.parametrize(
        "mixed,fetched,extra",
        [
            (False, True, 0),
            (True, True, 2),  # Blank line and footnote
            (False, False, 3),  # Blank line and two error lines
            (True, False, 5),
        ],
    )
    def test_no_wrap(self, mixed, fetched, extra):
        versions = {s.name: LONG if fetched else None for s in RELEASE_SPECS}
        installed = {s.name: LONG for s in RELEASE_SPECS}
        lines = render(make_box(versions, installed, mixed))
        # Header plus one line per package, then any notes
        assert len(lines) == 1 + len(RELEASE_SPECS) + extra
        assert all(cell_len(line) <= INNER_WIDTH for line in lines)


class TestVersionsStatus:
    @pytest.mark.parametrize(
        "current,tested,glyph",
        [
            ("2.10.0", "2.11.0", "↑"),
            ("2.11.0", "2.11.0", "✓"),
            ("2.12.0", "2.11.0", "▲"),
            (None, "2.11.0", ""),
            ("2.11.0", None, ""),
        ],
    )
    def test_glyph(self, current, tested, glyph):
        box = make_box({"tt-kmd": tested}, {"tt-kmd": current})
        mark, cur, test = box._row_cells("tt-kmd", tested)
        assert mark.plain == glyph
        assert cur.plain == (current or "—")
        assert test.plain == (tested or "—")

    def test_mixed_firmware_marked(self):
        name = "tt-system-firmware"
        box = make_box({name: "19.16.0"}, {name: "19.14.0"}, mixed=True)
        mark, cur, _ = box._row_cells(name, "19.16.0")
        assert mark.plain == "↑"
        assert cur.plain == "19.14.0*"
