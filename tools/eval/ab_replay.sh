#!/usr/bin/env bash
# ab_replay.sh MANIFEST LABEL [ITILA2_SRC] [SCANNER_SRC] [FIR_HDR]
#
# One command A/B replay: build the candidate decoder (itila2) and Fred's
# baseline (itila) from a clean checkout, replay every recording listed in
# MANIFEST through both, and score each against a CW Skimmer tee with
# parity_score.py. Exists because hand-built replay chains have run the
# wrong library twice: a candidate source compiled outside the repo could
# not find itila.h, and a stale .so left on disk ran instead of the fresh
# build; and once used the wrong centre frequency. Building fresh in a
# temp tree with an explicit include path removes both failure modes.
#
# MANIFEST is JSON, outside the repo:
#   {"config": "<path>", "baseline_dir": "<dir>", "out_dir": "<dir>",
#    "recordings": [{"name": "20m_run1", "wav": "<path>", "center_khz": 14050.0,
#                     "rate": 96000, "cws": "<path>", "held_out": false}, ...]}
#
# LABEL names the candidate's output folder under out_dir. ITILA2_SRC,
# SCANNER_SRC and FIR_HDR are optional candidate source overrides used in
# place of the working tree's itila2_core.c, itila_scanner.c and
# itila_fir_coeffs.h.
set -euo pipefail

