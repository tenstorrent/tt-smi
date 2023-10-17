from setuptools import setup, find_packages

setup(
    maintainer='sbansal',
    maintainer_email='sbansal@tenstorrent.com',
    name='TTToolsCommon',
    version=1.0,
    packages=find_packages(),
    url='http://tenstorrent.com',
    license='TODO: License',
    long_description=open('README.md').read(),
    setup_requires=['wheel'],
    install_requires=[
        'wheel', 'fabric', 'pyyaml', 'python-constraint', 'glob2', 'pyserial', 'tabulate',
        'prometheus_client', 'pyfiglet', 'elasticsearch==7.17.4', 'elasticsearch_dsl', 'ruamel.yaml', 'pyinstaller', 'blessed',
        'requests', 'numpy', 'pydantic==1.*', 'rich', 'distro', 'pandas', 'psutil', 'xlsxwriter', 'textual-dev',
        'termcolor', 'art', 'tqdm', 'bitstring', 'smbus2==0.4.2', 'openpyxl==3.0.10', 'cryptography'
    ],
    include_package_data=True,
    package_data={
        '': ['data/version.txt',],
    },
    entry_points={
        "console_scripts": [
            "tt-smi = tt_smi.main:main"
        ]
    }
)