# SPDX-FileCopyrightText: © 2023 Tenstorrent Inc.
# SPDX-License-Identifier: Apache-2.0

from pyluwen import PciChip
from pyluwen import detect_chips    
    
 
def main():
    try:
        devices = detect_chips()
        
    except Exception as e:
        print(e)
        print("Exiting...")
        return -1
     
    for i, device in enumerate(devices):
        # print(f"{i}" , dir(device))
        try:
            # if device.as_wh():
                print(f"{i}: {device.get_pci_bdf()}, {device.board_id()}")
                telem_struct = device.get_telemetry()
                import jsons
                map = jsons.dump(telem_struct)
                if  device.as_gs():
                    for key in map.keys():
                        print(key, hex(map[key]))
                
            # elif device.as_gs():
            #     print(dir(device.as_gs()))
            #     print(device.board_id())
            #     print(f"{i}: {device.get_pci_bdf()}, {device.as_gs().pci_board_type()}") 
        except:
            print(f"{i}: REMOTE, {device.board_id()}") 
        
        
         
if __name__ == "__main__":
    main()
