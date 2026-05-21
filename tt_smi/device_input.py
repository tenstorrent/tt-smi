# SPDX-FileCopyrightText: © 2026 Tenstorrent Inc.
# SPDX-License-Identifier: Apache-2.0

"""
Shared parsing for tt-smi CLI device selection: UMD logical ID, PCI BDF,
or /dev/tenstorrent/<int>. Used by --reset / -r, --bh_blinky, and any future
flags that need the same homogeneous input list.
Mixing input types in one invocation is rejected.
"""

import re
import sys
from dataclasses import dataclass
from enum import Enum
from typing import List, Optional, Tuple, Union

from tt_smi.ui_utils import CMD_LINE_COLOR

DEV_TENSTORRENT_PREFIX = "/dev/tenstorrent/"
PCI_BDF_FULL_RE = re.compile(
    r"^[0-9a-fA-F]{4}:[0-9a-fA-F]{2}:[0-9a-fA-F]{2}\.[0-9a-fA-F]$"
)

class SmiDeviceTargetKind(Enum):
    ALL = 1
    UMD_LOGICAL_ID = 2
    PCI_BDF = 3
    DEV_TENSTORRENT_ID = 4


@dataclass
class SmiDeviceInput:
    """Normalized device selection from argv inputs (one homogeneous kind per invocation)."""

    type: SmiDeviceTargetKind
    value: Optional[Union[List[int], List[str]]] = None


def classify_single_input(
    input: str,
) -> Tuple[SmiDeviceTargetKind, Union[int, str]]:
    """
    Classify a single input as a (SmiDeviceTargetKind, value) pair.

    Returns one of UMD_LOGICAL_ID, PCI_BDF, or DEV_TENSTORRENT_ID; for
    DEV_TENSTORRENT_ID the value is the integer ID only (path stripped).
    """
    input = input.strip()
    if not input:
        raise ValueError("Empty input")
    if input.lower() == "all":
        raise ValueError(
            "Use 'all' as the only argument to select all devices"
        )
    if input.startswith(DEV_TENSTORRENT_PREFIX):
        suffix = input[len(DEV_TENSTORRENT_PREFIX) :].strip()
        if suffix.isdigit():
            return (SmiDeviceTargetKind.DEV_TENSTORRENT_ID, int(suffix))
        raise ValueError(
            f"Invalid path: {input} (expected /dev/tenstorrent/<integer>)"
        )
    if PCI_BDF_FULL_RE.match(input):
        return (SmiDeviceTargetKind.PCI_BDF, input)
    if input.lstrip("-").isdigit():
        return (SmiDeviceTargetKind.UMD_LOGICAL_ID, int(input))
    raise ValueError(
        f"Invalid device target: '{input}'. "
        "Use UMD logical ID (integer), PCI BDF (e.g. 0000:0a:00.0), or /dev/tenstorrent/<integer>."
    )


def parse_smi_device_input(value: list) -> SmiDeviceInput:
    """
    Parse device-selection argv fragments (e.g. from ``nargs='*'``).

    All inputs must be the same kind (no mixing). Returns SmiDeviceInput with:
    - type ALL for no input or ``all``;
    - PCI_BDF, UMD_LOGICAL_ID, or DEV_TENSTORRENT_ID with deduplicated values.

    On parse errors, prints to stderr and calls ``sys.exit(1)``.
    """
    if value is None or len(value) == 0:
        return SmiDeviceInput(type=SmiDeviceTargetKind.ALL, value=None)
    inputs = [t.strip() for raw in value for t in raw.split(",") if t.strip()]
    if not inputs or (len(inputs) == 1 and inputs[0].lower() == "all"):
        return SmiDeviceInput(type=SmiDeviceTargetKind.ALL, value=None)

    seen: set = set()
    values: list = []
    target_kind: Optional[SmiDeviceTargetKind] = None

    for input in inputs:
        try:
            input_kind, val = classify_single_input(input)
            if target_kind is not None and input_kind != target_kind:
                raise ValueError(
                    f"Mixed device target kinds are not allowed. "
                    f"Got '{input}' which is a {input_kind.name}, but earlier inputs were {target_kind.name}. "
                    "Use only one kind per invocation."
                )
            target_kind = input_kind
            if val not in seen:
                seen.add(val)
                values.append(val)
        except ValueError as e:
            print(CMD_LINE_COLOR.RED, str(e), CMD_LINE_COLOR.ENDC)
            sys.exit(1)

    if target_kind in (
        SmiDeviceTargetKind.UMD_LOGICAL_ID,
        SmiDeviceTargetKind.DEV_TENSTORRENT_ID,
    ):
        values = sorted(values)

    return SmiDeviceInput(type=target_kind, value=values)
