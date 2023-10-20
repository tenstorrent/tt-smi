# SPDX-FileCopyrightText: © 2023 Tenstorrent Inc.
# SPDX-License-Identifier: Apache-2.0

"""
Generate tt-smi logs that are compatible with elasticsearch
"""
from __future__ import annotations
import json
import yaml
import base64
import inspect
import datetime
import functools
import elasticsearch
from pathlib import Path
from dataclasses import dataclass
from collections import defaultdict
from typing import Any, Union, List, TypeVar, Generic

es = elasticsearch.Elasticsearch(["http://yyz-elk:9200"],
                                 http_auth=("lab", "lab2019"))

from pydantic import BaseModel, conlist
from pydantic.fields import Field


class Long(int):
    ...


class Keyword(str):
    ...


class Text(str):
    ...


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

    if fields and inspect.isclass(fields[0]) and issubclass(
            fields[0], BaseModel):
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
        return {
            "type": "date",
            "format": "strict_date_optional_time||epoch_millis"
        }
    elif issubclass(type, ElasticModel):
        return {"type": "object", "properties": type.get_mapping()}
    else:
        raise NotImplementedError(
            f"Have not implemented mapping support for {type}")


def field_to_mapping(info: Field):
    try:
        # print(info.outer_type_, type(info.outer_type_))
        if hasattr(info.outer_type_,
                   "__origin__") and info.outer_type_.__origin__ == Nested:
            inner = type_to_mapping(info.type_)
            if inner.get("type", None) == "object":
                inner["type"] = "nested"
            else:
                inner = {"type": "nested", "properties": inner}
            return inner
        else:
            return type_to_mapping(info.type_)
    except NotImplementedError:
        raise NotImplementedError(
            f"Have not implemented mapping support for {info}")


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

    def save(self, index: str):
        es.index(index=index, document=self.json())


T = TypeVar("T", bound=ElasticModel)


class Nested(list, Generic[T]):
    ...


class HostInfo(ElasticModel):
    OS: str
    Distro: str
    Kernel: str
    Hostname: str
    Platform: str
    Python: str
    Memory: str
    Driver: str

@optional
class SmbusTelem(ElasticModel):
    BOARD_ID: str
    SMBUS_TX_ENUM_VERSION: str
    SMBUS_TX_DEVICE_ID: str
    SMBUS_TX_ASIC_RO: str
    SMBUS_TX_ASIC_IDD: str
    SMBUS_TX_BOARD_ID_HIGH: str
    SMBUS_TX_BOARD_ID_LOW: str
    SMBUS_TX_ARC0_FW_VERSION: str
    SMBUS_TX_ARC1_FW_VERSION: str
    SMBUS_TX_ARC2_FW_VERSION: str
    SMBUS_TX_ARC3_FW_VERSION: str
    SMBUS_TX_SPIBOOTROM_FW_VERSION: str
    SMBUS_TX_ETH_FW_VERSION: str
    SMBUS_TX_M3_BL_FW_VERSION: str
    SMBUS_TX_M3_APP_FW_VERSION: str
    SMBUS_TX_DDR_SPEED: str
    SMBUS_TX_DDR_STATUS: str
    SMBUS_TX_ETH_STATUS0: str
    SMBUS_TX_ETH_STATUS1: str
    SMBUS_TX_PCIE_STATUS: str
    SMBUS_TX_FAULTS: str
    SMBUS_TX_ARC0_HEALTH: str
    SMBUS_TX_ARC1_HEALTH: str
    SMBUS_TX_ARC2_HEALTH: str
    SMBUS_TX_ARC3_HEALTH: str
    SMBUS_TX_FAN_SPEED: str
    SMBUS_TX_AICLK: str
    SMBUS_TX_AXICLK: str
    SMBUS_TX_ARCCLK: str
    SMBUS_TX_THROTTLER: str
    SMBUS_TX_VCORE: str
    SMBUS_TX_ASIC_TEMPERATURE: str
    SMBUS_TX_VREG_TEMPERATURE: str
    SMBUS_TX_BOARD_TEMPERATURE: str
    SMBUS_TX_TDP: str
    SMBUS_TX_TDC: str
    SMBUS_TX_VDD_LIMITS: str
    SMBUS_TX_THM_LIMITS: str
    SMBUS_TX_WH_FW_DATE: str
    SMBUS_TX_ASIC_TMON0: str
    SMBUS_TX_ASIC_TMON1: str
    SMBUS_TX_MVDDQ_POWER: str
    SMBUS_TX_GDDR_TRAIN_TEMP0: str
    SMBUS_TX_GDDR_TRAIN_TEMP1: str
    SMBUS_TX_BOOT_DATE: str
    SMBUS_TX_RT_SECONDS: str
    SMBUS_TX_AUX_STATUS: str
    SMBUS_TX_ETH_DEBUG_STATUS0: str
    SMBUS_TX_ETH_DEBUG_STATUS1: str
    SMBUS_TX_TT_FLASH_VERSION: str
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
    voltage : str
    current: str
    aiclk: str
    power: str 
    asic_temperature: str

@optional
class Firmwares(ElasticModel):
    arc_fw : str
    arc_fw_date : str
    eth_fw : str
    m3_bl_fw : str
    m3_app_fw : str
    tt_flash_version : str


@optional
class Limits(ElasticModel):
    vdd_min : str
    vdd_max : str
    tdp_limit : str
    tdc_limit : str
    asic_fmax : str
    therm_trip_l1_limit : str
    thm_limit : str
    bus_peak_limit : str

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
    device_info: List[TTSMIDeviceLog]

    def save_as_json(self, fname: Union[str, Path]):
        with open(fname, "w") as f:
            raw_json = self.json(exclude_none=True)
            reloaded_json = json.loads(raw_json)
            json.dump(reloaded_json, f, indent=4)
