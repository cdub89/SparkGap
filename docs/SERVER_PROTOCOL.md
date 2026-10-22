# SparkGap agent protocol

The agent (`sparkgap_agent.py`) runs one machine's SparkGap instances for
SparkGap Web. It listens on one port, 7320 by default, and serves three things
there:

- `/agent`: this protocol, a WebSocket.
- `/feed/<name>`: the feed of running instance `<name>`, passed through
  unchanged. The protocol is the feed's own (`GUI_FEED_PROTOCOL.md`). The agent
  opens the copy's feed on its own machine with the copy's join code, so the
  copy's feed can stay on loopback and its code never leaves the machine.
- Any other path: SparkGap Web.

Instances on `"port": "auto"` take the feed ports after the agent's, from 7321.

## Settings

`<root>/agent.json`, written on first run with a new join code and readable by
its owner only. The root is `--root`, else `$SPARKGAP_ROOT`, else `~/sparkgap`:
the same layout `tools/sparkgap.sh` uses (`instances`, `config/`, `logs/`,
`rec/`).

```json
{"bind": "127.0.0.1", "port": 7320, "join_code": "XXXXXX"}
```

`tls_cert` and `tls_key` add TLS. Bound to anything but loopback without TLS,
the agent warns: reach it only over a network you trust.

## Connecting

Offer the subprotocol `sparkgap.agent.v1`, and the join code as
`sparkgap.code.<code>` (a browser) or `Authorization: Bearer <code>`. The same
code opens `/feed/<name>`. A wrong code closes with 4401; five wrong codes from
one address lock it out for 10 minutes (close 4429).

## Messages from the agent

`hello`, once:

```json
{"type": "hello", "v": 1, "agent": true, "host": "rednuc", "build": "3f41c0b"}
```

`seat`, on connect and every 2 seconds:

```json
{"type": "seat", "utc": 1792690000.0,
 "instances": [{"name": "dallas1", "state": "running", "busy": null,
                "pid": 2014134, "band": "17m", "radio": "192.168.1.134",
                "feed_port": 7321, "feed_web": true, "telnet_port": 7300,
                "settings": {"band": "17m", "bands_known": ["160m", "80m"],
                             "validation": "normal", "call_source": "rbn+skcc",
                             "spot_rule_split_runs": false,
                             "spot_rule_contest_words": false,
                             "spot_rule_cq_repeats": false, "record": false},
                "spots": 12, "top_calls": [["K8TLJ", 3]], "errors": 0,
                "last_error": null, "warnings": 2, "reconnects": 0,
                "log": "dallas1_17m_20261022_152327Z.log",
                "started": 1792690000.0}],
 "stations": [{"radio": "192.168.1.134", "state": "running",
               "station": "SparkGap-Dallas", "live": "live", "rtt_ms": 2.8,
               "losses_hour": 0, "last_loss": null}]}
```

- `state`: `running` or `stopped`. `busy`: the operation in progress
  (`starting`, `stopping`, `restarting`), else null.
- `feed_port`: the port this run of the copy got, null while stopped.
- `last_error`: the last error in a stopped copy's log.
- `stations`: the Flex station of each radio the instances use.

`log`, on connect the agent's last 200 log lines (`"reset": true`), then each
new line as it is logged: connections, requests and their results.

```json
{"type": "log", "reset": true,
 "lines": [{"utc": 1792690000.123, "level": "INFO",
            "msg": "Agent: 100.64.0.2 asks restart dallas1"}]}
```

`agent_result`, for each request:

```json
{"type": "agent_result", "id": 7, "ok": true, "message": "dallas1: live (pid 2014134)"}
{"type": "agent_result", "id": 8, "ok": false,
 "error": "callsign cannot be changed here"}
```

## Messages to the agent

```json
{"type": "agent", "id": 7, "cmd": "start", "name": "dallas1"}
{"type": "agent", "id": 8, "cmd": "set", "name": "dallas1",
 "settings": {"validation": "normal", "call_source": "rbn+skcc"}}
```

- `cmd`: `start`, `stop`, `restart` or `set`. A Flex instance's radio station
  is started before its first copy and stopped after its last.
- `set` writes the settings to the instance's config and restarts it if it is
  running. Only these, each checked: `band` (a band the instance's SparkGap
  knows), `validation` (`off`, `permissive`, `normal`, `aggressive`),
  `call_source` (`scp`, `rbn`, `rbn+skcc`), `spot_rule_split_runs`,
  `spot_rule_contest_words`, `spot_rule_cq_repeats` and `record` (true or
  false; recording on needs 5 GB free in `rec/`). Anything else is refused and
  nothing is written.
- One request at a time per instance: another while one runs is refused as
  busy. Requests run in order, at most 8 waiting.

The agent and `tools/sparkgap.sh` take the same locks (the radio's, then the
instance's) and write the same pid files and logs, so both can be used on one
machine.
