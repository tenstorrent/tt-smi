# tt-smi

## Getting started

### To Build from git:

- Requirements
    - luwen library (specifically pyluwen)
        - pre-installed
        - rust compiler and ability to compile rust

#### Optional
```
pip -m venv venv
source venv/bin/activate
```
#### Required
```
pip install -r requirements.txt
pip install .
```

### Help text (may not be up to date)
```
    $ tt-smi --help
    usage: tt-smi [-h] [--local] [-v] [-nl] [-ls] [-f [filename]]

    optional arguments:
      -h, --help            show this help message and exit
      --local               Run only on local chips
      -v, --version         show program's version number and exit
      -nl, --no_log         Runs tt-mod without generating the end SPI log
      -ls, --list           List boards that are available to modify SPI on and quits
      -f [filename], --filename [filename]
                            Change filename for test log. Default: ~/tt_mod_logs/<timestamp>_results.yaml
```

### Typical usage
```
tt-smi
```

## License

Apache 2.0 - https://www.apache.org/licenses/LICENSE-2.0.txt
