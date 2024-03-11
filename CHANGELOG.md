# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
