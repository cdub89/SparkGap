#!/bin/bash
# Runs the SparkGap instances on this machine; "sparkgap.sh help" lists the commands.
set -u
R=${SPARKGAP_ROOT:-$HOME/sparkgap}
START_WAIT_S=45   # longest a copy takes to reach the radio and go live
STOP_WAIT_S=30

usage() {
  cat <<EOF
sparkgap.sh: run the SparkGap instances on this machine.

  sparkgap.sh status [name ...]    each instance: running or stopped (with the last error), uptime, band,
                                   commit, GUI feed address and join code, telnet port, recording, spots,
                                   errors, IQ trimmed, packets lost, top calls and its log
  sparkgap.sh start [name ...]     start in the background and wait until it is live;
                                   the log goes to logs/<name>_<band>_<UTC>.log (UTC to the second, as recordings)
  sparkgap.sh stop [name ...]      stop cleanly (as Ctrl-C would) and wait up to ${STOP_WAIT_S} s
  sparkgap.sh restart [name ...]   stop, then start
  sparkgap.sh band <name> <band>   set the band in its config; restarts it if it was running
  sparkgap.sh rec <name> on|off    record IQ to rec/ or stop recording; restarts it if it was running
  sparkgap.sh update               pull each code folder in instances, rebuild the native libraries
                                   where C code changed, restart what was running from it, and
                                   refresh this script in the root from tools/sparkgap.sh
  sparkgap.sh help                 this list; -h or --help after any command shows that command

With no name, start, stop, restart and status act on every instance.
Only instances started by sparkgap.sh are seen: each has a pid file in logs/.
Root: $R (instances, logs/, rec/); set SPARKGAP_ROOT to use another.
EOF
  [ -f "$R/instances" ] && echo "Instances: $ALL   (from instances: name, code folder, config, python)"
}

cmd_usage() {  # the help lines of one command
  usage | awk -v c="  sparkgap.sh $1" 'index($0, c " ") == 1 || $0 == c {p = 1; print; next}
                                       p && /^    / {print; next} {p = 0}'
}

die() { echo "sparkgap.sh: $*" >&2; exit 2; }

[ -f "$R/instances" ] || die "no instances file in $R (set SPARKGAP_ROOT)"
ALL=$(awk '!/^#/ && NF {printf "%s ", $1}' "$R/instances")

field() { awk -v n="$1" -v f="$2" '$1 == n {print $f}' "$R/instances"; }
dir() { local d; d=$(field "$1" 2); [ -n "$d" ] || die "unknown instance $1 (instances: $ALL)"; echo "$d"; }
cfg() { echo "$(dir "$1")/$(field "$1" 3)"; }
band() { python3 -c "import json,sys; print(json.load(open(sys.argv[1]))['bands'][0])" "$(cfg "$1")"; }
bands() {  # band names sparkgap.py accepts, read from its BANDS line
  grep -m1 "^BANDS = dict(zip(" "$(dir "$1")/sparkgap.py" | grep -o "'[0-9]*m'" | tr -d "'" | tr '\n' ' '
}
pid() { local p; p=$(cat "$R/logs/$1.pid" 2>/dev/null) && kill -0 "$p" 2>/dev/null && echo "$p"; }
newest_log() { ls -t "$R"/logs/"$1"_*.log 2>/dev/null | head -1; }
# a log's ERROR lines and the exception that ends a traceback
errors_in() { grep -E '^[0-9:]+ ERROR |^[A-Za-z_.]*(Error|Exception): ' "$1"; }

