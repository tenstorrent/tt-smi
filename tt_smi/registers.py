"""
This file manages the register map for the device. 
It loads the register map from yaml files, and provides functions to read and write registers.
"""

import os
import re
import struct
import yaml
import importlib.resources
from contextlib import contextmanager


@contextmanager
def package_data_file(package_relative_path):
    # 3.7 doesn't have importlib.resources.files & importlib.resources.as_file.
    with importlib.resources.path("tt_smi", "") as package_base_path:
        yield package_base_path / package_relative_path


INDEX_REGEXP = re.compile(r"\[\s*(\d*)\s*\]")
NAME_WITH_INDEX_REGEXP = re.compile(r"([^[\t ]*)\s*(\[\s*\d*\s*\])?")


def parse_indexed_register(name):
    m = re.match(NAME_WITH_INDEX_REGEXP, name)

    if m:
        index = None
        if m.group(2) is not None:
            index = int(re.match(INDEX_REGEXP, m.group(2)).group(1))
        ret_val = (m.group(1), index)
    else:
        ret_val = None
    return ret_val


def yaml_load(filepath):
    with open(filepath, "r") as file:
        return yaml.load(file, Loader=yaml.FullLoader)


class Register(object):
    # field_info is the Fields: part of the yaml
    def __init__(self, bit_count, fields, on_set=None, on_get=None):
        self.bit_count = bit_count
        self.fields = fields
        self.on_set_function = on_set
        self.on_get_function = on_get

    def _get_mask_and_shift(self, field_info):
        msb = field_info[1]
        lsb = field_info[2]
        mask = 0xFFFFFFFF >> (31 - (msb - lsb))
        return (mask, lsb)

    # Simple accessors
    def _set(self, data):
        self.data = data
        if self.on_set_function is not None:
            self.on_set_function(data)

    def _get(self):
        if not hasattr(self, "data"):
            raise RuntimeError(
                "Register has no data. Call _set() or one of the write functions."
            )
        if self.on_get_function is not None:
            return self.on_get_function()
        return self.data

    # Fields to write it dictionary of field_name -> value_to_write
    def write_fields(self, **kwargs):
        init = kwargs["__init"] if kwargs and "__init" in kwargs else None
        if init is not None:
            new_val = init
            total_mask = 0xFFFFFFFF  # TODO: this needs to be in terms of bit_count
        else:
            new_val = 0
            total_mask = 0

        for f in kwargs:
            if f == "__init":
                continue
            if f in self.fields:
                f_value = kwargs[f]
                field_info = self.fields[f]
                mask, shift = self._get_mask_and_shift(field_info)
                new_val = new_val & (~(mask << shift))  # Clear
                if f_value & mask != f_value:
                    raise RuntimeError(
                        f"Value written to field '{f}' is too big. The value is 0x{kwargs[f]:0x} which does not fit into mask 0x{mask:0x}."
                    )
                else:
                    new_val = new_val | (f_value << shift)
            else:
                raise RuntimeError(f"Register does not contain field '{f}'")

        if total_mask != 0xFFFFFFFF:
            raise RuntimeError(
                "If argument '__init' is not supplied, all register fields must be set. Alternativelly, use read-modify-write function (rmw_fields) which reads the register first to preserve untouched fields."
            )

        self._set(new_val)

    # Read-modify-write
    # Similar to write_fields, but does not assume that you set the whole value: it will initialize the value with what
    # is read from the register first.
    def rmw_fields(self, **kwargs):
        kwargs["__init"] = self._get()
        self.write_fields(**kwargs)

    # Returns a dictionary with all field names and values
    def read_fields(self):
        fields = {}
        for f in self.fields:
            field_info = self.fields[f]
            mask, shift = self._get_mask_and_shift(field_info)
            fields[f] = (self._get() >> shift) & mask
        return fields


