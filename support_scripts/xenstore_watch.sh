#!/bin/bash

# xenstore_watch.sh
# -----------------
# This script is intended to be run on the Xen host machine to monitor a XenStore key for changes.
# It watches a specified XenStore path (default: /local/domain/2/test_key) for value changes.
#
# From within the guest VM when tt-smi reset is called, tt-smi writes "1" to the XenStore key initiates start of the reset sequence. It then writes "0" to the key when the reset is complete.
# This script detects the value transitions from "1" to "0" and triggers a PCI device detach and attach for the specified VM.
# Finally it clears the XenStore key to prepare for the next reset cycle.
#
# Arguments:
#   $1 - XenStore path to watch (default: /local/domain/2/test_key)
#   $2 - PCI BDF (Bus:Device.Function) to detach/attach (default: 31:00.0)
#   $3 - VM name (default: ubuntu22)
#
# Usage:
#   ./xenstore_watch.sh [XENSTORE_PATH] [PCI_BDF] [VM_NAME]

# Default values
DEFAULT_XENSTORE_PATH="/local/domain/2/test_key"  # the "2" here is the domain ID of the guest VM. It can be found by running `sudo xl list` on the host and checking the ID column for the VM.
DEFAULT_PCI_BDF="31:00.0"
DEFAULT_VM_NAME="ubuntu22"

# Get arguments or use defaults
XENSTORE_PATH="${1:-$DEFAULT_XENSTORE_PATH}"
PCI_BDF="${2:-$DEFAULT_PCI_BDF}"
VM_NAME="${3:-$DEFAULT_VM_NAME}"

echo "Watching for changes on: $XENSTORE_PATH"
echo "Using PCI BDF: $PCI_BDF"
echo "Target VM: $VM_NAME"

sudo xenstore-watch "$XENSTORE_PATH" | while read -r changed_path; do
    new_value=$(sudo xenstore-read "$changed_path")
    # Ignore empty values - means reset has not started yet
    if [ "$new_value" == "" ]; then
        continue
    fi
    echo "[$(date)] Path $changed_path changed to: $new_value"
    # When change from 1 -> 0 is detected, perform a pci detach + attach
    if [ "$new_value" == "0" ]; then
        echo "Detected change to 0, performing PCI detach and attach..."
        sudo xl pci-detach "$VM_NAME" "$PCI_BDF"
        sudo xl pci-attach "$VM_NAME" "$PCI_BDF"
        # Empty the xenstore file to reset the key
        sudo xenstore-write "$changed_path" ""
        echo "PCI detach and attach completed."
    fi
done