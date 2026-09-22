#!/usr/bin/env bash
# parity_capture.sh CONFIG CWS_HOST:PORT OUTDIR [MINUTES]
#
# Linux side of a parity run. Tees CW Skimmer's telnet output, runs SparkGap
# live from CONFIG with its log and IQ recording, stops both after MINUTES
# (default 30, 0 = until Ctrl-C), then scores the log with parity_score.py.
# CW Skimmer runs on its own machine on another DAX IQ channel of the same
# radio. Everything lands in OUTDIR/<UTC stamp>_<band>kHz/.
set -euo pipefail

usage() { echo "usage: $0 CONFIG CWS_HOST:PORT OUTDIR [MINUTES]" >&2; exit 2; }
[ $# -ge 3 ] || usage
CONFIG=$1; CWS=$2; OUTDIR=$3; MINUTES=${4:-30}

cd "$(dirname "$0")/../.."
if [ -n "${PYTHON:-}" ]; then PY=$PYTHON
elif [ -x .venv/bin/python ]; then PY=.venv/bin/python
else PY=python3; fi

# Pull and check the config fields the run depends on.
read -r CALL BAND_HZ RATE FT8 THOST RECORD DAXCH < <("$PY" - "$CONFIG" <<'EOF'
import json, sys
cfg = json.load(open(sys.argv[1]))
band = cfg.get("bands", [None])[0]
if isinstance(band, dict):
    band = int(band["center_khz"] * 1000)
print(cfg.get("callsign", ""), band, cfg.get("sample_rate", 192000),
      cfg.get("enable_ft8", True), cfg.get("telnet_host", "0.0.0.0"),
      bool(cfg.get("record_wav")), cfg.get("flex_daxiq_channel", "?"))
EOF
)
fail() { echo "refusing to run: $*" >&2; exit 1; }
[ -n "$CALL" ] || fail "no callsign in $CONFIG"
case "$CALL" in WF8Z*) fail "callsign $CALL is the upstream maintainer's";; esac
[ "$FT8" = "False" ] || fail "enable_ft8 must be false"
case "$THOST" in 127.0.0.1|localhost) ;; *) fail "telnet_host must be localhost";; esac
[ "$RECORD" = "True" ] || fail "record_wav is not set in $CONFIG"
case "$BAND_HZ" in ''|None) fail "bands[0] missing";; esac
CENTER_KHZ=$(awk "BEGIN{printf \"%.3f\", $BAND_HZ/1000}")
CWS_HOST=${CWS%%:*}; CWS_PORT=${CWS##*:}
[ "$CWS_HOST" != "$CWS_PORT" ] || usage

STAMP=$(date -u +%Y%m%d_%H%M%SZ)
RUN="$OUTDIR/${STAMP}_$((BAND_HZ / 1000))kHz"
mkdir -p "$RUN"
echo "run dir $RUN"
CODE=$(git rev-parse --short HEAD 2>/dev/null || echo unknown)
[ -z "$(git status --porcelain 2>/dev/null)" ] || CODE="$CODE-dirty"
{
    echo "all times UTC"
    echo "start      $STAMP"
    echo "host       $(hostname)"
    echo "code       $CODE ($(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo unknown))"
    echo "config     $CONFIG sha256 $(sha256sum "$CONFIG" | cut -c1-12)"
    echo "callsign   $CALL"
    echo "center     $CENTER_KHZ kHz"
    echo "rate       $RATE"
    echo "daxiq_ch   $DAXCH"
    echo "cws        $CWS"
    echo "minutes    $MINUTES"
} > "$RUN/run.txt"

: > "$RUN/cws.log"
"$PY" tools/capture_cluster.py "$CWS_HOST" "$CWS_PORT" "$CALL" "$RUN/cws.log" &
TEE_PID=$!
TZ=UTC "$PY" sparkgap.py --config "$CONFIG" > "$RUN/sparkgap.log" 2>&1 &
SG_PID=$!
echo "sparkgap pid $SG_PID, tee pid $TEE_PID; Ctrl-C stops early"

stop_all() {
    trap - INT TERM
    kill -INT "$SG_PID" 2>/dev/null || true
    wait "$SG_PID" 2>/dev/null || true
    kill "$TEE_PID" 2>/dev/null || true
    wait "$TEE_PID" 2>/dev/null || true
}
trap stop_all INT TERM

if [ "$MINUTES" -gt 0 ]; then
    for ((i = 0; i < MINUTES * 60; i++)); do
        kill -0 "$SG_PID" 2>/dev/null || { echo "sparkgap exited early" >&2; break; }
        sleep 1
    done
else
    wait "$SG_PID" || true
fi
stop_all

WAV=$(grep -o 'Recording IQ to .*' "$RUN/sparkgap.log" | head -1 | cut -d' ' -f4-)
WAVINFO=none
if [ -n "$WAV" ] && [ -f "$WAV" ]; then
    WAVINFO=$("$PY" -c "import sys,wave; w=wave.open(sys.argv[1]); print(f'{w.getnframes()/w.getframerate():.0f} s {w.getframerate()} Hz {w.getnchannels()} ch {w.getsampwidth()*8} bit')" "$WAV" 2>/dev/null || echo unreadable)
fi
first_stamp() { grep -m1 -o '^[0-9:]\{8\}' "$1" 2>/dev/null || echo none; }
last_stamp()  { { grep -o '^[0-9:]\{8\}' "$1" 2>/dev/null || true; } | tail -1; }
{
    echo "end        $(date -u +%Y%m%d_%H%M%SZ)"
    echo "sg_first   $(first_stamp "$RUN/sparkgap.log")"
    echo "sg_last    $(last_stamp "$RUN/sparkgap.log")"
    echo "sg_stop    $(grep -o 'Stopped: .*' "$RUN/sparkgap.log" | tail -1)"
    echo "wav        ${WAV:-none}"
    echo "wav_info   $WAVINFO"
    echo "cws_lines  $({ grep -c 'DX de' "$RUN/cws.log" 2>/dev/null || true; } | tail -1)"
    echo "cws_first  $(first_stamp "$RUN/cws.log")"
    echo "cws_last   $(last_stamp "$RUN/cws.log")"
} >> "$RUN/run.txt"
cat "$RUN/run.txt"

"$PY" tools/eval/parity_score.py --ours "$RUN/sparkgap.log" --cws "$RUN/cws.log" \
    --center-khz "$CENTER_KHZ" --rate "$RATE" | tee "$RUN/score.txt"
echo "replay:  $PY sparkgap.py --config $CONFIG --file ${WAV:-<wav>} --center-khz $CENTER_KHZ > replay.log 2>&1"
