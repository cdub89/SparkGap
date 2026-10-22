# SparkGap Web (experimental)

SparkGap Web is a running SparkGap seen in a browser, with nothing to install:
a live waterfall of the receiver's span, the calls on a frequency ladder, and
the decoded text of the signal you pick. Through each SparkGap server it also
starts, stops and restarts the SparkGap copies there and changes their
settings. SparkGap stays an independent service next to the radio; browsers
come and go without disturbing decoding, and one page can watch several
servers.

![SparkGap Web, 40m from a FLEX-6600](web.png)

## Getting connected

SparkGap Web connects to SparkGap servers. A SparkGap server
(`sparkgap_server.py`) runs one machine's SparkGap copies and passes their
feeds through. Protocol and settings:
[SERVER_PROTOCOL.md](SERVER_PROTOCOL.md).

1. On the machine running SparkGap, start the server:
   `python3 sparkgap_server.py`. At its first start it writes `server.json` in
   the run folder (`~/sparkgap` unless `--root` or `$SPARKGAP_ROOT` says
   otherwise) with a new join code, and prints the code. Set `bind` there to
   the machine's VPN or local IP address to reach it from another machine.
2. Open `http://<server-ip>:7320/` in a browser.
3. Open ⚙, choose **+ Add server**, and enter the address and the join code.
   Every copy the server runs appears in the band picker.

With no server saved, the page opens on **+ Add server**.

Without a server, a copy whose `gui_feed` has `"web": true` serves the page
itself, and that page shows only that copy (⚙ asks for its join code). Live
replays (`--file`) work this way.

## The main screen

**Header**, left to right:

- **Search** (Ctrl+F): a call, decoded text or a frequency in kHz. A frequency
  is taken as typed; a match on another band switches to it.
- **Band picker**: shown when there is a choice, from one SparkGap running
  several bands or several copies. Each entry is the copy's name and band.
- **State**: Live, Connecting, or why the feed is down.
- **⚙**: Settings and Info.

**Waterfall and call ladder.** The waterfall is the receiver's whole span. The
ladder above it marks each signal: spotted calls, calls heard but not spotted,
and signals with no call yet, by frequency. Click a trace or a ladder entry to
pick that signal.

- Drag or two-finger drag pans; Ctrl+scroll or a pinch zooms.
- The scroll bar beside the waterfall moves through the span.

**Decoded windows**, on the right: the picked signal's frequency, speed and SNR,
whether it was SPOTTED or only HEARD, and its decoded windows, newest first.
Text can be selected, and **Copy** copies all of the signal's decoded windows.

**Zoom panel**, under the decoded text: **Zoom** sets the width shown (the
button steps through Full, Half and Quarter of the span), **Span** is the range
on screen, and **SNR** is the waterfall's colour scale.

**Bottom bar**: SparkGap's load (percent of one core, a 60 s average), memory,
open decoder bins, connected browsers, decoder, server build and uptime.

## Settings and Info

⚙ opens one dialog. **Settings | Info** at the top picks what it shows; under
it, a **Servers** tab and one tab for each copy they run (with two servers,
each copy's tab names its server). It opens on the copy on screen, in the
mode used last, and updates every 2 seconds.

### Settings: Servers

- A card for each server: its address, its copies, **Edit** and **✕**, and the
  radio station's state (ping, losses in the last hour).
- **+ Add server**: an address and its join code.
- **This browser**: the theme (dark or light), and which readings of each
  decoded window are shown (Both, Wide, Narrow, or the one with the Lowest
  cost, the default). The browser keeps them, and the server list, for each
  address the page is opened from.

### Settings: a copy

- **Start**, **Restart** and **Stop** act at once; Restart and Stop ask first.
  **Show** puts that copy on the main screen.
- **Settings**, saved in the copy's config: band, recording, call validation
  and known call list, and under **Advanced: spot rule** three options of the
  spot rule. A change is held until **Apply**, which restarts the copy once for
  all of them (a stopped copy is only saved), or **Discard**. Hover a label for
  a line on what it does.
  - **Call validation**: a confidence score decides which calls are spotted.
    Permissive spots more, aggressive fewer; normal is the default. A config
    can also set it off, so calls are spotted on the repeat rule's count alone.
  - **Known call list**: calls on the list score higher in validation. SCP
    (MASTER.SCP), RBN (calls RBN skimmers heard in the last days), or RBN +
    SKCC (those and the SKCC roster).
  - **Split runs**: words sent with no gap between them are split before the
    rule reads them, so CQCQDE reads as CQ CQ DE. Hand-sent CW drops gaps.
  - **Contest words**: CWT, SST or MST right before a call opens a CQ group as
    CQ does, for runners who send CWT K5AX without the CQ.
  - **CQ repeats**: back-to-back CQs from one call count as separate copies, so
    a runner is spotted sooner.

The three advanced options are off by default: each spots more calls, at some
risk of a wrong one.

### Info: Servers

Each server's host and build, its radio station, a line for each copy (band,
state, uptime, spots), and the **server log**: the server's last 200 lines, then
each new one, showing browsers connecting and every start, stop and settings
change with its result. At the bottom the log follows new lines; scrolled up,
it stays.

