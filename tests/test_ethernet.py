# SPDX-FileCopyrightText: © 2026 Tenstorrent Inc.
# SPDX-License-Identifier: Apache-2.0

import sys
import json
import pytest
from enum import Enum
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import tt_umd
from tt_umd import ARCH
from tt_smi import constants, log
from tt_smi.backend import TTSMIBackend
from tt_smi.frontend import TTSMI
from tt_smi.tt_smi import parse_args
from tt_smi.device_input import parse_smi_device_input


class FakeEthTrainingStatus(Enum):
    """Mirror of UMD EthTrainingStatus (eth_training_status.hpp)."""

    IN_PROGRESS = 0
    SUCCESS = 1
    FAIL = 2
    NOT_CONNECTED = 3


def make_core(x, y):
    return SimpleNamespace(x=x, y=y)


def make_bh_device(links):
    """links: list of (channel, (x, y), status, train_speed, target_speed)"""
    dev = MagicMock()
    dev.get_arch.return_value = ARCH.BLACKHOLE
    dev.is_remote.return_value = False
    cores = [make_core(*core) for _, core, _, _, _ in links]
    by_core = {(c.x, c.y): l for c, l in zip(cores, links)}
    soc_desc = MagicMock()
    soc_desc.get_cores.return_value = cores
    soc_desc.translate_coord_to.side_effect = lambda c, _: SimpleNamespace(x=0, y=by_core[(c.x, c.y)][0])
    dev.get_soc_descriptor.return_value = soc_desc
    dev.read_eth_core_training_status.side_effect = lambda c: by_core[(c.x, c.y)][2]
    dev.read_eth_core_train_speed.side_effect = lambda c: by_core[(c.x, c.y)][3]
    dev.read_eth_core_target_speed.side_effect = lambda c: by_core[(c.x, c.y)][4]
    return dev


def make_old_umd_bh_device():
    """BH TTDevice from a tt-umd without the eth getters"""
    dev = MagicMock(spec=["get_arch", "is_remote", "get_soc_descriptor"])
    dev.get_arch.return_value = ARCH.BLACKHOLE
    dev.is_remote.return_value = False
    return dev


def make_wh_device(remote=False):
    dev = MagicMock()
    dev.get_arch.return_value = ARCH.WORMHOLE_B0
    dev.is_remote.return_value = remote
    return dev


BH_LINKS = [
    (0, (1, 25), FakeEthTrainingStatus.SUCCESS, 400, 400),
    (1, (2, 25), FakeEthTrainingStatus.FAIL, None, 400),
    (2, (3, 25), FakeEthTrainingStatus.NOT_CONNECTED, None, 400),
    (3, (4, 25), FakeEthTrainingStatus.IN_PROGRESS, None, 0),
]


HOST_INFO = {k: "" for k in ("OS", "Distro", "Kernel", "Hostname", "Platform", "Python", "Memory", "Driver")}
HOST_SW_VERS = {"tt_smi": "", "pyluwen": "", "tt_umd": ""}


def make_backend(devices, use_umd=True):
    """Backend without device reads at init."""
    with patch("tt_smi.backend.get_host_info", return_value=HOST_INFO), patch(
        "tt_smi.backend.get_host_software_versions", return_value=HOST_SW_VERS
    ):
        backend = TTSMIBackend(
            devices=devices,
            umd_cluster_descriptor=MagicMock() if use_umd else None,
            fully_init=False,
            pretty_output=False,
        )
    backend.device_infos = [{"board_type": "p150b"} for _ in devices]
    return backend


def assert_no_eth_read(dev):
    dev.get_soc_descriptor.assert_not_called()
    dev.read_eth_core_training_status.assert_not_called()
    dev.read_eth_core_train_speed.assert_not_called()
    dev.read_eth_core_target_speed.assert_not_called()


