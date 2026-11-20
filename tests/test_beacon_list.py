"""RBN's beacon list (bcn.csv) for gate 12 and the feeder's spot type, cdub89/SparkGap#57."""

from __future__ import annotations

from pathlib import Path

import pytest

import spot_rule2
from spot_rule2 import beacon_kind, listed_beacons, read_beacons

BCN = "28066,PY4ROE\n14100,4U1UN\n18110,4U1UN\n50085,PY4ROE\nnot a row\n28200,\n28219,W2DLL/B2\n"


def test_each_call_keeps_every_frequency_it_is_listed_on(tmp_path: Path) -> None:
    (tmp_path / "bcn.csv").write_text(BCN)
    got = read_beacons(tmp_path / "bcn.csv")
    assert got == {"PY4ROE": (28066.0, 50085.0), "4U1UN": (14100.0, 18110.0), "W2DLL": (28219.0,)}
    assert beacon_kind("PY4ROE", 50084.0, got) == "listed"
    assert beacon_kind("PY4ROE", 28068.0, got) == "listed"
    assert beacon_kind("PY4ROE", 28080.0, got) is None


def test_the_list_is_read_only_when_the_switch_is_on(tmp_path: Path,
                                                     caplog: pytest.LogCaptureFixture) -> None:
    spot_rule2._LISTED.clear()
    (tmp_path / "bcn.csv").write_text(BCN)
    assert listed_beacons({"rbn_known_dir": str(tmp_path)}) == {}
    on = {"rbn_known_dir": str(tmp_path), "spot_rule_beacons": True}
    assert listed_beacons(on)["PY4ROE"] == (28066.0, 50085.0)
    assert listed_beacons({**on, "rbn_known_dir": str(tmp_path / "none")}) == {}
    assert "no listed beacons" in caplog.text


def test_a_listed_beacon_spots_on_any_of_its_frequencies() -> None:
    r = spot_rule2.GateSpotRule(lambda c: "active", beacons={"PY4ROE": (28066.0, 50085.0)})
    win = r.window_id(280660, 0, 0.0)
    r.feed(win, 28066.0, "VVV DE PY4ROE", 0.0)
    assert [c for c, _ in r.end_pass()] == ["PY4ROE"]


def test_the_list_is_read_once_a_run(tmp_path: Path) -> None:
    spot_rule2._LISTED.clear()                  # the rule, the run log and the feeder agree
    (tmp_path / "bcn.csv").write_text(BCN)
    on = {"rbn_known_dir": str(tmp_path), "spot_rule_beacons": True}
    assert "W2DLL" in listed_beacons(on)
    (tmp_path / "bcn.csv").write_text("28300,K1NEW\n")
    assert "W2DLL" in listed_beacons(on) and "K1NEW" not in listed_beacons(on)
