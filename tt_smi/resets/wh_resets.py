# SPDX-FileCopyrightText: © 2023 Tenstorrent Inc.
# SPDX-License-Identifier: Apache-2.0

"""
This file contains functions to do resets on WH pcie boards and WH galaxy servers
"""
import sys
import time
import tqdm
import requests
import threading
from rich.live import Live
from rich.status import Status
from typing import List, Optional, Tuple
from tt_tools_common.wh_reset import WHChipReset
from tt_tools_common.ui_common.themes import CMD_LINE_COLOR
from tt_tools_common.utils_common.tools_utils import (
    get_board_type,
)
from tt_tools_common.utils_common.system_utils import (
    get_driver_version,
)

MINIMUM_DRIVER_VERSION_LDS_RESET = 21


def mobo_address_generator(mobo: str, command: str):
    """Generate url and auth for mobo given a command"""
    url = f"http://{mobo}:8000/{command}"
    auth = ("admin", "admin")
    return url, auth


def check_driver_version(operation: str):
    """Check if driver is beyond minimum version to perform resets"""
    driver = get_driver_version()
    if driver is None:
        print(
            CMD_LINE_COLOR.RED,
            "No Tenstorrent driver detected! Please install driver using tt-kmd: https://github.com/tenstorrent/tt-kmd ",
            CMD_LINE_COLOR.ENDC,
        )
        sys.exit(1)
    if int(driver.split(".")[1]) < MINIMUM_DRIVER_VERSION_LDS_RESET:
        print(
            f"{CMD_LINE_COLOR.RED}This script requires ttkmd version to be greater than {'.'.join(map(str, MINIMUM_DRIVER_VERSION_LDS_RESET))}, not continuing with {operation}{CMD_LINE_COLOR.ENDC}"
        )
        sys.exit(1)


def server_communication(
    post: bool,
    mobo: str,
    command: str,
    data: Optional[dict] = None,
    check_error: bool = True,
):
    response_url, response_auth = mobo_address_generator(mobo, command)
    if post:
        response = requests.post(response_url, auth=response_auth, json=data)
    else:
        response = requests.get(
            response_url,
            auth=response_auth,
        )

    try:
        exception = None
        response_json = {}  # Initializing just in case...

        # No response for successful shutdown/modules and boot/modules
        if response.text != "":
            response_json = response.json()

        if "error" in response_json:  # Error for boot, shutdown/modules, boot/modules
            exception = (
                f"{mobo} request {command} returned with error {response_json['error']}"
            )
        elif (
            "exception" in response_json and response_json["exception"] is not None
        ):  # Exception for boot/progress
            exception = f"{mobo} request {command} returned with exception {response_json['exception']}"
    except requests.exceptions.HTTPError as e:
        exception = f"{mobo} request {command} failed with HTTP error {e}, response {response.text}"
    except Exception as e:
        # This is somewhat of an unexpected error, so we want to raise it immediately
        raise Exception(
            f"{mobo} request {command} failed with exception {response.text}"
        )
    finally:
        if check_error and exception is not None:
            raise Exception(f"{CMD_LINE_COLOR.RED}{exception}{CMD_LINE_COLOR.ENDC}")

    return response_json


def get_server_version(mobo):
    # Try to get the server version, but some servers may not have the /about endpoint so defaulting to 0.0.0
    response_url, response_auth = mobo_address_generator(mobo, "about")
    try:
        response = requests.get(response_url, auth=response_auth, timeout=30)
        response.raise_for_status()

        response = response.json()
        server_version = tuple(map(int, response["version"].split(".")))
    except Exception:
        server_version = (0, 0, 0)

    return server_version


def wait_for_boot_complete(mobo_dict, mobo_list, timeout=600):
    mobo = mobo_dict["mobo"]
    # Do a check for the server version, if it is >= 0.3.0, then the boot command is posted and
    # we need to do a while loop to check for boot progress
    server_version = get_server_version(mobo)
    if server_version < (0, 3, 0):
        return

    # Derive position from mobo_list
    position = mobo_list.index(mobo)
    progress_bar = tqdm(total=100, bar_format="{desc} [{elapsed}]", position=position)

    # Get the update percentage and update the progress bar
    time_start = time.time()
    boot_progress = 0.0
    progress_bar.set_description_str(
        f"{mobo} - Waiting for server boot to complete... {boot_progress:6.2f}%"
    )
    while boot_progress < 100.0:
        if time.time() - time_start > timeout:
            raise Exception(
                f"{CMD_LINE_COLOR.RED}{mobo} - Boot timeout, please power cycle the galaxy and try boot again{CMD_LINE_COLOR.ENDC}"
            )

        response = server_communication(False, mobo, "boot/progress")

        boot_progress = float(response["boot_percent"])

        # If the server version is >= 1.3.2, then we have a more verbose display of boot status
        if server_version >= (1, 3, 2):
            extra_info = f" ({response['step']})"
        else:
            extra_info = ""

        progress_bar.set_description_str(
            f"{mobo} - Waiting for server boot to complete... {boot_progress:6.2f}%{extra_info}"
        )
        time.sleep(1)


