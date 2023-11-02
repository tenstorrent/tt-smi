# tt-smi

## Getting started

### To Build from git:

- Requirements:
    - Install and source rust for the luwen library
    ```
    curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh
    source "$HOME/.cargo/env"
    ```

#### Optional
```
python3 -m venv .venv
source .venv/bin/activate
```
#### Required
```
pip install .
```
If you want to edit the source code, you can also install it in editable mode so re-instealls won't be needed.
```
pip install --editable .
```

### Help text (may not be up to date)
```
    $ tt-smi --help
    usage: tt-smi [-h] [--local] [-v] [-nl] [-ls] [-f [filename]]

    optional arguments:
      -h, --help            show this help message and exit
      -v, --version         show program's version number and exit
      -ls, --list           List boards that are available on host
      -s, --snapshot        Generates a .json file with tt-smi info
      -f [filename], --filename [filename]
                            Change filename for the .json snapshot file dump. Default: ~/tt_smi/<timestamp>_results.yaml
```

### Typical usage
```
tt-smi
```

## License

Apache 2.0 - https://www.apache.org/licenses/LICENSE-2.0.txt
