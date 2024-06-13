# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
- pci speed/width in the snapshot
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
- GS support and tensix reset support
