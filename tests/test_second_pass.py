"""Second-pass decoder plug-in interface (second_pass.py) and DeepFist's
model-compatibility check. Runs without onnxruntime or a model file.

Runs under pytest, or standalone:  python3 tests/test_second_pass.py
"""
import os
import sys
import types

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import second_pass as SP  # noqa: E402


class EchoDecoder:
    """Minimal plug-in: returns a fixed text, records first_look."""
    name = 'echo'

    def __init__(self, cfg):
        if cfg.get('refuse'):
            raise ValueError('refused by config')
        self.text = cfg.get('text', 'CQ TEST W1AW')
        self.looks = []

    def decode_iq(self, iq, first_look=False):
        self.looks.append(first_look)
        if iq.size == 0:
            raise RuntimeError('empty window')
        return self.text


# expose EchoDecoder as an importable "module:Class" target
sys.modules['_echo_plugin'] = types.SimpleNamespace(EchoDecoder=EchoDecoder)


def _iq(n=10):
    return np.zeros(2 * n, dtype=np.float32)


def test_load_by_module_class():
    dec = SP.load_decoder({'decoder': '_echo_plugin:EchoDecoder', 'text': 'CQ K1ABC'})
    assert dec.name == 'echo' and dec.decode_iq(_iq()) == 'CQ K1ABC'


def test_unknown_decoder_rejected():
    try:
        SP.load_decoder({'decoder': 'no-such-decoder'})
    except ValueError as e:
        assert 'deepfist' in str(e)        # lists known decoders
    else:
        raise AssertionError('unknown decoder should raise')


def test_decoder_may_refuse_config():
    try:
        SP.load_decoder({'decoder': '_echo_plugin:EchoDecoder', 'refuse': True})
    except ValueError:
        pass
    else:
        raise AssertionError('refusal should propagate')


def test_worker_flush_and_errors():
    w = SP.SecondPassWorker(SP.load_decoder({'decoder': '_echo_plugin:EchoDecoder'}))
    assert w.submit(7030000.0, _iq(), first_look=True)
    assert w.submit(7031000.0, np.zeros(0, dtype=np.float32))   # decoder raises
    done = sorted(w.flush())
    assert done[0] == (7030000.0, 'CQ TEST W1AW', None)
    assert done[1][0] == 7031000.0 and done[1][1] == '' and 'empty window' in done[1][2]
    assert w.decoded == 2 and w.dropped == 0
    assert w.decoder.looks[0] is True


def test_worker_full_queue_drops():
    class Slow(EchoDecoder):
        def decode_iq(self, iq, first_look=False):
            import time
            time.sleep(0.2)
            return ''
    w = SP.SecondPassWorker(Slow({}), max_queue=1)
    results = [w.submit(i, _iq()) for i in range(5)]
    assert not all(results) and w.dropped >= 1
    w.flush()


def test_deepfist_registered_and_compat_check():
    assert SP.REGISTRY['deepfist'] == 'deepfist_pass:DeepFistOnnx'
    import deepfist_pass as P
    meta = {'preprocessing': dict(P._PREPROC), 'input': {'layout': '[batch,1,freq=65,time]'}}
    P.check_model_meta(meta)                       # matches what we implement
    meta['preprocessing']['hop_length'] = 64
    try:
        P.check_model_meta(meta)
    except ValueError as e:
        assert 'hop_length' in str(e)
    else:
        raise AssertionError('different preprocessing must be refused')
    # conditioning: absent (exp27_bt) or true is fine; a raw-audio model is refused
    meta = {'preprocessing': dict(P._PREPROC, conditioned=True),
            'input': {'layout': '[batch,1,freq=65,time]'}, 'ctc': {'time_downsample': 2}}
    P.check_model_meta(meta)
    for key, val in (('conditioned', False), ('time_downsample', 4)):
        m = {'preprocessing': dict(meta['preprocessing']), 'input': meta['input'], 'ctc': dict(meta['ctc'])}
        (m['preprocessing'] if key == 'conditioned' else m['ctc'])[key] = val
        try:
            P.check_model_meta(m)
        except ValueError as e:
            assert key in str(e)
        else:
            raise AssertionError('%s=%r must be refused' % (key, val))


def test_df_priority_ranking():
    """Budget ranking fitted on B1/DK3QN job logs: CQ evidence first, more
    ITILA text better, clean-ITILA-no-call and pure-noise windows demoted."""
    import sparkgap as sg
    job = lambda cls, snr=15.0, cost=0.3, noise=0.6, ilen=100: (7030000.0, 0, cls, snr, cost, noise, ilen)
    assert sg._df_priority(job(2)) > sg._df_priority(job(1))           # CQ evidence first
    assert sg._df_priority(job(1, ilen=150)) > sg._df_priority(job(1, ilen=10))
    assert sg._df_priority(job(1, cost=0.01)) < sg._df_priority(job(1, cost=0.3))   # clean ITILA, no call
    assert sg._df_priority(job(1, noise=0.9)) < sg._df_priority(job(1, noise=0.5))
    assert sg._df_priority(job(1, cost=-1.0, ilen=0)) < sg._df_priority(job(1))      # no ITILA text


