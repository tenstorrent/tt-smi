#!/usr/bin/env python3
"""
Compatibility setup.py for Ubuntu 22.04 packaging tools.
Uses setuptools_scm for dynamic versioning from git tags.
"""
from setuptools import setup, find_packages

import tomli

if __name__ == "__main__":
    with open("pyproject.toml", "rb") as f:
        toml_data = tomli.load(f)

    setup(
        # Fallback for older setuptools versions
        name="tt-smi",
        version=toml_data['project']['version'],
        packages=find_packages(),
        python_requires=">=3.10",
        entry_points={
            'console_scripts': [
                'tt-smi = tt_smi:main',
            ]
        },
    )
