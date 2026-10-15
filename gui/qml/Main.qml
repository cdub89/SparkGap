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
    Settings { category: "ui"; property alias theme: win.themeOverride }
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
    property string themeOverride: "dark"  // dark by default; the toggle switches it
    property alias settingsOpen: settings.visible
    readonly property bool dark: themeOverride ? themeOverride === "dark"
                                               : Application.styleHints.colorScheme === Qt.ColorScheme.Dark
    property var selectedKhz: -1
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
                text: [c.callsign,
                       c.flex_daxiq_channel ? "DAX IQ " + c.flex_daxiq_channel : "",
                       c.sample_rate ? (c.sample_rate / 1000) + " kHz" : "",
                       c.telnet_port ? "telnet " + c.telnet_port : "",
                       c.enable_ft8 ? "FT8 on" : ""].filter(x => x).join("  ·  ")
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
                          + "  " + inst.url.replace("ws://", "").replace("wss://", "").replace("/feed", "")
                          + (inst && inst.state !== "live" && inst.error ? "  ·  " + inst.error : "")
                }
            }
            Rectangle {
                id: gear
                width: 32; height: 32; radius: 8; color: settings.visible ? t.sunken : t.surface; border.color: t.line
                Text { anchors.centerIn: parent; text: "⚙"; color: t.fg; font.pixelSize: 23 }
                MouseArea {
                    anchors.fill: parent; cursorShape: Qt.PointingHandCursor
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
                    anchors.fill: parent; cursorShape: Qt.PointingHandCursor
                    onClicked: win.themeOverride = win.dark ? "light" : "dark"
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

                        // frequency ticks, one per kHz, labels every 5
                        Repeater {
                            model: wf.highKhz > wf.lowKhz ? Math.floor(wf.highKhz) - Math.ceil(wf.lowKhz) + 1 : 0
                            delegate: Item {
                                required property int index
                                readonly property int khz: Math.ceil(wf.lowKhz) + index
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
                        DragHandler {
                            target: null
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
                                wf.zoom(Math.pow(0.8, ev.angleDelta.y / 120), khz)
                            }
                        }
                        Text {
                            anchors.centerIn: parent
                            visible: !inst || inst.state !== "live" || inst.highKhz === 0
                            text: !inst ? "No feed configured" : inst.state === "live" ? "Waiting for spectrum" : "Connecting to " + inst.url
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
                                        return [["KHZ", d.khz ? d.khz.toFixed(2) : "-"],
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
                        Text { Layout.leftMargin: 16; Layout.topMargin: 10; text: "DECODED WINDOWS"; color: t.muted; font.pixelSize: 11; font.letterSpacing: 1 }
                        Text {
                            visible: !!detailPane.d.note
                            Layout.fillWidth: true; Layout.margins: 16; Layout.topMargin: 6
                            text: detailPane.d.note || ""; wrapMode: Text.WordWrap; color: t.muted; font.pixelSize: 12
                        }
                        Item {
                            Layout.fillWidth: true; Layout.fillHeight: true
                            Layout.leftMargin: 16; Layout.rightMargin: 2; Layout.topMargin: 6; Layout.bottomMargin: 16
                            readonly property bool overflows: winList.contentHeight > winList.height
                            ListView {
                                id: winList
                                anchors.fill: parent; anchors.rightMargin: 13
                                clip: true; spacing: 8
                                ScrollBar.vertical: winBar
                                model: detailPane.d.windows || []
                                // both decodes of each window: wide filter on top, narrow dimmed
                                delegate: RowLayout {
                                    required property var modelData
                                    width: ListView.view.width; spacing: 10
                                    Column {
                                        Layout.alignment: Qt.AlignTop
                                        Text { text: modelData.utc; color: t.faint; font.family: t.mono; font.features: t.zero; font.pixelSize: 11 }
                                    }
                                    Column {
                                        Layout.fillWidth: true; spacing: 3
                                        Text {
                                            width: parent.width; text: modelData.html; textFormat: Text.RichText
                                            wrapMode: Text.WrapAnywhere; color: t.fg; font.family: t.mono; font.features: t.zero; font.pixelSize: 11
                                        }
                                        Text {
                                            visible: !!modelData.alt_html
                                            width: parent.width; text: modelData.alt_html; textFormat: Text.RichText
                                            wrapMode: Text.WrapAnywhere; color: t.muted; opacity: 0.75
                                            font.family: t.mono; font.features: t.zero; font.pixelSize: 11
                                        }
                                        Text {
                                            // top reading first; "no fit" is the decoder's cost 999
                                            function c(v) { return v === null || v === undefined ? "-" : v >= 999 ? "no fit" : v.toFixed(2) }
                                            function n(r) { return r === 100 ? "narrow" : r === 200 ? "wide" : "cost" }
                                            width: parent.width; wrapMode: Text.WordWrap
                                            text: modelData.alt_html ? n(modelData.rate) + " " + c(modelData.cost) + "  ·  " + n(modelData.alt_rate) + " " + c(modelData.alt_cost)
                                                                     : "cost " + c(modelData.cost)
                                            color: t.faint; font.family: t.mono; font.features: t.zero; font.pixelSize: 10
                                        }
                                    }
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
                        // labelled rows: what is on screen, the zoom, the colour scale; values share a left edge
                        GridLayout {
                            id: leg; anchors.centerIn: parent
                            columns: 2; columnSpacing: 10; rowSpacing: 6
                            component ZLabel: Text { Layout.alignment: Qt.AlignRight | Qt.AlignVCenter; color: t.muted; font.pixelSize: 11 }
                            ZLabel { text: "Span" }
                            Text {
                                Layout.alignment: Qt.AlignLeft | Qt.AlignVCenter
                                text: wf.lowKhz.toFixed(1) + " – " + wf.highKhz.toFixed(1) + " kHz"
                                color: t.muted; font.family: t.mono; font.features: t.zero; font.pixelSize: 11
                            }
                            ZLabel { text: "Zoom" }
                            RowLayout {
                                Layout.alignment: Qt.AlignLeft | Qt.AlignVCenter
                                spacing: 8
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
                                Text { text: fscroll.span.toFixed(0) + " kHz"; color: t.fg; font.family: t.mono; font.features: t.zero; font.pixelSize: 11 }
                                Rectangle {
                                    width: fullTxt.implicitWidth + 14; height: 20; radius: 6
                                    color: fullHov.hovered ? t.sunken : "transparent"; border.color: t.line
                                    Text { id: fullTxt; anchors.centerIn: parent; text: "Full"; color: t.fg; font.pixelSize: 11 }
                                    HoverHandler { id: fullHov; cursorShape: Qt.PointingHandCursor }
                                    TapHandler { onTapped: if (inst) wf.setView(inst.lowKhz, inst.highKhz) }
                                }
                            }
                            ZLabel { text: "SNR" }
                            Column {
                                Layout.fillWidth: true
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
                                    Tick { text: "30 dB" }
                                }
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
                    const d = inst && win.selectedKhz > 0 ? inst.detail : {}
                    const up = s.uptime_s || 0
                    return [["Load", s.cpu !== undefined && s.cpu !== null ? s.cpu + "%" : "-"],
                            ["Memory", s.rss_mb ? s.rss_mb + " MB" : "-"],
                            ["Bins", s.bins !== undefined ? s.bins : "-"],
                            ["Selected", d.wpm ? Math.round(d.wpm) + " WPM" : "-"],
                            ["Spots", s.spots !== undefined ? s.spots : "-"],
                            ["Decoder", inst && inst.info.decoder ? inst.info.decoder : "-"],
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
        contentItem: ColumnLayout {
            spacing: 14
            Text { text: "Settings"; color: t.fg; font.family: t.display; font.pixelSize: 15; font.weight: Font.DemiBold }
            Rectangle {
                visible: console_.demo
                Layout.fillWidth: true; Layout.topMargin: -6; implicitHeight: hintTxt.implicitHeight + 16; radius: 8; color: t.sunken
                Text {
                    id: hintTxt; x: 10; width: parent.width - 20; anchors.verticalCenter: parent.verticalCenter; wrapMode: Text.WordWrap
                    text: "Demo mode is on. Turn it off to add or connect to a SparkGap server."; color: t.muted; font.pixelSize: 12
                }
            }
            RowLayout {
                spacing: 10
                Text { Layout.preferredWidth: 92; text: "DEMO"; color: t.muted; font.pixelSize: 11; font.letterSpacing: 1 }
                Seg { labels: ["Off", "On"]; current: console_.demo ? 1 : 0; onPicked: (i) => console_.setDemo(i === 1) }
                Text { text: "Made-up signals instead of your servers."; color: t.faint; font.pixelSize: 11 }
            }
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
                visible: !console_.demo
                label: "CONNECTIONS"
                // the SparkGap servers this console watches, kept between runs; a click on a row shows that one
                ColumnLayout {
                    id: conns
                    width: 444; spacing: 4
                    property int editing: -2  // -2 no form, -1 adding, else the row being edited
                    property string err: ""
                    function edit(i) {
                        const f = i >= 0 ? console_.feedAt(i) : { address: "", port: 7320, token: "" }
                        addr.text = f.address; port.text = String(f.port); tok.text = f.token
                        err = ""; editing = i; addr.forceActiveFocus()
                    }
                    function save() {
                        const e = console_.saveFeed(editing, addr.text, parseInt(port.text) || 0, tok.text)
                        if (e) err = e; else editing = -2
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
                            TapHandler { onTapped: console_.setCurrent(index) }
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
                                Btn { text: "✕"; onClicked: { console_.removeFeed(index); conns.editing = -2 } }
                            }
                        }
                    }
                    Text {
                        visible: !console_.instances.some(i => !i.demo) && conns.editing === -2
                        text: "No servers yet."; color: t.muted; font.pixelSize: 12
                    }
                    ColumnLayout {
                        visible: conns.editing > -2
                        Layout.fillWidth: true; Layout.topMargin: 6; spacing: 6
                        Field { id: addr; placeholderText: "Address: tailnet name or IP"; onAccepted: conns.save() }
                        Field {
                            id: port; placeholderText: "Port"; onAccepted: conns.save()
                            validator: IntValidator { bottom: 1; top: 65535 }
                        }
                        Field {
                            id: tok; placeholderText: "Join code, e.g. XXX-XXX"; onAccepted: conns.save()
                            font.capitalization: Font.AllUppercase
                        }
                        Text { visible: !!conns.err; text: conns.err; color: t.bad; font.pixelSize: 12 }
                        Row {
                            spacing: 8
                            Btn { text: conns.editing === -1 ? "Add" : "Save"; onClicked: conns.save() }
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
                GridLabel { text: "VALIDATION" }
                Seg { readOnly: true; labels: ["off", "permissive", "normal", "aggressive"]; current: labels.indexOf(settings.c.spot_validation || (settings.c.spot_rule ? "off" : "")) }
                GridLabel { text: "CALL SOURCE" }
                Seg { readOnly: true; labels: ["SCP", "RBN", "RBN + SKCC"]; current: ["scp", "rbn", "rbn+skcc"].indexOf(settings.c.spot_validation_calls) }
                Item { width: 1; height: 1 }
                Text { text: "From the selected server's config."; color: t.faint; font.pixelSize: 11 }
            }
            RowLayout {
                Layout.fillWidth: true
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
                    width: matchCol.width; height: 28; radius: 6
                    color: mh.hovered ? t.sunken : "transparent"
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
    function goTo(m) {
        const span = wf.highKhz - wf.lowKhz
        wf.setView(m.khz - span / 2, m.khz + span / 2)
        let khz = m.khz, call = m.call
        if (!call && inst) {
            const near = inst.nearestSignal(m.khz, 0.5)
            if (near > 0) khz = near
        }
        pick(khz, call)
        search.focus = false
    }

    function pick(khz, call) {
        selectedKhz = khz
        wf.setSelected(khz)
        if (inst) inst.select(khz, call)
    }
}
