from pyluwen import PciChip
from pyluwen import detect_chips
from pathlib import Path
from importlib.resources import path
from yaml import safe_load
import functools
import itertools
import jsons
import time
from typing import Optional, OrderedDict
import os


def load_csm_addr(chip):
    register_file = safe_load(get_chip_data(chip, "axi-pci.yaml", False))

    register_defs = {}
    for top_level, sub_level in register_file.items():
        register_defs[top_level] = {}

        offset = sub_level["offset"]
        name = top_level
        for struct, struct_info in safe_load(
            get_chip_data(chip, "arc/csm.yaml", False)
        ).items():
            if not isinstance(struct_info, dict) or "Address" not in struct_info:
                continue
            for i in range(struct_info.get("ArraySize", 1)):
                struct_name = f"{name}.{struct}"

                def inner(struct_name: str):
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
    print("register_defs: ", register_defs)
    return register_defs


def axi_sread32(chip, csm_addr, name: str, additional_offset=None) -> int:
    msb, lsb, offset = csm_addr[name]

    if additional_offset is not None:
        msb = additional_offset[0]
        lsb = additional_offset[1]
        offset += additional_offset[2]

    mask = (1 << (msb - lsb + 1)) - 1
    return (chip.axi_read32(offset) >> lsb) & mask


def axi_read32(chip, addr: str) -> int:
    data = chip.axi_translate(addr)

    buffer = bytearray(data.size)
    chip.axi_read(data.addr, buffer)
    return int.from_bytes(buffer, "little")


def axi_write32(chip, addr: str, value: int):
    data = chip.axi_translate(addr)
    chip.axi_write(data.addr, value.to_bytes(data.size, "little"))


def package_data_file(package_relative_path):
    import importlib

    path = importlib.resources.files("tt_smi") / package_relative_path
    return importlib.resources.as_file(path)


def load_address_space_map(filename):
    def yaml_load(filepath):
        import yaml

        with open(filepath, "r") as file:
            return yaml.load(file, Loader=yaml.FullLoader)

    # if filename in Registers.cached_registers:
    #     self.addr_space_map = Registers.cached_registers[filename]
    #     return
    with package_data_file(filename) as f:
        addr_space_map = yaml_load(f)
    for addr_space_name in addr_space_map:
        register_definition_filename = addr_space_map[addr_space_name]["filename"]
        if "offset" not in addr_space_map[addr_space_name]:
            addr_space_map[addr_space_name]["offset"] = 0
        addr_space_offset = addr_space_map[addr_space_name]["offset"]
        # print ("Loading %s %s" % (register_definition_filename, addr_space_offset))
        # Load the file for the space
        regdef_filename = os.path.dirname(filename) + "/" + register_definition_filename
        addr_space_map[addr_space_name]["loaded_yaml"] = dict()
        # print ("Loading %s into addr_space %s " % (filename, addr_space_name))
        with package_data_file(filename) as f:
            loaded_yaml = yaml_load(f)


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
    print(f"aiclk: {aiclk}; reset_safe: {reset_safe}, target: {target}")
    return aiclk


def all_riscs_assert_reset(device):
    for i in range(8):
        axi_write32(device, f"ARC_RESET.RISCV_RESET[{i}]", 0x0)


def msg_tensix_toggle_reset(device):
    global fw_defines
    device.arc_msg(
        fw_defines["MSG_TYPE_TOGGLE_TENSIX_RESET"],
        wait_for_done=True,
        arg0=0xFF,
        arg1=0,
    )


def reverse_mapping_list(l):
    ret = [0] * len(l)
    for idx, val in enumerate(l):
        ret[val] = idx
    return ret


