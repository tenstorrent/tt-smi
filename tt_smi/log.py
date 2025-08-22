# SPDX-FileCopyrightText: © 2023 Tenstorrent Inc.
# SPDX-License-Identifier: Apache-2.0

"""
This file contains functions used to generate tt-smi logs that are compatible with elasticsearch.
"""
from __future__ import annotations
import json
import base64
import inspect
import datetime
from pathlib import Path
from typing import Any, Union, List, TypeVar, Generic

try:
    # Try the newer v2 pydantic and use that first
    from pydantic.v1 import BaseModel
    from pydantic.v1.fields import Field
except:
    # Assume we are on v1 and give that a go
    from pydantic import BaseModel
    from pydantic.fields import Field


class Long(int): ...


class Keyword(str): ...


class Text(str): ...


class Date(datetime.datetime):
    @classmethod
    def build(cls, format: str):
        cls.format = format

    @classmethod
    def get_mapping(cls):
        return {"type": "date", "format": cls.format}


def optional(*fields):
    """Decorator function used to modify a pydantic model's fields to all be optional.
    Alternatively, you can  also pass the field names that should be made optional as arguments
    to the decorator.
    Taken from https://github.com/samuelcolvin/pydantic/issues/1223#issuecomment-775363074
    """

    def dec(_cls):
        for field in fields:
            _cls.__fields__[field].required = False
            _cls.__fields__[field].default = None
        return _cls

    if fields and inspect.isclass(fields[0]) and issubclass(fields[0], BaseModel):
        cls = fields[0]
        fields = cls.__fields__
        return dec(cls)

    return dec


def type_to_mapping(type: Any):
    if issubclass(type, float):
        return {"type": "float"}
    elif issubclass(type, bool):
        return {"type": "boolean"}
    elif issubclass(type, Long):
        return {"type": "long"}
    elif issubclass(type, int):
        return {"type": "integer"}
    elif issubclass(type, bytes):
        return {"type": "binary"}
    elif issubclass(type, Keyword):
        return {"type": "keyword"}
    elif issubclass(type, Text):
        return {"type": "text"}
    elif issubclass(type, str):
        return {"type": "text", "fields": {"keyword": {"type": "keyword"}}}
    elif issubclass(type, Date):
        return type.get_mapping()
    elif issubclass(type, datetime.date):
        return {"type": "date", "format": "strict_date_optional_time||epoch_millis"}
    elif issubclass(type, ElasticModel):
        return {"type": "object", "properties": type.get_mapping()}
    else:
        raise NotImplementedError(f"Have not implemented mapping support for {type}")


def field_to_mapping(info: Field):
    try:
        # print(info.outer_type_, type(info.outer_type_))
        if (
            hasattr(info.outer_type_, "__origin__")
            and info.outer_type_.__origin__ == Nested
        ):
            inner = type_to_mapping(info.type_)
            if inner.get("type", None) == "object":
                inner["type"] = "nested"
            else:
                inner = {"type": "nested", "properties": inner}
            return inner
        else:
            return type_to_mapping(info.type_)
    except NotImplementedError as exc:
        raise NotImplementedError(
            f"Have not implemented mapping support for {info}"
        ) from exc


def json_load_bytes(obj):
    if "__type__" in obj:
        if obj["__type__"] == "bytes":
            return base64.b64decode(obj["bytes"].encode("ascii"))
    return obj


class ElasticModel(BaseModel):
    @classmethod
    def get_mapping(cls):
        mapping = {}
        for name, info in cls.__fields__.items():
            mapping[name] = field_to_mapping(info)

        return mapping

    # Will add the ability to save to elasticsearch as needed
    # def save(self, index: str):
    #     es.index(index=index, document=self.json())


T = TypeVar("T", bound=ElasticModel)


class Nested(list, Generic[T]): ...


class HostInfo(ElasticModel):
    OS: str
    Distro: str
    Kernel: str
    Hostname: str
    Platform: str
    Python: str
    Memory: str
    Driver: str


class HostSWVersions(ElasticModel):
    tt_smi: str
    pyluwen: str