class TestEthStatusArgs:
    def run(self, monkeypatch, argv):
        monkeypatch.setattr(sys, "argv", ["tt-smi"] + argv)
        return parse_args()

    def test_alone(self, monkeypatch):
        assert self.run(monkeypatch, ["--eth_status"]).eth_status == []

    def test_with_devices(self, monkeypatch):
        args = self.run(monkeypatch, ["--eth_status", "0", "1"])
        assert args.eth_status == ["0", "1"]
        assert parse_smi_device_input(args.eth_status).value == [0, 1]

    def test_with_bdf(self, monkeypatch):
        args = self.run(monkeypatch, ["--eth_status", "0000:01:00.0"])
        assert parse_smi_device_input(args.eth_status).value == ["0000:01:00.0"]

    @pytest.mark.parametrize(
        "other",
        [
            ["-s"],
            ["-f"],
            ["-f", "out.json"],
            ["-r"],
            ["-r", "0"],
            ["-ls"],
            ["-glx_reset"],
            ["-glx_reset_auto"],
            ["-glx_list_tray_to_device"],
        ],
    )
    def test_conflicts(self, monkeypatch, other):
        with pytest.raises(SystemExit) as e:
            self.run(monkeypatch, ["--eth_status"] + other)
        assert e.value.code == 2
        with pytest.raises(SystemExit):
            self.run(monkeypatch, other + ["--eth_status"])

    @pytest.mark.parametrize(
        "argv", [[], ["-s"], ["-f"], ["-r"], ["-r", "0"], ["-ls"]]
    )
    def test_other_flags_unchanged(self, monkeypatch, argv):
        assert self.run(monkeypatch, argv).eth_status is None


class TestGetEthernetStatus:
    def test_bh(self):
        dev = make_bh_device(BH_LINKS)
        links = make_backend({0: dev}).get_ethernet_status(0)
        assert links == [
            {"channel": 0, "core": "1-25", "link": "UP", "train_speed_gbps": 400, "target_speed_gbps": 400},
            {"channel": 1, "core": "2-25", "link": "DOWN", "train_speed_gbps": None, "target_speed_gbps": 400},
            {"channel": 2, "core": "3-25", "link": "UNUSED", "train_speed_gbps": None, "target_speed_gbps": 400},
            {"channel": 3, "core": "4-25", "link": "UNKNOWN", "train_speed_gbps": None, "target_speed_gbps": 0},
        ]

    def test_link_map_covers_umd_enum(self):
        assert set(constants.ETH_LINK_STATUS) == {s.name for s in FakeEthTrainingStatus}

    def test_link_map_covers_real_umd_enum(self):
        if not hasattr(tt_umd, "EthTrainingStatus"):
            pytest.skip("installed tt-umd lacks EthTrainingStatus")
        names = set(tt_umd.EthTrainingStatus.__members__)
        assert names <= set(constants.ETH_LINK_STATUS)

    def test_unknown_status_value(self):
        dev = make_bh_device(BH_LINKS[:1])
        dev.read_eth_core_training_status.side_effect = ValueError("bad enum value")
        links = make_backend({0: dev}).get_ethernet_status(0)
        assert links[0]["link"] == "UNKNOWN"

    def test_old_umd_none(self):
        dev = make_old_umd_bh_device()
        backend = make_backend({0: dev})
        assert backend.get_ethernet_status(0) is None
        dev.get_soc_descriptor.assert_not_called()
        reason = backend.get_ethernet_not_supported_reason(0)
        assert reason.startswith("requires a newer tt-umd")
        for name in constants.ETH_UMD_API:
            assert name in reason

    def test_luwen_none(self):
        dev = make_bh_device(BH_LINKS)
        backend = make_backend({0: dev}, use_umd=False)
        assert backend.get_ethernet_status(0) is None
        assert_no_eth_read(dev)
        assert "luwen" in backend.get_ethernet_not_supported_reason(0)

    def test_wh_none(self):
        dev = make_wh_device()
        backend = make_backend({0: dev})
        assert backend.get_ethernet_status(0) is None
        assert_no_eth_read(dev)
        assert "Wormhole" in backend.get_ethernet_not_supported_reason(0)

    def test_wh_remote_none(self):
        dev = make_wh_device(remote=True)
        assert make_backend({0: dev}).get_ethernet_status(0) is None
        assert_no_eth_read(dev)

    def test_bh_remote_none(self):
        dev = make_bh_device(BH_LINKS)
        dev.is_remote.return_value = True
        backend = make_backend({0: dev})
        assert backend.get_ethernet_status(0) is None
        assert_no_eth_read(dev)
        assert "remote" in backend.get_ethernet_not_supported_reason(0)