def setup_interface(device):
    # self.AXI.write_fields("ARC_RESET.DDR_RESET", { "axi_reset" : 1, "ddrc_reset" : 1 }, init=0)
    axi_write32(
        device, "ARC_RESET.DDR_RESET", {"axi_reset": 1, "ddrc_reset": 1}, init=0
    )

    NIU_CFG_0 = 0x100  # bit 0 is CG enable, bit 12 is tile clock disable
    ROUTER_CFG_0 = 0x104  # bit 0 is CG enable

    # ROUTER_CFG_1,2 are a 64-bit mask for column broadcast disable
    # ROUTER_CFG_3,4 are a 64-bit mask for row broadcast disable
    # A node will not receive broadcasts if it is in a disabled row or column.
    ROUTER_CFG_1 = 0x108
    ROUTER_CFG_3 = 0x110
    GRID_SIZE_X = 13
    GRID_SIZE_Y = 12
    NUM_TENSIX_X = GRID_SIZE_X - 1
    NUM_TENSIX_Y = GRID_SIZE_Y - 2
    TLB_SIZE = 1024 * 1024
    L1_SIZE = 1024 * 1024
    CSM_SIZE = 512 * 1024
    NOC_MAX_BACKOFF_EXP = 0xF
    PHYS_X_TO_NOC_0_X = [0, 12, 1, 11, 2, 10, 3, 9, 4, 8, 5, 7, 6]
    PHYS_Y_TO_NOC_0_Y = [0, 11, 1, 10, 2, 9, 3, 8, 4, 7, 5, 6]
    PHYS_X_TO_NOC_1_X = [12, 0, 11, 1, 10, 2, 9, 3, 8, 4, 7, 5, 6]
    PHYS_Y_TO_NOC_1_Y = [11, 0, 10, 1, 9, 2, 8, 3, 7, 4, 6, 5]
    NOC_0_X_TO_PHYS_X = reverse_mapping_list(PHYS_X_TO_NOC_0_X)
    NOC_0_Y_TO_PHYS_Y = reverse_mapping_list(PHYS_Y_TO_NOC_0_Y)
    NOC_1_X_TO_PHYS_X = reverse_mapping_list(PHYS_X_TO_NOC_1_X)
    NOC_1_Y_TO_PHYS_Y = reverse_mapping_list(PHYS_Y_TO_NOC_1_Y)
    DRAM_LOCATIONS = [(1, 6), (4, 6), (7, 6), (10, 6), (1, 0), (4, 0), (7, 0), (10, 0)]
    ARC_LOCATIONS = [(0, 2)]
    PCI_LOCATIONS = [(0, 4)]

    noc0_router_cfg_1 = 1 << 0  # disable broadcast to column 0
    noc1_router_cfg_1 = 1 << 12  # remap noc0 to noc1: disable broadcast to column 12

    def is_tensix_core_loc(x, y):
        return (x, y) in _core_list

    def int_to_bits(x):
        return list(filter(lambda b: x & (1 << b), range(x.bit_length())))

    def get_harvesting(device):
        if "T6PY_HARVESTING_OVERRIDE" in os.environ:
            harvesting_fuses = int(os.environ["T6PY_HARVESTING_OVERRIDE"], 0)
        else:
            harvesting_fuses, exit_code = device.arc_msg(
                fw_defines["MSG_TYPE_ARC_GET_HARVESTING"],
                wait_for_done=True,
                arg0=0,
                arg1=0,
            )
            if exit_code != 0:
                assert False, "FW is too old, please update fw"
        return harvesting_fuses
        # disable broadcast to rows 0, 6 and any in _disabled_rows.

    harvesting_fuses = get_harvesting(device)
    bad_mem_bits = harvesting_fuses & 0x3FF
    bad_logic_bits = (harvesting_fuses >> 10) & 0x3FF

    bad_row_bits = (bad_mem_bits | bad_logic_bits) << 1

    bad_physical_rows = int_to_bits(bad_row_bits)
    broadcast_disabled_rows = [0, 6]
    disabled_rows = frozenset(
        map(lambda y: PHYS_Y_TO_NOC_0_Y[GRID_SIZE_Y - y - 1], bad_physical_rows)
    )
    broadcast_disabled_rows += disabled_rows
    noc0_router_cfg_3 = functools.reduce(
        int.__or__, map(lambda y: 1 << y, broadcast_disabled_rows), 0
    )

    broadcast_disabled_rows_noc1 = map(
        lambda y: GRID_SIZE_Y - y - 1, broadcast_disabled_rows
    )
    noc1_router_cfg_3 = functools.reduce(
        int.__or__, map(lambda y: 1 << y, broadcast_disabled_rows_noc1), 0
    )
    good_rows = filter(
        lambda y: y not in disabled_rows, [1, 2, 3, 4, 5, 7, 8, 9, 10, 11]
    )
    good_cores = list(itertools.product(list(range(1, GRID_SIZE_X)), good_rows))
    _core_list = OrderedDict(map(lambda c: (c, None), good_cores))

    # Write all the registers for NOC NIU & router on a node.
    # x, y are NOC0 coordinates. reg_bases[i] is NOCi reg base address
    def setup_noc_by_xy(x, y, reg_bases):
        # x, y are NOC-noc_id coordinates. noc_reg_base is for noc_id.
        # This writes values that are NOC-independent.
        def setup_noc_common(x, y, noc_id, noc_reg_base):
            # CG enable
            rmw_val = device.noc_read32(x, y, noc_id, noc_reg_base + NIU_CFG_0)
            device.noc_write32(x, y, noc_id, noc_reg_base + NIU_CFG_0, rmw_val | 0x1)
            rmw_val = device.noc_read32(x, y, noc_id, noc_reg_base + ROUTER_CFG_0)
            device.noc_write32(x, y, noc_id, noc_reg_base + ROUTER_CFG_0, rmw_val | 0x1)

            # maximum exponential backoff
            rmw_val = device.noc_read32(x, y, noc_id, noc_reg_base + ROUTER_CFG_0)
            rmw_val &= ~0xF00
            rmw_val |= NOC_MAX_BACKOFF_EXP << 8
            device.noc_write32(x, y, noc_id, noc_reg_base + ROUTER_CFG_0, rmw_val)

        n1x = GRID_SIZE_X - x - 1
        n1y = GRID_SIZE_Y - y - 1

        device.noc_write32(x, y, 0, reg_bases[0] + ROUTER_CFG_1, noc0_router_cfg_1)
        device.noc_write32(x, y, 0, reg_bases[0] + ROUTER_CFG_3, noc0_router_cfg_3)
        device.noc_write32(n1x, n1y, 1, reg_bases[1] + ROUTER_CFG_1, noc1_router_cfg_1)
        device.noc_write32(n1x, n1y, 1, reg_bases[1] + ROUTER_CFG_3, noc1_router_cfg_3)

        setup_noc_common(x, y, 0, reg_bases[0])
        setup_noc_common(n1x, n1y, 1, reg_bases[1])

    # Write all the registers for NOC NIU & router on a node.
    # reg_bases[i] are PCI BAR-relative addresses for registers.
    def setup_noc_by_address(reg_bases):
        # This writes values that are NOC-independent.
        def setup_noc_common(noc_reg_base):
            # CG enable
            rmw_val = device.pci_axi_read32(noc_reg_base + NIU_CFG_0)
            device.pci_axi_write32(noc_reg_base + NIU_CFG_0, rmw_val | 0x1)
            rmw_val = device.pci_axi_read32(noc_reg_base + ROUTER_CFG_0)
            device.pci_axi_write32(noc_reg_base + ROUTER_CFG_0, rmw_val | 0x1)

            # maximum exponential backoff
            rmw_val = device.pci_axi_read32(noc_reg_base + ROUTER_CFG_0)
            rmw_val &= ~0xF00
            rmw_val |= NOC_MAX_BACKOFF_EXP << 8
            device.pci_axi_write32(noc_reg_base + ROUTER_CFG_0, rmw_val)

        device.pci_axi_write32(reg_bases[0] + ROUTER_CFG_1, noc0_router_cfg_1)
        device.pci_axi_write32(reg_bases[0] + ROUTER_CFG_3, noc0_router_cfg_3)
        device.pci_axi_write32(reg_bases[1] + ROUTER_CFG_1, noc1_router_cfg_1)
        device.pci_axi_write32(reg_bases[1] + ROUTER_CFG_3, noc1_router_cfg_3)

        setup_noc_common(reg_bases[0])
        setup_noc_common(reg_bases[1])

    for y in range(0, GRID_SIZE_Y):
        for x in range(0, GRID_SIZE_X):
            if (x, y) in DRAM_LOCATIONS:
                setup_noc_by_xy(x, y, [0xFFFF4000, 0xFFFF5000])

            elif (x, y) in ARC_LOCATIONS:
                setup_noc_by_address([0x1FF50000, 0x1FF58000])

            elif (x, y) in PCI_LOCATIONS:
                setup_noc_by_address([0x1FD00000, 0x1FD08000])

            elif x == 0 or y == 0 or y == 6:
                setup_noc_by_xy(x, y, [0xFFB20000, 0xFFB30000])

            else:
                # Tensix core node
                noc_reg_bases = [0xFFB20000, 0xFFB30000]
                setup_noc_by_xy(x, y, noc_reg_bases)

                # Set NIU_CFG_0 tile clock disable based on core harvesting.
                rmw_val = device.noc_read32(x, y, 0, noc_reg_bases[0] + NIU_CFG_0)
                rmw_val &= ~(1 << 12)
                rmw_val |= (0 if is_tensix_core_loc(x, y) else 1) << 12
                device.noc_write32(x, y, 0, noc_reg_bases[0] + NIU_CFG_0, rmw_val)

                n1x = GRID_SIZE_X - x - 1
                n1y = GRID_SIZE_Y - y - 1

                rmw_val = device.noc_read32(n1x, n1y, 1, noc_reg_bases[1] + NIU_CFG_0)
                rmw_val &= ~(1 << 12)
                rmw_val |= (0 if is_tensix_core_loc(x, y) else 1) << 12
                device.noc_write32(n1x, n1y, 1, noc_reg_bases[1] + NIU_CFG_0, rmw_val)


def assert_all_riscv_soft_reset(device):
    BRISC_SOFT_RESET = 1 << 11
    TRISC_SOFT_RESETS = (1 << 12) | (1 << 13) | (1 << 14)
    NCRISC_SOFT_RESET = 1 << 18
    device.noc_broadcast32(
        0xFFB121B0, BRISC_SOFT_RESET | TRISC_SOFT_RESETS | NCRISC_SOFT_RESET
    )
    pass


def all_riscs_deassert_reset(device):
    global fw_defines
    device.arc_msg(
        fw_defines["MSG_TYPE_DEASSERT_RISCV_RESET"], wait_for_done=True, arg0=0, arg1=0
    )


def msg_reset(device):
    global fw_defines

    """
    
    self.all_riscs_assert_reset() -> axi write i/

    self._tensix_toggle_reset() -> asc_msg i/

    self.setup_interface()

    self.assert_all_riscv_soft_reset() -> noc_broadcast32 i/

    self.all_riscs_deassert_reset() -> arc_msg i/

    """

    all_riscs_assert_reset()
    msg_tensix_toggle_reset()
    setup_interface()
    assert_all_riscv_soft_reset()
    all_riscs_deassert_reset()


def main():
    global fw_defines
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


if __name__ == "__main__":
    main()
