ROOT?=$(shell git rev-parse --show-toplevel)

# Make sure that the exit code propagites through pipes. Otherwise "false | tee" returns 0 and masks failure.
SHELL=/bin/bash -o pipefail

PYTHON=python3
BUILD_DIR=build

#@>> t6py Makefile
# - dev: Create an editible wheel for developing tenstorrent
#@<<
.PHONY: dev
dev: $(BUILD_DIR)
	mkdir -p /tmp/$(USER)
	# Sometimes the log directory is /tmp/None
	mkdir -p /tmp/None
	pip install --upgrade --ignore-installed -ve . | tee ../$(BUILD_DIR)/packages.log
	$(PRINT_SUCCESS)

.PHONY: packages
packages: $(BUILD_DIR)
	mkdir -p /tmp/$(USER)
	cd packages && $(PYTHON) setup.py bdist_wheel | tee ../$(BUILD_DIR)/packages.log
	cd packages && $(PYTHON) setup_tt_tools.py bdist_wheel | tee ../$(BUILD_DIR)/packages_tt_tools.log
	$(PRINT_SUCCESS)
