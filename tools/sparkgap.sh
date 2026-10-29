#!/bin/bash
# Runs the SparkGap instances on this machine; "sparkgap.sh help" lists the commands.
set -u
R=${SPARKGAP_ROOT:-$HOME/sparkgap}
CODE=$R/code                  # the clone every instance is a worktree of
TEMPLATE=$R/config/template.json   # what add copies
REC_MIN_GB=5                  # rec on refuses below this much free space in rec/
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
                                   the log goes to logs/<name>_<UTC>.log (UTC to the second, as recordings);
                                   a Flex instance first starts its radio's station (flex_station.py, log
                                   logs/station_<radio>_<UTC>.log), one per radio, which every copy binds to
  sparkgap.sh stop [name ...]      stop cleanly (as Ctrl-C would) and wait up to ${STOP_WAIT_S} s; the last
                                   Flex instance on a radio stops its station too
  sparkgap.sh restart [name ...]   stop, then start
  sparkgap.sh band <name> <band>   set the band in its config; restarts it if it was running
  sparkgap.sh rec <name> on|off    record IQ to rec/ or stop recording; restarts it if it was running
  sparkgap.sh add <name> <band>    a new instance: name = letters and the DAX-IQ channel (lake4);
                                   a worktree of code/ in <name>/, config/template.json copied to
                                   config/<name>.local.json with that channel, the band and the first
                                   free telnet and UDP ports from the template's; the GUI feed takes the
                                   first free port from 7321 when it starts, 7320 being the server's (an existing
                                   config/<name>.local.json is reused); not started
  sparkgap.sh remove <name>        take a stopped instance out: its worktree goes, its config stays in config/
  sparkgap.sh rename <old> <new>   rename a stopped instance, keeping its digit (the DAX-IQ channel):
                                   its folder, config/<new>.local.json and recording names follow;
                                   logs keep the old name
  sparkgap.sh update               pull code/, stop the copies whose code moves, move each code folder
                                   to it and rebuild its native libraries, start them again, and
                                   refresh this script in the root from tools/sparkgap.sh
  sparkgap.sh init <repository> <branch> [radio address]
                                   set up a new root: code/ cloned from the repository, an empty
                                   instances and the template (as template below); makes only what is
                                   missing
  sparkgap.sh template [radio address]
                                   config/template.json from skimmer_flex.example.json and the radio:
                                   its address and callsign, and in comments its model, nickname,
                                   software and limits; the radio is found by discovery unless an
                                   address is given (needed on Windows, where SmartSDR holds the
                                   discovery port); an old template is kept as template.json.<UTC>
  sparkgap.sh migrate              an older root, where the first instance holds the clone: that clone
                                   becomes code/ and the instance a worktree of it; every instance
                                   stopped first; rerun it to finish one that stopped partway
  sparkgap.sh help                 this list; -h or --help after any command shows that command

With no name, start, stop, restart and status act on every instance.
Only instances started by sparkgap.sh are seen: each has a pid file in logs/.
Root: $R (code/, instances, config/, logs/, rec/); set SPARKGAP_ROOT to use another.
EOF
  [ -f "$R/instances" ] && echo "Instances: $ALL   (from instances: name, code folder, config, python)"
}

cmd_usage() {  # the help lines of one command
  usage | awk -v c="  sparkgap.sh $1" 'index($0, c " ") == 1 || $0 == c {p = 1; print; next}
                                       p && /^    / {print; next} {p = 0}'
}

die() { echo "sparkgap.sh: $*" >&2; exit 2; }

ALL=$([ -f "$R/instances" ] && awk '!/^#/ && NF {printf "%s ", $1}' "$R/instances")
[ -f "$R/instances" ] || [ "${1:-}" = init ] || [[ "${1:-}" =~ ^(|help|-h|--help)$ ]] \
  || die "no instances file in $R (sparkgap.sh init makes one; set SPARKGAP_ROOT for another root)"

