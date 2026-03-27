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
import argparse
from importlib.metadata import version

from tt_umd import TopologyDiscovery
from tt_tools_common.ui_common.themes import CMD_LINE_COLOR
from tt_tools_common.utils_common.tools_utils import detect_chips_with_callback
from tt_tools_common.utils_common.system_utils import get_driver_version

from tt_smi import constants
from tt_smi.tt_smi_backend import TTSMIBackend
from tt_smi.tt_smi_utils import check_is_galaxy, is_vm
from tt_smi.tt_smi_reset import (
    pci_board_reset,
    glx_6u_trays_reset,
    parse_reset_input,
)
from tt_smi.tt_smi_frontend import TTSMI


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
        help=(
            "List boards on the host and quit. With UMD (default), tables include "
            "UMD Chip ID, PCI BDF, PCI Dev ID (/dev/tenstorrent/<n>), board type, series, and board number."
        ),
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
        metavar="TARGETS",
        default=None,
        nargs="*",
        help=(
            "Reset targets: UMD logical IDs, PCI BDFs (e.g. 0000:0a:00.0), or "
            "/dev/tenstorrent/<id>. Use -ls to list devices. "
            "Omit targets or use 'all' to reset all devices. "
            "Do not mix types in one command. "
            "With --use_luwen, use BDF or /dev/tenstorrent/<id> (not bare integers)."
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
    parser.add_argument(
        "--use_luwen",
        default=False,
        action="store_true",
        help="Use deprecated Luwen driver instead of UMD (default).",
    )
    parser.add_argument(
        "--eth_train_skip",
        default=False,
        action="store_true",
        help="Skip waiting for Ethernet training post reset.",
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
    if args.list:
        if backend.use_umd:
            backend.print_all_available_devices_umd()
        else:
            backend.print_all_available_devices_luwen()
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
    if not sys.stdin.isatty():
        print(f"{CMD_LINE_COLOR.RED}No TTY detected! Interactive shell required.\nUse tt-smi -s for snapshot output.{CMD_LINE_COLOR.ENDC}")
        sys.exit(1)
    tt_smi_app = TTSMI(
        backend=backend,
        snapshot=args.snapshot,
        result_filename=args.filename,
        show_sidebar=not args.compact,
    )
    tt_smi_app.run()

def main():
    """
    First entry point for TT-SMI. Detects devices and instantiates backend.
    """
    # Enable backtrace for debugging
    # "0" is no backtrace, "1" is short backtrace, "full" is full backtrace
    # os.environ["RUST_BACKTRACE"] = "full"

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
    if not is_tty:
        # Suppress UMD log messages to be error only
        os.environ["TT_LOGGER_LEVEL"] = "error"

    # Handle reset first, without setting up backend
    if args.reset is not None:
        reset_input = parse_reset_input(args.reset)
        pci_board_reset(reset_input, reinit=not(args.no_reinit), print_status=is_tty, use_umd=not args.use_luwen, eth_train_skip=args.eth_train_skip)
        sys.exit(0)
    # Handle ubb reset without backend
    if args.glx_reset:
        # Galaxy reset, without auto retries
        try:
            print(
                CMD_LINE_COLOR.YELLOW,
                "Hint: tt-smi -r is now supported on Galaxy 6U.",
                CMD_LINE_COLOR.ENDC,
            )
            # reinit has to be enabled to detect devices post reset
            glx_6u_trays_reset(reinit=not(args.no_reinit), print_status=is_tty, use_umd=not args.use_luwen)
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
                glx_6u_trays_reset(reinit=True, print_status=is_tty, use_umd=not args.use_luwen)
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
            glx_6u_trays_reset(reinit=not(args.no_reinit), ubb_num=tray_num_bitmask, dev_num="0xFF", op_mode="0x0", reset_time="0xF", print_status=is_tty, use_umd=not args.use_luwen)
        except Exception as e:
            print(
                CMD_LINE_COLOR.RED,
                f"Error in resetting galaxy 6u tray {args.glx_reset_tray}!\n{e}\n Exiting...",
                CMD_LINE_COLOR.ENDC,
            )
            sys.exit(1)

    try:
        if not args.use_luwen:
            cluster_descriptor, devices = TopologyDiscovery.discover(options=constants.SMBUS_TELEMETRY_OPTIONS)
        else:
            cluster_descriptor = None
            devices = dict(enumerate(detect_chips_with_callback(
                local_only=args.local, ignore_ethernet=args.local, print_status=is_tty
            )))
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
    backend = TTSMIBackend(devices=devices, umd_cluster_descriptor=cluster_descriptor, pretty_output=is_tty)

    tt_smi_main(backend, args)


if __name__ == "__main__":
    main()
