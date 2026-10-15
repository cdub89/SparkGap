# SparkGap Console (experimental)

A desktop client, and SparkGap Web, its browser twin, that show what a running
SparkGap hears: a live waterfall of the receiver's span, the calls on a
frequency ladder, and the decoded text of the signal you pick. SparkGap stays an
independent service next to the radio; the console connects to it over the
network and can come and go without disturbing decoding. One console can watch
several SparkGaps.

![SparkGap Console on Linux, 20m from a FLEX-6400M](console.png)

Status: experimental. The consoles only watch: each SparkGap keeps its own
config, startup and shutdown, and Settings shows its config read-only.

## Server: the feed

The feed is off unless the config has a `gui_feed` block:

```json
"gui_feed": {"bind": "127.0.0.1", "port": 7320, "join_code": null, "web": false}
```

- `bind`: the address to listen on. `127.0.0.1` serves only the same machine. To
  watch from other machines, bind the Tailscale address of the SparkGap host;
  the feed is then reachable from your tailnet and nothing else.
- `port`: 7320 by default.
- `join_code`: required whenever `bind` is not a loopback address. Six
  characters, minted without any pair easily confused by eye or over the air
  (O/0, I/1 and similar); case, spaces and dashes are ignored on entry. Make one
  with `python3 feed_server.py --new-code`. SparkGap logs the code at startup:

  ```text
  GUI feed on ws://100.64.0.1:7320/feed, join code XXX-XXX
  ```

- Five wrong codes from one address lock that address out for 10 minutes. A
  missing code is refused but not counted.
- At most 16 consoles at once; one that stops reading is disconnected.
- `web`: `true` also serves SparkGap Web on the same port (see below).
- `tls_cert` and `tls_key` (optional) serve `wss://`. Tailscale already encrypts
  the link. Settings adds servers as `ws://`, so give a `wss://` server on the
  command line.

What the feed sends is described in [GUI_FEED_PROTOCOL.md](GUI_FEED_PROTOCOL.md).

The feed works in live and file mode and needs no extra Python packages on the
server. Its spectrum costs under 1% of a core, and nothing while no client is
connected.

## Console: running from source

Python 3.10 or later, from the `console` folder:

```sh
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python sparkgap_console.py
```

On Windows, `run_console.bat` does the same on first run.

Add servers in Settings, under CONNECTIONS: address, port and join code. The
list is kept between runs. Servers given on the command line are added to it:

```sh
.venv/bin/python sparkgap_console.py ws://100.64.0.1:7320/feed#XXX-XXX
```

The console draws with Qt's software renderer, which costs far less CPU over
remote desktop. `--gpu` uses the graphics card instead.

## Console: Windows build

On Windows, from the `console` folder, with the venv above:

```bat
.venv\Scripts\pip install pyinstaller
.venv\Scripts\pyinstaller --noconfirm SparkGapConsole.spec
```

The result is `dist\SparkGapConsole`, a folder of about 125 MB with
`SparkGapConsole.exe`. It runs on any Windows PC without Python; copy the whole
folder. Settings shows the build's commit when a `build.txt` holding it sits
next to the spec at build time.

## SparkGap Web

The same console in a browser, with nothing to install. Set `"web": true` in
the `gui_feed` block and open the address SparkGap logs at startup:

```text
GUI feed: browser console at http://100.64.0.1:7320/
```

Settings works as in the desktop console: add the server with its join code
(the address and port are filled in from the page). The list, theme and demo
setting are kept in that browser, separately for each address the page is
opened from.

Demo mode shows made-up signals, so the page can be tried without a server; it
is on until a server has been added. Turning it on only switches the view: the
servers stay connected and keep their waterfall, spots and text, and the server
list is hidden until demo is off again.

The page also opens from a file (`web/index.html`), for demo mode or for
servers that do not serve it. A server without a join code accepts a page only
when the page itself was opened from a loopback address on the feed's port.

## Using it

- Drag or two-finger drag pans the waterfall; Ctrl+scroll or a pinch zooms; the
  scroll bar beside it moves through the receiver's span.
- The panel under the decoded text: Zoom sets the width shown (Full shows the
  whole receiver), Span is the range on screen, and SNR is the waterfall's
  colour scale.
- Click a trace or a call to see its decoded windows. Ctrl+F searches calls,
  decoded text and frequencies.
- Window size, position and theme are kept between runs.

## Keeping the two clients in step

The desktop console (`console/`) and SparkGap Web (`web/`) are two builds of one
design. A change to either lands in the other in the same commit, or this list
says why not.

Shared, and kept the same:

- Feed handling: the wide reading of each window on top unless it has no fit;
  a wrong join code is not retried; a lockout is retried after 10 minutes;
  reconnects back off over 1, 2, 5, 10 and 20 seconds; a change in the
  spectrum's bins, start, bin width or dB step clears the waterfall.
- The constants at the top of `console/backend.py` and `web/app.js`: same names,
  same values.
- Layout, theme tokens (`t` in `Main.qml`, `:root` in `style.css`), the
  waterfall palette (dark in both themes), header glyphs and fonts.
- Call ladder, bubbles, decoded windows, search, status bar, and the zoom
  panel (Zoom, Span and SNR rows).
- Settings: demo, connections, the read-only spot rule, validation and call
  source (shown when the server reports them), the build, at 480 px.
- Demo mode: the same made-up signals; turning it on only switches the view.
- Scroll bars: thin, with small arrows, the same on the waterfall and the
  decoded text.
- Server names: host name and band; the address appears only in Settings.

Different on purpose:

- Desktop only: window size and position kept between runs.
- Web only: the layout for narrow (phone) screens and touch pan and pinch; the
  page fills in its own server's address when one is added.
- Refusals: the desktop console reads the HTTP status, the browser the close
  code. Both show the same message.
