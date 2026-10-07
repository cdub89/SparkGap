"""Second-pass decoder plug-in interface for the ITILA scanner.

ITILA decodes every scanner bin from its 200 Hz envelope. A second-pass
decoder gets a bin's *real* audio -- the scanner's 1 kHz complex baseband for
one decode window, when baseband capture is enabled -- on the bins the
scanner's trigger, budget and priority select (see _ItilaScanner in
sparkgap.py). Its text goes through the same runner extraction and spot
gates as ITILA's.

Writing a decoder
-----------------
Any class with this shape works::

    class MyDecoder:
        name = 'mydec'          # short id: log tags ("MYDEC raw ...") and
                                # the raw_text prefix of its spots

        def __init__(self, cfg):
            # cfg = the whole "second_pass" config dict; read your own keys
            # (model path, threads, ...). Raise to refuse to start (e.g. an
            # incompatible model) -- the scanner logs it and stays ITILA-only.
            ...

        def decode_iq(self, iq, first_look=False):
            # iq: float32 array, interleaved I,Q, complex baseband at
            #     IQ_RATE (1 kHz) for one ITILA window (60 s by default),
            #     the bin's signal at 0 Hz, about +-100 Hz of bandwidth.
            # first_look: a cheap look is acceptable (e.g. decode only the
            #     most active part); a full decode is always correct too.
            # Return decoded text ('' for nothing). Called on a background
            # thread, one call at a time per worker.
            ...

Select it in the config with "decoder": "<registered name>" or, without
touching this file, "decoder": "package.module:ClassName".
"""
import importlib
import queue
import threading

IQ_RATE = 1000

# Built-in decoders: name -> "module:Class"
REGISTRY = {
    'deepfist': 'deepfist_pass:DeepFistOnnx',
}


def load_decoder(cfg):
    """Instantiate the decoder named by cfg['decoder'] (registry name or
    'module:Class'). Raises if it can't be loaded or refuses the config."""
    spec = cfg.get('decoder', '')
    target = REGISTRY.get(spec, spec)
    if ':' not in target:
        raise ValueError('unknown second-pass decoder %r (known: %s, or module:Class)'
                         % (spec, ', '.join(sorted(REGISTRY))))
    mod_name, cls_name = target.split(':', 1)
    cls = getattr(importlib.import_module(mod_name), cls_name)
    dec = cls(cfg)
    if not callable(getattr(dec, 'decode_iq', None)):
        raise TypeError('%s has no decode_iq()' % target)
    if not getattr(dec, 'name', None):
        dec.name = spec.split(':')[-1].lower()
    return dec


class SecondPassWorker:
    """Runs a decoder on a background thread. submit() never blocks (a full
    queue drops the job and counts it); drain() returns finished
    (key, text, err) tuples; flush() waits for everything submitted so far
    (file mode, for deterministic replay)."""

    def __init__(self, decoder, max_queue=256, workers=1, nice=10, batch_jobs=1):
        """workers > 1 runs that many decode threads on the same queue; the
        decoder's decode_iq must then be thread-safe (DeepFist's is: numpy
        front end + onnxruntime Session.run). batch_jobs > 1: a worker takes
        up to that many queued jobs at once and hands them to the decoder's
        optional decode_iq_batch([(iq, first_look), ...]) -> [text, ...]
        (GPU batching); without that method jobs run one at a time."""
        self.decoder = decoder
        self.name = decoder.name
        self._jobs = queue.Queue(maxsize=max_queue)
        self._done = queue.Queue()
        self._lock = threading.Lock()
        self.dropped = 0
        self.decoded = 0
        self._nice = int(nice)
        self._batch = max(1, int(batch_jobs)) if callable(
            getattr(decoder, 'decode_iq_batch', None)) else 1
        for i in range(max(1, int(workers))):
            threading.Thread(target=self._run, name='second-pass-%s-%d' % (self.name, i),
                             daemon=True).start()

    def _run(self):
        # Lower this decode thread's scheduling priority (Linux: nice is per
        # thread) so the primary decoder's threads always win the CPU -- the
        # second pass must never starve ITILA / the receiver.
        if self._nice:
            try:
                import os
                os.setpriority(os.PRIO_PROCESS, threading.get_native_id(), self._nice)
            except (AttributeError, OSError):
                pass
        while True:
            jobs = [self._jobs.get()]
            while len(jobs) < self._batch:          # whatever else is already queued
                try:
                    jobs.append(self._jobs.get_nowait())
                except queue.Empty:
                    break
            try:
                if len(jobs) == 1:
                    key, iq, first_look = jobs[0]
                    out = [(key, self.decoder.decode_iq(iq, first_look) or '', None)]
                else:
                    texts = self.decoder.decode_iq_batch([(iq, fl) for _k, iq, fl in jobs])
                    out = [(k, t or '', None) for (k, _iq, _fl), t in zip(jobs, texts)]
            except Exception as e:      # one bad batch must not kill the worker
                out = [(k, '', repr(e)) for k, _iq, _fl in jobs]
            for r in out:
                self._done.put(r)
            with self._lock:
                self.decoded += len(jobs)
            for _ in jobs:
                self._jobs.task_done()

    def submit(self, key, iq, first_look=False):
        try:
            self._jobs.put_nowait((key, iq, first_look))
            return True
        except queue.Full:
            with self._lock:
                self.dropped += 1
            return False

    def clear(self):
        """Discard queued (not yet started) jobs; returns how many."""
        n = 0
        while True:
            try:
                self._jobs.get_nowait()
            except queue.Empty:
                return n
            self._jobs.task_done()
            n += 1

    def drain(self):
        out = []
        while True:
            try:
                out.append(self._done.get_nowait())
            except queue.Empty:
                return out

    def flush(self):
        self._jobs.join()
        return self.drain()