class TestLazyRead:
    def test_fully_init_no_eth_read(self):
        dev = make_bh_device(BH_LINKS)
        getters = [
            "get_smbus_board_info",
            "get_firmware_versions",
            "get_pci_properties",
            "get_device_info",
            "get_chip_telemetry",
            "get_gddr_telemetry",
            "get_chip_limits",
        ]
        patches = [patch.object(TTSMIBackend, g, return_value={}) for g in getters]
        for p in patches:
            p.start()
        try:
            with patch("tt_smi.backend.get_host_info", return_value=HOST_INFO), patch(
                "tt_smi.backend.get_host_software_versions", return_value=HOST_SW_VERS
            ):
                backend = TTSMIBackend(
                    devices={0: dev}, umd_cluster_descriptor=MagicMock(), pretty_output=False
                )
            backend.update_telem()
        finally:
            for p in patches:
                p.stop()
        assert_no_eth_read(dev)


class TestPrintEthernetStatus:
    def test_all(self, capsys):
        backend = make_backend({0: make_bh_device(BH_LINKS), 1: make_wh_device()})
        backend.get_pci_bdf = lambda i: f"0000:0{i + 1}:00.0"
        backend.print_ethernet_status(parse_smi_device_input([]))
        out = capsys.readouterr().out
        assert "Device 0: Blackhole p150b  (0000:01:00.0)" in out
        assert "Link  0: UP      core 1-25   speed  400 Gbps target  400 Gbps" in out
        assert "Link  1: DOWN    core 2-25   speed    - Gbps target  400 Gbps" in out
        assert "Link  2: UNUSED" in out
        assert "Device 1: Wormhole" in out
        assert "Ethernet link status not supported on Wormhole." in out

    def test_filter_logical_id(self, capsys):
        wh = make_wh_device()
        backend = make_backend({0: wh, 1: make_bh_device(BH_LINKS)})
        backend.get_pci_bdf = lambda i: "0000:01:00.0"
        backend.print_ethernet_status(parse_smi_device_input(["1"]))
        out = capsys.readouterr().out
        assert "Device 0" not in out
        assert "Device 1: Blackhole" in out

    def test_partial_match_warns(self, capsys):
        backend = make_backend({0: make_bh_device(BH_LINKS)})
        backend.get_pci_bdf = lambda i: "0000:01:00.0"
        backend.print_ethernet_status(parse_smi_device_input(["0", "99"]))
        out, err = capsys.readouterr()
        assert "Device 0: Blackhole" in out
        assert "no device matches 99" in err
        assert "no device matches 0" not in err

    def test_old_umd_reason(self, capsys):
        backend = make_backend({0: make_old_umd_bh_device()})
        backend.get_pci_bdf = lambda i: "0000:01:00.0"
        backend.print_ethernet_status(parse_smi_device_input([]))
        assert "Ethernet link status requires a newer tt-umd" in capsys.readouterr().out

    def test_no_match_exits(self):
        backend = make_backend({0: make_bh_device(BH_LINKS)})
        with pytest.raises(SystemExit) as e:
            backend.print_ethernet_status(parse_smi_device_input(["5"]))
        assert e.value.code == 1


class TestSnapshotEthernet:
    def snapshot(self, devices):
        backend = make_backend(devices)
        n = len(devices)
        backend.smbus_telem_info = [{}] * n
        backend.device_telemetrys = [{}] * n
        backend.device_gddr_telemetrys = [{}] * n
        backend.firmware_infos = [{}] * n
        backend.chip_limits = [{}] * n
        with patch.object(TTSMIBackend, "update_processes"):
            return json.loads(backend.get_logs_json())

    def test_bh_has_ethernet_wh_omitted(self):
        snap = self.snapshot({0: make_bh_device(BH_LINKS), 1: make_wh_device()})
        eth = snap["device_info"][0]["ethernet"]
        assert len(eth) == len(BH_LINKS)
        assert eth[0] == {"channel": 0, "core": "1-25", "link": "UP", "train_speed_gbps": 400, "target_speed_gbps": 400}
        assert eth[2] == {"channel": 2, "core": "3-25", "link": "UNUSED", "train_speed_gbps": None, "target_speed_gbps": 400}
        assert "ethernet" not in snap["device_info"][1]

    def test_old_umd_omitted(self):
        snap = self.snapshot({0: make_old_umd_bh_device()})
        assert "ethernet" not in snap["device_info"][0]

    def test_read_error_omitted(self, capsys):
        dev = make_bh_device(BH_LINKS)
        dev.get_soc_descriptor.side_effect = RuntimeError("eth read failed")
        snap = self.snapshot({0: dev, 1: make_bh_device(BH_LINKS)})
        assert "ethernet" not in snap["device_info"][0]
        assert len(snap["device_info"][1]["ethernet"]) == len(BH_LINKS)
        assert "device 0: ethernet status read failed" in capsys.readouterr().err

    def test_speed_none_validates(self):
        link = log.EthLink(channel=0, core="1-25", link="DOWN", train_speed_gbps=None, target_speed_gbps=None)
        assert link.train_speed_gbps is None


