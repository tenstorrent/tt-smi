#@>>''' reset-throught-adafruit-ftdi manual
"""
This tool uses FTDI-based USB dongle to reset one or more chips.
"""
#@<<

import time
import sys
import argparse
import os.path
import traceback

import platform

import os

# Import GPIO and FT232H modules.
from adafruit_blinka.microcontroller.ftdi_mpsse.ft232h import pin as ft232h

def ftdi_reset(pins):
    # Temporarily disable the built-in FTDI serial driver on Mac & Linux platforms.
    # FT232H.use_FT232H()

    # Figure out which dongle to use
    # num_reset_dongles = len (FT232H.enumerate_device_serials())
    num_reset_dongles = 1

    if num_reset_dongles > 1:
        print ("ERROR: The Adafruit_GPIO.FT232H API does not support multiple dongles properly (%d are connected). " % num_reset_dongles)
        return

    for pin in pins:
        # We must set the levels of the signals first, since otherwise they go to LOW by default.
        # Start the signals in the same state as when exiting. This prevents the glitch on init above which brings the signals to 2V
        pin = ft232h.Pin(pin)
        pin.value(pin.HIGH)
        pin.init(pin.OUT) # This goes to reset wire
        pin.value(pin.LOW)
        print ('Pin %d -> 0' % pin.id)

    time.sleep(0.1)

    for pin in pins:
        pin = ft232h.Pin(pin)
        pin.value(pin.HIGH)
        print ('Pin %d -> 1' % pin.id)

    time.sleep(0.1)


# # Make version available in --help
# VERSION_FILE = utility.package_root_path() + "/data/version.txt"
# if os.path.isfile(VERSION_FILE):
#     VERSION_STR = (open(VERSION_FILE, 'r').read().strip())
#     if __doc__ == None:
#         __doc__ = "Version: %s" % VERSION_STR
#     else:
#         __doc__ = "Version: %s. %s" % (VERSION_STR, __doc__)

# Parse arguments
# parser = utility.ArgumentParser(description=__doc__)
# parser.add_argument('pins', type=int, default=[ 8 ], nargs='*', help='Pins to on the dongle to toggle. Normally 8 for the first device, 9 for the second and so on.')
# parser.add_argument('--no-cfg-restore', default=False, action="store_true", help='When not present, it will restore the PCI config')
# parser.add_argument('--delay', default=0.5, type=float, help='Set the delay between powering the device down and back up')
# args = parser.parse_args()

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('pins', type=int, default=[ 8 ], nargs='*', help='Pins to on the dongle to toggle. Normally 8 for the first device, 9 for the second and so on.')
parser.add_argument('--no-cfg-restore', default=False, action="store_true", help='When not present, it will restore the PCI config')
parser.add_argument('--delay', default=0.5, type=float, help='Set the delay between powering the device down and back up')
args = parser.parse_args()

if os.geteuid() != 0:
    print ("You must run this with sudo")
    sys.exit (1)

ftdi_reset(args.pins)

if not args.no_cfg_restore:
    import subprocess
    import shutil

    print(f"Sleeping for {args.delay} seconds before pci cfg restore")
    time.sleep(args.delay) # Without this, the board can hang requiring host reboot

    lspci_output = subprocess.check_output(['lspci', '-n', '-d', '1e52:']).decode()
    lspci_lines = lspci_output.split ('\n')
    for lspci_line in lspci_lines:
        try:
            bsf = lspci_line.split()[0]
        except:
            continue

        cfg_file_saved_on_boot_path = "/var/run/tenstorrent/saved-config-%s" % bsf
        cfg_file_running = "/sys/bus/pci/devices/0000:%s/config" % bsf
        try:
            # print (f"cp {cfg_file_saved_on_boot_path} {cfg_file_running}")
            shutil.copyfile (cfg_file_saved_on_boot_path, cfg_file_running)
        except:
            print (f"Could not copy {cfg_file_saved_on_boot_path} to {cfg_file_running}")
            print (f"This file must exist and is normally created by /etc/rc.local by running something like this:")
            print ("""
                mkdir -p /var/run/tenstorrent

                echo "Saving the PCI configuration" > /var/run/tenstorrent/status
                BUS_SLOT_FUN_ARRAY=$(lspci -n | awk '/(?:faca|401e)/{print $1}')
                for bsf in $BUS_SLOT_FUN_ARRAY; do
                    PCI_DEVICE_PATH="/sys/bus/pci/devices/0000:$bsf"
                    PCI_DEVICE_CFG_FILE="$PCI_DEVICE_PATH/config"
                    CFG_SAVEFILE="/var/run/tenstorrent/saved-config-$bsf"
                    cp $PCI_DEVICE_CFG_FILE $CFG_SAVEFILE
                done

                echo "DONE: OK" > /var/run/tenstorrent/status
                exit 0
        """)
            sys.exit (1)
    print ("Reset complete")
