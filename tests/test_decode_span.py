"""The Info page's Decoding range: the band plan's CW segment, cut to the IQ span."""

from sparkgap import _decode_span


def test_48k_centred_inside_the_segment_is_cut_to_the_iq() -> None:
    assert _decode_span(14000.0, 14070.0, 14044.0, 48_000) == (14020.0, 14068.0)


def test_96k_covers_the_segment_and_keeps_it() -> None:
    assert _decode_span(7000.0, 7060.0, 7038.4, 96_000) == (7000.0, 7060.0)


def test_one_edge_cut() -> None:
    assert _decode_span(7000.0, 7060.0, 7019.2, 48_000) == (7000.0, 7043.2)
