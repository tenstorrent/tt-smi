# TT-SMI

Tenstorrent System Management Interface (TT-SMI) is a command line utility
to interact with all Tenstorrent devices on host.

Main objective of TT-SMI is to provide a simple and easy to use interface
to collect and display device, telemetry and firmware information.

In addition user can issue Grayskull board tensix core reset.

## Official Repository

[https://github.com/tenstorrent/tt-smi/](https://github.com/tenstorrent/tt-smi/)

# Getting started
Build and editing instruction are as follows -

### Building from Git

Install and source rust for the luwen library
```
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh
source "$HOME/.cargo/env"
```

### Optional
Generate and source a python environment.  This is useful not only to isolate
your environment, but potentially easier to debug and use.  This environment
can be shared if you want to use a single environment for all your Tenstorrent
tools

```
python3 -m venv .venv
source .venv/bin/activate
pip3 install --upgrade pip
```
### Required

Install tt-smi.
```
pip3 install .
```

### Optional - for TT-SMI developers

Generate and source a python3 environment
```
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install pre-commit
```

For users who would like to edit the code without re-building, install SMI in editable mode.
```
pip install --editable .
```
Recommended: install the pre-commit hooks so there is auto formatting for all files on committing.
```
pre-commit install
```

# Usage

Command line arguments
```
tt-smi [-h] [-l] [-v] [-s] [-ls] [-f [filename]] [-g] [-r 0,1 ... or config.json]
```
## Getting Help!

Running tt-smi with the ```-h, --help``` flag should bring up something that looks like this

```
$ tt-smi --help

  usage: tt-smi [-h] [-l] [-v] [-s] [-ls] [-f [filename]] [-g] [-r 0,1 ... or config.json]

  Tenstorrent System Management Interface (TT-SMI) is a command line utility to interact with all Tenstorrent devices on host. Main objective of TT-SMI is to provide a simple and easy to use interface to
  collect and display device, telemetry and firmware information. In addition user can issue Grayskull and Wormhole board level resets.

  optional arguments:
    -h, --help            show this help message and exit
    -l, --local           Run on local chips (Wormhole only)
    -v, --version         show program's version number and exit
    -s, --snapshot        Dump snapshot of current TT-SMI information to .json log.Default: ~/tt_smi/<timestamp>_snapshot.json User can use -f to change filename
    -ls, --list           List boards that are available on host and quits
    -f [filename], --filename [filename]
                          Change filename for test log. Default: ~/tt_smi/<timestamp>_snapshot.json
  -g [GENERATE_RESET_JSON], --generate_reset_json [GENERATE_RESET_JSON]
                        Generate default reset json file that reset consumes. Default stored at ~/.config/tenstorrent/reset_config.json. Update the generated file and use it as an
                        input for the --reset option
    -r 0,1 ... or config.json, --reset 0,1 ... or config.json
                          Provide list of pci index or a json file with reset configs. Find pci index of board using the -ls option. Generate a default reset json file with the -g option.
  ```

Some of these flags will be discussed in more detail in the following sections.

## GUI
To bring up the tt-smi GUI run
```
$ tt-smi
```
This should bring up a display that looks as below.

![tt-smi](images/tt_smi.png)

This is the default mode where user can see device information, telemetry and firmware.

### Latest SW Versions
This section will display the software version of the device. If failures occur, error messages will show as below.

![tt-smi](images/error.png)

### App keyboard shortcuts
All app keyboard shortcuts can be found in the help menu that user can bring up by hitting "h" or clicking the "help" button on the footer.

![help_menu](images/help.png)

## Resets

Another feature of tt-smi is performing resets on WH and GS pci cards, using the  ```-r/ --reset``` argument.
```
$ tt-smi -r 0,1 ... or config.json, --reset 0,1 ... or config.json

    Provide list of pci index or a json file with reset configs. Find pci index of board using the -ls option. Generate a default reset json file with the -g option.
```

To perform the reset, either provide a list of comma separated values of the pci index of the cards on the host, or an input reset_config.json file that can be generated using the ```-g/ --generate_reset_json``` command line argument.

TT-SMI will perform different types of resets depending on the device:
- GS devices have a tensix level reset that will reset each tensix cores.
- WH nb150's and nb300's have a board level reset.

By default, the reset command will re-initialize the boards after reset. To disable this, update the json config file.


A successful reset on a system with both WH and GS should look something like the follows:

```
$ tt-smi -r 0,1

  Starting pci link reset on WH devices at pci indices: 1
  Finishing pci link reset on WH devices at pci indices: 1

  Starting tensix reset on GS board at pci index 0
  Lowering clks to safe value...
  Beginning reset sequence...
  Finishing reset sequence...
  Returning clks to original values...
  Finished tensix reset on GS board at pci index 0

  Re-initializing boards after reset....
 Done! Detected 3 boards on host.
```
OR
```
tt-smi -r reset_config.json

  Starting pci link reset on WH devices at pci indices: 1
  Finishing pci link reset on WH devices at pci indices: 1

  Starting tensix reset on GS board at pci index 0
  Lowering clks to safe value...
  Beginning reset sequence...
  Finishing reset sequence...
  Returning clks to original values...
  Finished tensix reset on GS board at pci index 0

  Re-initializing boards after reset....
  Done! Detected 3 boards on host.

```

In order to find the correct board index to call the reset on, the user can look at the GUI index or the desired device OR use the tt-smi board list function.
Board list should produce an output that looks like:
```
$ tt-smi -ls

Gathering Information ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ 100% 0:00:00
 All available boards on host:
 0: 0100007311523010 (grayskull - e75)
 1: 0100014211703001 (wormhole - n300 L)
 2: 0100014211703001 (wormhole - n300 R)
 Boards that can be reset:
 0: 0100007311523010 (grayskull - e75)
 1: 0100014211703001 (wormhole - n300 L)
```

## Snapshots

TT-SMI provides an easy way to get all the information that is displayed on the GUI in a json file, using the ```-s, --snapshot``` argument. By default the file is named and stored as
``` ~/tt_smi/<timestamp>_snapshot.json```. User can also provide their own filename if desired, using the ```-f``` option

Example usage:
```
$ tt-smi -s -f tt_smi_example.json

    Gathering Information ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ 100% 0:00:00
      Saved tt-smi log to: tt_smi_example.json
```

## License

Apache 2.0 - https://www.apache.org/licenses/LICENSE-2.0.txt