field() { awk -v n="$1" -v f="$2" '$1 == n {print $f}' "$R/instances"; }
dir() { local d; d=$(field "$1" 2); [ -n "$d" ] || die "unknown instance $1 (instances: $ALL)"; echo "$d"; }
cfg() { dir "$1" > /dev/null; echo "$R/config/$(field "$1" 3)"; }
band() { python3 -c "import json,sys; print(json.load(open(sys.argv[1]))['bands'][0])" "$(cfg "$1")"; }
bands() { bands_in "$(dir "$1")"; }
bands_in() {  # band names sparkgap.py in a code folder accepts, read from its BANDS line
  grep -m1 "^BANDS = dict(zip(" "$1/sparkgap.py" | grep -o "'[0-9]*m'" | tr -d "'" | tr '\n' ' '
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
bandlabel() {  # the band, and AFC when the config turns it on
  local b a; b=$(band "$1")
  a=$(python3 -c "import json,sys; print(' AFC' if json.load(open(sys.argv[1])).get('itila2_afc') else '')" "$(cfg "$1")")
  echo "${BOLD}Band: ${b^^}$a$OFF"
}
newest_log() { ls -t "$R"/logs/"$1"_*.log 2>/dev/null | head -1; }
# a log's ERROR lines and the exception that ends a traceback
errors_in() { grep -E '^[0-9:]+ ERROR |^[A-Za-z_.]*(Error|Exception): ' "$1"; }

radio() {  # the Flex radio an instance uses (its sdr_ip), empty for any other receiver
  python3 -c "import json,sys; c=json.load(open(sys.argv[1])); print(c.get('sdr_ip') or '' if c.get('sdr_type') == 'flex' else '')" "$(cfg "$1")"
}
spid() { local p; p=$(cat "$R/logs/station_$1.pid" 2>/dev/null) && kill -0 "$p" 2>/dev/null && echo "$p"; }

locked() {  # run "$@" holding instance $2's radio lock, then its own (the server takes the same, in this order)
  local ip; ip=$(radio "$2")
  if [ -n "$ip" ]; then ( flock 9; ( flock 8; "$@" ) 8> "$R/logs/$2.lock" ) 9> "$R/logs/station_$ip.lock"
  else ( flock 8; "$@" ) 8> "$R/logs/$2.lock"; fi
}

station_up() {  # the radio's station (flex_station.py), started from instance $1 unless it runs
  local i=$1 ip log
  ip=$(radio "$i"); [ -n "$ip" ] || return 0
  spid "$ip" > /dev/null && return 0
  log="$R/logs/station_${ip}_$(date -u +%Y%m%d_%H%M%SZ).log"
  (cd "$(dir "$i")" && exec setsid bash -c 'echo $$ > "$1"; exec "$2" flex_station.py --config "$3" --status "$4" > "$5" 2>&1' \
     _ "$R/logs/station_$ip.pid" "$(field "$i" 4)" "$(cfg "$i")" "$R/logs/station_$ip.json" "$log" < /dev/null > /dev/null 2>&1 8>&- 9>&- &)
  for _ in $(seq 1 "$START_WAIT_S"); do
    sleep 1
    grep -q "holds the GUI client" "$log" 2>/dev/null && { echo "station $ip: live (pid $(spid "$ip")), log $log"; return; }
    spid "$ip" > /dev/null || break
  done
  grep -q "already has station" "$log" 2>/dev/null && { echo "station $ip: held by another process"; rm -f "$R/logs/station_$ip.pid"; return; }
  echo "station $ip: failed to start: $(errors_in "$log" | tail -1)"; echo "   log $log"; rm -f "$R/logs/station_$ip.pid"; return 1
}

station_down() {  # stop the station of instance $1's radio once no other running instance uses it
  local i=$1 ip j p
  ip=$(radio "$i"); [ -n "$ip" ] || return 0
  for j in $ALL; do [ "$j" != "$i" ] && pid "$j" > /dev/null && [ "$(radio "$j")" = "$ip" ] && return 0; done
  p=$(spid "$ip") || { rm -f "$R/logs/station_$ip.pid"; return 0; }
  kill -INT "$p"
  for _ in $(seq 1 "$STOP_WAIT_S"); do kill -0 "$p" 2>/dev/null || break; sleep 1; done
  if kill -0 "$p" 2>/dev/null; then echo "station $ip: still running after ${STOP_WAIT_S} s (pid $p)"; return 1; fi
  rm -f "$R/logs/station_$ip.pid"; echo "station $ip: stopped"
}

start() { locked start_flex "$1"; }
start_flex() { station_up "$1" && start_copy "$1"; }
stop() { locked stop_flex "$1"; }
stop_flex() { stop_copy "$1" && station_down "$1"; }

start_copy() {
  local i=$1 p t log
  if p=$(pid "$i"); then echo "$i: already running (pid $p)"; return; fi
  t=$(date -u +%Y%m%d_%H%M%SZ); log="$R/logs/${i}_$t.log"
  # exec: the subshell becomes the copy, so no sparkgap.sh is left holding the caller's output
  (cd "$(dir "$i")" && exec setsid bash -c 'echo $$ > "$1"; exec "$2" sparkgap.py --config "$3" > "$4" 2>&1' \
     _ "$R/logs/$i.pid" "$(field "$i" 4)" "$(cfg "$i")" "$log" < /dev/null > /dev/null 2>&1 8>&- 9>&- &)
  for _ in $(seq 1 "$START_WAIT_S"); do
    sleep 1
    grep -q "SparkGap LIVE:" "$log" 2>/dev/null && { echo "$i: live (pid $(pid "$i")), log $log"; return; }
    pid "$i" >/dev/null || break
  done
  if pid "$i" >/dev/null; then echo "$i: running but not live after ${START_WAIT_S} s, see $log"; return 1; fi
  echo "$i: failed to start: $(errors_in "$log" | tail -1)"; echo "   log $log"; rm -f "$R/logs/$i.pid"; return 1
}

stop_copy() {
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
    [ -n "$log" ] && last=$(errors_in "$log" | tail -1) && [ -n "$last" ] && echo "   ${RED}last error $(date -u -r "$log" +"%m-%d %H:%MZ"): $last$OFF"
  fi
  local ip; ip=$(radio "$i")
  if [ -n "$ip" ] && spid "$ip" > /dev/null; then
    python3 - "$R/logs/station_$ip.json" <<'PY'
import json, sys, time
try:
    h = json.load(open(sys.argv[1]))
except (OSError, ValueError):
    sys.exit(print("   station: starting"))
hour = [l for l in h.get("losses", []) if time.time() - l[0] < 3600]
last = f", last {time.strftime('%H:%MZ', time.gmtime(hour[-1][0]))}: {hour[-1][1]}" if hour else ""
print(f"   station {h['station']}: {h['state']}, ping {h['rtt_ms']} ms, losses in the last hour {len(hour)}{last}")
PY
  elif [ -n "$ip" ]; then
    echo "   station: not running"
  fi
  python3 - "$(cfg "$i")" "$BOLD$YELLOW" "$OFF" "$([ -n "$p" ] && echo "$log")" "$REC_MIN_GB" <<'PY'
import json, os, re, shutil, socket, sys
c = json.load(open(sys.argv[1]))
g = c.get("gui_feed")
if g:
    host = g.get("bind", "127.0.0.1")
    host = socket.gethostname() if host in ("0.0.0.0", "::") else host
    port = g.get("port", "auto")
    if port == "auto":  # the port it got is in its log; stopped, it gets one at the next start
        got = re.findall(r"GUI feed on wss?://\S+?:(\d+)/", open(sys.argv[4]).read()) if sys.argv[4] else []
        port = got[-1] if got else "auto"
    web = f", browser http://{host}:{port}/" if g.get("web") and port != "auto" else ""
    print(f"   feed {host}:{port}, join code {(sys.argv[2] + g['join_code'] + sys.argv[3]) if g.get('join_code') else 'none (local only)'}{web}")
else:
    print("   feed off")
rec = c.get("record_wav")
if rec:
    gb = shutil.disk_usage(os.path.dirname(rec) or ".").free // 2**30
    low = gb < int(sys.argv[5])
    rec = f"on, {sys.argv[2] if low else ''}{gb} GB free{' (rec on needs ' + sys.argv[5] + ')' + sys.argv[3] if low else ''}"
print(f"   telnet {c.get('telnet_port', 7300)}, recording {rec or 'off'}")
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

server_status() {  # the machine's server (sparkgap_server.py): running or not, its address, join code and web page
  local p
  [ -f "$R/server.json" ] || { echo "${BOLD}server$OFF: not set up (sparkgap_server.py writes server.json at its first start)"; return; }
  local cmd  # running only while that pid is an server for this root (its --root, or none given)
  p=$(cat "$R/logs/server.pid" 2>/dev/null) && cmd=$(tr '\0' ' ' < "/proc/$p/cmdline" 2>/dev/null) || cmd=
  [[ $cmd == *sparkgap_server.py* ]] && { [[ $cmd != *--root* ]] || [[ $cmd == *"--root $R "* ]]; } || p=
  python3 - "$R/server.json" "$p" "$BOLD" "$GREEN" "$RED" "$YELLOW" "$OFF" <<'PY'
import json, sys
c = json.load(open(sys.argv[1])); pid, B, G, R, Y, O = sys.argv[2:8]
code = str(c.get("join_code", "")).upper().replace("-", "")
show = f"{code[:3]}-{code[3:]}" if len(code) == 6 else code
host, port = c.get("bind", "127.0.0.1"), c.get("port", 7320)
web = "https" if c.get("tls_cert") else "http"
state = f"{G}running{O}, pid {pid}" if pid else f"{R}stopped{O}"
print(f"{B}server{O}: {state}, {host}:{port}, join code {B}{Y}{show}{O}, browser {web}://{host}:{port}/")
PY
}

setcfg() {  # setcfg <name> <key> <json value or null>; keeps the file's own indentation
  python3 - "$(cfg "$1")" "$2" "$3" <<'PY' || { rm -f "$(cfg "$1").new"; die "$(cfg "$1") not changed"; }
import json, os, re, sys
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
try:  # a full disk fails here and leaves the config as it was
    with open(f + ".new", "w") as out:
        out.write(json.dumps(c, indent=indent) + "\n")
except OSError as e:
    sys.exit(e.strerror)
os.replace(f + ".new", f)
PY
}

free_gb() { df -BG --output=avail "$R/rec" | tail -1 | tr -dc 0-9; }

set_restart() {  # setcfg, then restart if running: one step under the locks the caller holds
  setcfg "$1" "$2" "$3" || return
  if pid "$1" > /dev/null; then stop_flex "$1" && start_flex "$1"; fi
}

clone_of() {  # the clone, on a branch, that a code folder shares its repository with
  git -C "$1" worktree list --porcelain | awk '/^worktree / {w = $2} /^branch / {print w; exit}'
}

add() {
  local n=$1 b=$2 num top d conf tpl py l
  [[ $n =~ ^[a-z]+([1-9])$ ]] || die "name must be letters and one digit 1 to 9, the DAX-IQ channel (e.g. lake4)"
  num=${BASH_REMATCH[1]}
  [ -z "$(field "$n" 1)" ] || die "$n is already in instances"
  d=$R/$n; [ ! -e "$d" ] || die "$d already exists"
  top=$(source_clone 2>/dev/null); [ -n "$top" ] || die "no code/ clone and no instance to copy (run sparkgap.sh init)"
  [[ " $(bands_in "$top") " == *" $b "* ]] || die "band must be one of: $(bands_in "$top")"
  if [ -f "$TEMPLATE" ]; then tpl=$TEMPLATE
  elif [ -n "$ALL" ]; then tpl=$(cfg "${ALL%% *}")
  else die "no $TEMPLATE and no instance to copy (run sparkgap.sh init)"; fi
  conf=$R/config/$n.local.json
  py=$(field "${ALL%% *}" 4); [ -n "$py" ] || py=$R/.venv/bin/python; [ -x "$py" ] || py=$(command -v python3)
  # check and write the config before touching git, so a refusal leaves nothing behind
  local cfgs=(); for l in $ALL; do cfgs+=("$(cfg "$l")"); done
  python3 - "$R" "$n" "$num" "$b" "$conf" "$tpl" "${cfgs[@]}" <<'PY' || exit 2
import json, os, re, sys
root, name, num, band, conf, tpl, *others = sys.argv[1:]
num = int(num)
cs = {f: json.load(open(f)) for f in others}
for f, c in cs.items():
    if c.get("flex_daxiq_channel") == num and f != conf:
        sys.exit(f"DAX-IQ {num} is already used by {os.path.basename(f)}")
keys = ("flex_udp_port", "telnet_port", "telnet_local_port")
def ports(c):
    feed = c.get("gui_feed", {}).get("port")
    return [c[k] for k in keys if isinstance(c.get(k), int)] + ([feed] if isinstance(feed, int) else [])
used = {p for f, o in cs.items() if f != conf for p in ports(o)}
if os.path.exists(conf):
    text = open(conf).read()
    c = json.loads(text)
    clash = set(ports(c)) & used
    if clash:
        sys.exit(f"{conf} uses ports already taken: {sorted(clash)}")
    if c.get("flex_daxiq_channel") != num:
        sys.exit(f"{conf} uses DAX-IQ {c.get('flex_daxiq_channel')}, not {num}")
    print(f"{name}: reusing {conf}")
else:
    text = open(tpl).read()
    c = json.loads(text)
    if c.get("sdr_type") != "flex":
        sys.exit(f"add makes Flex instances; {os.path.basename(tpl)} is {c.get('sdr_type')}")
    for k in keys:  # the template's port, or the next free one after it
        if isinstance(c.get(k), int):
            while c[k] in used:
                c[k] += 1
            used.add(c[k])
    if "gui_feed" in c:
        c["gui_feed"]["port"] = "auto"  # the first free one from 7321 when it starts
    c["flex_daxiq_channel"] = num
    c.pop("flex_station", None)  # the station comes from the radio's nickname
    c["rbn_known_dir"] = f"{root}/rbn_known"  # one RBN cache for every instance on this machine
    if c.get("record_wav"):
        c["record_wav"] = f"{root}/rec/{name}_{{band}}_{{ts}}.wav"
c["bands"] = [band]
m = re.search(r'\n([ \t]+)"', text)
with open(conf + ".new", "w") as out:
    out.write(json.dumps(c, indent=m.group(1) if m else 2) + "\n")
g = c.get("gui_feed", {})
print(f"{name}: DAX-IQ {num}, {band}, telnet {c.get('telnet_port')}, feed {g.get('port', 'off')}, "
      f"UDP {c.get('flex_udp_port')}, config {conf}")
limit = (c.get("_radio") or json.load(open(tpl)).get("_radio") or {}).get("limit")
if limit and len(cs) + 1 > limit:
    print(f"warning: {len(cs) + 1} instances, but the {c['_radio'].get('model') if c.get('_radio') else 'radio'} "
          f"holds {limit} SparkGap copies (a pan, a slice and a DAX-IQ channel each), fewer while SmartSDR uses some")
PY
  git -C "$top" worktree add -q --detach "$d" HEAD || { rm -f "$conf.new"; exit 2; }
  so_native "$d"   # a failed build leaves the portable libraries, which run
  mv "$conf.new" "$conf"
  echo "$n $d $n.local.json $py" >> "$R/instances"
  echo "start with: sparkgap.sh start $n"
}

template() {  # template [radio address]: config/template.json from the example and what the radio reports
  local top
  top=$(source_clone 2>/dev/null); [ -n "$top" ] || die "no code/ clone (run sparkgap.sh init)"
  mkdir -p "$R/config" || die "cannot make $R/config"
  python3 "$top/tools/flex_template.py" --example "$top/skimmer_flex.example.json" --out "$TEMPLATE" \
    --root "$R" ${1:+--address "$1"} || exit 2
  echo "edit $TEMPLATE for this station (spot rule, feed, validation) before add"
}

init() {  # init <repository> <branch> [radio address]: what is missing, nothing replaced
  local repo=$1 br=$2
  mkdir -p "$R/config" "$R/logs" "$R/rec" || die "cannot make $R"
  if [ -d "$CODE/.git" ]; then echo "$CODE: already there"
  elif [ -e "$CODE" ]; then die "$CODE exists but is not a git clone"
  elif [ -f "$R/instances" ] && [ -n "$ALL" ]; then die "instances already run here: use sparkgap.sh migrate"
  else git clone -q --branch "$br" "$repo" "$CODE" || die "clone of $repo ($br) failed"; echo "$CODE: cloned $br"
  fi
  [ -f "$R/instances" ] || { echo "# name code-dir config python" > "$R/instances"; echo "$R/instances: made"; }
  if [ -f "$TEMPLATE" ]; then echo "$TEMPLATE: already there (sparkgap.sh template makes a new one)"
  else template "${3:-}"; fi
  echo "next: sparkgap.sh add <name> <band>"
}

migrate() {  # the first instance's clone becomes code/, the instance a worktree of it; rerun to finish a stopped one
  local d0 n0 head i d w
  n0=${ALL%% *}; [ -n "$n0" ] || die "no instances to migrate (use sparkgap.sh init)"
  dir "$n0" > /dev/null; d0=$(dir "$n0")
  for i in $ALL; do pid "$i" > /dev/null && die "$i is running; stop every instance first"; done
  pgrep -f "[s]parkgap.py --config $R/config/" > /dev/null && die "a SparkGap from $R/config is running outside sparkgap.sh"
  if [ -d "$CODE/.git" ]; then  # moved already: make what is missing
    [ -e "$d0" ] && [ ! -f "$d0/.git" ] && die "$CODE and $d0 both exist and $d0 is not a worktree; fix by hand"
  else
    [ ! -e "$CODE" ] || die "$CODE exists but is not a git clone"
    [ -d "$d0/.git" ] || die "$d0 is not the clone; nothing to migrate"
    for d in $(awk '!/^#/ && NF {print $2}' "$R/instances" | sort -u); do
      git -C "$d" status > /dev/null 2>&1 || die "$d: git cannot read it; nothing changed"
      [ -z "$(git -C "$d" status --porcelain --untracked-files=no | grep -v '\.so$')" ] || die "$d has local changes; nothing changed"
    done
    git -C "$d0" rev-parse HEAD > "$R/logs/migrate.head"
    mv "$d0" "$CODE" || die "$d0 not moved; nothing changed"
    git -C "$CODE" worktree repair 2> /dev/null || die "moved $d0 to $CODE, but git worktree repair failed; rerun migrate"
  fi
  for w in $(git -C "$CODE" worktree list --porcelain | awk '/^worktree / {print $2}'); do
    git -C "$w" status > /dev/null 2>&1 || die "$w: lost its link to $CODE (git -C $CODE worktree repair $w, then rerun migrate)"
  done
  so_portable "$CODE"
  if [ ! -e "$d0" ]; then
    head=$(cat "$R/logs/migrate.head" 2>/dev/null || git -C "$CODE" rev-parse HEAD)
    git -C "$CODE" worktree add -q --detach "$d0" "$head" || die "$CODE: worktree $d0 not made; rerun migrate"
    so_native "$d0"
  fi
  rm -f "$R/logs/migrate.head"
  echo "$CODE: the clone; $n0 is a worktree of it in $d0. Start with: sparkgap.sh start"
}

remove() {
  local n=$1 d f
  dir "$n" > /dev/null; d=$(dir "$n"); f=$(field "$n" 3)
  pid "$n" > /dev/null && die "$n is running; stop it first"
  [ -f "$d/.git" ] || die "$d is the clone the other instances share; only a worktree can be removed"
  git -C "$(clone_of "$d")" worktree remove "$d" || die "git kept $d (changes in it?); nothing removed"
  awk -v n="$n" '$1 != n' "$R/instances" > "$R/instances.new" && mv "$R/instances.new" "$R/instances"
  echo "$n: removed; its config stays in $R/config/$f"
}

source_clone() {  # the clone on a branch that every code folder follows: code/, or before migrate the first instance's
  if [ -d "$CODE/.git" ]; then echo "$CODE"; else clone_of "$(dir "${ALL%% *}")"; fi
}

so_portable() {  # put the tracked portable builds back, so git can move the folder
  git -C "$1" ls-files '*.so' | (cd "$1" && xargs -r git update-index --no-assume-unchanged)
  git -C "$1" checkout -q -- '*.so'
}

so_native() {  # rebuild the native libraries; on a failed build keep the portable ones, which run (slower)
  if (cd "$1" && make -B native ARCH=native > "$R/logs/make_native.log" 2>&1); then
    git -C "$1" ls-files '*.so' | (cd "$1" && xargs -r git update-index --assume-unchanged)
    echo "$1: native libraries rebuilt"
  else
    git -C "$1" checkout -q -- '*.so'
    echo "$1: native build failed (logs/make_native.log); running on the portable libraries"; return 1
  fi
}


update() {
  local src new d i h changed="" running="" failed="" stuck=""
  src=$(source_clone); [ -n "$src" ] || die "no clone on a branch to update from"
  git -C "$src" fetch -q || die "$src: fetch failed"
  new=$(git -C "$src" rev-parse '@{u}') || die "$src: its branch follows no upstream"
  # everything is checked before anything stops: a refusal leaves every copy running
  git -C "$src" merge-base --is-ancestor HEAD "$new" || die "$src has commits $new lacks; nothing updated"
  for d in $( (echo "$src"; awk '!/^#/ && NF {print $2}' "$R/instances") | sort -u); do
    h=$(git -C "$d" rev-parse HEAD 2> /dev/null) || die "$d: git cannot read it; nothing updated"
    [ -z "$(git -C "$d" status --porcelain --untracked-files=no | grep -v '\.so$')" ] \
      || die "$d has local changes; nothing updated"
    [ "$d" = "$src" ] && [ "$src" = "$CODE" ] && continue
    if [ "$h" = "$new" ]; then echo "$d: already at $(git -C "$d" log --oneline -1)"; else changed="$changed $d"; fi
  done
  for i in $ALL; do  # stopped before their code moves: a running copy loads some libraries late
    [[ " $changed " == *" $(dir "$i") "* ]] && pid "$i" >/dev/null && running="$running $i"
  done
  for i in $running; do stop "$i" || die "$i did not stop; nothing more updated"; done
  if [ "$src" = "$CODE" ] && [ "$(git -C "$src" rev-parse HEAD)" != "$new" ]; then
    git -C "$src" merge -q --ff-only "$new" && echo "$src: $(git -C "$src" log --oneline -1)" \
      || { echo "$src: update to $new failed; every code folder left as it was"; failed=1; stuck=1; }
  fi
  for d in $changed; do  # a folder that fails stays runnable: its old code, or the new one on portable libraries
    [ -n "$stuck" ] && break
    so_portable "$d"
    if [ "$d" = "$src" ]; then git -C "$d" merge -q --ff-only "$new"; else git -C "$d" checkout -q --detach "$new"; fi \
      || { echo "$d: update to $new failed; left at $(git -C "$d" log --oneline -1)"; so_native "$d"; failed=1; continue; }
    echo "$d: $(git -C "$d" log --oneline -1)"
    so_native "$d" || failed=1
  done
  if [ -f "$src/tools/sparkgap.sh" ] && ! cmp -s "$src/tools/sparkgap.sh" "$R/sparkgap.sh"; then
    # a new file renamed into place: bash keeps reading the old one it is running
    cp "$src/tools/sparkgap.sh" "$R/sparkgap.sh.new" && mv "$R/sparkgap.sh.new" "$R/sparkgap.sh" &&
      echo "$R/sparkgap.sh: updated from $src"
  fi
  for i in $running; do start "$i"; done   # always: what ran before runs again
  [ -z "$failed" ] || { echo "sparkgap.sh: update incomplete, see above; run it again once fixed"; return 1; }
}

rename() {
  local o=$1 n=$2 d nd f nf
  dir "$o" > /dev/null; d=$(dir "$o"); f=$(cfg "$o")
  [[ $n =~ ^[a-z]+([1-9])$ ]] || die "name must be letters and one digit 1 to 9, the DAX-IQ channel (e.g. lake4)"
  [ "${n: -1}" = "${o: -1}" ] || die "keep the digit ${o: -1}: it is the DAX-IQ channel"
  [ -z "$(field "$n" 1)" ] || die "$n is already in instances"
  pid "$o" > /dev/null && die "$o is running; stop it first"
  [ -r "$f" ] || die "$f is missing; nothing renamed"
  nf=$R/config/$n.local.json; [ ! -e "$nf" ] || die "$nf already exists"
  nd=$d; [ "$d" = "$R/$o" ] && nd=$R/$n   # a code folder outside the root keeps its place
  [ "$nd" = "$d" ] || [ ! -e "$nd" ] || die "$nd already exists"
  # the new config first: a failed move below leaves only it to delete
  python3 - "$f" "$nf" "$R/rec/${o}_" "$R/rec/${n}_" <<'PY' || die "$nf not written; nothing renamed"
import json, re, sys
f, nf, old, new = sys.argv[1:5]
text = open(f).read()
c = json.loads(text)
if str(c.get("record_wav", "")).startswith(old):
    c["record_wav"] = new + c["record_wav"][len(old):]
    m = re.search(r'\n([ \t]+)"', text)
    text = json.dumps(c, indent=m.group(1) if m else 2) + "\n"
with open(nf, "w") as out:
    out.write(text)
PY
  if [ "$nd" != "$d" ]; then
    if [ -f "$d/.git" ]; then
      git -C "$(clone_of "$d")" worktree move "$d" "$nd" || { rm -f "$nf"; die "git kept $d; nothing renamed"; }
    else  # the clone: its worktrees find it again with repair
      mv "$d" "$nd" || { rm -f "$nf"; die "$d not moved; nothing renamed"; }
      git -C "$nd" worktree repair 2> /dev/null || echo "sparkgap.sh: git worktree repair failed in $nd; run it by hand"
    fi
  fi
  awk -v o="$o" -v n="$n" -v d="$nd" '$1 == o {$1 = n; $2 = d; $3 = n ".local.json"} {print}' "$R/instances" \
    > "$R/instances.new" && mv "$R/instances.new" "$R/instances" || die "instances not updated: $o is now in $nd, config $nf"
  rm -f "$f"
  echo "$o: renamed $n, code in $nd, config $nf"
}

[ $# -gt 0 ] || { usage; exit 0; }
cmd=$1; shift
case $cmd in help|-h|--help) usage; exit 0 ;; esac
case $cmd in
  status|start|stop|restart|band|rec|update|add|remove|rename|init|migrate|template) ;;
  *) echo "sparkgap.sh: unknown command '$cmd'" >&2; echo >&2; usage >&2; exit 2 ;;