# Function for booting credos concurrently
def credo_boot(mobo_dict):
    mobo = mobo_dict["mobo"]
    if "credo" in mobo_dict.keys():
        credo_ports = mobo_dict["credo"]
    else:
        print(
            CMD_LINE_COLOR.BLUE,
            f"{mobo} - No credos to be booted, moving on ...",
            CMD_LINE_COLOR.ENDC,
        )
        return
    if "disabled_ports" in mobo_dict.keys():
        disable_ports = mobo_dict["disabled_ports"]
    else:
        disable_ports = []

    server_version = get_server_version(mobo)

    # disable_ports is really only a feature for 1.3.2 and above, so if the server version is less than that, then
    # output a message and ignore the disable_ports flag
    boot_data = {"groups": None, "credo": True, "retimer_sel": credo_ports}
    if server_version >= (1, 3, 2):
        boot_data["disable_sel"] = disable_ports
    elif server_version < (1, 3, 2) and disable_ports is not None:
        print(
            f"{CMD_LINE_COLOR.RED}Warning: port disable is only available for server version 1.3.2 and above, ignoring flag for {mobo}{CMD_LINE_COLOR.ENDC}"
        )

    print(CMD_LINE_COLOR.BLUE, f"{mobo} - Booting credo ...", CMD_LINE_COLOR.ENDC)
    server_communication(True, mobo, "boot", boot_data)


# Function for shutting down modules concurrently
def shutdown_modules(mobo_dict):
    mobo = mobo_dict["mobo"]
    print(
        CMD_LINE_COLOR.BLUE,
        f"{mobo} - Turning off modules ...",
        CMD_LINE_COLOR.ENDC,
    )
    server_communication(
        True, mobo, "shutdown/modules", {"groups": None}, check_error=False
    )


# Function for booting modules concurrently
def boot_modules(mobo_dict):
    mobo = mobo_dict["mobo"]
    print(
        CMD_LINE_COLOR.BLUE,
        f"{mobo} - Turning on modules ...",
        CMD_LINE_COLOR.ENDC,
    )
    server_communication(True, mobo, "boot/modules", {"groups": None})


# Wrapper function to run the above functions concurrently on all mobos in mobo_list
def threaded_mobo_reset(mobo_dict_list, function, args=()):
    class ThreadWrapper(threading.Thread):
        """
        Wrapper class to allow exceptions to be raised from threads,
        needed because exceptions raised in threads will simply cause
        the thread to exit and not stop the program
        """

        def __init__(self, target, args):
            threading.Thread.__init__(self)
            self.target = target
            self.args = args
            self.exc = None

        def run(self):
            try:
                self.target(*self.args)
            except Exception as e:
                self.exc = e

    # Thread the function on all mobos
    all_threads = []
    for mobo_dict in mobo_dict_list:
        t = ThreadWrapper(target=function, args=(mobo_dict,) + args)
        all_threads.append(t)
        t.start()

    for t in all_threads:
        t.join()

    # If any exceptions were raised, print them and exit
    exceptions = [t.exc for t in all_threads if t.exc is not None]
    if exceptions:
        for e in exceptions:
            print(e)
        sys.exit(1)


def warm_reset_mobo(mobo_dict_list):
    """Warm boot mobos in dict list form json file"""
    check_driver_version("boot mobo")

    #  Credo boot if needed
    threaded_mobo_reset(mobo_dict_list, credo_boot)

    # Poll for boot completion status if needed
    mobo_list = [entry["mobo"] for entry in mobo_dict_list["wh_mobo_reset"]]
    threaded_mobo_reset(mobo_dict_list, wait_for_boot_complete, (mobo_list,))

    # Powercycle modules
    threaded_mobo_reset(mobo_dict_list, shutdown_modules)
    threaded_mobo_reset(mobo_dict_list, boot_modules)


def reset_wh_boards(boards_to_reset: List[int]) -> List[int]:
    """Reset devices given their pci ids"""

    check_driver_version("board reset")
    print(
        f"{CMD_LINE_COLOR.BLUE} Starting pci link reset on WH devices at pci indices: {str(boards_to_reset)[1:-1]} {CMD_LINE_COLOR.BLUE}"
    )
    reset_devices = WHChipReset().full_lds_reset(pci_interfaces=boards_to_reset)
    print(
        f"{CMD_LINE_COLOR.GREEN} Finishing pci link reset on WH devices at pci indices: {str(boards_to_reset)[1:-1]} {CMD_LINE_COLOR.BLUE}\n"
    )

    return reset_devices
