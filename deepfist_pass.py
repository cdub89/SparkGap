"""Experimental DeepFist second-pass decoder for ITILA scanner bins (issue #5).

DeepFist (https://github.com/n9bc/DeepFist, N9BC, GPL-3.0-or-later) is a
CNN+CTC CW decoder that needs real audio, not the 200 Hz envelope ITILA uses
(rebuilding a tone from the envelope halves its recall). The scanner keeps a
1 kHz complex baseband per bin when capture is enabled
(itila_sc_enable_iq_capture / itila_sc_peek_iq); this module turns that into
8 kHz audio with the signal at 600 Hz and decodes it with DeepFist's own
pipeline (conditioner -> spectrogram -> CwCtcNet -> greedy CTC), 15 s windows,
each gated by DeepFist's keying squelch.

Experiment only: enabled by SPARKGAP_DEEPFIST_CKPT=<path to model.pt> with
SPARKGAP_DEEPFIST_SRC=<DeepFist checkout> on sys.path. Uses torch for now;
production would export to ONNX and use onnxruntime.
"""
import json
import os
import sys

import numpy as np
from scipy.signal import resample_poly

IQ_RATE = 1000          # scanner capture rate (complex)
AUDIO_RATE = 8000
PITCH_HZ = 600.0
WIN_SEC = 15.0


class DeepFistPass:
    def __init__(self, ckpt, src):
        os.environ.setdefault('DEEPFIST_CONDITION', '1')
        for p in (src, os.path.join(src, 'tools')):
            if p not in sys.path:
                sys.path.insert(0, p)
        import torch
        from deepfist.features.spectrogram import audio_to_spectrogram, SAMPLE_RATE
        from deepfist.features.conditioner import maybe_condition
        from deepfist.model.net import CwCtcNet
        from deepfist.model.decode import greedy_ctc_decode
        from squelch import has_signal
        self._torch = torch
        self._spec, self._sr = audio_to_spectrogram, SAMPLE_RATE
        self._cond, self._ctc, self._has_signal = maybe_condition, greedy_ctc_decode, has_signal
        cfg = json.load(open(os.path.join(os.path.dirname(ckpt), 'config.json')))
        self._net = CwCtcNet(time_downsample=cfg['time_downsample'], width=cfg['width'])
        self._net.load_state_dict(torch.load(ckpt, map_location='cpu'))
        self._net.eval()

    @staticmethod
    def iq_to_audio(iq_interleaved):
        """1 kHz complex baseband (interleaved float32 I,Q) -> 8 kHz real audio
        with the signal at PITCH_HZ, peak-normalised."""
        z = iq_interleaved[0::2].astype(np.float64) + 1j * iq_interleaved[1::2]
        up = AUDIO_RATE // IQ_RATE
        z = resample_poly(z.real, up, 1) + 1j * resample_poly(z.imag, up, 1)
        t = np.arange(z.size) / AUDIO_RATE
        a = np.real(z * np.exp(2j * np.pi * PITCH_HZ * t))
        return (a / (np.abs(a).max() + 1e-12) * 0.9).astype(np.float32)

    def decode_iq(self, iq_interleaved):
        a = self.iq_to_audio(iq_interleaved)
        win = int(WIN_SEC * AUDIO_RATE)
        parts = []
        with self._torch.no_grad():
            for i in range(0, len(a), win):
                seg = a[i:i + win]
                if len(seg) < AUDIO_RATE or not self._has_signal(seg, AUDIO_RATE)[0]:
                    continue
                x = resample_poly(seg, self._sr, AUDIO_RATE).astype(np.float32)
                spec = self._spec(self._cond(x, self._sr), self._sr)
                parts.append(self._ctc(self._net(spec.unsqueeze(0).unsqueeze(0)))[0])
        return ' '.join(p for p in parts if p.strip())
