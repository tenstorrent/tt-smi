# SPDX-FileCopyrightText: © 2023 Tenstorrent Inc.
# SPDX-License-Identifier: Apache-2.0

import sys
import time
import json
import signal
import argparse
from pyluwen import detect_chips, detect_chips_fallible, PciChip


def parse_args():
    """Parse user args"""
    parser = argparse.ArgumentParser(description=__doc__)

    def parse_reset_input(value):
        """Parse the reset inputs - either list of int pci IDs or a json config file"""
        try:
            # Attempt to parse as a JSON file
            with open(value, "r") as json_file:
                data = json.load(json_file)
                if isinstance(data, list) and all(
                    isinstance(item, int) for item in data
                ):
                    return data
                else:
                    raise argparse.ArgumentTypeError(
                        f"Invalid JSON data in file: {value}"
                    )
        except (json.JSONDecodeError, FileNotFoundError):
            # If parsing as JSON fails, treat it as a comma-separated list of integers
            try:
                return [int(item) for item in value.split(",")]
            except ValueError:
                raise argparse.ArgumentTypeError(f"Invalid input: {value}")

    parser.add_argument(
        "-r",
        "--reset",
        type=parse_reset_input,
        default=None,
        metavar="0,1 ... or config.json",
        help="Provide pci index's of boards to reset or reset config file. Use -g to generate reset config",
        dest="reset",
    )
    args = parser.parse_args()
    return args


def main():
    args = parse_args()
    if args.reset is not None:
        print(args.reset)

    for i in args.reset:
        try:
            chip = PciChip(pci_interface=i)
            print("GS: ", chip.as_gs())
            print("WH: ", chip.as_wh())
            print("Remote: ", chip.is_remote())
        except Exception as e:
            print(e)


if __name__ == "__main__":
    main()
