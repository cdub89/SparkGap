"""record_wav rolls over to _pN part files before the WAV 4 GiB limit."""

import wave
from pathlib import Path

import pytest

import sparkgap
from sparkgap import SparkGap

LIMIT = 4000
FRAME = 4  # 2 channels, 2 bytes


def test_rollover_parts_are_contiguous(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sparkgap, "RECORD_WAV_MAX_BYTES", LIMIT)
    base = tmp_path / "rec.wav"
    app = SparkGap.__new__(SparkGap)
    w = wave.open(str(base), "wb")
    w.setnchannels(2)
    w.setsampwidth(2)
    w.setframerate(96_000)
    app._wav_record = w
    app._wav_base, app._wav_part, app._wav_bytes = str(base), 1, 0

    chunks = [bytes([i % 251]) * 1200 for i in range(1, 10)]
    for chunk in chunks:
        app._wav_write(chunk)
    app._wav_record.close()

    parts = [base, tmp_path / "rec_p2.wav", tmp_path / "rec_p3.wav"]
    assert all(p.exists() for p in parts)
    assert not (tmp_path / "rec_p4.wav").exists()
    got = b""
    for p in parts:
        with wave.open(str(p), "rb") as r:
            assert (r.getnchannels(), r.getsampwidth(), r.getframerate()) == (2, 2, 96_000)
            data = r.readframes(r.getnframes())
        assert len(data) <= LIMIT
        got += data
    assert got == b"".join(chunks)
    assert len(got) // FRAME == sum(len(c) for c in chunks) // FRAME
