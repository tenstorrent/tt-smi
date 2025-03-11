# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## 3.0.11 - 07/03/25

- Chore - bumped luwen version to detect when not running in a tui

## 3.0.10 - 10/03/25

- Chore - bumped luwen version to include protoc lib detection check

## 3.0.9 - 07/03/25

- Chore - bumped luwen version to fail with an error if chip reinit fails

## 3.0.8 - 06/03/25

- Chore - bumped luwen version to include telemetry check during bh arc init

## 3.0.7 - 04/02/25

- Updated tools common to 1.4.14 allowing users to disable the sw_version fetching via reset config
- Updated README with instructions to disable reporting and the improved "-f" option.

## 3.0.6 - 01/29/25

- The "Host Info" and "Compatibility" infoboxes have been merged into a more compact
unified box. Any compatibility issues are displayed modally.
- Calling `tt-smi -f -` now behaves like `tt-smi -s`, printing snapshot info to STDOUT

## 3.0.5 - 01/07/25

- Specifying `tt-smi -s` now prints a snapshot to stdout, per issue 39
    - `tt-smi -f <optional filename>` now behaves like `tt-smi -s -f`
    - tt-smi now attempts to determine if the output is a tty to better support scripting (primarily no statusbars)
    - Added `--snapshot_no_tty` arg to force no-tty behavior
- Updated tt-tools-common to 1.4.10

## 3.0.4 - 11/12/2024

### Changed

- Specifing `tt-smi -r` now resets all pci devices
- Updated tt-tools common; a failed reset now exists with a failcode

## 3.0.3 - 23/10/2024

- Bumped tt-tools-common and luwen versions to improve reset reliability when chip is inaccessible.

## 3.0.2 - 09/10/2024

- Bug fix for PCIE Gen1 reporting
- Added spelling fixes

## 3.0.1 - 16/09/2024

- Bumped luwen lib versions (0.4.0 -> 0.4.3)

## 3.0.0 - 22/07/2024

- NO BREAKING CHANGES! Major version bump to signify new generation of product.
- Added BH support
  - Telemetry reporting on GUI
  - Reset support
- Currently no limits are reported for telemetry - support incoming soon

## 2.2.3 - 12/07/2024

### Updated

- Added FW_Bundle_Version under the fw version tab
- Bumped luwen lib versions (0.3.8 -> 0.3.11)
- Bumped tt-tools-common lib versions (1.4.4 -> 1.4.5)

## 2.2.2 - 21/06/2024

### Updated
- Pydantic library version bump (1.* -> >=1.2) to resolve: [TT-SMI issue #27](https://github.com/tenstorrent/tt-smi/issues/27)
- tt-tools-common version bump (1.4.3 -> 1.4.4) to align pydantic, requests and tqdm library versions

## 2.2.1 - 14/05/2024

### Updated

- Bumped textual (0.59.0), luwen (0.3.8) and tt_tools_common (1.4.3) lib versions
- Removed unused python libraries

## 2.2.0 - 22/03/2024

### Added
- RUST_BACKTRACE env variable to get detailed device failure logs
- detect_device_fallible as the default device detection
  - improved error msg's for failures

### Fixed
- PCI speed/width in the snapshot
- list option now matches dev/tenstorrent device IDS
- reset_config generation is on local devices only

### Migrated
- All reset related code to tt_tools_common
- All reset config generation related code to tt_tools_common

## 2.1.0 - 08/03/2024

### Migrated
- All reset related code to tt_tools_common
- All reset config generation related code to tt_tools_common

### Fixed
- PCIE gen speed reporting bug
- Removed unused GUI footer option

## 2.0.0 - 08/02/2024
All WH related SMI support - no breaking changes
### Added
- WH support
- Coordinate reporting in GUI
- WH Resets
  - WH board level reset support
  - Mobo reset support
- Generate a reset config file and reset boards with it

### Fixed
- PCIE gen speed reporting from sysfs


## 1.1.0 - 29/01/2024

### Added
- Latest SW version section to let users know if their TT software is out of date


## 1.0.0 - 20/10/2023

First release of opensource tt-smi

### Added
- GS support and Tensix reset support
