# SPDX-FileCopyrightText: © 2023 Tenstorrent Inc.
# SPDX-License-Identifier: Apache-2.0

from pyluwen import PciChip
from pyluwen import detect_chips
from pathlib import Path
from importlib.resources import path
from yaml import safe_load
import jsons
import time
from typing import Optional


def load_csm_addr(chip):
    register_file = safe_load(get_chip_data(chip, "axi-pci.yaml", False))

    register_defs = {}
    for top_level, sub_level in register_file.items():
        register_defs[top_level] = {}

        offset = sub_level["offset"]
        name = top_level
        filename = sub_level["filename"]
        for struct, struct_info in safe_load(
            get_chip_data(chip, filename, False)
        ).items():
            if not isinstance(struct_info, dict) or "Address" not in struct_info:
                continue
            for i in range(struct_info.get("ArraySize", 1)):
                struct_name = f"{name}.{struct}"

                def inner(struct_name: str):
                    # if "ARC_RESET" in struct_name:
                    #     print("struct_name: ", struct_name)
                    #     struct_offset = offset + struct_info["Address"]
                    #     register_defs[struct_name] = (31, 0, struct_offset)
                    #     print(register_defs[struct_name])
                    #     for register, register_info in struct_info.get("Fields", {}).items():
                    #         # if struct_name == "ARC_RESET":
                    #         register_name = f"{struct_name}.{register}"
                    #         register_offset = struct_offset + register_info[2]
                    #         register_defs[register_name] = (register_info[1], register_info[2], register_offset)
                    #         print("register_defs[register_name]:", register_defs[register_name])
                    # else:
                    struct_offset = (
                        offset
                        + struct_info["Address"]
                        + i * struct_info.get("AddressIncrement", 4)
                    )
                    register_defs[struct_name] = (31, 0, struct_offset)
                    for register, register_info in struct_info.get(
                        "Fields", {}
                    ).items():
                        register_name = f"{struct_name}.{register}"
                        register_offset = struct_offset + register_info[3]
                        register_defs[register_name] = (
                            register_info[1],
                            register_info[2],
                            register_offset,
                        )

                if i == 0:
                    inner(struct_name)
                inner(f"{struct_name}[{i}]")

    if chip.as_gs():
        import json

        # print(json.dumps(register_defs, sort_keys=True, indent=4))

    return register_defs


def axi_read32(chip, addr: str) -> int:
    data = chip.axi_translate(addr)

    buffer = bytearray(data.size)
    chip.axi_read(data.addr, buffer)
    return int.from_bytes(buffer, "little")


def axi_write32(chip, addr: str, value: int):
    data = chip.axi_translate(addr)
    chip.axi_write(data.addr, value.to_bytes(data.size, "little"))


def axi_sread32(chip, csm_addr, name: str, additional_offset=None) -> int:
    msb, lsb, offset = csm_addr[name]

    if additional_offset is not None:
        msb = additional_offset[0]
        lsb = additional_offset[1]
        offset += additional_offset[2]

    mask = (1 << (msb - lsb + 1)) - 1
    return (chip.axi_read32(offset) >> lsb) & mask


def axi_swrite32(chip, csm_addr, data, name: str, additional_offset=None) -> int:
    msb, lsb, offset = csm_addr[name]

    if additional_offset is not None:
        msb = additional_offset[0]
        lsb = additional_offset[1]
        offset += additional_offset[2]

    mask = (1 << (msb - lsb + 1)) - 1
    chip.axi_write32(offset, data)
    return (chip.axi_read32(offset) >> lsb) & mask


def package_root_path():
    return path("tt_smi", "")


def get_chip_data(chip, file, internal: bool):
    with package_root_path() as path:
        if chip.as_wh() is not None:
            prefix = "wormhole"
        elif chip.as_gs() is not None:
            prefix = "grayskull"
        else:
            raise Exception("Only support flashing Wh or GS chips")
        if internal:
            prefix = f".ignored/{prefix}"
        else:
            prefix = f"data/{prefix}"
        return open(str(path.joinpath(f"{prefix}/{file}")))


def init_fw_defines(chip):
    global fw_defines

    fw_defines = safe_load(get_chip_data(chip, "fw_defines.yaml", False))


def get_aiclk(device, address_maps):
    csm_addr = address_maps[id(device)]
    aiclk = axi_sread32(device, csm_addr, "ARC_CSM.AICLK_PPM.curr_aiclk")
    reset_safe = axi_sread32(device, csm_addr, "ARC_CSM.AICLK_PPM.reset_safe")
    target = axi_sread32(device, csm_addr, "ARC_CSM.AICLK_PPM.targ_aiclk")
    force = axi_sread32(device, csm_addr, "ARC_CSM.AICLK_PPM.force_aiclk")
    print(f"aiclk: {aiclk}; reset_safe: {reset_safe}, target: {target}, force: {force}")
    return aiclk


def force_update_aiclk(device, address_maps):
    csm_addr = address_maps[id(device)]
    axi_swrite32(device, csm_addr, 0, "ARC_CSM.AICLK_PPM.force")
    axi_sread32(device, csm_addr, "ARC_CSM.AICLK_PPM.force_aiclk")


def main():
    devices = detect_chips()

    if devices == []:
        print("No devices detected.. Exiting")
        return -1

    for i, device in enumerate(devices):
        if device.as_gs():
            address_maps = {}
            init_fw_defines(device)
            address_maps[id(device)] = load_csm_addr(device)

            gs = device.as_gs()
            get_aiclk(device, address_maps)
            gs.arc_msg(
                fw_defines["MSG_TYPE_ARC_GO_BUSY"], wait_for_done=True, arg0=0, arg1=0
            )
            get_aiclk(device, address_maps)
            time.sleep(5)
            gs.arc_msg(
                fw_defines["MSG_TYPE_RESET_SAFE_CLKS"],
                wait_for_done=True,
                arg0=1,
                arg1=0,
            )
            get_aiclk(device, address_maps)
            time.sleep(5)
            gs.arc_msg(
                fw_defines["MSG_TYPE_RESET_SAFE_CLKS"],
                wait_for_done=True,
                arg0=0,
                arg1=0,
            )
            get_aiclk(device, address_maps)
            time.sleep(5)
            gs.arc_msg(
                fw_defines["MSG_TYPE_ARC_GO_SHORT_IDLE"],
                wait_for_done=True,
                arg0=0,
                arg1=0,
            )
            get_aiclk(device, address_maps)

            # exit_code = gs.arc_msg(fw_defines["MSG_TYPE_ARC_GET_HARVESTING"], wait_for_done=True, arg0=0, arg1=0)
            # print("exit_code: ",exit_code)
            # harvesting_fuses = axi_read32(device, "ARC_RESET.SCRATCH[3]")
            # print("harvesting_fuses: ", harvesting_fuses)
            # get_aiclk(device, address_maps)


if __name__ == "__main__":
    main()