### Info: a copy

- **Health line**, on top: green when the radio is live, decoding keeps up and
  there are no errors; otherwise the problems, worst first.
- **Tiles**: uptime, spots (and in the last hour), calls, decoded windows, bins
  (and the peak), CPU and memory.
- **RADIO**: type, address and state, plus what the receiver reports, such as
  packets and packets lost.
- **SPARKGAP**: host, build, clock (NTP synchronised or not), decoder, bands,
  config and log files, recording, and IQ trimmed (above zero means the machine
  is short of CPU).
- **SPOT OUTPUT**: the telnet port, how many clients are on it, and the copy's
  top calls.
- **CONSOLES**: this browser, and every browser connected to this copy by host
  name (from reverse DNS) with its address and connect time.
- **PROBLEMS**: errors since start, with the last five; warnings are in the
  log only.

A stopped copy shows its state, build, last log and last error.

Selecting text holds the dialog still until the selection is cleared, so it can
be copied.

## Tips

- For access from outside the LAN, use a VPN that encrypts the link rather than
  opening the port to the internet. Without `tls_cert`, the server and its join
  code cross the network unencrypted.
- A browser that falls behind is listed under CONSOLES and on the health line.

## The server: server.json

`server.json`, in the run folder:

```json
{"bind": "127.0.0.1", "port": 7320, "join_code": "XXXXXX"}
```

- `bind`: the address to listen on; `127.0.0.1` serves only the same machine.
- `port`: 7320, kept for the server; the copies' feeds take 7321 to 7339.
- `join_code`: written at first start. Five wrong codes from one address lock
  that address out for 10 minutes.
- `tls_cert` and `tls_key` (optional) serve `https://` and `wss://`.

The server passes each copy's feed through, so the copies' feeds can stay on
`127.0.0.1` with codes that never leave the machine. `sparkgap.sh status` shows
the server, its address, join code and web page.

## Server: a copy's feed

A copy's feed is off unless its config has a `gui_feed` block:

```json
"gui_feed": {"bind": "127.0.0.1", "port": "auto", "join_code": null, "web": false}
```

- `bind`: the address to listen on. Behind an server, `127.0.0.1`.
- `port`: `"auto"` by default, the first free port from 7321 to 7339. 7320 is
  the machine's server's.
- `join_code`: required whenever `bind` is not a loopback address. Six
  characters, minted without any pair easily confused by eye or over the air
  (O/0, I/1 and similar); case, spaces and dashes are ignored on entry. Make one
  with `python3 feed_server.py --new-code`. SparkGap logs the code at startup:

  ```text
  GUI feed on ws://<host-ip>:<port>/feed, join code XXX-XXX
  ```

- Five wrong codes from one address lock that address out for 10 minutes. A
  missing code is refused but not counted.
- At most 16 browsers at once; one that stops reading is disconnected.
- `web`: `true` also serves SparkGap Web on the same port, showing that copy
  alone.
- `spots`: which spots the page shows. `"rbn"` (default) is what `telnet_port`
  carries, the stream the RBN feeder reads; `"local"` is what
  `telnet_local_port` carries, with its own `local_spot_rule`.
- `tls_cert` and `tls_key` (optional) serve `wss://`; without them the feed is
  not encrypted.

What the feed sends is described in [GUI_FEED_PROTOCOL.md](GUI_FEED_PROTOCOL.md).

The feed works in live and file mode and needs no extra Python packages on the
server. With one or two browsers connected it costs about 5% of a core (96 kHz
on a Ryzen 5 5500U), and nothing while no browser is connected.
