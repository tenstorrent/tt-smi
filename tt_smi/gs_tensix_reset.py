# SPDX-FileCopyrightText: © 2023 Tenstorrent Inc.
# SPDX-License-Identifier: Apache-2.0

"""
This file contains functions used to reset tensix cores on a Grayskull chip.
"""

import os
import functools
import itertools
from pyluwen import PciChip
from tt_smi.registers import Registers
from tt_smi.ui.common_themes import CMD_LINE_COLOR
from tt_utils_common import init_fw_defines, int_to_bits


class GSTensixReset:
    """
    This class is used to reset tensix cores on a GS chip.
    """

    # GS Magic numbers for tensix resetting and NOC setup
    NIU_CFG_0 = 0x100  # bit 0 is CG enable, bit 12 is tile clock disable
    ROUTER_CFG_0 = 0x104  # bit 0 is CG enable

    ROUTER_CFG_1 = 0x108
    ROUTER_CFG_3 = 0x110
    GRID_SIZE_X = 13
    GRID_SIZE_Y = 12
    NUM_TENSIX_X = GRID_SIZE_X - 1
    NUM_TENSIX_Y = GRID_SIZE_Y - 2
    NOC_MAX_BACKOFF_EXP = 0xF
    PHYS_Y_TO_NOC_0_Y = [0, 11, 1, 10, 2, 9, 3, 8, 4, 7, 5, 6]
    DRAM_LOCATIONS = [
        (1, 6),
        (4, 6),
        (7, 6),
        (10, 6),
        (1, 0),
        (4, 0),
        (7, 0),
        (10, 0),
    ]
    ARC_LOCATIONS = [(0, 2)]
    PCI_LOCATIONS = [(0, 4)]

    def __init__(
        self,
        device: PciChip,
        axi_registers: Registers,
    ):
        self.ROUTING_TO_PHYSICAL_TABLE = dict()
        for phys_y in range(self.GRID_SIZE_Y):
            for phys_x in range(self.GRID_SIZE_X):
                rout_x = self._physical_to_routing(self.GRID_SIZE_X, phys_x)
                rout_y = self._physical_to_routing(self.GRID_SIZE_Y, phys_y)
                self.ROUTING_TO_PHYSICAL_TABLE[(rout_x, rout_y)] = (phys_x, phys_y)
        self.device = device
        self.axi_registers = axi_registers
        self.fw_defines = init_fw_defines(self.device)
        self.harvested_rows = self.get_harvested_rows()
        self.core_list = self.get_core_list()

    @staticmethod
    def _physical_to_routing(limit, phys):
        if (phys % 2) == 0:
            rout = phys // 2
        else:
            rout = limit - 1 - (phys // 2)
        return rout

    def get_harvested_rows(self):
        """
        Get the rows that are harvested on the chip based on efuses.
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
        bad_mem_bits = harvesting_fuses & 0x3FF
        bad_logic_bits = (harvesting_fuses >> 10) & 0x3FF

        bad_row_bits = (bad_mem_bits | bad_logic_bits) << 1
        bad_physical_rows = int_to_bits(bad_row_bits)
        disabled_rows = frozenset(
            map(
                lambda y: self.PHYS_Y_TO_NOC_0_Y[self.GRID_SIZE_Y - y - 1],
                bad_physical_rows,
            )
        )
        return disabled_rows

    def get_core_list(self):
        """
        Using the harvesting fuses, get a list of tensix cores that are not harvested.

        Returns:
            Dict of cores that are not harvested
        """
        good_rows = filter(
            lambda y: y not in self.harvested_rows, [1, 2, 3, 4, 5, 7, 8, 9, 10, 11]
        )
        good_cores = list(
            itertools.product(list(range(1, self.GRID_SIZE_X)), good_rows)
        )
        core_list = {
            c: None for c in good_cores
        }  # dict for deterministic order and fast contains checks.
        return core_list

    def get_noc_router_cfg(self):
        """
        Compute the noc router settings for broadcast disable based on harvested rows.
        """
        broadcast_disabled_rows = [0, 6]
        broadcast_disabled_rows += self.harvested_rows
        noc0_router_cfg_1 = 1 << 0  # disable broadcast to column 0
        noc1_router_cfg_1 = (
            1 << 12
        )  # remap noc0 to noc1: disable broadcast to column 12
        noc0_router_cfg_3 = functools.reduce(
            int.__or__, (1 << y for y in broadcast_disabled_rows), 0
        )
        broadcast_disabled_rows_noc1 = map(
            lambda y: self.GRID_SIZE_Y - y - 1, broadcast_disabled_rows
        )
        noc1_router_cfg_3 = functools.reduce(
            int.__or__, map(lambda y: 1 << y, broadcast_disabled_rows_noc1), 0
        )
        return (
            noc0_router_cfg_1,
            noc1_router_cfg_1,
            noc0_router_cfg_3,
            noc1_router_cfg_3,
        )

    def set_safe_clks(self, enter_safe_clks: bool):
        """
        Send arc msg to enter safe clks mode.
        It lowers the clks to a safe level to toggle tensix resets
        """
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

    def all_riscs_assert_hard_reset(self):
        """
        Place all risc's into hard reset. This requires set_safe_clks to be run prior.
        """
        for i in range(8):
            self.axi_registers.write32(f"ARC_RESET.RISCV_RESET[{i}]", 0x0)

    def tensix_toggle_reset(self):
        """
        Put all tensix's into hard reset and takes them out again.
        This requires set_safe_clks to be run prior.
        This includes both harvested and non-harvested tensix's because we need all tensix's NOC routers.
        """
        for i in range(8):
            self.axi_registers.write32(f"ARC_RESET.TENSIX_RESET[{i}]", 0x0)
        for i in range(8):
            self.axi_registers.write32(f"ARC_RESET.TENSIX_RESET[{i}]", 0xFFFFFFFF)

    def is_tensix_core_loc(self, x, y):
        """
        Checks if the given noc 0 coordinate is a tensix core.
        """
        return (x, y) in self.core_list

    def setup_noc(self):
        """
        This configures all NOC notes.
        It sets broadcast disables, CG enables, and maximum exponential backoff.
        It activates the tile clk disable for harvested tensix's.
        """
        self.axi_registers.write_fields(
            "ARC_RESET.DDR_RESET", {"axi_reset": 1, "ddrc_reset": 1}, init=0
        )
        device = self.device.as_gs()
        (
            noc0_router_cfg_1,
            noc1_router_cfg_1,
            noc0_router_cfg_3,
            noc1_router_cfg_3,
        ) = self.get_noc_router_cfg()

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
                0, x, y, reg_bases[0] + self.ROUTER_CFG_1, noc0_router_cfg_1
            )
            device.noc_write32(
                0, x, y, reg_bases[0] + self.ROUTER_CFG_3, noc0_router_cfg_3
            )
            device.noc_write32(
                1, n1x, n1y, reg_bases[1] + self.ROUTER_CFG_1, noc1_router_cfg_1
            )
            device.noc_write32(
                1, n1x, n1y, reg_bases[1] + self.ROUTER_CFG_3, noc1_router_cfg_3
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

            device.pci_axi_write32(reg_bases[0] + self.ROUTER_CFG_1, noc0_router_cfg_1)
            device.pci_axi_write32(reg_bases[0] + self.ROUTER_CFG_3, noc0_router_cfg_3)
            device.pci_axi_write32(reg_bases[1] + self.ROUTER_CFG_1, noc1_router_cfg_1)
            device.pci_axi_write32(reg_bases[1] + self.ROUTER_CFG_3, noc1_router_cfg_3)

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
        """
        Assert all riscv soft resets using a noc broadcast.
        This reset includes only un-harvested tensix's.
        """
        device = self.device.as_gs()
        BRISC_SOFT_RESET = 1 << 11
        TRISC_SOFT_RESETS = (1 << 12) | (1 << 13) | (1 << 14)
        NCRISC_SOFT_RESET = 1 << 18
        device.noc_broadcast32(
            0, 0xFFB121B0, BRISC_SOFT_RESET | TRISC_SOFT_RESETS | NCRISC_SOFT_RESET
        )

    def noc_loc_to_reset_mask(self, noc_x, noc_y):
        """
        Get the reset bit location for a specific tensix core.
        This applies to both RISCV_RESET and TENSIX_RESET registers.
        """
        phys_x, phys_y = self.ROUTING_TO_PHYSICAL_TABLE[(noc_x, noc_y)]
        blue_x, blue_y = phys_x - 1, phys_y - 1
        reset_bit_index = blue_x * self.NUM_TENSIX_X + blue_y
        reset_reg_index = reset_bit_index // 32
        reset_bit_mask = 1 << (reset_bit_index % 32)
        return reset_reg_index, reset_bit_mask

    def all_tensix_reset_mask(self):
        """
        Get the reset mask for un-harvested tensix cores.
        """
        reset_mask = [0] * 8

        for x, y in self.core_list.keys():
            index, bit_mask = self.noc_loc_to_reset_mask(x, y)
            reset_mask[index] |= bit_mask

        return reset_mask

    def all_riscs_deassert_hard_reset(self):
        """
        Deassert riscv hard resets. We use a mask to get the un-harvested tensix's.
        We only deassert this on un-harvested tensix's because un-harvested tensix's recieved the soft reset signal
        The harvested tensix's need to stay in a state of hard reset.
        """
        for index, mask in enumerate(self.all_tensix_reset_mask()):
            self.axi_registers.write32(f"ARC_RESET.RISCV_RESET[{index}]", mask)

    def tensix_reset(self) -> None:
        """
        This resets all tensix cores on a GS chip, leaving the un-harvested risc's in soft reset.
        """
        print(
            CMD_LINE_COLOR.YELLOW, "Lowering clks to safe value...", CMD_LINE_COLOR.ENDC
        )
        self.set_safe_clks(True)
        try:
            print(
                CMD_LINE_COLOR.YELLOW,
                "Beginning reset sequence...",
                CMD_LINE_COLOR.ENDC,
            )
            self.all_riscs_assert_hard_reset()
            self.tensix_toggle_reset()
            self.setup_noc()
            self.assert_all_riscv_soft_reset()
            self.all_riscs_deassert_hard_reset()
            print(
                CMD_LINE_COLOR.YELLOW,
                "Finishing reset sequence...",
                CMD_LINE_COLOR.ENDC,
            )
        finally:
            print(
                CMD_LINE_COLOR.YELLOW,
                "Returning clks to original values...",
                CMD_LINE_COLOR.ENDC,
            )
            self.set_safe_clks(False)
