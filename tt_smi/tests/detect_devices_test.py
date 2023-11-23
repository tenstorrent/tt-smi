# SPDX-FileCopyrightText: © 2023 Tenstorrent Inc.
# SPDX-License-Identifier: Apache-2.0
"""
Simple unit test to detect devices and print telemetry.
This is the first test to run to ensure that the library is working properly.
"""

import jsons
from pyluwen import detect_chips


def main():
    try:
        devices = detect_chips()

    except Exception as e:
        print(e)
        print("Exiting...")
        return -1

    for i, device in enumerate(devices):
        print("Device", i, ":", device.get_board_id())
        telem_struct = device.get_telemetry()

        map = jsons.dump(telem_struct)
        for key in map.keys():
            print(key, hex(map[key]))


if __name__ == "__main__":
    main()
