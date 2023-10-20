"""TT-SMI Versioning"""
import os
from datetime import datetime as dt
import pkg_resources

# Returns the root path of the package, so we can access data files and such
def package_root_path():
    return pkg_resources.resource_filename("TTToolsCommon", "")

VERSION_FILE = package_root_path() + "/data/version.txt"
if os.path.isfile(VERSION_FILE):
    with open(VERSION_FILE, "r", encoding="utf-8") as f:
        VERSION_STR = f.read().strip()
else:
    VERSION_STR = "N/A"

VERSION_STR = "1.0"
VERSION_DATE = dt.strptime(VERSION_STR[:10], "%Y-%m-%d").date()
VERSION_HASH = int(VERSION_STR[-16:], 16)

APP_SIGNATURE = f"TT-SMI Version {VERSION_STR} - Tenstorrent, Inc."
