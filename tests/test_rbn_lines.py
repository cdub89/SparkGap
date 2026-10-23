"""The RBN archive lines in a run log: what the node validation tool reads to score a dry run."""

import csv
import logging

import pytest

import sparkgap

Caplog = pytest.LogCaptureFixture
COLUMNS = ["callsign", "de_pfx", "de_cont", "freq", "band", "dx", "dx_pfx", "dx_cont",
           "mode", "db", "date", "speed", "tx_mode"]
T = 1_792_586_154.0  # 2026-10-21 12:35:54 UTC


def _lines(caplog: Caplog, prefix: str) -> list[str]:
    return [m[len(prefix):] for r in caplog.records if (m := r.getMessage()).startswith(prefix)]


def test_a_spot_is_one_archive_row(caplog: Caplog) -> None:
    with caplog.at_level(logging.INFO, logger="sparkgap"):
        sparkgap._log_rbn_row("wx7v", 7027.64, "JA0FVU", 24.6, 30, T)
    (row,) = _lines(caplog, "RBN: ")
    got = dict(zip(COLUMNS, next(csv.reader([row]), []), strict=True))
    assert got == {"callsign": "WX7V", "de_pfx": "", "de_cont": "", "freq": "7027.6", "band": "40m",
                   "dx": "JA0FVU", "dx_pfx": "", "dx_cont": "", "mode": "CQ", "db": "25",
                   "date": "2026-10-21 12:35:54", "speed": "30", "tx_mode": "CW"}


def test_a_span_line_holds_every_band(caplog: Caplog) -> None:
    with caplog.at_level(logging.INFO, logger="sparkgap"):
        sparkgap._log_rbn_span(T, [7038.4], 96_000)
        sparkgap._log_rbn_span(T + 60, [7038.4, 14038.4], 96_000)
    assert _lines(caplog, "RBN span: ") == ["2026-10-21 12:35:54 6990.4-7086.4",
                                            "2026-10-21 12:36:54 6990.4-7086.4 13990.4-14086.4"]


def test_the_node_line_carries_a_valid_grid_only(caplog: Caplog) -> None:
    with caplog.at_level(logging.INFO, logger="sparkgap"):
        sparkgap._log_rbn_node("wx7v", "em12OU")
        sparkgap._log_rbn_node("wx7v", "EM12")
        sparkgap._log_rbn_node("wx7v", "")
        sparkgap._log_rbn_node("wx7v", "Dallas")
    assert _lines(caplog, "RBN node: ") == ["WX7V EM12ou", "WX7V EM12", "WX7V", "WX7V"]