@optional
class SmbusTelem(ElasticModel):
    BOARD_ID: str
    ENUM_VERSION: str
    DEVICE_ID: str
    ASIC_RO: str
    ASIC_IDD: str
    BOARD_ID_HIGH: str
    BOARD_ID_LOW: str
    ARC0_FW_VERSION: str
    ARC1_FW_VERSION: str
    ARC2_FW_VERSION: str
    ARC3_FW_VERSION: str
    SPIBOOTROM_FW_VERSION: str
    ETH_FW_VERSION: str
    M3_BL_FW_VERSION: str
    M3_APP_FW_VERSION: str
    DDR_SPEED: str
    DDR_STATUS: str
    ETH_STATUS0: str
    ETH_STATUS1: str
    PCIE_STATUS: str
    FAULTS: str
    ARC0_HEALTH: str
    ARC1_HEALTH: str
    ARC2_HEALTH: str
    ARC3_HEALTH: str
    FAN_SPEED: str
    AICLK: str
    AXICLK: str
    ARCCLK: str
    THROTTLER: str
    VCORE: str
    ASIC_TEMPERATURE: str
    VREG_TEMPERATURE: str
    BOARD_TEMPERATURE: str
    TDP: str
    TDC: str
    VDD_LIMITS: str
    THM_LIMITS: str
    WH_FW_DATE: str
    ASIC_TMON0: str
    ASIC_TMON1: str
    MVDDQ_POWER: str
    GDDR_TRAIN_TEMP0: str
    GDDR_TRAIN_TEMP1: str
    BOOT_DATE: str
    RT_SECONDS: str
    AUX_STATUS: str
    ETH_DEBUG_STATUS0: str
    ETH_DEBUG_STATUS1: str
    TT_FLASH_VERSION: str
    THERM_TRIP_COUNT: str
    INPUT_POWER: str
    BOARD_POWER_LIMIT: str


@optional
class BoardInfo(ElasticModel):
    bus_id: str
    board_type: str
    board_id: str
    coords: str
    dram_status: str
    dram_speed: str
    pcie_speed: str
    pcie_width: str


@optional
class Telemetry(ElasticModel):
    voltage: str
    current: str
    aiclk: str
    power: str
    asic_temperature: str


@optional
class Firmwares(ElasticModel):
    arc_fw: str
    arc_fw_date: str
    eth_fw: str
    m3_bl_fw: str
    m3_app_fw: str
    tt_flash_version: str


@optional
class Limits(ElasticModel):
    vdd_min: str
    vdd_max: str
    tdp_limit: str
    tdc_limit: str
    asic_fmax: str
    therm_trip_l1_limit: str
    thm_limit: str
    bus_peak_limit: str
    board_power_limit: str


@optional
class TTSMIDeviceLog(ElasticModel):
    smbus_telem: SmbusTelem
    board_info: BoardInfo
    telemetry: Telemetry
    firmwares: Firmwares
    limits: Limits


@optional
class TTSMILog(ElasticModel):
    time: datetime.datetime
    host_info: HostInfo
    host_sw_vers: HostSWVersions
    device_info: List[TTSMIDeviceLog]

    def get_clean_json_string(self):
        """Returns a cleaned json string"""
        raw_json = self.json(exclude_none=True)
        clean_json = json.loads(raw_json)
        json_str = json.dumps(clean_json, indent=4)

        return json_str


@optional
class PciResetDeviceInfo(ElasticModel):
    pci_index: List[int]


@optional
class MoboReset(ElasticModel):
    mobo: str
    credo: List[str]
    disabled_ports: List[str]


@optional
class TTSMIResetLog(ElasticModel):
    time: datetime.datetime
    host_name: str
    gs_tensix_reset: PciResetDeviceInfo
    wh_link_reset: PciResetDeviceInfo
    re_init_devices: bool
    wh_mobo_reset: List[MoboReset]

    def save_as_json(self, fname: Union[str, Path]):
        with open(fname, "w") as f:
            raw_json = self.json(exclude_none=True)
            reloaded_json = json.loads(raw_json)
            json.dump(reloaded_json, f, indent=4)