esac
for a in "$@"; do [[ $a == -h || $a == --help ]] && { cmd_usage "$cmd"; exit 0; }; done
case $cmd in
  start|stop) [ $# -gt 0 ] || set -- $ALL; for i in "$@"; do dir "$i" > /dev/null; $cmd "$i"; done ;;
  status) [ $# -gt 0 ] || { server_status; echo; set -- $ALL; }; sep=; for i in "$@"; do dir "$i" > /dev/null; [ -n "$sep" ] && echo; sep=1; status "$i"; done ;;
  restart) [ $# -gt 0 ] || set -- $ALL; for i in "$@"; do dir "$i" > /dev/null; stop "$i" && start "$i"; done ;;
  band)
    [ $# -eq 2 ] || { cmd_usage band; exit 2; }
    dir "$1" > /dev/null
    [[ " $(bands "$1") " == *" $2 "* ]] || die "band must be one of: $(bands "$1")"
    echo "$1: band $2"; locked set_restart "$1" bands "[\"$2\"]" ;;
  rec)
    [ $# -eq 2 ] && [[ $2 == on || $2 == off ]] || { cmd_usage rec; exit 2; }
    dir "$1" > /dev/null
    [ "$2" = off ] || [ "$(free_gb)" -ge "$REC_MIN_GB" ] || die "only $(free_gb) GB free in $R/rec (rec on needs $REC_MIN_GB)"
    echo "$1: recording $2 ($(df -h --output=avail "$R/rec" | tail -1 | tr -d ' ') free in rec/)"
    if [ "$2" = on ]; then locked set_restart "$1" record_wav "\"$R/rec/${1}_{band}_{ts}.wav\""; else locked set_restart "$1" record_wav null; fi ;;
  update) [ $# -eq 0 ] || { cmd_usage update; exit 2; }; update ;;
  add) [ $# -eq 2 ] || { cmd_usage add; exit 2; }; add "$1" "$2" ;;
  remove) [ $# -eq 1 ] || { cmd_usage remove; exit 2; }; remove "$1" ;;
  rename) [ $# -eq 2 ] || { cmd_usage rename; exit 2; }; rename "$1" "$2" ;;
  init) [ $# -eq 2 ] || [ $# -eq 3 ] || { cmd_usage init; exit 2; }; init "$@" ;;
  template) [ $# -le 1 ] || { cmd_usage template; exit 2; }; template "${1:-}" ;;
  migrate) [ $# -eq 0 ] || { cmd_usage migrate; exit 2; }; migrate ;;
esac
