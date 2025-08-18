#!/usr/bin/env python3
"""
Compatibility setup.py for Ubuntu 22.04 packaging tools.
Uses setuptools_scm for dynamic versioning from git tags.
"""
from setuptools import setup, find_packages

if __name__ == "__main__":
    setup(
        # Fallback for older setuptools versions
        name="tt-smi",
        use_scm_version=True,
        packages=find_packages(),
        python_requires=">=3.10",
        setup_requires=['setuptools_scm'],
    )