start() {
  local i=$1 p t log
  if p=$(pid "$i"); then echo "$i: already running (pid $p)"; return; fi
  t=$(date -u +%Y%m%d_%H%M%SZ); log="$R/logs/${i}_$(band "$i")_$t.log"
  (cd "$(dir "$i")" && setsid bash -c 'echo $$ > "$1"; exec "$2" sparkgap.py --config "$3" > "$4" 2>&1 < /dev/null' \
     _ "$R/logs/$i.pid" "$(field "$i" 4)" "$(field "$i" 3)" "$log" &)
  for _ in $(seq 1 "$START_WAIT_S"); do
    sleep 1
    grep -q "SparkGap LIVE:" "$log" 2>/dev/null && { echo "$i: live (pid $(pid "$i")), log $log"; return; }
    pid "$i" >/dev/null || break
  done
  if pid "$i" >/dev/null; then echo "$i: running but not live after ${START_WAIT_S} s, see $log"; return 1; fi
  echo "$i: failed to start: $(errors_in "$log" | tail -1)"; echo "   log $log"; rm -f "$R/logs/$i.pid"; return 1
}

stop() {
  local i=$1 p
  if ! p=$(pid "$i"); then echo "$i: not running"; rm -f "$R/logs/$i.pid"; return; fi
  kill -INT "$p"
  for _ in $(seq 1 "$STOP_WAIT_S"); do kill -0 "$p" 2>/dev/null || break; sleep 1; done
  if kill -0 "$p" 2>/dev/null; then echo "$i: still running after ${STOP_WAIT_S} s (pid $p)"; return 1; fi
  rm -f "$R/logs/$i.pid"; echo "$i: stopped"
}

status() {
  local i=$1 p log spots last
  log=$(newest_log "$i")
  if p=$(pid "$i"); then
    echo "$i: running, pid $p, up $(ps -o etime= -p "$p" 2>/dev/null | tr -d ' '), $(band "$i"), $(git -C "$(dir "$i")" describe --always --dirty)"
  else
    echo "$i: stopped, $(band "$i"), $(git -C "$(dir "$i")" describe --always --dirty)"
    [ -n "$log" ] && last=$(errors_in "$log" | tail -1) && [ -n "$last" ] && echo "   last error: $last"
  fi
  python3 - "$(cfg "$i")" <<'PY'
import json, socket, sys
c = json.load(open(sys.argv[1]))
g = c.get("gui_feed")
if g:
    host = g.get("bind", "127.0.0.1")
    host = socket.gethostname() if host in ("0.0.0.0", "::") else host
    web = f", browser http://{host}:{g.get('port', 7320)}/" if g.get("web") else ""
    print(f"   feed {host}:{g.get('port', 7320)}, join code {g.get('join_code') or 'none (local only)'}{web}")
else:
    print("   feed off")
print(f"   telnet {c.get('telnet_port', 7300)}, recording {'on' if c.get('record_wav') else 'off'}")
PY
  [ -n "$log" ] || return 0
  spots=$(grep -c 'SPOT:' "$log")
  local line trim
  line="   spots $spots, errors $(errors_in "$log" | wc -l)"
  trim=$(grep -h 'IQ trimmed' "$log" | tail -1 | sed 's/.*INFO //')
  [ -n "$trim" ] && line="$line, $trim"
  echo "$line"
  grep -h "\[Flex\].*packets" "$log" | tail -1 | sed 's/.*\[Flex\] /   radio: /'
  [ "$spots" -gt 0 ] && grep -h 'SPOT:' "$log" | awk '{print $6}' | sort | uniq -c | sort -rn | head -10 |
    awk 'BEGIN {printf "   top calls:"} {printf " %s(%s)", $2, $1} END {print ""}'
  echo "   log $log"
}

setcfg() {  # setcfg <name> <key> <json value or null>; keeps the file's own indentation
  python3 - "$(cfg "$1")" "$2" "$3" <<'PY'
import json, re, sys
f, k, v = sys.argv[1:4]
text = open(f).read()
m = re.search(r'\n([ \t]+)"', text)
indent = m.group(1) if m else 2
c = json.loads(text)
v = json.loads(v)
if v is None:
    c.pop(k, None)
else:
    c[k] = v
open(f, "w").write(json.dumps(c, indent=indent) + "\n")
PY
}

reconfigure() {  # restart after a config change, only if it was running
  if pid "$1" >/dev/null; then stop "$1" && start "$1"; fi
}

