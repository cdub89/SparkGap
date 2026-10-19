#!/bin/bash
# Runs the SparkGap instances on this machine; "sparkgap.sh help" lists the commands.
set -u
R=${SPARKGAP_ROOT:-$HOME/sparkgap}
START_WAIT_S=45   # longest a copy takes to reach the radio and go live
STOP_WAIT_S=30
CPU_SAMPLE_S=2    # status measures each copy's CPU over this long
if [ -t 1 ] && [ -z "${NO_COLOR:-}" ]; then BOLD=$'\e[1m' GREEN=$'\e[32m' CYAN=$'\e[36m' YELLOW=$'\e[33m' RED=$'\e[31m' OFF=$'\e[0m'; else BOLD= GREEN= CYAN= YELLOW= RED= OFF=; fi

usage() {
  cat <<EOF
sparkgap.sh: run the SparkGap instances on this machine.

  sparkgap.sh status [name ...]    each instance: running or stopped (with the last error), commit, uptime,
                                   CPU, memory, band, GUI feed address and join code, telnet port, recording,
                                   spots, errors, warnings, reconnects, IQ trimmed, top calls and its log
  sparkgap.sh start [name ...]     start in the background and wait until it is live;
                                   the log goes to logs/<name>_<band>_<UTC>.log (UTC to the second, as recordings)
  sparkgap.sh stop [name ...]      stop cleanly (as Ctrl-C would) and wait up to ${STOP_WAIT_S} s
  sparkgap.sh restart [name ...]   stop, then start
  sparkgap.sh band <name> <band>   set the band in its config; restarts it if it was running
  sparkgap.sh rec <name> on|off    record IQ to rec/ or stop recording; restarts it if it was running
  sparkgap.sh add <name> <band>    a new instance: name = letters and the DAX-IQ channel (lakehouse4);
                                   a worktree of the first instance's clone in <name>/, its config copied
                                   to config/<name>.local.json with that channel, the band and the next
                                   free telnet and UDP ports; the GUI feed takes the first free port from
                                   7320 when it starts (an existing config/<name>.local.json is reused);
                                   not started
  sparkgap.sh remove <name>        take a stopped instance out: its worktree goes, its config stays in config/
  sparkgap.sh update               pull each code folder in instances, rebuild the native libraries
                                   where C code changed, restart what was running from it, and
                                   refresh this script in the root from tools/sparkgap.sh
  sparkgap.sh help                 this list; -h or --help after any command shows that command

With no name, start, stop, restart and status act on every instance.
Only instances started by sparkgap.sh are seen: each has a pid file in logs/.
Root: $R (instances, config/, logs/, rec/); set SPARKGAP_ROOT to use another.
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
cfg() { dir "$1" > /dev/null; echo "$R/config/$(field "$1" 3)"; }
band() { python3 -c "import json,sys; print(json.load(open(sys.argv[1]))['bands'][0])" "$(cfg "$1")"; }
bands() {  # band names sparkgap.py accepts, read from its BANDS line
  grep -m1 "^BANDS = dict(zip(" "$(dir "$1")/sparkgap.py" | grep -o "'[0-9]*m'" | tr -d "'" | tr '\n' ' '
}
pid() { local p; p=$(cat "$R/logs/$1.pid" 2>/dev/null) && kill -0 "$p" 2>/dev/null && echo "$p"; }
ticks() { local s; s=$(cat "/proc/$1/stat" 2>/dev/null) || return 1; s=${s##*) }; awk '{print $12 + $13}' <<< "$s"; }  # user + system CPU
procinfo() {  # uptime as the console shows it, CPU % of one core over CPU_SAMPLE_S, resident memory
  local s kb up t0 t1
  read -r s kb < <(ps -o etimes=,rss= -p "$1" 2>/dev/null) || return 0
  if [ "$s" -ge 3600 ]; then up="$((s / 3600))h $((s % 3600 / 60))m"; else up="$((s / 60))m $((s % 60))s"; fi
  t0=$(ticks "$1") && sleep "$CPU_SAMPLE_S" && t1=$(ticks "$1") || { echo "up $up, $BOLD$CYAN$((kb / 1024)) MB$OFF"; return 0; }
  echo "up $up, $BOLD${CYAN}cpu $((100 * (t1 - t0) / CPU_SAMPLE_S / $(getconf CLK_TCK)))%$OFF, $BOLD$CYAN$((kb / 1024)) MB$OFF"
}
bandlabel() { local b; b=$(band "$1"); echo "${BOLD}Band: ${b^^}$OFF"; }
newest_log() { ls -t "$R"/logs/"$1"_*.log 2>/dev/null | head -1; }
# a log's ERROR lines and the exception that ends a traceback
errors_in() { grep -E '^[0-9:]+ ERROR |^[A-Za-z_.]*(Error|Exception): ' "$1"; }

start() {
  local i=$1 p t log
  if p=$(pid "$i"); then echo "$i: already running (pid $p)"; return; fi
  t=$(date -u +%Y%m%d_%H%M%SZ); log="$R/logs/${i}_$(band "$i")_$t.log"
  # exec: the subshell becomes the copy, so no sparkgap.sh is left holding the caller's output
  (cd "$(dir "$i")" && exec setsid bash -c 'echo $$ > "$1"; exec "$2" sparkgap.py --config "$3" > "$4" 2>&1' \
     _ "$R/logs/$i.pid" "$(field "$i" 4)" "$(cfg "$i")" "$log" < /dev/null > /dev/null 2>&1 &)
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
    echo "$BOLD$i$OFF: ${GREEN}running$OFF, $(git -C "$(dir "$i")" describe --always --dirty), pid $p, $(procinfo "$p"), $(bandlabel "$i")"
  else
    echo "$BOLD$i$OFF: ${RED}stopped$OFF, $(git -C "$(dir "$i")" describe --always --dirty), $(bandlabel "$i")"
    [ -n "$log" ] && last=$(errors_in "$log" | tail -1) && [ -n "$last" ] && echo "   ${RED}last error: $last$OFF"
  fi
  python3 - "$(cfg "$i")" "$BOLD$YELLOW" "$OFF" "$([ -n "$p" ] && echo "$log")" <<'PY'
import json, re, socket, sys
c = json.load(open(sys.argv[1]))
g = c.get("gui_feed")
if g:
    host = g.get("bind", "127.0.0.1")
    host = socket.gethostname() if host in ("0.0.0.0", "::") else host
    port = g.get("port", 7320)
    if port == "auto":  # the port it got is in its log; stopped, it gets one at the next start
        got = re.findall(r"GUI feed on wss?://\S+?:(\d+)/", open(sys.argv[4]).read()) if sys.argv[4] else []
        port = got[-1] if got else "auto"
    web = f", browser http://{host}:{port}/" if g.get("web") and port != "auto" else ""
    print(f"   feed {host}:{port}, join code {(sys.argv[2] + g['join_code'] + sys.argv[3]) if g.get('join_code') else 'none (local only)'}{web}")
else:
    print("   feed off")
print(f"   telnet {c.get('telnet_port', 7300)}, recording {'on' if c.get('record_wav') else 'off'}")
PY
  [ -n "$log" ] || return 0
  spots=$(grep -c 'SPOT:' "$log")
  local line trim errs warns recon
  errs=$(errors_in "$log" | wc -l)
  [ "$errs" -gt 0 ] && errs="$RED$errs$OFF"
  warns=$(grep -c '^[0-9:]* WARNING ' "$log")
  [ "$warns" -gt 0 ] && warns="$YELLOW$warns$OFF"
  recon=$(grep -c 'connecting again' "$log")
  [ "$recon" -gt 0 ] && recon="$YELLOW$recon$OFF"
  line="   spots $spots, errors $errs, warnings $warns, reconnects $recon"
  trim=$(grep -h 'IQ trimmed' "$log" | tail -1 | sed 's/.*INFO //')
  [[ -n $trim && ! $trim =~ :\ 0(\.0)?s ]] && trim="$RED$trim$OFF"
  [ -n "$trim" ] && line="$line, $trim"
  echo "$line"
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

clone_of() {  # the clone, on a branch, that a code folder shares its repository with
  git -C "$1" worktree list --porcelain | awk '/^worktree / {w = $2} /^branch / {print w; exit}'
}

add() {
  local n=$1 b=$2 num src top d conf py l
  [[ $n =~ ^[a-z]+([1-9])$ ]] || die "name must be letters and one digit 1 to 9, the DAX-IQ channel (e.g. lakehouse4)"
  num=${BASH_REMATCH[1]}
  [ -z "$(field "$n" 1)" ] || die "$n is already in instances"
  d=$R/$n; [ ! -e "$d" ] || die "$d already exists"
  src=${ALL%% *}; [ -n "$src" ] || die "add copies the first instance in instances; there is none"
  [[ " $(bands "$src") " == *" $b "* ]] || die "band must be one of: $(bands "$src")"
  top=$(clone_of "$(dir "$src")"); [ -n "$top" ] || die "$src has no clone on a branch to add a worktree to"
  conf=$R/config/$n.local.json; py=$(field "$src" 4)
  # check and write the config before touching git, so a refusal leaves nothing behind
  local cfgs=(); for l in $ALL; do cfgs+=("$(cfg "$l")"); done
  python3 - "$R" "$n" "$num" "$b" "$conf" "${cfgs[@]}" <<'PY' || exit 2
import json, os, re, sys
root, name, num, band, conf, *others = sys.argv[1:]
num = int(num)
cs = {f: json.load(open(f)) for f in others}
src = cs[others[0]]
if src.get("sdr_type") != "flex":
    sys.exit(f"add copies a Flex instance; {others[0]} is {src.get('sdr_type')}")
for f, c in cs.items():
    if c.get("flex_daxiq_channel") == num and f != conf:
        sys.exit(f"DAX-IQ {num} is already used by {os.path.basename(f)}")
keys = ("flex_udp_port", "telnet_port")
def ports(c):
    feed = c.get("gui_feed", {}).get("port")
    return [c[k] for k in keys if k in c] + ([feed] if isinstance(feed, int) else [])
if os.path.exists(conf):
    text = open(conf).read()
    c = json.loads(text)
    clash = set(ports(c)) & {p for f, o in cs.items() if f != conf for p in ports(o)}
    if clash:
        sys.exit(f"{conf} uses ports already taken: {sorted(clash)}")
    if c.get("flex_daxiq_channel") != num:
        sys.exit(f"{conf} uses DAX-IQ {c.get('flex_daxiq_channel')}, not {num}")
    print(f"{name}: reusing {conf}")
else:
    text = open(others[0]).read()
    c = json.loads(text)
    for k in keys:
        if k in c:
            c[k] = max(o.get(k, 0) for o in cs.values()) + 1
    if "gui_feed" in c:
        c["gui_feed"]["port"] = "auto"  # the first free one from 7320 when it starts
    c["flex_daxiq_channel"] = num
    c.pop("flex_station", None)  # the station comes from the radio's nickname
    c["rbn_known_dir"] = f"{root}/rbn_known"  # one RBN cache for every instance on this machine
    if c.get("record_wav"):
        c["record_wav"] = f"{root}/rec/{name}_{{band}}_{{ts}}.wav"
c["bands"] = [band]
m = re.search(r'\n([ \t]+)"', text)
open(conf + ".new", "w").write(json.dumps(c, indent=m.group(1) if m else 2) + "\n")
g = c.get("gui_feed", {})
print(f"{name}: DAX-IQ {num}, {band}, telnet {c.get('telnet_port')}, feed {g.get('port', 'off')}, "
      f"UDP {c.get('flex_udp_port')}, config {conf}")
PY
  git -C "$top" worktree add -q --detach "$d" HEAD || { rm -f "$conf.new"; exit 2; }
  for l in "$top"/*.so; do cp "$l" "$d/"; done
  git -C "$d" ls-files '*.so' | (cd "$d" && xargs -r git update-index --assume-unchanged)
  mv "$conf.new" "$conf"
  echo "$n $d $n.local.json $py" >> "$R/instances"
  echo "start with: sparkgap.sh start $n"
}

remove() {
  local n=$1 d f
  d=$(dir "$n"); f=$(field "$n" 3)
  pid "$n" > /dev/null && die "$n is running; stop it first"
  [ -f "$d/.git" ] || die "$d is the clone the other instances share; only a worktree can be removed"
  git -C "$(clone_of "$d")" worktree remove "$d" || die "git kept $d (changes in it?); nothing removed"
  awk -v n="$n" '$1 != n' "$R/instances" > "$R/instances.new" && mv "$R/instances.new" "$R/instances"
  echo "$n: removed; its config stays in $R/config/$f"
}

update() {
  local d i old new top changed=""
  declare -A was
  for d in $(awk '!/^#/ && NF {print $2}' "$R/instances" | sort -u); do
    old=$(git -C "$d" rev-parse HEAD) || return 1
    if git -C "$d" symbolic-ref -q HEAD >/dev/null; then
      git -C "$d" fetch -q || { echo "$d: fetch failed"; return 1; }
      new=$(git -C "$d" rev-parse '@{u}')
    else  # a detached worktree follows the clone it shares a repository with
      top=$(git -C "$d" worktree list --porcelain | awk '/^worktree / {w = $2} /^branch / {print w; exit}')
      [ -n "$top" ] || { echo "$d: detached, no branch to follow"; continue; }
      new=$(git -C "$top" rev-parse HEAD)
    fi
    if [ "$new" = "$old" ]; then echo "$d: already at $(git -C "$d" log --oneline -1)"; continue; fi
    # the native builds stand in for the tracked portable ones: put those back so the move can update them
    git -C "$d" ls-files '*.so' | (cd "$d" && xargs -r git update-index --no-assume-unchanged)
    git -C "$d" checkout -q -- '*.so'
    git -C "$d" merge -q --ff-only "$new" 2>/dev/null || git -C "$d" checkout -q --detach "$new" \
      || { echo "$d: update to $new failed"; return 1; }
    git -C "$d" ls-files '*.so' | (cd "$d" && xargs -r git update-index --assume-unchanged)
    echo "$d: $(git -C "$d" log --oneline -1)"
    was[$d]=$old; changed="$changed $d"
  done
  d=$(awk '!/^#/ && NF {print $2; exit}' "$R/instances")  # the root's copy of this script follows the code
  if [ -f "$d/tools/sparkgap.sh" ] && ! cmp -s "$d/tools/sparkgap.sh" "$R/sparkgap.sh"; then
    # a new file renamed into place: bash keeps reading the old one it is running
    cp "$d/tools/sparkgap.sh" "$R/sparkgap.sh.new" && mv "$R/sparkgap.sh.new" "$R/sparkgap.sh" &&
      echo "$R/sparkgap.sh: updated from $d"
  fi
  [ -n "$changed" ] || return 0
  local running=""
  for i in $ALL; do
    [[ " $changed " == *" $(dir "$i") "* ]] && pid "$i" >/dev/null && running="$running $i"
  done
  for i in $running; do stop "$i"; done
  for d in $changed; do  # the move put the portable builds back
    (cd "$d" && make -B native ARCH=native > "$R/logs/make_native.log" 2>&1) \
      || { echo "$d: native build failed, see logs/make_native.log"; return 1; }
    echo "$d: native libraries rebuilt"
  done
  for i in $running; do start "$i"; done
}

[ $# -gt 0 ] || { usage; exit 0; }
cmd=$1; shift
case $cmd in help|-h|--help) usage; exit 0 ;; esac
case $cmd in
  status|start|stop|restart|band|rec|update|add|remove) ;;
  *) echo "sparkgap.sh: unknown command '$cmd'" >&2; echo >&2; usage >&2; exit 2 ;;
esac
for a in "$@"; do [[ $a == -h || $a == --help ]] && { cmd_usage "$cmd"; exit 0; }; done
case $cmd in
  start|stop) [ $# -gt 0 ] || set -- $ALL; for i in "$@"; do dir "$i" > /dev/null; $cmd "$i"; done ;;
  status) [ $# -gt 0 ] || set -- $ALL; sep=; for i in "$@"; do dir "$i" > /dev/null; [ -n "$sep" ] && echo; sep=1; status "$i"; done ;;
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
  add) [ $# -eq 2 ] || { cmd_usage add; exit 2; }; add "$1" "$2" ;;
  remove) [ $# -eq 1 ] || { cmd_usage remove; exit 2; }; remove "$1" ;;
esac
