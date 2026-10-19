import QtCore
import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts
import QtQuick.Window
import SparkGap

ApplicationWindow {
    id: win
    width: 1280; height: 800
    title: "SparkGap Console"

    // window size and position kept across runs; a position off every screen (monitor gone) opens centred
    Settings {
        id: geom
        category: "window"
        property int x: -1
        property int y: -1
        property int width: 1280
        property int height: 800
        property bool maximized: false
    }
    Settings { category: "ui"; property alias theme: win.themeOverride; property alias decoded: win.decoded }
    Component.onCompleted: {
        width = geom.width; height = geom.height
        const onScreen = Qt.application.screens.some(s => geom.x >= s.virtualX && geom.y >= s.virtualY
                                                         && geom.x < s.virtualX + s.width && geom.y < s.virtualY + s.height)
        if (onScreen) { x = geom.x; y = geom.y }
        visibility = geom.maximized ? Window.Maximized : Window.Windowed
        applyColors()
        if (console_.instances.length === 0) { settings.open(); conns.edit(-1) }
    }
    onClosing: {
        geom.maximized = visibility === Window.Maximized
        if (visibility === Window.Windowed) { geom.x = x; geom.y = y; geom.width = width; geom.height = height }
    }
    color: t.bg

    property var inst: console_.current
    readonly property string helpUrl: "https://github.com/hotairfred/SparkGap/blob/main/docs/CONSOLE.md"  // the guide, from Settings
    property bool codeShown: false  // Status shows this console's join code in clear
    TextEdit { id: codeClip; visible: false }  // clipboard for the join code
    property string themeOverride: "dark"  // dark by default; the toggle switches it
    // the zoom step the view is at (0 Full, 1 Half, 2 Quarter), or -1 between steps (a view within 5% of a step is at it)
    function zoomStep() {
        if (!inst) return 0
        const full = inst.highKhz - inst.lowKhz, span = wf.highKhz - wf.lowKhz
        return full > 0 ? [1, 2, 4].findIndex(d => Math.abs(span - full / d) <= full / d * 0.05) : 0
    }
    property string decoded: "lowest"  // readings of each window shown: both, wide, narrow, lowest (cost)
    property alias settingsOpen: settings.visible
    readonly property bool dark: themeOverride ? themeOverride === "dark"
                                               : Application.styleHints.colorScheme === Qt.ColorScheme.Dark
    property var selectedKhz: -1

    // the readings of a window to show: both, one filter width, or the lower timing cost (a tie or no fit keeps wide)
    function readings(w, mode) {
        const all = [{ html: w.html, rate: w.rate, cost: w.cost }]
        if (w.alt_html) all.push({ html: w.alt_html, rate: w.alt_rate, cost: w.alt_cost })
        if (mode === "both" || all.length === 1) return all
        const wide = all.find(x => x.rate === 200) || all[0]
        if (mode === "wide") return [wide]
        if (mode === "narrow") return [all.find(x => x.rate === 100) || all[0]]
        const k = c => (c === null || c === undefined || c >= 999 ? Infinity : c)  // 999: itila2's "no fit"
        return [k(all[0].cost) === k(all[1].cost) ? wide : k(all[0].cost) < k(all[1].cost) ? all[0] : all[1]]
    }
    // the decoded windows as one rich-text document: time, readings (the second dimmed), costs
    function windowsHtml(ws, mode) {
        const c = v => v === null || v === undefined ? "-" : v >= 999 ? "no fit" : v.toFixed(2)
        const n = r => r === 100 ? "narrow" : r === 200 ? "wide" : "cost"
        return "<table cellspacing='0' cellpadding='0' width='100%'>" + ws.map(w => {
            const rs = readings(w, mode)
            const costs = rs.length > 1 || w.alt_html ? rs.map(x => n(x.rate) + " " + c(x.cost)).join("  ·  ") : "cost " + c(w.cost)
            return "<tr><td valign='top' style='padding-right:10px; padding-bottom:8px; color:" + t.faint + "'>" + w.utc + "</td>"
                + "<td valign='top' style='padding-bottom:8px'>"
                + rs.map((x, i) => i ? "<br><span style='color:" + Qt.alpha(t.muted, 0.75) + "'>" + x.html + "</span>" : x.html).join("")
                + "<br><span style='font-size:10px; color:" + t.faint + "'>" + costs + "</span></td></tr>"
        }).join("") + "</table>"
    }
    property int ladderTick: 0

    // ---- theme tokens ----
    QtObject {
        id: t
        readonly property color bg:      win.dark ? "#0b0f13" : "#d9dee2"
        readonly property color surface: win.dark ? "#11171d" : "#e9ecef"
        readonly property color sunken:  win.dark ? "#0d1318" : "#dde2e6"
        readonly property color line:    win.dark ? "#1f2a33" : "#c4ccd2"
        readonly property color fg:      win.dark ? "#dbe4ea" : "#16202a"
        readonly property color muted:   win.dark ? "#8796a2" : "#56636e"
        readonly property color faint:   win.dark ? "#5c6a75" : "#76838e"
        readonly property color accent:  win.dark ? "#f3a640" : "#b55f00"
        readonly property color good:    win.dark ? "#4fc28a" : "#1f7a4d"
        readonly property color bad:     win.dark ? "#ff6b5a" : "#c0392b"
        // the waterfall keeps the dark palette in both themes
        readonly property var wf: ["#070b0f", "#10263a", "#1f5fa8", "#19b3c4", "#4cc76a", "#f2c94c", "#ff6b3d"]
        readonly property color wfMark: "#f3a640"  // bubbles and the selection line on the waterfall
        readonly property string sans: "IBM Plex Sans"
        readonly property string mono: "IBM Plex Mono"
        readonly property string display: "Sora"
        readonly property var zero: ({ "zero": 1 })  // slashed zero, so 0 never reads as O in a call
        onAccentChanged: win.applyColors()
    }
    function applyColors() {
        wf.setColors(t.wf, t.wfMark)
        if (inst) inst.setAccent(t.accent)
    }
    onInstChanged: { applyColors(); selectedKhz = -1; wf.setSelected(-1) }

    font.family: t.sans
    font.pixelSize: 13

    component Seg: Rectangle {
        id: seg
        property var labels: []
        property int current: 0
        property bool readOnly: false
        signal picked(int index)
        implicitWidth: row.implicitWidth + 4; implicitHeight: 30
        radius: 8; color: t.sunken; border.color: t.line
        Row {
            id: row; anchors.centerIn: parent; spacing: 0
            Repeater {
                model: seg.labels
                delegate: Rectangle {
                    required property int index
                    required property var modelData
                    width: lbl.implicitWidth + 24; height: 26; radius: 6
                    color: index === seg.current ? t.surface : "transparent"
                    Text {
                        id: lbl; anchors.centerIn: parent; text: modelData
                        color: index === seg.current ? t.fg : t.muted; font.family: t.sans; font.pixelSize: 13
                    }
                    MouseArea {
                        anchors.fill: parent; enabled: !seg.readOnly; cursorShape: Qt.PointingHandCursor
                        onClicked: seg.picked(index)
                    }
                }
            }
        }
    }

    // thin scroll bar with small arrows, as the browser draws it (scrollbar-width: thin in web/style.css)
    component ThinBar: ScrollBar {
        id: tb
        width: 11; padding: 2; minimumSize: 0.04
        orientation: Qt.Vertical; policy: ScrollBar.AlwaysOn
        background: Rectangle { color: t.sunken }
        contentItem: Rectangle { implicitWidth: 7; radius: 3.5; color: tb.pressed ? t.fg : tb.hovered ? t.muted : t.faint }
    }
    component BarArrow: Rectangle {
        id: ba
        property int dir: 1  // +1 up, -1 down
        signal stepped()
        width: 11; height: 12; color: t.sunken
        Text {
            anchors.centerIn: parent; text: ba.dir > 0 ? "▲" : "▼"; font.pixelSize: 7
            color: tap.pressed ? t.fg : hov.hovered ? t.muted : t.faint
        }
        HoverHandler { id: hov }
        TapHandler { id: tap; onPressedChanged: if (pressed) ba.stepped() }
        Timer {  // repeats while held
            interval: 350; repeat: true; running: tap.pressed
            onTriggered: { interval = 80; ba.stepped() }
            onRunningChanged: if (!running) interval = 350
        }
    }

    component Pill: Rectangle {
        property alias text: pl.text
        property color tone: t.muted
        implicitWidth: pl.implicitWidth + 16; implicitHeight: 20; radius: 10
        color: "transparent"; border.color: tone
        Text { id: pl; anchors.centerIn: parent; color: parent.tone; font.pixelSize: 10; font.weight: Font.DemiBold; font.letterSpacing: 1 }
    }

    ColumnLayout {
        anchors.fill: parent
        anchors.leftMargin: 16; anchors.rightMargin: 16; anchors.topMargin: 12; anchors.bottomMargin: 8
        spacing: 12

        // ---- header ----
        RowLayout {
            spacing: 16
            Row {
                spacing: 8
                Rectangle { width: 9; height: 9; radius: 5; color: t.accent; anchors.verticalCenter: parent.verticalCenter }
                Text { text: "SparkGap"; color: t.fg; font.family: t.display; font.pixelSize: 17; font.weight: Font.DemiBold }
            }
            Item {
                id: utcClock  // the time for the decoded-text panel's clock; draws nothing here
                property string now: ""
                Timer {
                    interval: 250; running: true; repeat: true; triggeredOnStart: true
                    onTriggered: utcClock.now = new Date().toISOString().substr(11, 8)
                }
            }
            // server and band picker: every band of every connected server ("Beelink 40m"), when there is a choice
            ComboBox {
                id: bandSeg
                readonly property var choices: {
                    const out = []
                    const all = console_.instances
                    for (let n = 0; n < all.length; n++) {
                        const i = all[n]
                        if (i.demo) continue
                        const host = i.name || i.title
                        const names = i.bandNames.length ? i.bandNames : [""]
                        for (let k = 0; k < names.length; k++)
                            out.push({ n: n, band: k, label: names[k] ? host + " " + names[k] : host })
                    }
                    return out
                }
                visible: !!inst && !inst.demo && choices.length > 1
                ToolTip.visible: hovered && !popup.visible; ToolTip.delay: 600; ToolTip.text: "Server and band"
                implicitWidth: 190; implicitHeight: 30
                model: choices.map(c => c.label)
                currentIndex: choices.findIndex(c => c.n === console_.currentIndex && c.band === (inst ? inst.band : 0))
                onActivated: (k) => {
                    const c = choices[k]
                    if (c.n !== console_.currentIndex) console_.setCurrent(c.n)
                    if (c.band !== inst.band) win.setBand(c.band)
                }
                background: Rectangle { radius: 8; color: t.sunken; border.color: bandSeg.activeFocus ? t.accent : t.line }
                contentItem: Text {
                    leftPadding: 10; rightPadding: 24; verticalAlignment: Text.AlignVCenter; elide: Text.ElideRight
                    text: bandSeg.displayText; color: t.fg; font.family: t.sans; font.pixelSize: 13
                }
                indicator: Text { x: bandSeg.width - width - 8; anchors.verticalCenter: parent.verticalCenter; text: "▾"; color: t.muted; font.pixelSize: 12 }
                delegate: ItemDelegate {
                    required property int index
                    required property var modelData
                    width: bandSeg.width; height: 30
                    contentItem: Text { text: modelData; color: t.fg; font.family: t.sans; font.pixelSize: 13; verticalAlignment: Text.AlignVCenter }
                    background: Rectangle { color: highlighted ? t.sunken : t.surface }
                    highlighted: bandSeg.highlightedIndex === index
                }
                popup.background: Rectangle { radius: 8; color: t.surface; border.color: t.line }
            }
            // search: a call (or part of one) or a frequency; Enter takes the top match
            TextField {
                id: search
                Layout.preferredWidth: 110; implicitHeight: 26
                placeholderText: "Call or kHz"
                placeholderTextColor: t.faint; color: t.fg
                font.family: t.mono; font.features: t.zero; font.pixelSize: 12
                leftPadding: 8; topPadding: 0; bottomPadding: 0; verticalAlignment: TextInput.AlignVCenter
                background: Rectangle { radius: 8; color: t.sunken; border.color: search.activeFocus ? t.accent : t.line }
                property var matches: []
                onTextChanged: matches = inst ? inst.search(text) : []
                onAccepted: if (matches.length) win.goTo(matches[0])
                Keys.onEscapePressed: { text = ""; focus = false }
            }
            Item { Layout.fillWidth: true }
            // station line: set once per station, informative only
            Text {
                readonly property var c: inst && inst.info.config ? inst.info.config : ({})
                visible: !!c.callsign && !inst.demo  // demo: the status says DEMO MODE
                text: [c.callsign, c.enable_ft8 ? "FT8 on" : ""].filter(x => x).join("  ·  ")
                color: t.muted; font.family: t.mono; font.features: t.zero; font.pixelSize: 12
            }
            Row {
                spacing: 8
                Rectangle {
                    width: 8; height: 8; radius: 4; anchors.verticalCenter: parent.verticalCenter
                    color: !inst ? t.faint : inst.state === "live" ? t.good : inst.state === "connecting" ? t.accent : t.bad
                }
                Text {
                    color: t.muted; font.family: t.mono; font.features: t.zero; font.pixelSize: 12
                    text: !inst ? "No feed" : inst.demo ? "DEMO MODE" : (inst.state === "live" ? "Live" : inst.state === "connecting" ? "Connecting"
                                            : inst.state === "refused" ? "Refused" : "Reconnecting")
                          + (inst && !inst.demo && inst.station && !bandSeg.visible ? "  " + inst.station : "")  // the picker names it
                          + (inst && inst.state !== "live" && inst.error ? "  ·  " + inst.error : "")
                }
            }
            Row {  // the server is writing IQ: REC beside the state
                spacing: 6
                visible: !!inst && !inst.demo && !!inst.status && !!inst.status.recording
                HoverHandler { id: recHover }
                ToolTip.visible: recHover.hovered; ToolTip.delay: 600
                ToolTip.text: visible ? "Recording IQ to " + inst.status.recording.file : ""
                Rectangle {
                    width: 8; height: 8; radius: 4; anchors.verticalCenter: parent.verticalCenter; color: t.bad
                    SequentialAnimation on opacity {
                        running: parent.visible; loops: Animation.Infinite
                        PropertyAction { value: 1.0 } PauseAnimation { duration: 500 }
                        PropertyAction { value: 0.15 } PauseAnimation { duration: 500 }
                    }
                }
                Text { text: "REC"; color: t.bad; font.family: t.mono; font.features: t.zero; font.pixelSize: 12; font.weight: Font.DemiBold }
            }
            Rectangle {
                id: gear
                width: 32; height: 32; radius: 8; color: settings.visible ? t.sunken : t.surface; border.color: t.line
                Text { anchors.centerIn: parent; text: "⚙"; color: t.fg; font.pixelSize: 23 }
                MouseArea {
                    anchors.fill: parent; cursorShape: Qt.PointingHandCursor; hoverEnabled: true
                    ToolTip.visible: containsMouse; ToolTip.delay: 600; ToolTip.text: "Settings"
                    onClicked: settings.visible ? settings.close() : settings.open()
                }
            }
            Rectangle {
                width: 32; height: 32; radius: 8; color: t.surface; border.color: t.line
                Rectangle {  // half-filled circle: none of the bundled fonts has ◐
                    anchors.centerIn: parent; width: 17; height: 17; radius: 8.5; color: "transparent"; border.color: t.fg; border.width: 2
                    Item {
                        x: 8.5; width: 8.5; height: 17; clip: true
                        Rectangle { x: -8.5; width: 17; height: 17; radius: 8.5; color: t.fg }
                    }
                }
                MouseArea {
                    anchors.fill: parent; cursorShape: Qt.PointingHandCursor; hoverEnabled: true
                    ToolTip.visible: containsMouse; ToolTip.delay: 600; ToolTip.text: "Light or dark"
                    onClicked: win.themeOverride = win.dark ? "light" : "dark"
                }
            }
            Rectangle {
                width: 32; height: 32; radius: 8; color: statusPop.visible ? t.sunken : t.surface; border.color: t.line
                Text { anchors.centerIn: parent; text: "ⓘ"; color: t.fg; font.pixelSize: 19 }
                MouseArea {
                    anchors.fill: parent; cursorShape: Qt.PointingHandCursor; hoverEnabled: true
                    ToolTip.visible: containsMouse; ToolTip.delay: 600; ToolTip.text: "Status"
                    onClicked: statusPop.visible ? statusPop.close() : statusPop.open()
                }
            }
        }

        // ---- body ----
        StackLayout {
            Layout.fillWidth: true; Layout.fillHeight: true
            currentIndex: 0

            RowLayout {
                spacing: 12
                Rectangle {
                    Layout.fillWidth: true; Layout.fillHeight: true
                    radius: 10; color: t.surface; border.color: t.line; clip: true

                    Item {
                        id: ladder
                        anchors.left: parent.left; anchors.top: parent.top; anchors.bottom: parent.bottom
                        anchors.margins: 1
                        width: 176
                        Rectangle { anchors.right: parent.right; width: 1; height: parent.height; color: t.line }

                        function yOf(khz) { return (wf.highKhz - khz) / Math.max(0.001, wf.highKhz - wf.lowKhz) * height }

                        // frequency ticks, one per kHz (every 5 on spans over 200 kHz), labels every 5 or 10
                        Repeater {
                            id: ticks
                            readonly property int step: wf.highKhz - wf.lowKhz > 200 ? 5 : 1
                            readonly property int first: Math.ceil(wf.lowKhz / step) * step
                            model: wf.highKhz > wf.lowKhz ? Math.floor((wf.highKhz - first) / step) + 1 : 0
                            delegate: Item {
                                required property int index
                                readonly property int khz: ticks.first + index * ticks.step
                                readonly property bool major: (wf.highKhz - wf.lowKhz) > 40 ? khz % 10 === 0 : khz % 5 === 0
                                y: ladder.yOf(khz); width: ladder.width
                                Rectangle { width: major ? 10 : 6; height: 1; color: major ? t.muted : t.line }
                                Text {
                                    visible: major; x: 12; y: -height / 2; text: khz
                                    color: t.muted; font.family: t.mono; font.features: t.zero; font.pixelSize: 11
                                }
                            }
                        }

                        // call chips, pushed apart so they never overlap
                        Repeater {
                            id: chips
                            model: {
                                win.ladderTick
                                if (!inst) return []
                                const items = inst.ladder(wf.lowKhz, wf.highKhz)
                                // top-down (highest frequency first): push each chip below the one above
                                let last = -99
                                for (const it of items) {
                                    it.y = Math.max(ladder.yOf(it.khz), last + 24)
                                    last = it.y
                                }
                                return items.filter(it => it.y < ladder.height - 8)
                            }
                            delegate: Rectangle {
                                required property var modelData
                                readonly property bool sel: Math.abs(win.selectedKhz - modelData.khz) < 0.05
                                x: 52; y: modelData.y - 11; width: ladder.width - 60; height: 22; radius: 6
                                color: sel ? Qt.alpha(t.accent, 0.14) : hov.hovered ? t.sunken : "transparent"
                                border.color: sel ? t.accent : "transparent"
                                HoverHandler { id: hov; cursorShape: Qt.PointingHandCursor }
                                Rectangle {
                                    x: 6; anchors.verticalCenter: parent.verticalCenter; width: 6; height: 6; radius: 3
                                    color: modelData.spot ? t.accent : "transparent"
                                    border.color: modelData.spot ? t.accent : t.faint
                                }
                                Text {
                                    x: 18; anchors.verticalCenter: parent.verticalCenter
                                    text: modelData.call || modelData.khz.toFixed(1)
                                    color: modelData.spot ? t.fg : t.faint
                                    font.family: t.mono; font.features: t.zero; font.pixelSize: modelData.call ? 13 : 11
                                    font.weight: modelData.call ? Font.DemiBold : Font.Normal
                                }
                                Text {
                                    anchors.right: parent.right; anchors.rightMargin: 6; anchors.verticalCenter: parent.verticalCenter
                                    text: modelData.wpm ? Math.round(modelData.wpm) : ""
                                    color: t.faint; font.family: t.mono; font.features: t.zero; font.pixelSize: 11
                                }
                                TapHandler { onTapped: win.pick(modelData.khz, modelData.call) }
                            }
                        }
                        Timer { interval: 1000; running: true; repeat: true; onTriggered: win.ladderTick++ }
                        Connections { target: inst; function onLadderChanged() { win.ladderTick++ } }
                    }

                    // whole receiver span: the handle is the part on screen
                    ThinBar {
                        id: fscroll
                        readonly property real full: inst ? inst.highKhz - inst.lowKhz : 0
                        readonly property real span: wf.highKhz - wf.lowKhz
                        anchors.right: parent.right; anchors.top: upArrow.bottom; anchors.bottom: downArrow.top
                        anchors.rightMargin: 1
                        Binding on size { when: !fscroll.pressed; value: fscroll.full > 0 ? fscroll.span / fscroll.full : 1 }
                        Binding on position { when: !fscroll.pressed; value: fscroll.full > 0 ? (inst.highKhz - wf.highKhz) / fscroll.full : 0 }
                        onPositionChanged: if (pressed && full > 0) {
                            const hi = inst.highKhz - position * full
                            wf.setView(hi - span, hi)
                        }
                    }
                    Rectangle { anchors.right: fscroll.left; anchors.top: parent.top; anchors.bottom: parent.bottom; anchors.margins: 1; width: 1; color: t.line }
                    // a tenth of the view per step
                    BarArrow {
                        id: upArrow; dir: 1; anchors.right: parent.right; anchors.top: parent.top; anchors.margins: 1
                        onStepped: { const d = (wf.highKhz - wf.lowKhz) / 10; wf.setView(wf.lowKhz + d, wf.highKhz + d) }
                    }
                    BarArrow {
                        id: downArrow; dir: -1; anchors.right: parent.right; anchors.bottom: parent.bottom; anchors.margins: 1
                        onStepped: { const d = (wf.highKhz - wf.lowKhz) / 10; wf.setView(wf.lowKhz - d, wf.highKhz - d) }
                    }

                    // bubbles at the waterfall's leading edge: every signal SparkGap is decoding,
                    // filled once spotted
                    Repeater {
                        z: 2
                        model: { win.ladderTick; return inst ? inst.bubbles() : [] }
                        delegate: Rectangle {
                            required property var modelData
                            z: 3  // above the waterfall, which is declared after
                            visible: modelData.khz >= wf.lowKhz && modelData.khz <= wf.highKhz
                            x: wf.x + wf.width - width - 4
                            y: ladder.y + ladder.yOf(modelData.khz) - height / 2
                            width: 10; height: 10; radius: 5
                            color: modelData.spot ? t.wfMark : t.wf[0]
                            border.color: t.wfMark; border.width: 1.5
                        }
                    }

                    Waterfall {
                        id: wf
                        instance: win.inst
                        anchors.left: ladder.right; anchors.right: fscroll.left
                        anchors.top: parent.top; anchors.bottom: parent.bottom; anchors.margins: 1
                        FrameAnimation { running: true; onTriggered: wf.tick() }
                        TapHandler {
                            onTapped: (ev) => {
                                const khz = wf.highKhz - ev.position.y / wf.height * (wf.highKhz - wf.lowKhz)
                                const tol = 10 / wf.height * (wf.highKhz - wf.lowKhz)  // 10 px either side
                                const near = inst ? inst.nearestSignal(khz, tol) : -1
                                if (near > 0) win.pick(near, "")
                            }
                        }
                        HoverHandler { cursorShape: Qt.OpenHandCursor }
                        DragHandler {
                            target: null
                            cursorShape: Qt.ClosedHandCursor
                            property real startLo: 0
                            onActiveChanged: if (active) startLo = wf.lowKhz
                            onTranslationChanged: {
                                const span = wf.highKhz - wf.lowKhz
                                wf.setView(startLo + translation.y / wf.height * span, startLo + translation.y / wf.height * span + span)
                            }
                        }
                        // two-finger drag or wheel pans like the decoded text list; Ctrl+wheel (and a Windows pinch) zooms
                        WheelHandler {
                            acceptedModifiers: Qt.NoModifier
                            onWheel: (ev) => {
                                const span = wf.highKhz - wf.lowKhz
                                wf.pan(ev.pixelDelta.y !== 0 ? ev.pixelDelta.y / wf.height * span : ev.angleDelta.y / 120 * span / 10)
                            }
                        }
                        WheelHandler {
                            acceptedModifiers: Qt.ControlModifier
                            onWheel: (ev) => {
                                const khz = wf.highKhz - ev.y / wf.height * (wf.highKhz - wf.lowKhz)
                                const px = ev.pixelDelta.y !== 0 ? ev.pixelDelta.y : ev.angleDelta.y / 120 * 100  // a notch is 100 px
                                wf.zoom(Math.pow(0.8, px / 100), khz)
                            }
                        }
                        // pinch on a touch screen or touchpad zooms about the fingers
                        PinchHandler {
                            target: null
                            property real lo: 0
                            property real hi: 0
                            property real at: 0
                            onActiveChanged: if (active) {
                                lo = wf.lowKhz; hi = wf.highKhz
                                at = wf.highKhz - centroid.position.y / wf.height * (wf.highKhz - wf.lowKhz)
                            }
                            onActiveScaleChanged: if (active) wf.setView(at - (at - lo) / activeScale, at + (hi - at) / activeScale)
                        }
                        Text {
                            anchors.centerIn: parent
                            visible: !inst || inst.state !== "live" || inst.highKhz === 0
                            text: !inst ? "No feed configured" : inst.state === "live" ? "Waiting for spectrum" : (inst.station ? "Connecting to " + inst.station : "Connecting")
                            color: t.muted; font.pixelSize: 15
                        }
                    }
                }

                // ---- detail, with the zoom controls under it ----
                ColumnLayout {
                Layout.preferredWidth: 370; Layout.fillWidth: false; Layout.fillHeight: true; spacing: 12
                Rectangle {
                    id: detailPane
                    Layout.fillWidth: true; Layout.fillHeight: true
                    radius: 10; color: t.surface; border.color: t.line; clip: true
                    readonly property var d: inst && win.selectedKhz > 0 ? inst.detail : ({})
                    ColumnLayout {
                        anchors.fill: parent; spacing: 0
                        ColumnLayout {
                            Layout.fillWidth: true; Layout.margins: 16; spacing: 10
                            RowLayout {
                                spacing: 10
                                Text {
                                    text: detailPane.d.call || (detailPane.d.khz ? detailPane.d.khz.toFixed(1) : "Pick a signal")
                                    color: t.fg; font.family: t.mono; font.features: t.zero; font.pixelSize: 26; font.weight: Font.DemiBold
                                }
                                Pill {
                                    visible: !!detailPane.d.khz
                                    text: detailPane.d.spotted ? "SPOTTED" : "HEARD"
                                    tone: detailPane.d.spotted ? t.accent : t.muted
                                }
                                Item { Layout.fillWidth: true }
                                // the time now, next to the window timestamps below (same style as their label)
                                Text { text: "UTC: " + utcClock.now; color: t.muted; font.pixelSize: 11; font.letterSpacing: 1 }
                            }
                            RowLayout {
                                spacing: 8
                                Repeater {
                                    model: {
                                        const d = detailPane.d
                                        return [["KHZ", d.spot_khz ? d.spot_khz.toFixed(1) : d.khz ? d.khz.toFixed(2) : "-"],  // a spot as spotted
                                                ["WPM", d.wpm ? Math.round(d.wpm) : "-"],
                                                ["SNR", d.snr ? Math.round(d.snr) + " dB" : "-"]]
                                    }
                                    delegate: Rectangle {
                                        required property var modelData
                                        Layout.fillWidth: true; height: 48; radius: 8; color: t.sunken
                                        Column {
                                            x: 10; anchors.verticalCenter: parent.verticalCenter
                                            Text { text: modelData[0]; color: t.muted; font.pixelSize: 10; font.letterSpacing: 1 }
                                            Text { text: modelData[1]; color: t.fg; font.family: t.mono; font.features: t.zero; font.pixelSize: 15; font.weight: Font.Medium }
                                        }
                                    }
                                }
                            }
                        }
                        Rectangle { Layout.fillWidth: true; height: 1; color: t.line }
                        RowLayout {
                            Layout.fillWidth: true; Layout.leftMargin: 16; Layout.rightMargin: 16; Layout.topMargin: 6
                            Text { Layout.fillWidth: true; text: "DECODED WINDOWS"; color: t.muted; font.pixelSize: 11; font.letterSpacing: 1 }
                            Btn {
                                text: "Copy"; visible: (detailPane.d.windows || []).length > 0
                                onClicked: { winText.selectAll(); winText.copy(); winText.deselect() }
                            }
                        }
                        Text {
                            visible: !!detailPane.d.note
                            Layout.fillWidth: true; Layout.margins: 16; Layout.topMargin: 6
                            text: detailPane.d.note || ""; wrapMode: Text.WordWrap; color: t.muted; font.pixelSize: 12
                        }
                        Item {
                            Layout.fillWidth: true; Layout.fillHeight: true
                            Layout.leftMargin: 16; Layout.rightMargin: 2; Layout.topMargin: 6; Layout.bottomMargin: 16
                            readonly property bool overflows: winList.contentHeight > winList.height
                            // one read-only document, so a drag selects across windows and Ctrl+C copies
                            Flickable {
                                id: winList
                                anchors.fill: parent; anchors.rightMargin: 13
                                clip: true; contentWidth: width; contentHeight: winText.height
                                boundsBehavior: Flickable.StopAtBounds
                                ScrollBar.vertical: winBar
                                TextEdit {
                                    id: winText
                                    width: winList.width
                                    readOnly: true; selectByMouse: true
                                    textFormat: TextEdit.RichText; wrapMode: TextEdit.WrapAnywhere
                                    color: t.fg; selectionColor: Qt.alpha(t.accent, 0.35); selectedTextColor: t.fg
                                    font.family: t.mono; font.features: t.zero; font.pixelSize: 11
                                    text: win.windowsHtml(detailPane.d.windows || [], win.decoded)
                                }
                            }
                            function scrollBy(dy) {
                                winList.contentY = Math.max(0, Math.min(winList.contentHeight - winList.height, winList.contentY + dy))
                            }
                            BarArrow { id: winUp; visible: parent.overflows; dir: 1; anchors.right: parent.right; anchors.top: parent.top; onStepped: parent.scrollBy(-40) }
                            ThinBar {
                                id: winBar; visible: parent.overflows
                                anchors.right: parent.right; anchors.top: winUp.bottom; anchors.bottom: winDown.top
                            }
                            BarArrow { id: winDown; visible: parent.overflows; dir: -1; anchors.right: parent.right; anchors.bottom: parent.bottom; onStepped: parent.scrollBy(40) }
                        }
                    }
                }
                    Rectangle {
                        id: zoomBox
                        Layout.fillWidth: true; Layout.preferredHeight: leg.implicitHeight + 20
                        radius: 10; color: t.surface; border.color: t.line
                        // labelled rows: the zoom, what is on screen, the colour scale; values share a left edge,
                        // and the third column holds the span, Full and the scale's unit
                        GridLayout {
                            id: leg; anchors.centerIn: parent
                            columns: 3; columnSpacing: 10; rowSpacing: 6
                            component ZLabel: Text { Layout.alignment: Qt.AlignRight | Qt.AlignVCenter; color: t.muted; font.pixelSize: 11 }
                            ZLabel { text: "Zoom" }
                                Slider {
                                    id: spanSlider
                                    Layout.preferredWidth: 170
                                    leftPadding: 0  // starts on the column's left edge, with Span and SNR
                                    from: 2; to: Math.max(3, fscroll.full); stepSize: 0
                                    Binding on value { when: !spanSlider.pressed; value: fscroll.span }
                                    onMoved: {
                                        const mid = (wf.lowKhz + wf.highKhz) / 2
                                        wf.setView(mid - value / 2, mid + value / 2)
                                    }
                                    background: Rectangle {
                                        x: spanSlider.leftPadding; y: spanSlider.topPadding + spanSlider.availableHeight / 2 - 2
                                        width: spanSlider.availableWidth; height: 4; radius: 2; color: t.line
                                        Rectangle { width: spanSlider.visualPosition * parent.width; height: 4; radius: 2; color: t.accent }
                                    }
                                    handle: Rectangle {
                                        x: spanSlider.leftPadding + spanSlider.visualPosition * (spanSlider.availableWidth - width)
                                        y: spanSlider.topPadding + spanSlider.availableHeight / 2 - 7
                                        width: 14; height: 14; radius: 7; color: t.surface; border.color: t.accent; border.width: 2
                                    }
                                }
                                Rectangle {
                                    Layout.alignment: Qt.AlignHCenter | Qt.AlignVCenter
                                    width: fullTxt.implicitWidth + 14; height: 20; radius: 6
                                    color: fullHov.hovered ? t.sunken : "transparent"; border.color: t.line
                                    Text { id: fullTxt; anchors.centerIn: parent; color: t.fg; font.pixelSize: 11  // names the zoom shown
                                           text: { const k = win.zoomStep(); return k >= 0 ? ["Full", "Half", "Quarter"][k]
                                                       : Math.round(100 * (wf.highKhz - wf.lowKhz) / Math.max(0.001, inst.highKhz - inst.lowKhz)) + "%" } }
                                    HoverHandler { id: fullHov; cursorShape: Qt.PointingHandCursor }
                                    TapHandler {
                                        onTapped: {  // the next step: Full, Half, Quarter of the band around the view's centre
                                            if (!inst) return
                                            const k = win.zoomStep(), d = [1, 2, 4][k < 0 ? 0 : (k + 1) % 3]
                                            const span = (inst.highKhz - inst.lowKhz) / d
                                            const mid = d === 1 ? (inst.lowKhz + inst.highKhz) / 2 : (wf.lowKhz + wf.highKhz) / 2
                                            wf.setView(mid - span / 2, mid + span / 2)
                                        }
                                    }
                                }
                            ZLabel { text: "Span" }
                            Text {
                                Layout.alignment: Qt.AlignHCenter | Qt.AlignVCenter  // centred over the slider and the scale
                                text: wf.lowKhz.toFixed(1) + " – " + wf.highKhz.toFixed(1) + " kHz"
                                color: t.muted; font.family: t.mono; font.features: t.zero; font.pixelSize: 11
                            }
                            Text {
                                Layout.alignment: Qt.AlignHCenter | Qt.AlignVCenter
                                text: fscroll.span.toFixed(0) + " kHz"; color: t.fg; font.family: t.mono; font.features: t.zero; font.pixelSize: 11
                            }
                            ZLabel { text: "SNR"; Layout.alignment: Qt.AlignRight | Qt.AlignTop }  // level with the bar, not centred across bar and numbers
                            Column {
                                Layout.preferredWidth: spanSlider.width  // as long as the zoom slider
                                Layout.topMargin: 4
                                spacing: 4
                                Rectangle {
                                    width: parent.width; height: 6; radius: 3
                                    gradient: Gradient {
                                        orientation: Gradient.Horizontal
                                        GradientStop { position: 0.0; color: t.wf[1] }
                                        GradientStop { position: 0.2; color: t.wf[2] }
                                        GradientStop { position: 0.4; color: t.wf[3] }
                                        GradientStop { position: 0.6; color: t.wf[4] }
                                        GradientStop { position: 0.8; color: t.wf[5] }
                                        GradientStop { position: 1.0; color: t.wf[6] }
                                    }
                                }
                                RowLayout {  // scale labels spread evenly, as the web version's space-between
                                    width: parent.width; spacing: 0
                                    component Tick: Text { color: t.faint; font.family: t.mono; font.features: t.zero; font.pixelSize: 10 }
                                    Tick { text: "0" }
                                    Item { Layout.fillWidth: true }
                                    Tick { text: "10" }
                                    Item { Layout.fillWidth: true }
                                    Tick { text: "20" }
                                    Item { Layout.fillWidth: true }
                                    Tick { text: "30" }
                                }
                            }
                            Text {  // the scale's unit, on the numbers' line
                                Layout.alignment: Qt.AlignHCenter | Qt.AlignBottom
                                Layout.topMargin: 4
                                text: "dB"; color: t.fg; font.family: t.mono; font.features: t.zero; font.pixelSize: 11  // as the span value
                            }
                        }
                    }
                }
            }

        }

        // ---- status bar ----
        Row {
            spacing: 22
            Repeater {
                model: {
                    const s = inst ? inst.status : {}
                    const up = s.uptime_s || 0
                    return [["Load", s.cpu !== undefined && s.cpu !== null ? s.cpu + "%" : "-"],
                            ["Memory", s.rss_mb ? s.rss_mb + " MB" : "-"],
                            ["Bins", s.bins !== undefined ? s.bins : "-"],
                            ["Consoles", s.clients ? s.clients.length : "-"],
                            ["Decoder", inst && inst.info.decoder ? inst.info.decoder : "-"],
                            ["Build", inst && inst.info.build ? inst.info.build : "-"],
                            ["Up", Math.floor(up / 3600) + "h " + Math.floor(up % 3600 / 60) + "m"]]
                }
                delegate: Row {
                    required property var modelData
                    spacing: 5
                    Text { text: modelData[0]; color: t.muted; font.pixelSize: 12 }
                    Text { text: modelData[1]; color: t.fg; font.family: t.mono; font.features: t.zero; font.pixelSize: 12 }
                }
            }
        }
    }

    // ---- status page: radio, SparkGap, decoding and this console, for troubleshooting ----
    function statusSections() {
        // { head, live, tiles: [{k, v, sub, bad}], cards: [{title, wide, rows: [{k, v, sub, mono, bad, level}]}] }; empty in demo
        const i = inst, s = (i && i.status) || {}, h = (i && i.info) || {}, run = h.run || {}, r = s.radio || {}
        if (!i || i.demo) return { head: "", live: false, health: "ok", issues: [], tiles: [], cards: [] }
        const code = console_.feedAt(console_.currentIndex).token || ""
        const dash = v => v === null || v === undefined || v === "" ? "-" : String(v)
        const num = v => v === null || v === undefined ? "-" : Number(v).toLocaleString(Qt.locale("en_US"), "f", 0)
        const utc = x => x ? new Date(x * 1000).toISOString().slice(11, 19) + "Z" : "-"
        const dur = x => !(x >= 0) ? "-" : Math.floor(x / 3600) ? Math.floor(x / 3600) + "h " + Math.floor(x % 3600 / 60) + "m"
                                                                : Math.floor(x / 60) + "m " + Math.floor(x % 60) + "s"
        const gb = x => x === null || x === undefined ? "-" : x >= 1073741824 ? (x / 1073741824).toFixed(2) + " GiB" : (x / 1048576).toFixed(0) + " MiB"
        const near = 3.5 * 1073741824  // record_wav files break past 4 GiB
        // key: a value that signals trouble (full weight); ref: reference detail (muted); bad: red
        const row = (k, v, o) => Object.assign({ k: k, v: dash(v), sub: "", title: "", mono: false, bad: false, key: false, ref: false, level: "", act: "" }, o || {})
        const fileRow = (k, p, none, o) => {
            if (!p) return row(k, none, o)
            const n = String(p).lastIndexOf("/")
            return row(k, n < 0 ? p : p.slice(n + 1), Object.assign({ sub: n < 0 ? "" : p.slice(0, n), title: String(p) }, o || {}))
        }
        const tiles = [
            { k: "UPTIME", v: dur(s.uptime_s), sub: "", bad: false },
            { k: "SPOTS", v: num(s.spots), sub: s.spots_hour !== undefined ? s.spots_hour + " last hour" : "", bad: false, primary: true },
            { k: "CALLS", v: num(s.calls), sub: "", bad: false, primary: true },
            { k: "WINDOWS", v: num(s.windows), sub: "", bad: false, primary: true },
            { k: "BINS", v: num(s.bins), sub: s.bins_peak !== undefined ? "peak " + s.bins_peak : "", bad: false },
            { k: "CPU", v: s.cpu !== undefined && s.cpu !== null ? s.cpu + "%" : "-", sub: "of a core" + (s.rss_mb ? " · " + s.rss_mb + " MB" : ""), bad: false }]
        const lossy = r.packets > 0 && r.lost / r.packets > 0.001  // more than 0.1% of packets lost is worth a look
        const radio = [row("Type", r.type === "flex" ? "FlexRadio" : r.type === "hpsdr" ? "HPSDR" : r.type === "file" ? "File replay" : r.type, { ref: true }),
                       r.type === "file" ? fileRow("File", r.address, "-", { ref: true }) : row("Address", r.address, { mono: true, ref: true })]
        if ("station" in r) radio.push(row("Station", r.station, { ref: true }), row("DAX-IQ channel", r.daxiq_channel, { mono: true, ref: true }))
        const mhz = khz => (khz / 1000).toFixed(3), bands = h.bands || []
        bands.forEach((b, n) => {
            const tag = bands.length > 1 ? " " + (i.bandNames[n] || b.name) : "", d = (s.decode_khz || [])[n]
            radio.push(row("IQ" + tag, mhz(b.center_khz) + " MHz, " + b.span_khz + " kHz", { mono: true, ref: true }))
            if (d) radio.push(row("Decoding" + tag, mhz(d[0]) + " to " + mhz(d[1]) + " MHz", { mono: true, ref: true }))
        })
        if ("packets" in r) radio.push(row("Packets", num(r.packets), { mono: true, ref: true }), row("Lost", num(r.lost), { mono: true, key: true, bad: lossy }))
        if ("pkt_per_s" in r) radio.push(row("Packets a second", num(r.pkt_per_s), { mono: true, key: true }))
        if ("reconnects" in r) radio.push(row("Reconnects", r.reconnects ? r.reconnects + ", last " + utc(r.last_reconnect) : "none",
                                              { mono: !!r.reconnects, key: true, bad: r.reconnects > 0 }))
        if ("station" in r) radio.push(row("Other stations", (r.others || []).join(", ") || "none", { key: true }))
        if ("position_s" in r) radio.push(row("Position", dur(r.position_s), { mono: true }))
        const rec = s.recording, nearLimit = !!rec && rec.bytes > near
        const sg = [row("Host", h.host + ", port " + i.url.split(":").pop().split("/")[0], { ref: true }),
                    code ? row("Join code", win.codeShown ? code : "•••-•••", { mono: true, ref: true, act: "code", title: win.codeShown ? "Click to hide" : "Click to show and copy" })
                         : row("Join code", "none (local only)", { ref: true }), row("Decoder", h.decoder, { ref: true }),
            row("Bands", (h.bands || []).map((b, n) => i.bandNames[n] || b.name).join(", "), { key: true }),
            fileRow("Config", run.config, "-", { ref: true }), fileRow("Log", run.log, "terminal", { ref: true }),
            rec ? fileRow("Recording", rec.file, "off", { bad: nearLimit, ref: !nearLimit }) : row("Recording", "off", { ref: true })]
        if (rec) sg.push(row("Recording size", gb(rec.bytes) + (nearLimit ? "  (near the 4 GiB limit)" : ""), { mono: true, key: true, bad: nearLimit }))
        sg.push(row("IQ trimmed", s.iq_trimmed_s !== undefined ? s.iq_trimmed_s + " s" + (s.iq_trimmed_s > 0 ? "  (decoder behind)" : "") : null,
                    { mono: true, key: true, bad: s.iq_trimmed_s > 0 }))
        const out = s.telnet ? [row("Telnet port", s.telnet.port, { mono: true, ref: true }), row("Telnet clients", s.telnet.clients, { mono: true, key: true })]
                             : [row("Telnet", "not reported")]
        const cons = [row("This console", "since " + utc(i.since) + (i.reconnects ? ", " + i.reconnects + " reconnects" : ""), { mono: true, ref: true }),
                      row("Rows a second", statusPop.rate, { mono: true, key: true })]
        for (const c of s.clients || []) cons.push(row(c.name || c.peer, num(c.dropped) + " dropped", { mono: true, sub: (c.name ? c.peer + ", " : "") + "since " + utc(c.since), key: true, bad: c.dropped > 600 }))
        const p = s.problems || {}
        const probs = [row("Since start", num(p.errors) + " errors, " + num(p.warnings) + " warnings", { bad: p.errors > 0 })]
        for (const e of (p.last || []).slice().reverse()) probs.push(row(utc(e.utc), e.msg, { level: e.level }))
        // health at a glance: what needs attention, worst first
        const live = ["live", "replaying"].indexOf(r.state) >= 0
        const issues = []
        if (r.state && !live) issues.push({ lv: "bad", t: "Radio " + r.state })
        if (s.iq_trimmed_s > 0) issues.push({ lv: "bad", t: "Decoder behind: " + s.iq_trimmed_s + " s of IQ trimmed" })
        if (s.clock_synced === false) issues.push({ lv: "bad", t: "Clock not NTP synchronised" })
        if (p.errors > 0) issues.push({ lv: "bad", t: num(p.errors) + " errors since start" })
        if (r.reconnects > 0) issues.push({ lv: "warn", t: "Radio reconnected " + r.reconnects + "x, last " + utc(r.last_reconnect) })
        if (lossy) issues.push({ lv: "warn", t: num(r.lost) + " packets lost (" + (100 * r.lost / r.packets).toFixed(2) + "%)" })
        if (nearLimit) issues.push({ lv: "warn", t: "Recording near the 4 GiB limit" })
        for (const c of s.clients || []) if (c.dropped > 600) issues.push({ lv: "warn", t: "Console " + (c.name || c.peer) + " falling behind" })
        if (p.warnings > 0) issues.push({ lv: "warn", t: num(p.warnings) + " warnings since start" })
        const worst = issues.some(x => x.lv === "bad") ? "bad" : issues.length ? "warn" : "ok"
        const tag = (text, lv) => ({ text: text, lv: lv })
        const clock = s.clock_synced === true ? "NTP synchronised" : s.clock_synced === false ? "not synchronised" : "unknown"
        return { head: i.station || "", state: r.state ? String(r.state).toUpperCase() : "", live: live,
                 foot: "Build <b>" + (h.build || "-") + "</b>.  Clock <b>" + clock + "</b>.",
                 health: worst, issues: issues.length ? issues : [{ lv: "ok", t: "Healthy: radio live, decoding keeping up, no errors" }],
                 tiles: tiles,
                 cards: [{ title: "RADIO", wide: false, rows: radio,
                           tag: r.state ? tag(String(r.state).toUpperCase(), live ? (r.reconnects || lossy ? "warn" : "ok") : "bad") : null },
                         { title: "SPARKGAP", wide: false, rows: sg,
                           tag: s.clock_synced === false ? tag("CLOCK OFF", "bad") : s.clock_synced ? tag("CLOCK OK", "ok") : null },
                         { title: "SPOT OUTPUT", wide: false, rows: out, tag: null },
                         { title: "CONSOLES", wide: false, rows: cons, tag: null },
                         { title: "PROBLEMS", wide: true, rows: probs,
                           tag: p.errors ? tag(num(p.errors) + " ERRORS", "bad") : p.warnings ? tag(num(p.warnings) + " WARNINGS", "warn") : tag("NONE", "ok") }] }
    }
    Popup {
        id: statusPop
        anchors.centerIn: Overlay.overlay
        width: Math.min(760, win.width - 32); padding: 18
        height: Math.min(implicitHeight, win.height - 32)
        modal: true; dim: true
        closePolicy: Popup.CloseOnEscape | Popup.CloseOnPressOutside
        Overlay.modal: Rectangle { color: Qt.alpha("#000000", win.dark ? 0.55 : 0.35) }
        background: Rectangle { radius: 10; color: t.surface; border.color: t.line }
        property var sections: ({ head: "", state: "", live: false, health: "ok", issues: [], tiles: [], cards: [] })
        property string rate: "-"  // waterfall rows a second, from rowsTotal between refreshes
        property real lastTotal: -1
        property real lastAt: 0
        function refresh() {
            if (inst && lastTotal >= 0) {
                const dt = Date.now() / 1000 - lastAt
                if (dt > 0.5) rate = ((inst.rowsTotal - lastTotal) / dt).toFixed(0)
            }
            lastTotal = inst ? inst.rowsTotal : -1; lastAt = Date.now() / 1000
            if (!held) sections = win.statusSections()
        }
        property bool held: false  // text selected: hold the page still until it is cleared
        onOpened: { held = false; lastTotal = -1; rate = "-"; refresh() }
        Connections { target: inst; enabled: statusPop.visible; function onStatusChanged() { statusPop.refresh() } }
        contentItem: ColumnLayout {
            spacing: 12
            id: statusBody
            TapHandler { onTapped: statusBody.forceActiveFocus() }  // a click off the text clears its selection
            RowLayout {
                Layout.fillWidth: true; spacing: 10
                Text { text: "Status"; color: t.fg; font.family: t.display; font.pixelSize: 15; font.weight: Font.DemiBold }
                Item { Layout.fillWidth: true }
                Text { text: statusPop.sections.head; color: t.muted; font.family: t.mono; font.features: t.zero; font.pixelSize: 12 }
                Pill { visible: !!statusPop.sections.state; text: statusPop.sections.state; tone: statusPop.sections.live ? t.good : t.bad }
            }
            Flickable {
                id: statFlick
                Layout.fillWidth: true; Layout.fillHeight: true
                implicitHeight: statCol.implicitHeight; contentHeight: statCol.implicitHeight
                clip: true; boundsBehavior: Flickable.StopAtBounds
                ScrollBar.vertical: ScrollBar { policy: statFlick.contentHeight > statFlick.height ? ScrollBar.AlwaysOn : ScrollBar.AlwaysOff }
                ColumnLayout {
                    id: statCol
                    width: statFlick.width - (statFlick.contentHeight > statFlick.height ? 14 : 0)
                    spacing: 10
                    Text {
                        visible: statusPop.sections.cards.length === 0
                        text: "Status is shown for a connected SparkGap, not demo mode."; color: t.muted; font.pixelSize: 12
                    }
                    // health at a glance
                    Rectangle {
                        readonly property color tone: statusPop.sections.health === "bad" ? t.bad : statusPop.sections.health === "warn" ? t.accent : t.good
                        visible: statusPop.sections.cards.length > 0
                        Layout.fillWidth: true; implicitHeight: issueFlow.implicitHeight + 18; radius: 8
                        color: Qt.alpha(tone, 0.10); border.color: tone
                        Flow {
                            id: issueFlow; x: 12; y: 9; width: parent.width - 24; spacing: 16
                            Repeater {
                                model: statusPop.sections.issues
                                delegate: Text {
                                    required property var modelData
                                    text: modelData.t; font.pixelSize: 13; font.weight: Font.Medium
                                    color: modelData.lv === "bad" ? t.bad : modelData.lv === "warn" ? t.accent : t.good
                                }
                            }
                        }
                    }
                    // headline tiles, like the detail panel's KHZ / WPM / SNR
                    RowLayout {
                        Layout.fillWidth: true; spacing: 8
                        Repeater {
                            model: statusPop.sections.tiles
                            delegate: Rectangle {
                                required property var modelData
                                Layout.fillWidth: true; Layout.fillHeight: true; Layout.preferredWidth: 80
                                implicitHeight: tileCol.implicitHeight + 16; radius: 8; color: t.sunken
                                Column {
                                    id: tileCol; x: 10; y: 8; spacing: 1
                                    Text { text: modelData.k; color: t.muted; font.pixelSize: 10; font.letterSpacing: 1 }
                                    Text { text: modelData.v; color: modelData.bad ? t.bad : t.fg; font.family: t.mono; font.features: t.zero; font.pixelSize: modelData.primary ? 20 : 15; font.weight: Font.Medium }
                                    Text { visible: !!modelData.sub; text: modelData.sub; color: t.faint; font.pixelSize: 10 }
                                }
                            }
                        }
                    }
                    // cards: two columns, Problems across both
                    GridLayout {
                        Layout.fillWidth: true; columns: 2; columnSpacing: 10; rowSpacing: 10
                        Repeater {
                            model: statusPop.sections.cards
                            delegate: Rectangle {
                                id: card
                                required property var modelData
                                Layout.fillWidth: true; Layout.columnSpan: modelData.wide ? 2 : 1; Layout.alignment: Qt.AlignTop
                                Layout.preferredWidth: 100; Layout.fillHeight: true
                                implicitHeight: cardCol.implicitHeight + 20; radius: 8; color: t.sunken
                                ColumnLayout {
                                    id: cardCol; x: 12; y: 10; width: card.width - 24; spacing: 0
                                    RowLayout {
                                        Layout.fillWidth: true; Layout.bottomMargin: 6
                                        Text { Layout.fillWidth: true; text: card.modelData.title; color: t.muted; font.pixelSize: 11; font.letterSpacing: 1 }
                                        Pill {
                                            visible: !!card.modelData.tag; text: card.modelData.tag ? card.modelData.tag.text : ""
                                            tone: !card.modelData.tag ? t.muted : card.modelData.tag.lv === "bad" ? t.bad : card.modelData.tag.lv === "warn" ? t.accent : t.good
                                        }
                                    }
                                    Repeater {
                                        model: card.modelData.rows
                                        delegate: ColumnLayout {
                                            required property var modelData
                                            required property int index
                                            Layout.fillWidth: true; spacing: 0
                                            Rectangle { visible: index > 0; Layout.fillWidth: true; height: 1; color: Qt.alpha(t.line, 0.5) }
                                            RowLayout {
                                                Layout.fillWidth: true; Layout.topMargin: 3; Layout.bottomMargin: 3; spacing: 10
                                                Text {
                                                    text: modelData.k; color: modelData.level ? t.faint : t.muted
                                                    font.family: modelData.level ? t.mono : t.sans; font.pixelSize: modelData.level ? 11 : 12
                                                    Layout.alignment: Qt.AlignTop
                                                }
                                                Pill {
                                                    visible: !!modelData.level; text: modelData.level
                                                    tone: modelData.level === "WARNING" ? t.accent : t.bad
                                                }
                                                Column {
                                                    Layout.fillWidth: true; Layout.alignment: Qt.AlignTop
                                                    HoverHandler { id: rowHover; cursorShape: modelData.act ? Qt.PointingHandCursor : Qt.ArrowCursor }
                                                    TapHandler {
                                                        enabled: modelData.act === "code"
                                                        onTapped: {
                                                            win.codeShown = !win.codeShown
                                                            if (win.codeShown) { codeClip.text = console_.feedAt(console_.currentIndex).token; codeClip.selectAll(); codeClip.copy() }
                                                            statusPop.sections = win.statusSections()
                                                        }
                                                    }
                                                    ToolTip.visible: rowHover.hovered && !!modelData.title; ToolTip.delay: 600; ToolTip.text: modelData.title
                                                    TextEdit {  // selectable; a selection holds the page still (statusPop.held)
                                                        width: parent.width; wrapMode: TextEdit.WrapAnywhere; readOnly: true
                                                        selectByMouse: true; enabled: !modelData.act; selectionColor: Qt.alpha(t.accent, 0.4)
                                                        horizontalAlignment: modelData.level ? TextEdit.AlignLeft : TextEdit.AlignRight
                                                        text: modelData.v; font.pixelSize: 12; font.weight: modelData.key ? Font.DemiBold : Font.Normal
                                                        color: modelData.bad ? t.bad : modelData.ref ? t.muted : t.fg
                                                        font.family: modelData.mono ? t.mono : t.sans; font.features: t.zero
                                                        onSelectedTextChanged: statusPop.held = selectedText !== ""
                                                    }
                                                    TextEdit {
                                                        visible: !!modelData.sub; width: parent.width; horizontalAlignment: TextEdit.AlignRight
                                                        wrapMode: TextEdit.WrapAnywhere; readOnly: true; selectByMouse: true; selectionColor: Qt.alpha(t.accent, 0.4)
                                                        text: modelData.sub; color: t.faint
                                                        font.family: t.mono; font.features: t.zero; font.pixelSize: 10
                                                        onSelectedTextChanged: statusPop.held = selectedText !== ""
                                                    }
                                                }
                                            }
                                        }
                                    }
                                }
                            }
                        }
                    }
                }
            }
            RowLayout {
                Layout.fillWidth: true
                Text { text: statusPop.sections.foot || ""; textFormat: Text.StyledText; color: t.faint; font.pixelSize: 11 }
                Item { Layout.fillWidth: true }
                Btn { text: "Close"; onClicked: statusPop.close() }
            }
        }
    }

    // ---- settings: what an operator will change; shown read-only until the launcher ----
    Popup {
        id: settings
        anchors.centerIn: Overlay.overlay
        width: 480; padding: 18
        modal: true; dim: true
        closePolicy: Popup.CloseOnEscape | Popup.CloseOnPressOutside
        Overlay.modal: Rectangle { color: Qt.alpha("#000000", win.dark ? 0.55 : 0.35) }
        readonly property var c: inst && inst.info.config ? inst.info.config : ({})
        background: Rectangle { radius: 10; color: t.surface; border.color: t.line }
        height: Math.min(implicitHeight, win.height - 32)  // a short window scrolls the settings, Close stays in view
        contentItem: ColumnLayout {
            spacing: 14
            Flickable {
                id: setFlick
                Layout.fillWidth: true; Layout.fillHeight: true
                implicitHeight: setCol.implicitHeight; contentHeight: setCol.implicitHeight
                clip: true; boundsBehavior: Flickable.StopAtBounds
                ScrollBar.vertical: ScrollBar { policy: setFlick.contentHeight > setFlick.height ? ScrollBar.AlwaysOn : ScrollBar.AlwaysOff }
                ColumnLayout {
                    id: setCol
                    width: setFlick.width - (setFlick.contentHeight > setFlick.height ? 14 : 0)
                    spacing: 14
                    Text { text: "Settings"; color: t.fg; font.family: t.display; font.pixelSize: 15; font.weight: Font.DemiBold }
                    RowLayout {
                        spacing: 10
                        Text { Layout.preferredWidth: 92; text: "DEMO"; color: t.muted; font.pixelSize: 11; font.letterSpacing: 1 }
                        Seg { labels: ["Off", "On"]; current: console_.demo ? 1 : 0; onPicked: (i) => console_.setDemo(i === 1) }
                        Text { text: "Made-up signals instead of your servers."; color: t.faint; font.pixelSize: 11 }
                    }
                    RowLayout {
                        spacing: 10
                        Text { Layout.preferredWidth: 92; text: "DECODED"; color: t.muted; font.pixelSize: 11; font.letterSpacing: 1 }
                        Seg {
                            readonly property var modes: ["both", "wide", "narrow", "lowest"]
                            labels: ["Both", "Wide", "Narrow", "Lowest cost"]; current: modes.indexOf(win.decoded)
                            onPicked: (i) => win.decoded = modes[i]
                        }
                    }
                    Text { Layout.leftMargin: 102; Layout.topMargin: -8; text: "Readings of each window shown."; color: t.faint; font.pixelSize: 11 }
                    component Setting: ColumnLayout {
                        property alias label: lbl.text
                        default property alias control: holder.data
                        spacing: 6
                        Text { id: lbl; color: t.muted; font.pixelSize: 11; font.letterSpacing: 1 }
                        Item { id: holder; implicitWidth: childrenRect.width; implicitHeight: childrenRect.height }
                    }
                    component Btn: Rectangle {
                        property alias text: btnTxt.text
                        signal clicked()
                        width: btnTxt.implicitWidth + 24; height: 28; radius: 8
                        color: btnHov.hovered ? t.sunken : "transparent"; border.color: t.line
                        Text { id: btnTxt; anchors.centerIn: parent; color: t.fg; font.pixelSize: 12 }
                        HoverHandler { id: btnHov; cursorShape: Qt.PointingHandCursor }
                        TapHandler { onTapped: parent.clicked() }
                    }
                    component Field: TextField {
                        Layout.fillWidth: true; implicitHeight: 30
                        placeholderTextColor: t.faint; color: t.fg
                        font.family: t.mono; font.features: t.zero; font.pixelSize: 12
                        leftPadding: 8; topPadding: 0; bottomPadding: 0; verticalAlignment: TextInput.AlignVCenter
                        background: Rectangle { radius: 8; color: t.sunken; border.color: parent.activeFocus ? t.accent : t.line }
                    }
                    Setting {
                        label: "CONNECTIONS"
                        // the SparkGap servers this console watches, kept between runs; a click on a row shows that one
                        ColumnLayout {
                            id: conns
                            width: 444; spacing: 4
                            property int editing: -2  // -2 no form, -1 adding, else the row being edited
                            property string err: ""
                            property string note: ""  // what the last search for servers found
                            property bool searching: false
                            property int scanFor: -2  // the form a search was started from
                            function edit(i) {
                                if (console_.demo) console_.setDemo(false)  // demo goes off when a server is added or edited
                                const f = i >= 0 ? console_.feedAt(i) : { address: "", port: "", token: "" }  // no port: find them all
                                addr.text = f.address; port.text = String(f.port); tok.text = f.token
                                err = ""; note = ""; editing = i; addr.forceActiveFocus()
                            }
                            function save() {
                                if (port.text.trim() === "") {
                                    const e = console_.findFeeds(editing, addr.text, tok.text)
                                    if (e) err = e; else { err = ""; searching = true; scanFor = editing }
                                    return
                                }
                                const e = console_.saveFeed(editing, addr.text, parseInt(port.text) || 0, tok.text)
                                if (e) err = e; else editing = -2
                            }
                            Connections {
                                target: console_
                                function onScanDone(added, text) {
                                    conns.searching = false
                                    if (conns.editing !== conns.scanFor) return  // cancelled meanwhile
                                    if (added) { conns.note = text; conns.editing = -2 } else conns.err = text
                                }
                            }
                            Repeater {
                                model: console_.instances
                                delegate: Rectangle {
                                    required property int index
                                    required property var modelData
                                    readonly property bool cur: index === console_.currentIndex
                                    visible: !modelData.demo  // demo is the toggle above, not a server
                                    Layout.fillWidth: true; height: 34; radius: 8
                                    color: cur ? t.sunken : rowHov.hovered ? Qt.alpha(t.sunken, 0.5) : "transparent"
                                    border.color: cur ? t.line : "transparent"
                                    HoverHandler { id: rowHov; cursorShape: Qt.PointingHandCursor }
                                    TapHandler { onTapped: { if (console_.demo) console_.setDemo(false); console_.setCurrent(index) } }
                                    RowLayout {
                                        anchors.fill: parent; anchors.leftMargin: 10; anchors.rightMargin: 4; spacing: 8
                                        Rectangle {
                                            width: 8; height: 8; radius: 4
                                            color: modelData.state === "live" ? t.good : modelData.state === "connecting" ? t.accent : t.bad
                                        }
                                        Text {
                                            Layout.fillWidth: true; elide: Text.ElideRight
                                            text: modelData.title; color: t.fg; font.pixelSize: 13
                                        }
                                        Text {
                                            readonly property bool failing: modelData.state !== "live" && !!modelData.error
                                            text: failing ? modelData.error : modelData.url.replace("ws://", "").replace("wss://", "").replace("/feed", "")
                                            color: failing ? t.bad : t.muted; font.family: t.mono; font.features: t.zero; font.pixelSize: 11
                                            Layout.maximumWidth: 170; elide: Text.ElideRight
                                        }
                                        Btn { text: "Edit"; onClicked: conns.edit(index) }
                                        Btn { text: "✕"; onClicked: { if (console_.demo) console_.setDemo(false); console_.removeFeed(index); conns.editing = -2 } }
                                    }
                                }
                            }
                            Text {
                                visible: !console_.instances.some(i => !i.demo) && conns.editing === -2
                                text: "No servers yet."; color: t.muted; font.pixelSize: 12
                            }
                            Text {
                                visible: !!conns.note && conns.editing === -2
                                Layout.fillWidth: true; wrapMode: Text.WordWrap
                                text: conns.note; color: t.muted; font.pixelSize: 12
                            }
                            ColumnLayout {
                                visible: conns.editing > -2
                                Layout.fillWidth: true; Layout.topMargin: 6; spacing: 6
                                Field { id: addr; placeholderText: "Address: VPN or local IP address"; onAccepted: conns.save() }
                                Field {
                                    id: port; placeholderText: "Port: blank finds every SparkGap on 7320 to 7339"; onAccepted: conns.save()
                                    validator: IntValidator { bottom: 1; top: 65535 }
                                }
                                Field {
                                    id: tok; placeholderText: "Join code, e.g. XXX-XXX"; onAccepted: conns.save()
                                    font.capitalization: Font.AllUppercase
                                }
                                Text { visible: !!conns.err; text: conns.err; color: t.bad; font.pixelSize: 12 }
                                Row {
                                    spacing: 8
                                    Btn {
                                        text: conns.searching ? "Searching..." : conns.editing === -1 ? "Add" : "Save"
                                        enabled: !conns.searching; onClicked: conns.save()
                                    }
                                    Btn { text: "Cancel"; onClicked: conns.editing = -2 }
                                }
                            }
                            Btn { visible: conns.editing === -2; Layout.topMargin: 4; text: "+ Add server"; onClicked: conns.edit(-1) }
                        }
                    }
                    GridLayout {
                        columns: 2; columnSpacing: 10; rowSpacing: 8
                        component GridLabel: Text { Layout.preferredWidth: 92; color: t.muted; font.pixelSize: 11; font.letterSpacing: 1 }
                        GridLabel { text: "SPOT RULE" }
                        Seg { readOnly: true; labels: ["off", "on"]; current: settings.c.spot_rule === "repeat" ? 1 : settings.c.spot_rule ? 0 : -1 }
                        GridLabel { text: "VALIDATION"; visible: "spot_validation" in settings.c }
                        Seg { visible: "spot_validation" in settings.c; readOnly: true; labels: ["off", "permissive", "normal", "aggressive"]; current: labels.indexOf(settings.c.spot_validation || (settings.c.spot_rule ? "off" : "")) }
                        GridLabel { text: "CALL SOURCE"; visible: "spot_validation" in settings.c }
                        Seg { visible: "spot_validation" in settings.c; readOnly: true; labels: ["SCP", "RBN", "RBN + SKCC"]; current: ["scp", "rbn", "rbn+skcc"].indexOf(settings.c.spot_validation_calls) }
                        Item { width: 1; height: 1 }
                        Text { text: "From the selected server's config."; color: t.faint; font.pixelSize: 11 }
                    }
                }
            }
            RowLayout {
                Layout.fillWidth: true
                Text {
                    text: "Help"; color: t.muted; font.underline: true; font.pixelSize: 11
                    HoverHandler { cursorShape: Qt.PointingHandCursor }
                    TapHandler { onTapped: Qt.openUrlExternally(win.helpUrl) }
                }
                Text { text: build_; color: t.faint; font.family: t.mono; font.features: t.zero; font.pixelSize: 11 }
                Item { Layout.fillWidth: true }
                Rectangle {
                    width: closeTxt.implicitWidth + 28; height: 30; radius: 8
                    color: closeHov.hovered ? t.sunken : "transparent"; border.color: t.line
                    Text { id: closeTxt; anchors.centerIn: parent; text: "Close"; color: t.fg }
                    HoverHandler { id: closeHov; cursorShape: Qt.PointingHandCursor }
                    TapHandler { onTapped: settings.close() }
                }
            }
        }
    }

    Shortcut { sequence: "Ctrl+F"; onActivated: { search.forceActiveFocus(); search.selectAll() } }

    // matches for the search box, under it
    Rectangle {
        visible: search.activeFocus && search.matches.length > 0
        z: 10
        x: search.mapToItem(win.contentItem, 0, 0).x; y: search.mapToItem(win.contentItem, 0, 0).y + search.height + 4
        width: Math.max(search.width, 300); height: matchCol.implicitHeight + 8; radius: 8
        color: t.surface; border.color: t.line
        Column {
            id: matchCol; x: 4; y: 4; width: parent.width - 8
            Repeater {
                model: search.matches
                delegate: Rectangle {
                    required property var modelData
                    required property int index
                    width: matchCol.width; height: 28; radius: 6
                    color: mh.hovered || index === 0 ? t.sunken : "transparent"
                    HoverHandler { id: mh; cursorShape: Qt.PointingHandCursor }
                    TapHandler { onTapped: win.goTo(modelData) }
                    Row {
                        x: 8; anchors.verticalCenter: parent.verticalCenter; spacing: 10
                        Rectangle {
                            anchors.verticalCenter: parent.verticalCenter; width: 6; height: 6; radius: 3
                            color: modelData.spot ? t.accent : "transparent"; border.color: modelData.spot ? t.accent : t.faint
                        }
                        Text { width: 90; text: modelData.label; color: t.fg; font.family: t.mono; font.features: t.zero; font.weight: Font.DemiBold }
                        Text { width: 80; text: modelData.call ? modelData.khz.toFixed(1) : ""; color: t.muted; font.family: t.mono; font.features: t.zero }
                        Text { text: modelData.ago ? modelData.ago + " ago" : "go to"; color: t.faint; font.pixelSize: 11 }
                    }
                }
            }
        }
    }

    // centre the waterfall on a match (keeping the span) and select it
    function setBand(n) {
        if (!inst) return
        inst.setBand(n)
        selectedKhz = -1
    }

    function goTo(m) {
        const b = inst ? inst.bandAt(m.khz) : -1
        if (b >= 0 && b !== inst.band) { inst.goTo(m.khz); setBand(b) }  // the match is on another band
        const span = wf.highKhz - wf.lowKhz
        wf.setView(m.khz - span / 2, m.khz + span / 2)
        pick(m.khz, m.call)  // a typed frequency is taken as typed
        search.focus = false
    }

    function pick(khz, call) {
        selectedKhz = khz
        wf.setSelected(khz)
        if (inst) inst.select(khz, call)
    }
}