class Registers(object):
    cached_registers = {}

    # Constructor:
    # - address_space_map_filename is the top-level yaml file that specifies register map
    # - reader_function takes 32-bit address and should return the value at that address
    # - writer_function takes 32-bit address, and 32-bit value: it should write the value to that address
    def __init__(self, address_space_map_filename, reader_function, writer_function):
        self.addr_space_map = {}
        self.set_access_functions(reader_function, writer_function)
        self.load_address_space_map(address_space_map_filename)

    # Sanity check for yaml file
    def sanity_check(self, loaded_yaml):
        if loaded_yaml is None:
            return
        for rname in loaded_yaml:
            if rname == "Regsize" or "Fields" not in loaded_yaml[rname]:
                continue
            r = loaded_yaml[rname]
            for fname in r["Fields"]:
                field_info = r["Fields"][fname]
                msb = field_info[1]
                lsb = field_info[2]
                if msb < lsb:
                    raise RuntimeError(
                        "In yaml for register %s: msb < lsb in fields %s"
                        % (rname, fname)
                    )

    def load_address_space_map(self, filename):
        self.filename = filename
        if filename in Registers.cached_registers:
            self.addr_space_map = Registers.cached_registers[filename]
            return
        with package_data_file(filename) as f:
            self.addr_space_map = yaml_load(f)
        for addr_space_name in self.addr_space_map:
            register_definition_filename = self.addr_space_map[addr_space_name][
                "filename"
            ]
            if "offset" not in self.addr_space_map[addr_space_name]:
                self.addr_space_map[addr_space_name]["offset"] = 0
            addr_space_offset = self.addr_space_map[addr_space_name]["offset"]
            # print ("Loading %s %s" % (register_definition_filename, addr_space_offset))
            # Load the file for the space
            regdef_filename = (
                os.path.dirname(filename) + "/" + register_definition_filename
            )
            self.addr_space_map[addr_space_name]["loaded_yaml"] = dict()
            # print ("Loading %s into addr_space %s " % (filename, addr_space_name))
            with package_data_file(regdef_filename) as f:
                loaded_yaml = yaml_load(f)

            if "Regsize" not in loaded_yaml:
                print(loaded_yaml)
                print(regdef_filename)
                raise RuntimeError(f"Regsize not in {regdef_filename}")

            Regsize = int(loaded_yaml["Regsize"])

            for reg in loaded_yaml:
                if reg != "Regsize":
                    loaded_yaml[reg]["Regsize"] = Regsize

            self.addr_space_map[addr_space_name]["loaded_yaml"] = loaded_yaml
            Registers.cached_registers[filename] = self.addr_space_map
            self.sanity_check(loaded_yaml)

    # Access functions work on 32 bit words
    def set_access_functions(self, read_function, write_function):
        self.read_function = read_function
        self.write_function = write_function

    def get_reg_space_addr(self, path):
        addr_space = self.addr_space_map[path]
        return addr_space["offset"]

    def get_path_info(self, path):
        """
        Given a path to a register (or register field), this function returns a tuple of:
        - addr:  32bit register address
        - mask:  covers all possible values for the field/register (0xffffffff for whole register). It is not shifted to the field position - use shift for that.
        - shift: for fields only (0 for whole register)
        - reg_info: full register definition with all fields, address ...
        """
        sections = path.split(".")
        addr_space_identifier = sections[0]

        reg_i = parse_indexed_register(addr_space_identifier)
        addr_space_identifier = reg_i[0]  # Discard the index, as it is not used here

        addr_space = self.addr_space_map[addr_space_identifier]

        if len(sections) == 1:  # This is only address space
            return addr_space["offset"]

        reg_name = sections[1]

        reg_i = parse_indexed_register(reg_name)
        reg_name = reg_i[0]
        array_index = int(reg_i[1]) if reg_i[1] is not None else 0

        if reg_name not in addr_space["loaded_yaml"]:
            assert False, f"Cannot find register {reg_name}"
        reg_info = addr_space["loaded_yaml"][reg_name]
        mask = self.get_mask_for_regsize(reg_info["Regsize"])
        shift = 0
        offset = 0

        if len(sections) > 2:  # There is a field
            field_info = reg_info["Fields"][sections[2]]
            mask, shift = self.get_mask_and_shift(field_info, reg_info["Regsize"])
            if len(field_info) > 4:  # There is an offset
                offset = field_info[3]

        addr = addr_space["offset"] + reg_info["Address"]
        if array_index > 0:
            if array_index >= reg_info["ArraySize"]:
                raise RuntimeError(
                    "Register in array %s has only %d elements, but path %s requested element with index %d"
                    % (reg_name, reg_info["ArraySize"], path, array_index)
                )

            addr = addr + array_index * reg_info["AddressIncrement"]
        addr += offset

        return (addr, mask, shift, reg_info)

    def get_mask_and_shift(self, field_info, regsize):
        msb = field_info[1]
        lsb = field_info[2]
        mask = self.get_mask_for_regsize(regsize) >> (regsize - 1 - (msb - lsb))
        return (mask, lsb)

    def set_write_delay_function(self, f):
        self.write_delay_f = f

    def set_write_delay(self, d):
        self.write_delay_f(d)

    def get_mask_for_regsize(self, regsize):
        if regsize == 32:
            return 0xFFFFFFFF
        elif regsize == 64:
            return 0xFFFFFFFFFFFFFFFF
        else:
            raise RuntimeError(f"Unsupported regsize in {self.filename}")

    # Given a dictionary 'fields' of field_name:value mappings, it sets the register with those values
    # - if 'init' is not provided, all fields must be supplied
    #   if 'init' is provided, the 32 bit value will be initialized to that before fields are written
    def write_fields(self, path, fields, init=None):
        addr, _, _, reg_info = self.get_path_info(path)
        Regsize = reg_info["Regsize"]
        assert Regsize <= 64, "read_fields not supported for Regsize > 64"
        FULL_REG_MASK = self.get_mask_for_regsize(Regsize)

        if init is not None:
            new_val = init
            total_mask = FULL_REG_MASK
        else:
            total_mask = 0
            new_val = 0

        for f in fields:
            if f not in reg_info["Fields"]:
                raise RuntimeError("Cannot find field %s in %s" % (f, path))
            else:
                field_info = reg_info["Fields"][f]
                mask, shift = self.get_mask_and_shift(field_info, reg_info["Regsize"])
                new_val = new_val & (~(mask << shift))  # Clear
                if fields[f] & mask != fields[f]:
                    raise RuntimeError(
                        "Value written to %s.%s is too big. The value is 0x%x which does not fit into mask 0x%x."
                        % (path, f, fields[f], mask)
                    )
                    return
                else:
                    new_val = new_val | (fields[f] << shift)
                    total_mask = total_mask | (mask << shift)

        if total_mask != FULL_REG_MASK:
            raise RuntimeError(
                f"If argument 'init' is not given, you must set all the fields in the register. Alternativelly, use read-modify-write function (rmw_fields) which reads the register first to preserve untouched fields. total_mask: {total_mask:0x} FULL_REG_MASK: {FULL_REG_MASK:0x}"
            )
            # raise RuntimeError ("Register has no data. Call _set() or one of the write functions.")
        if Regsize == 32:
            self.write_function(addr, new_val, path=path)
        else:
            self.write_function(addr, new_val & 0xFFFFFFFF, path=path)
            self.write_function(addr + 4, (new_val >> 32) & 0xFFFFFFFF, path=path)

    # Returns a dictionary with all field names and values
    def read_fields(self, path):
        fields = {}
        addr, _, _, reg_info = self.get_path_info(path)
        Regsize = reg_info["Regsize"]
        assert Regsize <= 64, "read_fields not supported for Regsize > 64"
        val = self.read_function(addr, path=path)
        if Regsize == 64:  # Read the upper 32 bits
            val = val | (self.read_function(addr + 4, path=path) << 32)
        for f in reg_info["Fields"]:
            field_info = reg_info["Fields"][f]
            mask, shift = self.get_mask_and_shift(field_info, reg_info["Regsize"])
            if len(field_info) > 4:  # Handle offsets
                val = self.read_function(addr + field_info[3], path=path)
            fields[f] = (val >> shift) & mask
        return fields

    # Read-modify-write
    # Similar to write_fields, but does not assume that you set the whole value: it will initialize the value with what
    # is read from the register first.
    # Returns the value of the register as it is read
    def rmw_fields(self, path, fields, init=None):
        addr, _, _, reg_info = self.get_path_info(path)
        if (
            len(next(iter(reg_info["Fields"].values()))) > 4
        ):  # Handle multi-register fields
            for f in fields:
                self.write32(f"{path}.{f}", fields[f])
        else:
            assert (
                reg_info["Regsize"] <= 64
            ), "rmw_fields not supported for Regsize > 64"
            current_reg_value = self.read32(path)
            if reg_info["Regsize"] == 64:  # Read the upper 32 bits
                current_reg_value = current_reg_value | (
                    self.read_function(addr + 4, path=path) << 32
                )
            self.write_fields(path, fields, init=current_reg_value)
            return current_reg_value

    def read32(self, path):
        addr, mask, shift, _ = self.get_path_info(path)
        val = self.read_function(addr, path=path)
        if type(val) is int:
            val = val >> shift  # Lose all lower bits
        else:
            raise RuntimeError("Expected integer value, but received %s" % str(val))
        val = val & mask
        return val

    def write32(self, path, data):
        addr, mask, shift, reg_info = self.get_path_info(path)
        if data & ~mask != 0:
            raise RuntimeError(
                "Value written to %s is too big. Value is 0x%x, Which does not fit into mask 0x%x."
                % (path, data, mask)
            )

        if mask != self.get_mask_for_regsize(
            reg_info["Regsize"]
        ):  # Need to read modify write
            current_val = self.read_function(addr, path=path)
            current_val = current_val & (~(mask << shift))  # Clear out the value
            new_val = current_val | (data << shift)
        else:
            new_val = data
        self.write_function(addr, new_val, path=path)

    def write_number_as_float32(self, path, num):
        # I don't want a duck, I want a float!!!!
        as_float32 = struct.pack("f", num)
        as_uint32 = struct.unpack("I", as_float32)
        self.write32(path, as_uint32[0])

    def read_number_as_float32(self, path):
        # I don't want a duck, I want a float!!!!
        val = self.read32(path)
        as_uint32 = struct.pack("I", val)
        as_float32 = struct.unpack("f", as_uint32)
        return as_float32[0]

    # set_value should already be shifted to match set_mask
    def rmw32(self, path, set_mask, set_value):
        assert ~set_mask & set_value == 0
        r = self.read32(path)
        self.write32(path, (r & ~set_mask) | set_value)

    # Gets only the address of a register
    def get_addr(self, path):
        addr, _, _, _ = self.get_path_info(path)
        return addr

    def WrNOC(self, addr, data, tlb):
        noc_addr = (tlb << 20) | (addr & 0xFFFFF)
        self.write_function(noc_addr, data, path="N/A")

    def RdNOC(self, addr, tlb):
        noc_addr = (tlb << 20) | (addr & 0xFFFFF)
        return self.read_function(noc_addr, path="N/A")

    # Finds all register paths that match the regexp
    def search(self, regexp, verbose=False):
        num_matches = 0
        printed_regs = (
            set()
        )  # So we only print the whole register once even if we mach multiple fields
        for addr_space_name in self.addr_space_map:
            addr_space = self.addr_space_map[addr_space_name]["loaded_yaml"]
            if addr_space is None:
                continue
            for reg_name in addr_space:
                reg = addr_space[reg_name]
                if reg_name != "Regsize" and "Fields" in reg:
                    for field_name in reg["Fields"]:
                        path = "%s.%s.%s" % (addr_space_name, reg_name, field_name)
                        m = re.search(regexp, path)
                        if m:
                            print("Matched '%s' in %s" % (m.group(0), path))
                            if verbose and reg_name not in printed_regs:
                                print("%s" % reg)
                                printed_regs.add(reg_name)
                            num_matches = num_matches + 1
        return num_matches

    def dump_scratch_regs(self):
        for i in range(0, 6):
            s = self.read32("ARC_RESET.SCRATCH[%d]" % i)
            print("  scratch[%d]: 0x%-8x %d " % (i, s, s))
