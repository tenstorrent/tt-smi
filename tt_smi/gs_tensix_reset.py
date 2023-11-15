from pyluwen import PciChip
from pathlib import Path
from importlib.resources import path
from yaml import safe_load
import functools
import itertools
import jsons
import time
from typing import Optional, OrderedDict, Callable
import os
import importlib.resources
from contextlib import contextmanager
import yaml
from tt_smi.registers import Registers


def get_chip_data(chip, file, internal: bool):
    with importlib.resources.path("tt_smi", "") as path:
        if chip.as_wh() is not None:
            prefix = "wormhole"
        elif chip.as_gs() is not None:
            prefix = "grayskull"
        else:
            raise Exception("Only support fw messages for Wh or GS chips")
        if internal:
            prefix = f".ignored/{prefix}"
        else:
            prefix = f"data/{prefix}"
        return open(str(path.joinpath(f"{prefix}/{file}")))


def init_fw_defines(chip):
    fw_defines = safe_load(get_chip_data(chip, "fw_defines.yaml", False))
    return fw_defines


def _physical_to_routing(limit, phys):
    if (phys % 2) == 0:
        rout = phys // 2
    else:
        rout = limit - 1 - (phys // 2)
    return rout


def int_to_bits(x):
    return list(filter(lambda b: x & (1 << b), range(x.bit_length())))


def reverse_mapping_list(l):
    ret = [0] * len(l)
    for idx, val in enumerate(l):
        ret[val] = idx
    return ret


class GSTensixReset:
    """
    This class is used to reset tensix cores on a GS chip.
    """

    def __init__(
        self,
        device: PciChip,
        axi_registers: Registers,
    ):
        # GS Magic numbers for tensix resetting and NOC setup
        self.NIU_CFG_0 = 0x100  # bit 0 is CG enable, bit 12 is tile clock disable
        self.ROUTER_CFG_0 = 0x104  # bit 0 is CG enable

        self.ROUTER_CFG_1 = 0x108
        self.ROUTER_CFG_3 = 0x110
        self.GRID_SIZE_X = 13
        self.GRID_SIZE_Y = 12
        self.NUM_TENSIX_X = self.GRID_SIZE_X - 1
        self.NUM_TENSIX_Y = self.GRID_SIZE_Y - 2
        self.NOC_MAX_BACKOFF_EXP = 0xF
        self.PHYS_Y_TO_NOC_0_Y = [0, 11, 1, 10, 2, 9, 3, 8, 4, 7, 5, 6]
        self.DRAM_LOCATIONS = [
            (1, 6),
            (4, 6),
            (7, 6),
            (10, 6),
            (1, 0),
            (4, 0),
            (7, 0),
            (10, 0),
        ]
        self.ARC_LOCATIONS = [(0, 2)]
        self.PCI_LOCATIONS = [(0, 4)]
        self.ROUTING_TO_PHYSICAL_TABLE = dict()
        for phys_y in range(self.GRID_SIZE_Y):
            for phys_x in range(self.GRID_SIZE_X):
                rout_x = _physical_to_routing(self.GRID_SIZE_X, phys_x)
                rout_y = _physical_to_routing(self.GRID_SIZE_Y, phys_y)
                self.ROUTING_TO_PHYSICAL_TABLE[(rout_x, rout_y)] = (phys_x, phys_y)
        self.device = device
        self.axi_registers = axi_registers
        self.fw_defines = init_fw_defines(self.device)
        self.harvesting_fuses = self.get_harvesting()
        (
            self.core_list,
            self.noc0_router_cfg_1,
            self.noc1_router_cfg_1,
            self.noc0_router_cfg_3,
            self.noc1_router_cfg_3,
        ) = self.get_core_list()

    def get_harvesting(self):
        """
        Get the harvesting fuses from the chip.

        Returns:
            list of harvested rows
        """
        if "T6PY_HARVESTING_OVERRIDE" in os.environ:
            harvesting_fuses = int(os.environ["T6PY_HARVESTING_OVERRIDE"], 0)
        else:
            harvesting_fuses, exit_code = self.device.arc_msg(
                self.fw_defines["MSG_TYPE_ARC_GET_HARVESTING"],
                wait_for_done=True,
                arg0=0,
                arg1=0,
            )
            if exit_code != 0:
                assert False, "FW is too old, please update fw"
        return harvesting_fuses

    def get_core_list(self):
        """
        Using the harvesting fuses, get a list of cores that are not harvested.

        Returns:
            Dict of cores that are not harvested
        """
        # disable broadcast to rows 0, 6 and any in _disabled_rows.
        bad_mem_bits = self.harvesting_fuses & 0x3FF
        bad_logic_bits = (self.harvesting_fuses >> 10) & 0x3FF

        bad_row_bits = (bad_mem_bits | bad_logic_bits) << 1
        bad_physical_rows = int_to_bits(bad_row_bits)
        broadcast_disabled_rows = [0, 6]
        disabled_rows = frozenset(
            map(
                lambda y: self.PHYS_Y_TO_NOC_0_Y[self.GRID_SIZE_Y - y - 1],
                bad_physical_rows,
            )
        )
        broadcast_disabled_rows += disabled_rows
        good_rows = filter(
            lambda y: y not in disabled_rows, [1, 2, 3, 4, 5, 7, 8, 9, 10, 11]
        )
        good_cores = list(
            itertools.product(list(range(1, self.GRID_SIZE_X)), good_rows)
        )
        core_list = OrderedDict(map(lambda c: (c, None), good_cores))
        noc0_router_cfg_1 = 1 << 0  # disable broadcast to column 0
        noc1_router_cfg_1 = (
            1 << 12
        )  # remap noc0 to noc1: disable broadcast to column 12
        noc0_router_cfg_3 = functools.reduce(
            int.__or__, map(lambda y: 1 << y, broadcast_disabled_rows), 0
        )
        broadcast_disabled_rows_noc1 = map(
            lambda y: self.GRID_SIZE_Y - y - 1, broadcast_disabled_rows
        )
        noc1_router_cfg_3 = functools.reduce(
            int.__or__, map(lambda y: 1 << y, broadcast_disabled_rows_noc1), 0
        )
        return (
            core_list,
            noc0_router_cfg_1,
            noc1_router_cfg_1,
            noc0_router_cfg_3,
            noc1_router_cfg_3,
        )

    def set_safe_clks(self, enter_safe_clks: bool):
        """Send arc msg to enter safe clks mode. It lowers the clks to a safe level to toggle tensix resets"""
        if enter_safe_clks:
            self.device.arc_msg(
                self.fw_defines["MSG_TYPE_RESET_SAFE_CLKS"],
                wait_for_done=True,
                arg0=1,
                arg1=0,
            )
        else:
            # Return clks to before safe clks mode
            self.device.arc_msg(
                self.fw_defines["MSG_TYPE_RESET_SAFE_CLKS"],
                wait_for_done=True,
                arg0=0,
                arg1=0,
            )

    def all_riscs_assert_reset(self):
        for i in range(8):
            self.axi_registers.write32(f"ARC_RESET.RISCV_RESET[{i}]", 0x0)

    def msg_tensix_toggle_reset(self):
        for i in range(8):
            self.axi_registers.write32(f"ARC_RESET.TENSIX_RESET[{i}]", 0x0)
        for i in range(8):
            self.axi_registers.write32(f"ARC_RESET.TENSIX_RESET[{i}]", 0xFFFFFFFF)

    def is_tensix_core_loc(self, x, y):
        return (x, y) in self.core_list

    def setup_interface(self):
        self.axi_registers.write_fields(
            "ARC_RESET.DDR_RESET", {"axi_reset": 1, "ddrc_reset": 1}, init=0
        )
        device = self.device.as_gs()

        # Write all the registers for NOC NIU & router on a node.
        # x, y are NOC0 coordinates. reg_bases[i] is NOCi reg base address
        def setup_noc_by_xy(x, y, reg_bases):
            # x, y are NOC-noc_id coordinates. noc_reg_base is for noc_id.
            # This writes values that are NOC-independent.
            def setup_noc_common(x, y, noc_id, noc_reg_base):
                # CG enable
                rmw_val = device.noc_read32(noc_id, x, y, noc_reg_base + self.NIU_CFG_0)
                device.noc_write32(
                    noc_id, x, y, noc_reg_base + self.NIU_CFG_0, rmw_val | 0x1
                )
                rmw_val = device.noc_read32(
                    noc_id, x, y, noc_reg_base + self.ROUTER_CFG_0
                )
                device.noc_write32(
                    noc_id, x, y, noc_reg_base + self.ROUTER_CFG_0, rmw_val | 0x1
                )

                # maximum exponential backoff
                rmw_val = device.noc_read32(
                    noc_id, x, y, noc_reg_base + self.ROUTER_CFG_0
                )
                rmw_val &= ~0xF00
                rmw_val |= self.NOC_MAX_BACKOFF_EXP << 8
                device.noc_write32(
                    noc_id, x, y, noc_reg_base + self.ROUTER_CFG_0, rmw_val
                )

            n1x = self.GRID_SIZE_X - x - 1
            n1y = self.GRID_SIZE_Y - y - 1

            device.noc_write32(
                0, x, y, reg_bases[0] + self.ROUTER_CFG_1, self.noc0_router_cfg_1
            )
            device.noc_write32(
                0, x, y, reg_bases[0] + self.ROUTER_CFG_3, self.noc0_router_cfg_3
            )
            device.noc_write32(
                1, n1x, n1y, reg_bases[1] + self.ROUTER_CFG_1, self.noc1_router_cfg_1
            )
            device.noc_write32(
                1, n1x, n1y, reg_bases[1] + self.ROUTER_CFG_3, self.noc1_router_cfg_3
            )

            setup_noc_common(x, y, 0, reg_bases[0])
            setup_noc_common(n1x, n1y, 1, reg_bases[1])

        # Write all the registers for NOC NIU & router on a node.
        # reg_bases[i] are PCI BAR-relative addresses for registers.
        def setup_noc_by_address(reg_bases):
            # This writes values that are NOC-independent.
            def setup_noc_common(noc_reg_base):
                # CG enable
                rmw_val = device.pci_axi_read32(noc_reg_base + self.NIU_CFG_0)
                # print(f"rmw_val NIU_CFG_0 = {rmw_val}")
                device.pci_axi_write32(noc_reg_base + self.NIU_CFG_0, rmw_val | 0x1)
                rmw_val = device.pci_axi_read32(noc_reg_base + self.ROUTER_CFG_0)
                # print(f"rmw_val ROUTER_CFG_0 = {rmw_val}")
                device.pci_axi_write32(noc_reg_base + self.ROUTER_CFG_0, rmw_val | 0x1)

                # maximum exponential backoff
                rmw_val = device.pci_axi_read32(noc_reg_base + self.ROUTER_CFG_0)
                rmw_val &= ~0xF00
                rmw_val |= self.NOC_MAX_BACKOFF_EXP << 8
                # device.pci_axi_write32(noc_reg_base + ROUTER_CFG_0, rmw_val)

            device.pci_axi_write32(
                reg_bases[0] + self.ROUTER_CFG_1, self.noc0_router_cfg_1
            )
            device.pci_axi_write32(
                reg_bases[0] + self.ROUTER_CFG_3, self.noc0_router_cfg_3
            )
            device.pci_axi_write32(
                reg_bases[1] + self.ROUTER_CFG_1, self.noc1_router_cfg_1
            )
            device.pci_axi_write32(
                reg_bases[1] + self.ROUTER_CFG_3, self.noc1_router_cfg_3
            )

            setup_noc_common(reg_bases[0])
            setup_noc_common(reg_bases[1])

        for y in range(0, self.GRID_SIZE_Y):
            for x in range(0, self.GRID_SIZE_X):
                if (x, y) in self.DRAM_LOCATIONS:
                    setup_noc_by_xy(x, y, [0xFFFF4000, 0xFFFF5000])

                elif (x, y) in self.ARC_LOCATIONS:
                    setup_noc_by_address([0x1FF50000, 0x1FF58000])

                elif (x, y) in self.PCI_LOCATIONS:
                    setup_noc_by_address([0x1FD00000, 0x1FD08000])

                elif x == 0 or y == 0 or y == 6:
                    setup_noc_by_xy(x, y, [0xFFB20000, 0xFFB30000])

                else:
                    # Tensix core node
                    noc_reg_bases = [0xFFB20000, 0xFFB30000]
                    setup_noc_by_xy(x, y, noc_reg_bases)

                    # Set NIU_CFG_0 tile clock disable based on core harvesting.
                    rmw_val = device.noc_read32(
                        0, x, y, noc_reg_bases[0] + self.NIU_CFG_0
                    )
                    rmw_val &= ~(1 << 12)
                    rmw_val |= (0 if self.is_tensix_core_loc(x, y) else 1) << 12
                    device.noc_write32(
                        0, x, y, noc_reg_bases[0] + self.NIU_CFG_0, rmw_val
                    )

                    n1x = self.GRID_SIZE_X - x - 1
                    n1y = self.GRID_SIZE_Y - y - 1

                    rmw_val = device.noc_read32(
                        1, n1x, n1y, noc_reg_bases[1] + self.NIU_CFG_0
                    )
                    rmw_val &= ~(1 << 12)
                    rmw_val |= (0 if self.is_tensix_core_loc(x, y) else 1) << 12
                    device.noc_write32(
                        1, n1x, n1y, noc_reg_bases[1] + self.NIU_CFG_0, rmw_val
                    )

    def assert_all_riscv_soft_reset(self):
        device = self.device.as_gs()
        BRISC_SOFT_RESET = 1 << 11
        TRISC_SOFT_RESETS = (1 << 12) | (1 << 13) | (1 << 14)
        NCRISC_SOFT_RESET = 1 << 18
        device.noc_broadcast32(
            0, 0xFFB121B0, BRISC_SOFT_RESET | TRISC_SOFT_RESETS | NCRISC_SOFT_RESET
        )

    def noc_loc_to_reset_mask(self, noc_x, noc_y):
        phys_x, phys_y = self.ROUTING_TO_PHYSICAL_TABLE[(noc_x, noc_y)]
        blue_x, blue_y = phys_x - 1, phys_y - 1
        reset_bit_index = blue_x * self.NUM_TENSIX_X + blue_y
        reset_reg_index = reset_bit_index // 32
        reset_bit_mask = 1 << (reset_bit_index % 32)
        return reset_reg_index, reset_bit_mask

    def all_tensix_reset_mask(self):
        reset_mask = [0] * 8

        for x, y in self.core_list.keys():
            index, bit_mask = self.noc_loc_to_reset_mask(x, y)
            reset_mask[index] |= bit_mask

        return reset_mask

    def all_riscs_deassert_reset(self):
        for index, mask in enumerate(self.all_tensix_reset_mask()):
            self.axi_registers.write32(f"ARC_RESET.RISCV_RESET[{index}]", mask)