class TestEthernetTab:
    def fake_app(self, backend, hide_unused=False, hide_down=False):
        return SimpleNamespace(
            backend=backend,
            ethernet_links={i: backend.get_ethernet_status(i) for i in backend.devices},
            eth_hide_unused=hide_unused,
            eth_hide_down=hide_down,
            text_theme={"yellow_bold": "", "text_green": "", "gray": ""},
        )

    def test_rows(self):
        backend = make_backend({0: make_bh_device(BH_LINKS), 1: make_wh_device()})
        rows = TTSMI.format_ethernet_rows(self.fake_app(backend))
        ncols = len(constants.ETHERNET_TABLE_HEADER)
        assert all(len(r) == ncols for r in rows)
        assert len(rows) == len(BH_LINKS) + 1
        assert [t.plain for t in rows[0]] == ["0", "0", "1-25", "UP", "400", "400"]
        assert [t.plain for t in rows[2]] == ["0", "2", "3-25", "UNUSED", "-", "400"]
        assert rows[-1][0].plain == "1"
        assert rows[-1][3].plain == "not supported on Wormhole"

    def test_old_umd_reason_row(self):
        backend = make_backend({0: make_old_umd_bh_device()})
        rows = TTSMI.format_ethernet_rows(self.fake_app(backend))
        assert len(rows) == 1
        assert rows[0][3].plain.startswith("requires a newer tt-umd")

    @pytest.mark.parametrize("on_tab", [True, False])
    def test_filter_keys_only_on_tab(self, on_tab):
        app = SimpleNamespace(
            on_eth_tab=lambda: on_tab, eth_hide_unused=False, eth_hide_down=False, ethernet_links=None
        )
        assert TTSMI.check_action(app, "toggle_eth_unused", ()) is on_tab
        assert TTSMI.check_action(app, "toggle_eth_down", ()) is on_tab
        assert TTSMI.check_action(app, "tab_one", ()) is True
        TTSMI.action_toggle_eth_unused(app)
        TTSMI.action_toggle_eth_down(app)
        assert app.eth_hide_unused is on_tab
        assert app.eth_hide_down is on_tab

    def test_filters(self):
        backend = make_backend({0: make_bh_device(BH_LINKS)})
        links = lambda app: [r[3].plain for r in TTSMI.format_ethernet_rows(app)]
        assert "UNUSED" not in links(self.fake_app(backend, hide_unused=True))
        assert "DOWN" in links(self.fake_app(backend, hide_unused=True))
        assert "DOWN" not in links(self.fake_app(backend, hide_down=True))
        assert set(links(self.fake_app(backend, hide_unused=True, hide_down=True))) == {"UP", "UNKNOWN"}


@pytest.mark.requires_hardware
class TestEthernetHardware:
    def test_eth_status_shape(self, umd_backend):
        for i in umd_backend.devices:
            links = umd_backend.get_ethernet_status(i)
            if not umd_backend.is_blackhole(i):
                assert links is None
                continue
            assert links, "Blackhole should list eth links"
            for link in links:
                assert link["link"] in ("UP", "DOWN", "UNUSED", "UNKNOWN")
                assert link["train_speed_gbps"] is None or isinstance(link["train_speed_gbps"], int)
                assert link["target_speed_gbps"] is None or isinstance(link["target_speed_gbps"], int)
                if link["link"] != "UP":
                    assert link["train_speed_gbps"] is None
