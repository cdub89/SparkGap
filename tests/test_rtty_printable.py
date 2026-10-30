"""Decoded RTTY text reaches log and spot lines on one line: control characters become spaces."""

import sparkgap


def test_line_breaks_and_bells_become_spaces() -> None:
    assert sparkgap._rtty_printable("CQ WS7I WS7I \n\nS\r\x07K") == "CQ WS7I WS7I   S  K"


def test_printable_text_is_unchanged() -> None:
    assert sparkgap._rtty_printable("CQ TEST AI4WW 599 015") == "CQ TEST AI4WW 599 015"
