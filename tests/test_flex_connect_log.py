"""flex_iq logs every connection attempt, each failure and each successful reconnect."""

import logging

import pytest

import flex_iq
from flex_iq import FlexIQReceiver

Monkey = pytest.MonkeyPatch
Caplog = pytest.LogCaptureFixture
ATTEMPT = "[Flex] Connection attempt {} to 192.0.2.1:4992 ({})"
FAILED = "[Flex] Connection attempt {} failed: connection refused"


def _receiver(monkeypatch: Monkey, fail_times: int) -> FlexIQReceiver:
    rx = FlexIQReceiver("192.0.2.1", sample_rate=96_000)
    left = [fail_times]

    def setup() -> None:
        if left[0]:
            left[0] -= 1
            raise OSError("connection refused")

    monkeypatch.setattr(rx, "_setup", setup)
    monkeypatch.setattr(rx, "_remove_objects", lambda: None)
    monkeypatch.setattr(flex_iq, "RETRY_S", 0)
    return rx


def _lines(caplog: Caplog) -> list[tuple[int, str]]:
    return [(r.levelno, r.getMessage()) for r in caplog.records if r.name == "sparkgap.flex"]


def test_first_start_logs_the_attempt(monkeypatch: Monkey, caplog: Caplog) -> None:
    rx = _receiver(monkeypatch, 0)
    with caplog.at_level(logging.INFO, logger="sparkgap.flex"):
        rx.start()
    assert _lines(caplog) == [(logging.INFO, ATTEMPT.format(1, "first start"))]


def test_failed_first_start_logs_an_error_and_raises(monkeypatch: Monkey, caplog: Caplog) -> None:
    rx = _receiver(monkeypatch, 1)
    with caplog.at_level(logging.INFO, logger="sparkgap.flex"), pytest.raises(OSError):
        rx.start()
    assert (logging.ERROR, FAILED.format(1)) in _lines(caplog)


def test_reconnect_logs_attempts_failure_and_success(monkeypatch: Monkey, caplog: Caplog) -> None:
    rx = _receiver(monkeypatch, 1)
    rx._running = True
    with caplog.at_level(logging.INFO, logger="sparkgap.flex"):
        rx._restart("No DAX-IQ packets for 10 s")
    lines = _lines(caplog)
    reason = "reconnect: No DAX-IQ packets for 10 s"
    assert lines[0] == (logging.ERROR, "[Flex] No DAX-IQ packets for 10 s: connecting again")
    assert (logging.INFO, ATTEMPT.format(1, reason)) in lines
    assert (logging.ERROR, FAILED.format(1)) in lines
    assert (logging.INFO, ATTEMPT.format(2, reason)) in lines
    assert lines[-1][0] == logging.INFO
    assert lines[-1][1].startswith("[Flex] Reconnected on attempt 2, ")
    assert rx.attempts == 2 and rx.reconnects == 1


class _ResetSocket:
    def recv(self, *_: object) -> bytes:
        raise ConnectionResetError(104, "Connection reset by peer")

    def close(self) -> None:
        pass


def test_connection_error_keeps_its_cause() -> None:
    rx = FlexIQReceiver("192.0.2.1", sample_rate=96_000)
    rx._tcp = _ResetSocket()  # pyright: ignore[reportAttributeAccessIssue]
    assert not rx._read_status()
    assert rx._lost == "Control connection error: [Errno 104] Connection reset by peer"


def test_own_disconnect_names_the_client_and_reason(caplog: Caplog) -> None:
    rx = FlexIQReceiver("192.0.2.1", sample_rate=96_000)
    rx._tcp, rx._handle = _ResetSocket(), "290AB860"  # pyright: ignore[reportAttributeAccessIssue]
    with caplog.at_level(logging.INFO, logger="sparkgap.flex"):
        rx._close("reconnecting")
        rx._close("stopping")          # already closed: no second line
    line = "[Flex] Disconnected from 192.0.2.1 (client 0x290AB860, reconnecting)"
    assert _lines(caplog) == [(logging.INFO, line)]


def test_last_packet_time_reads_as_utc() -> None:
    assert flex_iq.utc_hms(1_792_376_071.0) == "02:14:31Z"
    assert flex_iq.utc_hms(None) == "none since connecting"


def test_reply_names_its_command_and_flags_refusals() -> None:
    sent = {7: "client gui", 12: "slice set 0 audio_mute=1"}
    ok = "[Flex] <<< R12|0| (slice set 0 audio_mute=1)"
    assert flex_iq.reply_log("R12|0|", sent) == (logging.INFO, ok)
    refused = flex_iq.reply_log("R7|F3000001|Unable to create GUI client", sent)
    text = "[Flex] <<< R7|F3000001|Unable to create GUI client (client gui)"
    assert refused == (logging.WARNING, text)
    assert sent == {}
    assert flex_iq.reply_log("R99|0|", sent) == (logging.INFO, "[Flex] <<< R99|0| (?)")
    assert flex_iq.reply_log("S1|slice 0 tx=0", sent) is None


def test_sent_commands_are_logged_at_info(caplog: Caplog) -> None:
    import socket

    rx = FlexIQReceiver("192.0.2.1", sample_rate=96_000)
    rx._tcp, radio = socket.socketpair()
    with caplog.at_level(logging.INFO, logger="sparkgap.flex"):
        rx._send("sub slice all")
        radio.sendall(b"R1|0|\n")
        rx._recv_line(timeout=1.0)
    assert _lines(caplog) == [(logging.INFO, "[Flex] >>> C1|sub slice all"),
                              (logging.INFO, "[Flex] <<< R1|0| (sub slice all)")]
    radio.close()
    rx._tcp.close()
