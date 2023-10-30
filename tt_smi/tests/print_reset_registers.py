from tt_smi.tt_smi_backend import TTSMIBackend
from pyluwen import PciChip
from pyluwen import detect_chips


def main():
    global interrupt_received
    interrupt_received = False
    try:
        devices = detect_chips()
    except Exception as e:
        print(e)
        print("Exiting...")
        return -1

    backend = TTSMIBackend(devices=devices, telem_struct_override=None)
    # print(backend.registers[0])
    axi_registers = backend.registers[0]
    print("Before: ", axi_registers.read_fields("ARC_RESET.DDR_RESET"))
    axi_registers.write_fields(
        "ARC_RESET.DDR_RESET", {"axi_reset": 1, "ddrc_reset": 1}, init=0
    )
    print("After: ", axi_registers.read_fields("ARC_RESET.DDR_RESET"))


if __name__ == "__main__":
    main()
