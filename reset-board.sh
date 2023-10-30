#!/bin/bash
# set -x # Print all commands
set -e # Stop on first error
SCRIPTPATH="$( cd "$(dirname "$0")" >/dev/null 2>&1 ; pwd -P )"

# Usage info as one of the options
show_help(){
cat << EOF
Used to reset the board
Usage: reset-board [-h or --help] [-n or --noinit]
    -h or --help   : Display this and exit
    -n or --noinit : Do not initialize arc fw with the reset
EOF
}

INIT_ARC_FW=1
# Set up case statement to add more arguments later if needed
# Using -n or --noinit will not initialize arc fw
while :; do
    case $1 in 
    -n|--noinit)
        echo "--noinit flag used, will not initialize ARC FW"
        INIT_ARC_FW=0
        ;;
    -h|--help)
        show_help
        exit 0
        ;;
    *)
        break # Default case break out of loop   
    esac
    shift
done

# Check board presence
NUM_BOARDS=`lspci -d 1e52: | wc -l`
if [ $NUM_BOARDS == "0" ]; then echo "No boards reported by lspci. Exiting"; exit 1; fi
if [ $NUM_BOARDS != "1" ]; then
    echo "Multiple boards detected. Resetting all of them."
    FTDI_PINS_TO_TOGGLE="8 9" # Only do it for two...
fi

# Check FTDI adapter presence
NUM_FTDI=`lsusb | grep FT232H | wc -l`
if [ $NUM_FTDI == "0" ]; then echo "FTDI adapter not detected. Exiting"; exit 1; fi
if [ $NUM_FTDI != "1" ]; then echo "More than one FTDI adapter detected ($NUM_FTDI). Exiting"; exit 1; fi

# Put ARC to sleep in case it is running: this will prevent a problem where we reset ARC while it is talking over I2C
for board in $(seq 0 $(($NUM_BOARDS-1))); do
    set +e
    tt-script scripts/send_fw_msg.py --interface pci:$board --args msg_id=aaa3,msg_arg0=0,msg_arg1=0,waitfordone=1 > /dev/null 2>&1 || true
    tt-script scripts/send_fw_msg.py --interface pci:$board --args msg_id=aa55,msg_arg0=0,msg_arg1=0,waitfordone=1 > /dev/null 2>&1 || true
    set -e
done

# Remove the driver
echo "Removing the driver"
sudo modprobe -r tenstorrent

BUS_SLOT_FUN_ARRAY=$(lspci -n -d 1e52: | awk '{print $1}')

for bsf in $BUS_SLOT_FUN_ARRAY; do
    PCI_DEVICE_PATH="/sys/bus/pci/devices/0000:$bsf"
    PCI_DEVICE_CFG_FILE="$PCI_DEVICE_PATH/config"
    CFG_SAVEFILE="./saved-config-$bsf"
    CFG_VAR_RUN="/var/run/tenstorrent/saved-config-$bsf"

    # Save PCI device configuration
    if [ ! -f "$CFG_VAR_RUN" ]; then # Only if there is no saved file in /var/run
        if [ "$SAVE_PCI_CFG" != "" ] || [ ! -f $CFG_SAVEFILE ]
        then
            echo "Copying $PCI_DEVICE_CFG_FILE to $CFG_SAVEFILE"
            # It is key to do this as a sudo, otherwise only 64 bytes are copied (as opposed to 256)
            sudo cp $PCI_DEVICE_CFG_FILE $CFG_SAVEFILE
        else
            echo "  NOTE: Not saving $CFG_SAVEFILE. Will use previous one."
        fi
    fi
done

#
# Reset the devices
#
# bin/reset-pci $bsf  # Couldn't get this to work. To try again with server motherboards.
sudo $(which python) $SCRIPTPATH/reset-through-adafruit-ftdi.py $FTDI_PINS_TO_TOGGLE --no-cfg-restore

echo "Sleeping for 0.5 seconds"
sleep 0.5

for bsf in $BUS_SLOT_FUN_ARRAY; do
    PCI_DEVICE_PATH="/sys/bus/pci/devices/0000:$bsf"
    PCI_DEVICE_CFG_FILE="$PCI_DEVICE_PATH/config"
    CFG_SAVEFILE="./saved-config-$bsf"
    CFG_VAR_RUN="/var/run/tenstorrent/saved-config-$bsf"
    # Restore PCI device configuration

    if [ ! -f "$CFG_VAR_RUN" ]; then # Only if there is no saved file in /var/run
        echo "Copying $CFG_SAVEFILE to $PCI_DEVICE_CFG_FILE"
        sudo cp $CFG_SAVEFILE $PCI_DEVICE_CFG_FILE
    else
        echo "Restoring PCI config from $CFG_VAR_RUN"
        sudo cp $CFG_VAR_RUN $PCI_DEVICE_CFG_FILE
    fi
done

# Add the driver
echo "Reinstalling the driver"
if [ $INIT_ARC_FW == "0" ]; then
    # If used with --noinit or -n, do not initialize arc fw
    sudo modprobe tenstorrent arc_fw_init=N
else
    sudo modprobe tenstorrent
fi