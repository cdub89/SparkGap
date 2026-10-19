"""File mode stamps log lines with audio time from the record_wav name's UTC start."""

import wave
from datetime import datetime, timezone
from pathlib import Path

from sparkgap import _wav_start_utc

RATE = 96_000
START = datetime(2026, 10, 18, 15, 20, 29, tzinfo=timezone.utc).timestamp()


def _wav(path: Path, seconds: float) -> None:
    w = wave.open(str(path), "wb")
    w.setnchannels(2)
    w.setsampwidth(2)
    w.setframerate(RATE)
    w.writeframes(b"\0" * 4 * int(seconds * RATE))
    w.close()


def test_part_one_starts_at_its_name(tmp_path: Path) -> None:
    assert _wav_start_utc(str(tmp_path / "b_14048_20261018_152029Z.wav")) == START


def test_later_parts_add_the_earlier_parts(tmp_path: Path) -> None:
    _wav(tmp_path / "b_14048_20261018_152029Z.wav", 2.0)
    _wav(tmp_path / "b_14048_20261018_152029Z_p2.wav", 1.5)
    assert _wav_start_utc(str(tmp_path / "b_14048_20261018_152029Z_p2.wav")) == START + 2.0
    assert _wav_start_utc(str(tmp_path / "b_14048_20261018_152029Z_p3.wav")) == START + 3.5


def test_missing_earlier_part_is_unknown(tmp_path: Path) -> None:
    _wav(tmp_path / "b_14048_20261018_152029Z.wav", 2.0)
    assert _wav_start_utc(str(tmp_path / "b_14048_20261018_152029Z_p3.wav")) is None


def test_name_without_a_start_is_unknown() -> None:
    assert _wav_start_utc("B1_recording.wav") is None