update() {
  local d i old new top changed=""
  declare -A was
  for d in $(awk '!/^#/ && NF {print $2}' "$R/instances" | sort -u); do
    old=$(git -C "$d" rev-parse HEAD) || return 1
    if git -C "$d" symbolic-ref -q HEAD >/dev/null; then
      git -C "$d" pull -q --ff-only || { echo "$d: pull failed"; return 1; }
    else  # a detached worktree follows the clone it shares a repository with
      top=$(git -C "$d" worktree list --porcelain | awk '/^worktree / {w = $2} /^branch / {print w; exit}')
      [ -n "$top" ] || { echo "$d: detached, no branch to follow"; continue; }
      git -C "$d" checkout -q --detach "$(git -C "$top" rev-parse HEAD)"
    fi
    new=$(git -C "$d" rev-parse HEAD)
    if [ "$new" = "$old" ]; then echo "$d: already at $(git -C "$d" log --oneline -1)"; continue; fi
    echo "$d: $(git -C "$d" log --oneline -1)"
    was[$d]=$old; changed="$changed $d"
  done
  d=$(awk '!/^#/ && NF {print $2; exit}' "$R/instances")  # the root's copy of this script follows the code
  if [ -f "$d/tools/sparkgap.sh" ] && ! cmp -s "$d/tools/sparkgap.sh" "$R/sparkgap.sh"; then
    cp "$d/tools/sparkgap.sh" "$R/sparkgap.sh" && echo "$R/sparkgap.sh: updated from $d"
  fi
  [ -n "$changed" ] || return 0
  local running=""
  for i in $ALL; do
    [[ " $changed " == *" $(dir "$i") "* ]] && pid "$i" >/dev/null && running="$running $i"
  done
  for i in $running; do stop "$i"; done
  for d in $changed; do
    if git -C "$d" diff --name-only "${was[$d]}" HEAD | grep -qE '\.(c|h|cpp)$|^Makefile$'; then
      (cd "$d" && make -B native ARCH=native > "$R/logs/make_native.log" 2>&1) \
        || { echo "$d: native build failed, see logs/make_native.log"; return 1; }
      echo "$d: native libraries rebuilt"
    fi
  done
  for i in $running; do start "$i"; done
}

[ $# -gt 0 ] || { usage; exit 0; }
cmd=$1; shift
case $cmd in help|-h|--help) usage; exit 0 ;; esac
case $cmd in
  status|start|stop|restart|band|rec|update) ;;
  *) echo "sparkgap.sh: unknown command '$cmd'" >&2; echo >&2; usage >&2; exit 2 ;;
esac
for a in "$@"; do [[ $a == -h || $a == --help ]] && { cmd_usage "$cmd"; exit 0; }; done
case $cmd in
  start|stop|status) [ $# -gt 0 ] || set -- $ALL; for i in "$@"; do dir "$i" > /dev/null; $cmd "$i"; done ;;
  restart) [ $# -gt 0 ] || set -- $ALL; for i in "$@"; do dir "$i" > /dev/null; stop "$i" && start "$i"; done ;;
  band)
    [ $# -eq 2 ] || { cmd_usage band; exit 2; }
    dir "$1" > /dev/null
    [[ " $(bands "$1") " == *" $2 "* ]] || die "band must be one of: $(bands "$1")"
    setcfg "$1" bands "[\"$2\"]"; echo "$1: band $2"; reconfigure "$1" ;;
  rec)
    [ $# -eq 2 ] && [[ $2 == on || $2 == off ]] || { cmd_usage rec; exit 2; }
    dir "$1" > /dev/null
    if [ "$2" = on ]; then setcfg "$1" record_wav "\"$R/rec/${1}_{band}_{ts}.wav\""; else setcfg "$1" record_wav null; fi
    echo "$1: recording $2 ($(df -h --output=avail "$R/rec" | tail -1 | tr -d ' ') free in rec/)"; reconfigure "$1" ;;
  update) [ $# -eq 0 ] || { cmd_usage update; exit 2; }; update ;;
esac