if __name__ == '__main__':
    fns = [v for k, v in sorted(globals().items()) if k.startswith('test_') and callable(v)]
    ok = 0
    for fn in fns:
        try:
            fn(); print('PASS ', fn.__name__); ok += 1
        except Exception as e:
            print('FAIL ', fn.__name__, type(e).__name__, e)
    print(f'\n{ok}/{len(fns)} passed'); sys.exit(0 if ok == len(fns) else 1)


def test_backoff_pauses_on_env_drops():
    """A rise in ITILA's env_drops pauses the pool, discards queued work,
    and blocks new jobs until backoff_sec has passed."""
    import sparkgap as S

    class Stub:
        name = 'stub'
        def __init__(self, cfg): pass
        def decode_iq(self, iq, first_look=False):
            return ''
    pool = S._SecondPass.__new__(S._SecondPass)
    pool.worker = SP.SecondPassWorker(Stub({}))
    pool.tag, pool.sync, pool.backoff_sec = 'STUB', False, 30.0
    pool.paused_until, pool.backed_off, pool.backoffs = 0.0, 0, 0
    pool._pressure, pool._pressure_t = None, 0.0
    pool.cands = [(1.0, None, None, None, False)] * 3
    t = [1000.0]
    pool.now = lambda: t[0]
    pool.pressure(500)                 # first reading: baseline only
    assert not pool.paused()
    t[0] += 2; pool.pressure(500)      # no rise
    assert not pool.paused()
    t[0] += 2; pool.pressure(900)      # ITILA dropping -> pause
    assert pool.paused() and pool.backoffs == 1 and pool.backed_off == 3 and not pool.cands
    t[0] += 0.5; pool.pressure(2000)   # < 1 s since last check: ignored
    assert pool.backoffs == 1 and pool._pressure == 900
    t[0] += 31; pool.pressure(900)     # no new drops, window passed
    assert not pool.paused()
    t[0] += 2; pool.pressure(1500)     # drops again -> second pause
    assert pool.paused() and pool.backoffs == 2
    t[0] += 20; pool.pressure(1600)    # still dropping -> pause extended, same episode
    assert pool.paused() and pool.backoffs == 2 and pool.paused_until == t[0] + 30
    t[0] += 31; pool.pressure(1600)
    assert not pool.paused()
    pool.backoff_sec = 0               # disabled
    t[0] += 2; pool.pressure(9999)
    assert not pool.paused()


def test_worker_batches_queued_jobs():
    """batch_jobs > 1: jobs queued while the worker is busy reach
    decode_iq_batch together (never more than batch_jobs), results map back
    to their keys, and a failing call reports an error for every job in it."""
    import threading
    import numpy as np
    gate = threading.Event()
    sizes = []

    class Batcher:
        name = 'batcher'
        def decode_iq(self, iq, first_look=False):
            return self.decode_iq_batch([(iq, first_look)])[0]
        def decode_iq_batch(self, jobs):
            gate.wait(5)                     # hold the first call until all are queued
            sizes.append(len(jobs))
            return ['t%d' % int(iq[0]) for iq, _fl in jobs]
    w = SP.SecondPassWorker(Batcher(), batch_jobs=4)
    for i in range(10):
        w.submit('k%d' % i, np.array([float(i)], np.float32))
    gate.set()
    got = {k: t for k, t, _e in w.flush()}
    assert got == {'k%d' % i: 't%d' % i for i in range(10)}
    assert sum(sizes) == 10 and max(sizes) == 4 and w.decoded == 10

    class Broken:
        name = 'broken'
        def decode_iq(self, iq, first_look=False):
            raise ValueError('boom')
        def decode_iq_batch(self, jobs):
            raise ValueError('boom')
    w = SP.SecondPassWorker(Broken(), batch_jobs=4)
    for i in range(5):
        w.submit(i, np.zeros(2, np.float32))
    res = w.flush()
    assert len(res) == 5 and all(t == '' and 'boom' in e for _k, t, e in res)


def test_worker_without_batch_method_runs_singly():
    class Plain:
        name = 'plain'
        def decode_iq(self, iq, first_look=False):
            return 'x'
    w = SP.SecondPassWorker(Plain(), batch_jobs=8)
    assert w._batch == 1
