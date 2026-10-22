# SPDX-FileCopyrightText: © 2026 Tenstorrent Inc.
# SPDX-License-Identifier: Apache-2.0

import sys

from tt_tools_common.ui_common.themes import CMD_LINE_COLOR as _CMD_LINE_COLOR

if not sys.stdout.isatty():
    for attr in dir(_CMD_LINE_COLOR):
        if not attr.startswith("_"):
            setattr(_CMD_LINE_COLOR, attr, "")

CMD_LINE_COLOR = _CMD_LINE_COLOR