usage() { echo "usage: $0 MANIFEST LABEL [ITILA2_SRC] [SCANNER_SRC] [FIR_HDR]" >&2; exit 2; }
[ $# -ge 2 ] || usage
MANIFEST=$1; LABEL=$2; ITILA2_SRC=${3:-}; SCANNER_SRC=${4:-}; FIR_HDR=${5:-}

cd "$(dirname "$0")/../.."
if [ -n "${PYTHON:-}" ]; then PY=$PYTHON
elif [ -x .venv/bin/python ]; then PY=$PWD/.venv/bin/python
else PY=python3; fi

fail() { echo "refusing to run: $*" >&2; exit 1; }

[ -f "$MANIFEST" ] || fail "manifest not found: $MANIFEST"
for src in "$ITILA2_SRC" "$SCANNER_SRC" "$FIR_HDR"; do
    [ -z "$src" ] || [ -f "$src" ] || fail "override source not found: $src"
done

echo "parsing manifest $MANIFEST"
MANIFEST_TSV=$("$PY" - "$MANIFEST" <<'EOF'
import json, sys
m = json.load(open(sys.argv[1]))
print(m["config"], m["baseline_dir"], m["out_dir"], sep="\t")
for r in m["recordings"]:
    print(r["name"], r["wav"], r["center_khz"], r["rate"], r["cws"],
          bool(r.get("held_out", False)), sep="\t")
EOF
) || fail "could not parse manifest $MANIFEST"

CONFIG=$(printf '%s\n' "$MANIFEST_TSV" | sed -n '1p' | cut -f1)
BASELINE_DIR=$(printf '%s\n' "$MANIFEST_TSV" | sed -n '1p' | cut -f2)
OUT_DIR_BASE=$(printf '%s\n' "$MANIFEST_TSV" | sed -n '1p' | cut -f3)
[ -f "$CONFIG" ] || fail "config not found: $CONFIG"

NAMES=(); WAVS=(); CENTERS=(); RATES=(); CWSES=(); HELDS=()
while IFS=$'\t' read -r name wav center rate cws held; do
    [ -n "$name" ] || continue
    NAMES+=("$name"); WAVS+=("$wav"); CENTERS+=("$center")
    RATES+=("$rate"); CWSES+=("$cws"); HELDS+=("$held")
done < <(printf '%s\n' "$MANIFEST_TSV" | tail -n +2)
[ "${#NAMES[@]}" -gt 0 ] || fail "manifest has no recordings"

for i in "${!NAMES[@]}"; do
    [ -f "${WAVS[$i]}" ] || fail "recording ${NAMES[$i]}: wav not found: ${WAVS[$i]}"
    [ -f "${CWSES[$i]}" ] || fail "recording ${NAMES[$i]}: cws not found: ${CWSES[$i]}"
done

OUT="$OUT_DIR_BASE/$LABEL"
mkdir -p "$OUT"

echo "building clean tree"
T=$(mktemp -d)
cleanup() {
    if [ "${AB_KEEP_TREE:-0}" = "1" ]; then
        echo "clean tree kept at $T"
    else
        rm -rf "$T"
    fi
}
trap cleanup EXIT

rsync -a --exclude=.git --exclude=.venv --exclude='*.wav' --exclude=__pycache__ --exclude='*.so' ./ "$T/"
[ -z "$ITILA2_SRC" ] || cp "$ITILA2_SRC" "$T/itila2_core.c"
[ -z "$SCANNER_SRC" ] || cp "$SCANNER_SRC" "$T/itila_scanner.c"
[ -z "$FIR_HDR" ] || cp "$FIR_HDR" "$T/itila_fir_coeffs.h"

CODE=$(git rev-parse --short HEAD)
[ -z "$(git status --porcelain)" ] || CODE="$CODE-dirty"
CODE_CLEAN=${CODE%-dirty}

{
    echo "all times UTC"
    echo "start      $(date -u +%Y-%m-%dT%H:%M:%SZ)"
    echo "label      $LABEL"
    echo "manifest   $MANIFEST"
    echo "code       $CODE"
    [ -z "$ITILA2_SRC" ] || echo "itila2_src $ITILA2_SRC sha256 $(sha256sum "$ITILA2_SRC" | cut -c1-12)"
    [ -z "$SCANNER_SRC" ] || echo "scanner_src $SCANNER_SRC sha256 $(sha256sum "$SCANNER_SRC" | cut -c1-12)"
    [ -z "$FIR_HDR" ] || echo "fir_hdr $FIR_HDR sha256 $(sha256sum "$FIR_HDR" | cut -c1-12)"
} > "$OUT/run.txt"

build_lib() {
    local desc="$1"; shift
    local log
    if ! log=$(cd "$T" && "$@" 2>&1); then
        echo "$log" >&2
        fail "$desc build failed"
    fi
}

echo "building libitila2.so (candidate)"
build_lib "libitila2.so" gcc -Wall -Wextra -O3 -march=native -ffast-math -I"$T" -shared -fPIC -o libitila2.so itila2_core.c fb_core.c -lm

echo "building libitila_scanner.so"
build_lib "libitila_scanner.so" gcc -O3 -march=native -ffast-math -I"$T" -shared -fPIC -o libitila_scanner.so itila_scanner.c -lm

echo "building libitila.so (baseline)"
build_lib "libitila.so" gcc -O3 -march=native -ffast-math -I"$T" -shared -fPIC -o libitila.so itila_core.c fb_core.c -lm

strings "$T/libitila2.so" | grep -q 'ITILA2 runs' || fail "libitila2.so lacks the itila2 marker"

echo "writing itila/itila2 configs"
"$PY" - "$CONFIG" "$T/config_itila.json" "$T/config_itila2.json" <<'EOF'
import json, sys
src, out1, out2 = sys.argv[1:4]
cfg = json.load(open(src))
cfg.pop("record_wav", None)
cfg["cw_decoder"] = "itila"
json.dump(cfg, open(out1, "w"), indent=4)
cfg["cw_decoder"] = "itila2"
json.dump(cfg, open(out2, "w"), indent=4)
EOF

B="$BASELINE_DIR/$CODE_CLEAN"
mkdir -p "$B"
if [ "$CODE" != "$CODE_CLEAN" ]; then
    echo "warning: baseline cache key $CODE_CLEAN was built from a dirty tree" >&2
fi

echo "running baselines in $B"
for i in "${!NAMES[@]}"; do
    name=${NAMES[$i]}; wav=${WAVS[$i]}; center=${CENTERS[$i]}
    log="$B/$name.log"
    if [ -f "$log" ]; then
        echo "  $name: baseline cached"
        continue
    fi
    echo "  $name: decoding baseline"
    if (cd "$T" && TZ=UTC "$PY" sparkgap.py --config config_itila.json --file "$wav" --center-khz "$center" > "$log" 2>&1); then
        rc=0
    else
        rc=$?
    fi
    echo "baseline $name exit $rc" >> "$OUT/run.txt"
done

echo "running candidate ($LABEL) into $OUT"
for i in "${!NAMES[@]}"; do
    name=${NAMES[$i]}; wav=${WAVS[$i]}; center=${CENTERS[$i]}
    echo "  $name: decoding candidate"
    if (cd "$T" && ITILA2_DUMP_RUNS=1 TZ=UTC "$PY" sparkgap.py --config config_itila2.json --file "$wav" --center-khz "$center" > "$OUT/$name.log" 2>&1); then
        rc=0
    else
        rc=$?
    fi
    echo "candidate $name exit $rc" >> "$OUT/run.txt"
done

echo "scoring"
DISP=(); BASEDEC=(); CANDDEC=(); DELTAS=(); GAINEDS=(); LOSTS=()
for i in "${!NAMES[@]}"; do
    name=${NAMES[$i]}; center=${CENTERS[$i]}; rate=${RATES[$i]}; cws=${CWSES[$i]}; held=${HELDS[$i]}
    score_out=$("$PY" tools/eval/parity_score.py --ours "$B/$name.log" "$OUT/$name.log" \
        --cws "$cws" --center-khz "$center" --rate "$rate" --show 0)
    printf '%s\n' "$score_out" | grep -E ': decode recall |^(decode|spot) (gained|lost):' > "$OUT/$name.score.txt"

    base_line=$(printf '%s\n' "$score_out" | grep -E ': decode recall ' | sed -n '1p')
    cand_line=$(printf '%s\n' "$score_out" | grep -E ': decode recall ' | sed -n '2p')
    delta_line_txt=$(printf '%s\n' "$score_out" | grep -E ': decode recall ' | sed -n '3p')
    base_decode=$(printf '%s\n' "$base_line" | grep -oE 'decode recall [0-9]+/[0-9]+' | sed 's/decode recall //')
    cand_decode=$(printf '%s\n' "$cand_line" | grep -oE 'decode recall [0-9]+/[0-9]+' | sed 's/decode recall //')
    delta_decode=$(printf '%s\n' "$delta_line_txt" | grep -oE 'decode recall [+-][0-9]+' | sed 's/decode recall //')
    gained=$(printf '%s\n' "$score_out" | grep '^decode gained:' | sed 's/^decode gained: //')
    lost=$(printf '%s\n' "$score_out" | grep '^decode lost:' | sed 's/^decode lost: //')

    disp_name=$name
    [ "$held" = "True" ] && disp_name="$name (held-out)"
    DISP+=("$disp_name"); BASEDEC+=("$base_decode"); CANDDEC+=("$cand_decode")
    DELTAS+=("$delta_decode"); GAINEDS+=("$gained"); LOSTS+=("$lost")

    printf '%s | baseline decode %s | candidate decode %s | delta %s | gained %s | lost %s\n' \
        "$disp_name" "$base_decode" "$cand_decode" "$delta_decode" "$gained" "$lost"
done

echo
echo "name | baseline decode | candidate decode | delta | gained | lost"
echo "---- | --------------- | ----------------- | ----- | ------ | ----"
for i in "${!DISP[@]}"; do
    printf '%s | %s | %s | %s | %s | %s\n' \
        "${DISP[$i]}" "${BASEDEC[$i]}" "${CANDDEC[$i]}" "${DELTAS[$i]}" "${GAINEDS[$i]}" "${LOSTS[$i]}"
done

{
    cat "$OUT/run.txt"
    echo
    echo "| name | baseline decode | candidate decode | delta | gained | lost |"
    echo "|---|---|---|---|---|---|"
    for i in "${!DISP[@]}"; do
        printf '| %s | %s | %s | %s | %s | %s |\n' \
            "${DISP[$i]}" "${BASEDEC[$i]}" "${CANDDEC[$i]}" "${DELTAS[$i]}" "${GAINEDS[$i]}" "${LOSTS[$i]}"
    done
} > "$OUT/summary.md"

exit 0
