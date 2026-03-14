import QtQuick 2.15
import QtQuick.Controls 2.15
import QtQuick.Layouts 1.15
import QtQuick.Dialogs

ApplicationWindow {
    id: root
    width: 1360
    height: 900
    visible: true
    visibility: Window.FullScreen
    title: "Questionario"
    color: "#eef2ff"

    property var chapters: []
    property var selectedChapters: []
    property var quizItems: []
    property var statsItems: []
    property var wrongStatsItems: []
    property string selectedStatId: ""
    property string selectedWrongStatId: ""
    property var percentProfiles: []
    property var percentMapData: ({})
    property bool inQuizMode: quizItems.length > 0
    property int answeredCount: 0
    property bool showUrlBox: false
    property bool showPasteBox: false
    property bool examModeEnabled: false
    property int quizElapsedSec: 0
    property int quizRemainingSec: 1800
    property int examNextCheckSec: 120
    property bool examModeLocked: false
    property var currentSnapshotItems: []
    property string snapshotAiText: ""
    property bool isLoading: false
    property string easterTyped: ""
    property int easterTapCount: 0
    property string quizLaunchMode: "base"
    property int quizRequestCount: 30
    property bool quizRequestAll: false
    property bool snapshotFromWrongContext: false
    property bool correctionMode: false
    property bool hasDataset: false
    property bool sessionDatasetChosen: false
    property bool userInitiatedLoad: false
    property bool datasetSelectionInProgress: false
    property bool showMainArea: false
    property real entranceProgress: 0.0
    property string lastDatasetName: ""
    property var correctionDetails: []
    property string pendingPdfPath: ""
    component FancyButton: Button {
        id: fb
        property color textColor: "#ffffff"
        font.bold: true
        font.pixelSize: 15
        implicitHeight: 44
        leftPadding: 16
        rightPadding: 16
        background: Rectangle {
            radius: 11
            border.width: 1
            border.color: "#0369a1"
            gradient: Gradient {
                orientation: Gradient.Vertical
                GradientStop { position: 0.0; color: fb.down ? "#0369a1" : (fb.hovered ? "#0ea5e9" : "#38bdf8") }
                GradientStop { position: 1.0; color: fb.down ? "#075985" : (fb.hovered ? "#0284c7" : "#0ea5e9") }
            }
            Rectangle {
                anchors.left: parent.left
                anchors.right: parent.right
                anchors.bottom: parent.bottom
                anchors.margins: 1
                height: 4
                radius: 2
                color: fb.down ? "#075985" : "#0369a1"
                opacity: 0.45
            }
        }
        contentItem: Text {
            text: fb.text
            color: fb.textColor
            horizontalAlignment: Text.AlignHCenter
            verticalAlignment: Text.AlignVCenter
            font.bold: true
            font.pixelSize: fb.font.pixelSize
            elide: Text.ElideRight
        }
    }

    function snapshotWrongExplain(item) {
        const choices = item && item.choices ? item.choices : []
        var sidx = -1
        var cidx = -1
        for (var i = 0; i < choices.length; i++) {
            if (choices[i] && choices[i].selected) sidx = i
            if (choices[i] && choices[i].correct) cidx = i
        }
        var selectedTxt = (sidx >= 0 && choices[sidx]) ? (choices[sidx].text || "") : "Nessuna risposta"
        var correctTxt = (cidx >= 0 && choices[cidx]) ? (choices[cidx].text || "") : "N/D"
        return "Domanda: " + (item.question || "") + "\n\n" +
               "Risposta selezionata: " + selectedTxt + "\n" +
               "Risposta corretta: " + correctTxt + "\n\n" +
               "Analisi: la scelta selezionata non coincide con la risposta corretta. Rivedi il capitolo '" + (item.chapter || "Generale") + "'."
    }
    function jsonStemLabel(name) {
        var s = String(name || "")
        s = s.replace(/\s*\[\s*errori\s*\]\s*$/i, "")
        s = s.replace(/\.[^.]+$/, "")
        return s.trim()
    }

    function recomputeAnswered() {
        var c = 0
        for (var i = 0; i < quizItems.length; i++) {
            var card = quizRepeater.itemAt(i)
            if (card && card.choiceGroup && card.choiceGroup.checkedButton) c += 1
        }
        answeredCount = c
    }

    function triggerMainEntrance() {
        if (!(hasDataset || inQuizMode)) {
            showMainArea = false
            entranceProgress = 0.0
            return
        }
        sessionDatasetChosen = true
        showMainArea = true
        entranceProgress = 0.0
        mainEntranceAnim.restart()
    }

    function refreshFromBridge() {
        try { chapters = JSON.parse(quizBridge.chaptersJson || "[]") } catch (e) { chapters = [] }
        try { selectedChapters = JSON.parse(quizBridge.selectedChaptersJson || "[]") } catch (e) { selectedChapters = [] }
        try { quizItems = JSON.parse(quizBridge.quizJson || "[]") } catch (e) { quizItems = [] }
        try { statsItems = JSON.parse(quizBridge.statsJson || "[]") } catch (e) { statsItems = [] }
        try { wrongStatsItems = JSON.parse(quizBridge.wrongStatsJson || "[]") } catch (e) { wrongStatsItems = [] }
        try { percentProfiles = Object.keys(JSON.parse(quizBridge.profilesJson || "{}")) } catch (e) { percentProfiles = [] }
        try { percentMapData = JSON.parse(quizBridge.percentMapJson || "{}") } catch (e) { percentMapData = ({}) }

        var ds = String(quizBridge.datasetName || "")
        hasDataset = ds.length > 0
        lastDatasetName = ds

        if (hasDataset && !quizBridge.isLoading && !datasetSelectionInProgress) {
            sessionDatasetChosen = true
        }

        if (!datasetSelectionInProgress && (inQuizMode || hasDataset)) {
            showMainArea = true
        } else {
            showMainArea = false
            if (!inQuizMode) entranceProgress = 0.0
        }

        answeredCount = 0
        Qt.callLater(recomputeAnswered)
    }

    Component.onCompleted: refreshFromBridge()

    NumberAnimation {
        id: mainEntranceAnim
        target: root
        property: "entranceProgress"
        from: 0.0
        to: 1.0
        duration: 1450
        easing.type: Easing.OutCubic
    }


    Shortcut { sequence: "E,N,Z,O"; onActivated: easterDialog.open() }
    Shortcut { sequence: "Ctrl+Shift+E"; onActivated: easterDialog.open() }
    Shortcut { sequence: "Alt+Shift+E"; onActivated: easterDialog.open() }

    Connections {
        target: quizBridge
        function onChaptersChanged() { refreshFromBridge() }
        function onSelectedChaptersChanged() { refreshFromBridge() }
        function onQuizChanged() { refreshFromBridge(); Qt.callLater(recomputeAnswered) }
        function onStatsChanged() {
            refreshFromBridge()
            var found = false
            for (var i = 0; i < statsItems.length; i++) {
                if ((statsItems[i].id || "") === selectedStatId) { found = true; break }
            }
            if (!found) selectedStatId = ""
        }
        function onWrongStatsChanged() {
            refreshFromBridge()
            var found = false
            var sel = String(selectedWrongStatId || "")
            for (var i = 0; i < wrongStatsItems.length; i++) {
                if (String((wrongStatsItems[i].id || "")) === sel) { found = true; break }
            }
            if (!found) {
                if (wrongStatsItems.length > 0)
                    selectedWrongStatId = String((wrongStatsItems[0].id || ""))
                else
                    selectedWrongStatId = ""
            }
        }
        function onPercentModeChanged() { }
        function onProfilesChanged() { refreshFromBridge() }
        function onDatasetNameChanged() {
            refreshFromBridge()
            var hasDataName = String(quizBridge.datasetName || "").length > 0
            if (!inQuizMode && !quizBridge.isLoading && hasDataName) {
                sessionDatasetChosen = true
                userInitiatedLoad = false
                datasetSelectionInProgress = false
                triggerMainEntrance()
            }
        }
        function onIsLoadingChanged() {
            // Sincronizza l'overlay di caricamento con i worker QThread di Python
            if (quizBridge.isLoading) isLoading = true
        }
        function onStatusChanged() {
            if (!quizBridge.isLoading) isLoading = false
            var hasDataName = String(quizBridge.datasetName || "").length > 0
            var loadFinished = !quizBridge.isLoading

            // Evita refresh/animazioni della pagina principale su status non legati al load
            // (snapshot, stampa, operazioni statistiche, ecc.).
            if (userInitiatedLoad && loadFinished) {
                userInitiatedLoad = false
                datasetSelectionInProgress = false
                showMainArea = (inQuizMode || hasDataName || hasDataset)
                if (!inQuizMode && hasDataName) {
                    sessionDatasetChosen = true
                    triggerMainEntrance()
                }
            }
        }
        function onSnapshotChanged() {
            try { currentSnapshotItems = JSON.parse(quizBridge.snapshotJson || "[]") } catch (e) { currentSnapshotItems = [] }
            snapshotDialog.open()
        }
        function onAiResultChanged() {
            snapshotAiText = quizBridge.aiResultText || ""
            if (snapshotAiText && snapshotAiText.length > 0) snapshotAiDialog.open()
        }
        function onWrongPayloadChanged() {
            wrongPayloadDialog.open()
        }
    }

    FileDialog {
        id: fileDialog
        title: "Seleziona JSON"
        nameFilters: ["JSON files (*.json)"]
        onAccepted: {
            // Su Windows Qt restituisce file:///C:/... — rimuovere 3 slash, non 2
            let raw = selectedFile.toString()
            const p = Qt.platform.os === "windows"
                ? decodeURIComponent(raw.replace(/^file:\/\/\//, ""))
                : decodeURIComponent(raw.replace(/^file:\/\//, ""))
            if (!p || p.length === 0) {
                isLoading = false
                return
            }
            userInitiatedLoad = true
            quizBridge.loadFromFile(p)
        }
        onRejected: {
            isLoading = false
            datasetSelectionInProgress = false
            showMainArea = (inQuizMode || (hasDataset && sessionDatasetChosen))
        }
    }
    FileDialog {
        id: pdfFileDialog
        title: "Seleziona PDF dispense"
        nameFilters: ["PDF files (*.pdf)"]
        onAccepted: {
            // Su Windows Qt restituisce file:///C:/... — rimuovere 3 slash, non 2
            let raw = selectedFile.toString()
            const p = Qt.platform.os === "windows"
                ? decodeURIComponent(raw.replace(/^file:\/\/\//, ""))
                : decodeURIComponent(raw.replace(/^file:\/\//, ""))
            if (!p || p.length === 0) { isLoading = false; return }
            pendingPdfPath = p
            pdfModeDialog.open()
        }
        onRejected: {
            isLoading = false
            datasetSelectionInProgress = false
            showMainArea = (inQuizMode || (hasDataset && sessionDatasetChosen))
        }
    }
    Dialog {
        id: pdfModeDialog
        modal: true
        anchors.centerIn: Overlay.overlay
        width: 420
        height: 230
        standardButtons: Dialog.NoButton
        background: Rectangle { radius: 12; color: "#ffffff"; border.width: 1; border.color: "#c7d2fe" }
        ColumnLayout {
            anchors.fill: parent
            anchors.margins: 14
            spacing: 10
            Text { text: "Modalita generazione PDF"; font.pixelSize: 22; font.bold: true; color: "#1b2d2b" }
            Text { text: "Scegli Quick (test rapido) o Complete (elaborazione piena)."; wrapMode: Text.WordWrap; color: "#355"; font.pixelSize: 14 }
            RowLayout {
                Layout.fillWidth: true
                spacing: 8
                FancyButton {
                    text: "Quick"
                    Layout.fillWidth: true
                    onClicked: {
                        pdfModeDialog.close()
                        if (!pendingPdfPath || pendingPdfPath.length === 0) return
                        userInitiatedLoad = true
                        datasetSelectionInProgress = true
                        showMainArea = false
                        isLoading = true
                        quizBridge.loadFromPdfMode(pendingPdfPath, "quick")
                    }
                }
                FancyButton {
                    text: "Complete"
                    Layout.fillWidth: true
                    onClicked: {
                        pdfModeDialog.close()
                        if (!pendingPdfPath || pendingPdfPath.length === 0) return
                        userInitiatedLoad = true
                        datasetSelectionInProgress = true
                        showMainArea = false
                        isLoading = true
                        quizBridge.loadFromPdfMode(pendingPdfPath, "complete")
                    }
                }
            }
            Item { Layout.fillHeight: true }
            RowLayout {
                Layout.fillWidth: true
                Item { Layout.fillWidth: true }
                FancyButton {
                    text: "Annulla"
                    onClicked: {
                        pendingPdfPath = ""
                        pdfModeDialog.close()
                        isLoading = false
                        datasetSelectionInProgress = false
                        showMainArea = (inQuizMode || (hasDataset && sessionDatasetChosen))
                    }
                }
            }
        }
    }
    Rectangle {
        anchors.fill: parent
        color: "#dfe7ff"
    }

    Image {
        anchors.fill: parent
        source: "images/algo.png"
        fillMode: Image.PreserveAspectFit
        smooth: true
        mipmap: true
        opacity: (!hasDataset && !inQuizMode) ? 0.22 : 0.0
        visible: true
        Behavior on opacity { NumberAnimation { duration: 260; easing.type: Easing.OutCubic } }
    }

    Rectangle {
        anchors.fill: parent
        gradient: Gradient {
            orientation: Gradient.Vertical
            GradientStop { position: 0.0; color: "#eef2ff" }
            GradientStop { position: 1.0; color: "#dfe7ff" }
        }
        opacity: (!hasDataset && !inQuizMode) ? 0.45 : 0.92
        Behavior on opacity { NumberAnimation { duration: 260; easing.type: Easing.OutCubic } }
    }

    ColumnLayout {

        anchors.fill: parent
        anchors.margins: 22
        spacing: 10

        Rectangle {
            Layout.fillWidth: true
            implicitHeight: 140
            radius: 16
            color: (hasDataset || inQuizMode) ? "#e6f1ff" : "transparent"
            border.color: (hasDataset || inQuizMode) ? "#c7d2fe" : "transparent"
            RowLayout {
                anchors.fill: parent
                anchors.margins: 12
                spacing: 12

                Rectangle {
                    Layout.preferredWidth: 120
                    Layout.preferredHeight: 120
                    visible: (hasDataset || inQuizMode)
                    radius: 10
                    color: "transparent"
                    border.color: "transparent"
                    Image {
                        anchors.fill: parent
                        anchors.margins: 2
                        source: "images/algo.png"
                        fillMode: Image.PreserveAspectFit
                        smooth: true
                        mipmap: true
                    }
                }

                ColumnLayout {
                    Layout.fillWidth: true
                    spacing: 2

                    Text {
                        text: "Questionario"
                        Layout.fillWidth: true
                        horizontalAlignment: Text.AlignHCenter
                        color: "#0f3b62"
                        font.pixelSize: 54
                        font.bold: true
                        MouseArea {
                            anchors.fill: parent
                            acceptedButtons: Qt.LeftButton | Qt.RightButton
                            onClicked: {
                                if (mouse.button === Qt.RightButton) {
                                    easterDialog.open()
                                    return
                                }
                                easterTapCount += 1
                                if (easterTapCount >= 10) {
                                    easterTapCount = 0
                                    easterDialog.open()
                                }
                            }
                            onDoubleClicked: easterDialog.open()
                        }
                    }

                    RowLayout {
                        Layout.alignment: Qt.AlignHCenter
                        visible: !hasDataset && !inQuizMode
                        spacing: 8
                        Text {
                            text: "Copyright 2026 AlgoTeam"
                            color: "#1f3f66"
                            font.pixelSize: 17
                            font.bold: true
                        }
                    }

                    Text {
                        Layout.fillWidth: true
                        visible: (quizBridge.datasetName || "").length > 0
                        text: jsonStemLabel(quizBridge.datasetName)
                        horizontalAlignment: Text.AlignHCenter
                        verticalAlignment: Text.AlignVCenter
                        color: "#1f3f66"
                        font.pixelSize: 30
                        font.bold: true
                        elide: Text.ElideRight
                    }
                }

                Rectangle {
                    Layout.preferredWidth: 84
                    Layout.preferredHeight: 84
                    visible: (hasDataset || inQuizMode)
                    radius: 10
                    color: "transparent"
                    border.color: "transparent"
                    Image {
                        anchors.fill: parent
                        anchors.margins: 4
                        source: "images/logo.jpg"
                        fillMode: Image.PreserveAspectFit
                        smooth: true
                        mipmap: true
                    }
                }
            }
        }

        Rectangle {
            Layout.fillWidth: true
            visible: !inQuizMode
            implicitHeight: showUrlBox || showPasteBox ? 180 : 96
            radius: 16
            color: "#ffffff"
            border.color: "#c7d2fe"
            Behavior on implicitHeight { NumberAnimation { duration: 140 } }

            ColumnLayout {
                anchors.fill: parent
                anchors.margins: 12
                spacing: 8

                RowLayout {
                    id: sourceButtonsRow
                    Layout.fillWidth: true
                    spacing: 10

                    FancyButton {
                        id: chooseFileBtn
                        text: "Scegli file"
                        Layout.fillWidth: true
                        Layout.minimumWidth: 170
                        Layout.preferredHeight: 48
                        font.pixelSize: 19
                        font.bold: true
                        onClicked: { showUrlBox = false; showPasteBox = false; datasetSelectionInProgress = true; showMainArea = false; isLoading = true; fileDialog.open() }
                    }
                    FancyButton {
                        id: loadPdfBtn
                        text: "PDF \u2192 Quiz"
                        Layout.fillWidth: true
                        Layout.minimumWidth: 170
                        Layout.preferredHeight: 48
                        font.pixelSize: 19
                        font.bold: true
                        enabled: (quizBridge.aiKey || "").trim().length > 0
                        opacity: enabled ? 1.0 : 0.5
                        ToolTip.visible: hovered && !enabled
                        ToolTip.text: "Richiede chiave OpenAI (Easter Egg)"
                        onClicked: {
                            showUrlBox = false; showPasteBox = false
                            datasetSelectionInProgress = true
                            showMainArea = false; isLoading = true
                            pdfFileDialog.open()
                        }
                    }
                    FancyButton {
                        id: loadUrlBtn
                        text: "Carica URL"
                        Layout.fillWidth: true
                        Layout.minimumWidth: 170
                        Layout.preferredHeight: 48
                        font.pixelSize: 19
                        font.bold: true
                        onClicked: { showUrlBox = !showUrlBox; if (showUrlBox) showPasteBox = false }
                    }
                    FancyButton {
                        text: "Usa JSON"
                        Layout.fillWidth: true
                        Layout.minimumWidth: 170
                        Layout.preferredHeight: 48
                        font.pixelSize: 19
                        font.bold: true
                        onClicked: { showPasteBox = !showPasteBox; if (showPasteBox) showUrlBox = false }
                    }
                    FancyButton {
                        text: "Cloud Picker"
                        Layout.fillWidth: true
                        Layout.minimumWidth: 170
                        Layout.preferredHeight: 48
                        font.pixelSize: 19
                        font.bold: true
                        onClicked: {
                            if (quizBridge.cloudLogged) {
                                cloudPickerDialog.cloudEntries = []
                                cloudPickerDialog.open()
                                isLoading = true
                                quizBridge.cloudLoadEntries(quizBridge.cloudManifestUrl || "")
                            } else {
                                cloudLoginDialog.open()
                            }
                        }
                    }
                }

                Rectangle {
                    Layout.fillWidth: true
                    visible: showUrlBox
                    implicitHeight: showUrlBox ? 62 : 0
                    radius: 10
                    color: "#ececec"
                    border.color: "#c7d2fe"
                    RowLayout {
                        anchors.fill: parent
                        anchors.margins: 8
                        spacing: 8
                        TextField {
                            id: urlField
                            Layout.fillWidth: true
                            placeholderText: "https://.../questions.json"
                            font.pixelSize: 15
                        }
                        FancyButton {
                            text: "Conferma URL"
                            onClicked: {
                                isLoading = true
                                userInitiatedLoad = true
                                datasetSelectionInProgress = true
                                showMainArea = false
                                quizBridge.loadFromUrl(urlField.text)
                                showUrlBox = false
                            }
                        }
                    }
                }

                Rectangle {
                    Layout.fillWidth: true
                    visible: showPasteBox
                    implicitHeight: showPasteBox ? 86 : 0
                    radius: 10
                    color: "#fff"
                    border.color: "#c7d2fe"
                    ColumnLayout {
                        anchors.fill: parent
                        anchors.margins: 8
                        spacing: 6
                        TextArea {
                            id: pasteArea
                            Layout.fillWidth: true
                            Layout.fillHeight: true
                            placeholderText: "Incolla qui il JSON"
                            wrapMode: TextArea.Wrap
                            font.pixelSize: 12
                        }
                        FancyButton {
                            text: "Carica JSON incollato"
                            Layout.alignment: Qt.AlignRight
                            onClicked: {
                                isLoading = true
                                userInitiatedLoad = true
                                datasetSelectionInProgress = true
                                showMainArea = false
                                quizBridge.loadFromPaste(pasteArea.text)
                                showPasteBox = false
                            }
                        }
                    }
                }
            }
        }

        Rectangle {
            id: homeStatusStrip
            visible: !showMainArea && !inQuizMode
            Layout.fillWidth: true
            implicitHeight: 50
            radius: 12
            color: "#ffffff"
            border.color: "#c7d2fe"

            Text {
                anchors.fill: parent
                anchors.margins: 12
                text: "Stato: " + (quizBridge.status || "Pronto")
                color: "#31403d"
                font.pixelSize: 15
                verticalAlignment: Text.AlignVCenter
                elide: Text.ElideRight
            }
        }

        RowLayout {
            id: panelsRow
            visible: showMainArea
            opacity: Math.max(0.0, Math.min(1.0, entranceProgress))
            Layout.fillWidth: true
            Layout.fillHeight: true
            spacing: 10
            transform: Translate {
                y: (1.0 - entranceProgress) * 340
            }

            Rectangle {
                Layout.fillWidth: true
                Layout.fillHeight: true
                clip: true
                radius: 16
                color: "#ffffff"
                border.color: "#c7d2fe"
                ColumnLayout {
                    anchors.fill: parent
                    anchors.margins: 12
                    spacing: 8

                    Text {
                        visible: !inQuizMode
                        text: "Capitoli"
                        font.pixelSize: 22
                        font.bold: true
                        color: "#1b2d2b"
                    }

                    RowLayout {
                        Layout.fillWidth: true
                        spacing: 10
                        visible: !inQuizMode
                        FancyButton {
                            visible: !inQuizMode
                            Layout.preferredHeight: 36
                            Layout.preferredWidth: chooseFileBtn.width
                            Layout.minimumWidth: chooseFileBtn.width
                            Layout.maximumWidth: chooseFileBtn.width
                            font.pixelSize: 14
                            font.bold: true
                            text: (chapters.length > 0 && selectedChapters.length === chapters.length) ? "Deseleziona" : "Seleziona"
                            onClicked: {
                                if (chapters.length === 0) return
                                if (selectedChapters.length === chapters.length)
                                    selectedChapters = []
                                else
                                    selectedChapters = chapters.slice(0)
                                quizBridge.setSelectedChapters(JSON.stringify(selectedChapters))
                            }
                        }
                        FancyButton {
                            id: percentModeBtn
                            visible: !inQuizMode
                            Layout.preferredHeight: 36
                            Layout.preferredWidth: loadUrlBtn.width
                            Layout.minimumWidth: loadUrlBtn.width
                            Layout.maximumWidth: loadUrlBtn.width
                            font.pixelSize: 14
                            font.bold: true
                            text: quizBridge.percentMode ? "Distribuzione percentuale (ON)" : "Distribuzione percentuale (OFF)"
                            onClicked: {
                                const next = !quizBridge.percentMode
                                quizBridge.setPercentMode(next)
                                if (next) percentDialog.open()
                            }
                        }
                        Item { Layout.fillWidth: true }
                    }

                    Rectangle {
                        id: chapterBox
                        Layout.fillWidth: true
                        Layout.preferredHeight: 240
                        visible: !inQuizMode
                        radius: 10
                        color: "#eef8ef"
                        border.color: "#c7d2fe"

                        ScrollView {
                            id: chapterScroll
                            anchors.fill: parent
                            anchors.margins: 8
                            clip: true
                            ScrollBar.vertical.policy: ScrollBar.AlwaysOn
                            ScrollBar.horizontal.policy: ScrollBar.AsNeeded

                            Flow {
                                id: chapterFlow
                                width: Math.max(320, chapterScroll.availableWidth > 0 ? chapterScroll.availableWidth - 6 : chapterBox.width - 20)
                                spacing: 8
                                property int cols: width >= 980 ? 3 : (width >= 620 ? 2 : 1)

                                Repeater {
                                    model: chapters
                                    delegate: Rectangle {
                                        width: Math.max(220, Math.floor((chapterFlow.width - (chapterFlow.cols - 1) * chapterFlow.spacing) / chapterFlow.cols))
                                        implicitHeight: 36
                                        radius: 8
                                        color: "#f7f7f7"
                                        border.color: "#c7d2fe"

                                        CheckBox {
                                            anchors.fill: parent
                                            anchors.margins: 6
                                            text: (typeof modelData === "string") ? modelData : (modelData.text || modelData.answer || modelData.risposta || "")
                                            font.pixelSize: 15
                                            checked: selectedChapters.indexOf(modelData) >= 0
                                            onToggled: {
                                                const idx = selectedChapters.indexOf(modelData)
                                                if (checked && idx < 0) selectedChapters.push(modelData)
                                                if (!checked && idx >= 0) selectedChapters.splice(idx, 1)
                                                quizBridge.setSelectedChapters(JSON.stringify(selectedChapters))
                                            }
                                        }
                                    }
                                }
                            }
                        }
                    }

                    RowLayout {
                        Layout.fillWidth: true
                        spacing: 10
                                                ColumnLayout {
                            visible: !inQuizMode
                            spacing: 6
                            Layout.alignment: Qt.AlignTop
                            FancyButton {
                                text: quizBridge.datasetName ? ("Quiz " + jsonStemLabel(quizBridge.datasetName)) : "Quiz"
                                Layout.preferredHeight: 46
                                Layout.preferredWidth: 230
                                font.pixelSize: 15
                                font.bold: true
                                onClicked: {
                                    quizLaunchMode = "base"
                                    quizRequestAll = false
                                    const poolNow = Number(quizBridge.prepareBasePoolForPopup() || 0)
                                    quizRequestCount = Math.max(1, Math.min(30, poolNow > 0 ? poolNow : 30))
                                    quizCountDialog.open()
                                }
                            }
                            FancyButton {
                                checkable: true
                                checked: examModeEnabled
                                text: checked ? "Modalità esame ON" : "Modalità esame"
                                textColor: checked ? "#ff3b30" : "#ffffff"
                                enabled: !inQuizMode && !examModeLocked
                                Layout.preferredHeight: 46
                                Layout.preferredWidth: 230
                                Layout.topMargin: 6
                                font.pixelSize: 15
                                font.bold: true
                                onClicked: examModeEnabled = checked
                            }
                        }
                        FancyButton {
                            text: "Quiz errori (" + quizBridge.wrongCount + ")"
                            visible: !inQuizMode
                            Layout.alignment: Qt.AlignTop
                            Layout.preferredHeight: 46
                            Layout.preferredWidth: 180
                            font.pixelSize: 15
                            font.bold: true
                            enabled: Number(quizBridge.wrongCount || 0) > 0
                            onClicked: {
                                quizLaunchMode = "wrong"
                                quizRequestAll = false
                                const wrongNow = Number(quizBridge.prepareWrongPoolForPopup() || 0)
                                quizRequestCount = Math.max(1, Math.min(30, wrongNow > 0 ? wrongNow : 30))
                                quizCountDialog.open()
                            }
                        }
                        FancyButton {
                            text: "Stampa errori"
                            visible: !inQuizMode
                            Layout.alignment: Qt.AlignTop
                            Layout.preferredHeight: 46
                            font.pixelSize: 15
                            font.bold: true
                            enabled: Number(quizBridge.wrongCount || 0) > 0
                            onClicked: quizBridge.printWrongPool()
                        }
                        FancyButton {
                            text: "Indietro"
                            visible: inQuizMode
                            Layout.preferredHeight: 46
                            font.pixelSize: 15
                            font.bold: true
                            onClicked: {
                                correctionMode = false
                                correctionDetails = []
                                quizBridge.clearQuiz()
                                quizTimer.stop()
                                answeredCount = 0
                                examModeLocked = false
                            }
                        }
                        FancyButton {
                            text: "Correggi tutto"
                            visible: inQuizMode
                            Layout.preferredHeight: 46
                            font.pixelSize: 15
                            font.bold: true
                            enabled: quizItems.length > 0 && answeredCount >= quizItems.length && (!examModeEnabled || quizElapsedSec >= 900)
                            onClicked: {
                                var ans = []
                                for (var i = 0; i < quizItems.length; i++) {
                                    const card = quizRepeater.itemAt(i)
                                    const bg = card ? card.choiceGroup : null
                                    ans.push((bg && bg.checkedButton) ? bg.checkedButton.choiceIndex : -1)
                                }
                                quizBridge.correctAll(JSON.stringify(ans))
                            }
                        }
                        Rectangle {
                            visible: inQuizMode
                            radius: 8
                            color: "#eef6ff"
                            border.color: "#c7d2fe"
                            implicitHeight: 36
                            implicitWidth: 190
                            Text {
                                anchors.centerIn: parent
                                text: "Risposte " + answeredCount + "/" + quizItems.length
                                color: "#0b4b85"
                                font.pixelSize: 15
                                font.bold: true
                            }
                        }
                        Rectangle {
                            visible: inQuizMode
                            radius: 8
                            color: "#f0f9f1"
                            border.color: "#c7d2fe"
                            implicitHeight: 36
                            implicitWidth: 240
                            Text {
                                anchors.centerIn: parent
                                text: "Timer " + Math.floor(quizElapsedSec/60).toString().padStart(2,'0') + ":" + (quizElapsedSec%60).toString().padStart(2,'0') + " / " + Math.floor(quizRemainingSec/60).toString().padStart(2,'0') + ":" + (quizRemainingSec%60).toString().padStart(2,'0')
                                color: "#1f5a2a"
                                font.pixelSize: 15
                                font.bold: true
                            }
                        }
                        Item { Layout.fillWidth: true }
                    }

                    Rectangle {
                        visible: !inQuizMode && (quizBridge.datasetName || "").length > 0
                        Layout.fillWidth: true
                        Layout.preferredHeight: 110
                        Layout.minimumHeight: 90
                        Layout.maximumHeight: 125
                        clip: true
                        radius: 10
                        color: "#ffffff"
                        border.color: "#c7d2fe"
                        ColumnLayout {
                            anchors.fill: parent
                            anchors.margins: 8
                            spacing: 6
                            Text { text: "Statistiche Errori"; font.pixelSize: 15; font.bold: true; color: "#7f1d1d" }
                            ScrollView {
                                id: wrongStatsScroll
                                Layout.fillWidth: true
                                Layout.fillHeight: true
                                clip: true
                                ScrollBar.vertical.policy: ScrollBar.AlwaysOn
                                ScrollBar.horizontal.policy: ScrollBar.AsNeeded
                                Grid {
                                    id: wrongStatsGrid
                                    width: Math.max(300, (wrongStatsScroll.availableWidth > 0 ? wrongStatsScroll.availableWidth : wrongStatsScroll.width) - 6)
                                    columns: 3
                                    flow: Grid.LeftToRight
                                    spacing: 8

                                    Repeater {
                                        model: wrongStatsItems
                                        delegate: Rectangle {
                                            width: Math.floor((wrongStatsGrid.width - ((wrongStatsGrid.columns - 1) * wrongStatsGrid.spacing)) / wrongStatsGrid.columns)
                                            height: 42
                                            radius: 8
                                            readonly property bool rowSelected: (String(selectedWrongStatId || "") === String(modelData.id || ""))
                                            color: rowSelected ? "#bfe3ff" : (wrongStatRowMouse.containsMouse ? "#eef7ff" : "#fff")
                                            border.color: rowSelected ? "#1d9bf0" : (wrongStatRowMouse.containsMouse ? "#6fbef7" : "#000000")
                                            MouseArea {
                                                id: wrongStatRowMouse
                                                anchors.fill: parent
                                                hoverEnabled: true
                                                onClicked: {
                                                    selectedWrongStatId = String(modelData.id || "")
                                                    root.snapshotFromWrongContext = true
                                                    isLoading = true
                                                    quizBridge.openStatSnapshot(selectedWrongStatId)
                                                }
                                            }
                                            RowLayout {
                                                anchors.fill: parent
                                                anchors.margins: 6
                                                spacing: 6
                                                Text {
                                                    Layout.fillWidth: true
                                                    text: modelData.date || ""
                                                    color: "#1b2d2b"
                                                    font.pixelSize: 11
                                                    font.bold: true
                                                    elide: Text.ElideRight
                                                }
                                                Text {
                                                    text: (modelData.correct || 0) + "/" + (modelData.total || 0) + " (" + (modelData.pct || 0) + "%)"
                                                    color: "#586765"
                                                    font.pixelSize: 11
                                                }
                                                Rectangle {
                                                    width: 20
                                                    height: 20
                                                    radius: 10
                                                    color: "#fff1f1"
                                                    border.color: "#e2b1b1"
                                                    Layout.alignment: Qt.AlignVCenter
                                                    Text {
                                                        anchors.centerIn: parent
                                                        text: "X"
                                                        color: "#c62828"
                                                        font.pixelSize: 11
                                                        font.bold: true
                                                    }
                                                    MouseArea {
                                                        anchors.fill: parent
                                                        onClicked: {
                                                            const id = String(modelData.id || "")
                                                            confirmDialog.message = "Eliminare questa statistica?"
                                                            confirmDialog.onConfirmed = function() {
                                                                if (String(selectedWrongStatId || "") === id) selectedWrongStatId = ""
                                                                quizBridge.deleteStat(id)
                                                            }
                                                            confirmDialog.open()
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

                    Text {
                        visible: inQuizMode
                        text: "Completate " + answeredCount + " su " + quizItems.length + " domande"
                        color: "#586765"
                        font.pixelSize: 14
                        font.bold: true
                    }

                    ScrollView {
                        id: quizScroll
                        Layout.fillWidth: true
                        Layout.fillHeight: true
                        clip: true
                        ScrollBar.vertical.policy: ScrollBar.AlwaysOn
                        ScrollBar.horizontal.policy: ScrollBar.AsNeeded

                        Column {
                            width: (quizScroll.availableWidth && quizScroll.availableWidth > 0) ? quizScroll.availableWidth - 10 : (quizScroll.width > 0 ? quizScroll.width - 10 : 900)
                            spacing: 8

                            Repeater {
                                id: quizRepeater
                                model: quizItems
                                delegate: Rectangle {
                                    width: parent.width
                                    height: cardCol.implicitHeight + 20
                                    radius: 10
                                    color: "#fff"
                                    border.color: "#c7d2fe"
                                    property alias choiceGroup: group
                                    property bool corrected: (root.correctionMode && root.correctionDetails.length > index)
                                    property var resultRow: (corrected ? root.correctionDetails[index] : ({}))
                                    property bool answered: (corrected ? (Number(resultRow.selected) >= 0) : (group.checkedButton !== null))
                                    property bool rowWrong: (corrected && !Boolean(resultRow.isCorrect))
                                    property string rowChapter: String(corrected ? (resultRow.chapter || modelData.chapter || "") : (modelData.chapter || ""))
                                    property string rowCorrectText: String(corrected ? (resultRow.correctText || "") : "")

                                    ColumnLayout {
                                        id: cardCol
                                        x: 10
                                        y: 10
                                        width: parent.width - 20
                                        spacing: 6

                                        Rectangle {
                                            Layout.fillWidth: true
                                            radius: 8
                                            color: corrected ? (rowWrong ? "#e06b6b" : "#8f959c") : (answered ? "#8f959c" : "#d64545")
                                            implicitHeight: Math.max(100, qTitle.paintedHeight + 38)
                                            Text {
                                                id: qTitle
                                                anchors.fill: parent
                                                anchors.margins: 14
                                                text: (index + 1) + ") " + (corrected ? (resultRow.question || modelData.question || "") : (modelData.question || ""))
                                                wrapMode: Text.WordWrap
                                                font.pixelSize: 18
                                                font.bold: true
                                                color: "white"
                                            }
                                        }
                                        Image {
                                            property string imgSrc: corrected
                                                ? (resultRow.image_url || "")
                                                : (modelData.image_url || "")
                                            Layout.fillWidth: true
                                            Layout.maximumHeight: 260
                                            fillMode: Image.PreserveAspectFit
                                            source: imgSrc
                                            visible: imgSrc !== ""
                                            smooth: true
                                            mipmap: true
                                            layer.enabled: true
                                        }
                                        Text { text: rowChapter; color: "#586765"; font.pixelSize: 12; font.bold: true }

                                        ButtonGroup { id: group }
                                        Connections {
                                            target: group
                                            function onCheckedButtonChanged() { root.recomputeAnswered() }
                                        }

                                        Repeater {
                                            model: corrected ? (resultRow.choices || []) : (modelData.choices || [])
                                            delegate: Rectangle {
                                                Layout.fillWidth: true
                                                implicitHeight: Math.max(34, choiceText.paintedHeight + 10)
                                                radius: 8
                                                property bool c: ((typeof modelData === "object") && !!(modelData.correct || modelData.isCorrect))
                                                property bool s: ((typeof modelData === "object") && !!(modelData.selected || modelData.checked))
                                                color: corrected
                                                       ? (c ? "#d8ebe5" : (s ? "#ead8dd" : "#fbf7ef"))
                                                       : (rb.checked ? "#eef1f4" : (rowMouse.containsMouse ? "#f5f7fa" : "#fbf7ef"))
                                                border.color: corrected
                                                             ? "#eadfcd"
                                                             : (rb.checked ? "#b4bcc6" : (rowMouse.containsMouse ? "#cfd8e3" : "#eadfcd"))

                                                MouseArea {
                                                    id: rowMouse
                                                    anchors.fill: parent
                                                    hoverEnabled: true
                                                    enabled: !corrected
                                                    onClicked: rb.checked = true
                                                }

                                                RowLayout {
                                                    anchors.fill: parent
                                                    anchors.margins: 4
                                                    spacing: 6

                                                    RadioButton {
                                                        id: rb
                                                        ButtonGroup.group: group
                                                        property int choiceIndex: index
                                                        Layout.alignment: Qt.AlignTop
                                                        Layout.topMargin: 1
                                                        visible: !corrected
                                                    }

                                                    Text {
                                                        id: choiceText
                                                        Layout.fillWidth: true
                                                        text: (typeof modelData === "string") ? modelData : (modelData.text || modelData.answer || modelData.risposta || "")
                                                        wrapMode: Text.WordWrap
                                                        color: "#2a3735"
                                                        font.pixelSize: 15
                                                        horizontalAlignment: Text.AlignJustify
                                                        lineHeight: 1.08
                                                    }
                                                }
                                            }
                                        }

                                        Text {
                                            Layout.fillWidth: true
                                            visible: corrected && rowWrong
                                            text: "Errata — Risposta: " + rowCorrectText
                                            color: "#c51616"
                                            font.pixelSize: 15
                                            font.bold: true
                                        }

                                        RowLayout {
                                            Layout.fillWidth: true
                                            visible: corrected && rowWrong
                                            spacing: 8
                                            FancyButton {
                                                text: "Apri PDF"
                                                enabled: quizBridge.canOpenPdfForChapter(rowChapter)
                                                visible: quizBridge.canOpenPdfForChapter(rowChapter)
                                                onClicked: quizBridge.openPdfForChapter(rowChapter)
                                            }
                                            FancyButton {
                                                text: "Interroga A.I."
                                                enabled: ((quizBridge.aiKey || "").trim().length > 0)
                                                visible: ((quizBridge.aiKey || "").trim().length > 0)
                                                onClicked: quizBridge.askAiSnapshot(JSON.stringify(resultRow || {}))
                                            }
                                            Item { Layout.fillWidth: true }
                                        }
                                    }
                                }
                            }
                        }
                    }
                }
            }

            Rectangle {
                Layout.preferredWidth: Math.max(320, Math.floor((panelsRow.width - panelsRow.spacing) / 4))
                Layout.minimumWidth: 300
                Layout.maximumWidth: Math.max(340, Math.floor((panelsRow.width - panelsRow.spacing) / 3.2))
                Layout.fillHeight: true
                clip: true
                visible: !inQuizMode
                radius: 16
                color: "#ffffff"
                border.color: "#c7d2fe"

                ColumnLayout {
                    anchors.fill: parent
                    anchors.margins: 12
                    spacing: 8
                    RowLayout {
                        Layout.fillWidth: true
                        spacing: 8
                        Text { text: "Statistiche"; font.pixelSize: 18; font.bold: true; color: "#1b2d2b" }
                        Item { Layout.fillWidth: true }
                    }

                    ScrollView {
                        Layout.fillWidth: true
                        Layout.fillHeight: true
                        clip: true
                        ScrollBar.vertical.policy: ScrollBar.AlwaysOn
                        ScrollBar.horizontal.policy: ScrollBar.AsNeeded

                        Column {
                            width: Math.max(280, (parent.availableWidth > 0 ? parent.availableWidth : parent.width) - 6)
                            spacing: 6

                            Repeater {
                                model: statsItems
                                delegate: Rectangle {
                                    width: parent ? parent.width : 320
                                    height: 38
                                    MouseArea {
                                        id: statRowMouse
                                        anchors.fill: parent
                                        hoverEnabled: true
                                        onClicked: {
                                            selectedStatId = modelData.id || ""
                                            root.snapshotFromWrongContext = false
                                            quizBridge.openStatSnapshot(selectedStatId)
                                        }
                                    }
                                    radius: 8
                                    readonly property bool rowSelected: (selectedStatId === (modelData.id || ""))
                                    color: rowSelected ? "#bfe3ff" : (statRowMouse.containsMouse ? "#eef7ff" : "#fff")
                                    border.color: rowSelected ? "#1d9bf0" : (statRowMouse.containsMouse ? "#6fbef7" : "#000000")
                                    RowLayout {
                                        anchors.fill: parent
                                        anchors.margins: 6
                                        spacing: 6
                                        Text {
                                            Layout.fillWidth: true
                                            text: modelData.date
                                            color: "#1b2d2b"
                                            font.pixelSize: 11
                                            font.bold: true
                                            elide: Text.ElideRight
                                        }
                                        Text {
                                            text: modelData.correct + "/" + modelData.total + " (" + modelData.pct + "%)"
                                            color: "#586765"
                                            font.pixelSize: 11
                                        }
                                        Rectangle {
                                            width: 20
                                            height: 20
                                            radius: 10
                                            color: "#fff1f1"
                                            border.color: "#e2b1b1"
                                            Layout.alignment: Qt.AlignVCenter
                                            Text {
                                                anchors.centerIn: parent
                                                text: "X"
                                                color: "#c62828"
                                                font.pixelSize: 11
                                                font.bold: true
                                            }
                                            MouseArea {
                                                anchors.fill: parent
                                                onClicked: {
                                                    const id = modelData.id || ""
                                                    confirmDialog.message = "Eliminare questa statistica?"
                                                    confirmDialog.onConfirmed = function() {
                                                        if (selectedStatId === id) selectedStatId = ""
                                                        quizBridge.deleteStat(id)
                                                    }
                                                    confirmDialog.open()
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

        Rectangle {
            visible: showMainArea
            opacity: Math.max(0.0, Math.min(1.0, entranceProgress))
            Layout.fillWidth: true
            implicitHeight: 58
            radius: 12
            color: "#ffffff"
            border.color: "#c7d2fe"
            transform: Translate {
                y: (1.0 - entranceProgress) * 220
            }

            RowLayout {
                anchors.fill: parent
                anchors.margins: 8
                spacing: 10

                FancyButton {
                    text: "Reset bacino " + quizBridge.poolUsed + "/" + quizBridge.poolTotal
                    Layout.preferredHeight: 40
                    Layout.preferredWidth: 260
                    Layout.minimumWidth: 260
                    Layout.maximumWidth: 260
                    Layout.fillWidth: false
                    enabled: !inQuizMode
                    font.pixelSize: 13
                    font.bold: true
                    onClicked: {
                        confirmDialog.message = "Azzerare il bacino delle domande usate per questa selezione?"
                        confirmDialog.onConfirmed = function() { quizBridge.resetPool() }
                        confirmDialog.open()
                    }
                }

                Text {
                    Layout.fillWidth: true
                    text: inQuizMode ? ("Stato: " + quizBridge.status) : ("Stato: " + quizBridge.status + "   |   Tempo: " + Math.floor(quizElapsedSec/60).toString().padStart(2,'0') + ":" + (quizElapsedSec%60).toString().padStart(2,'0') + " / " + Math.floor(quizRemainingSec/60).toString().padStart(2,'0') + ":" + (quizRemainingSec%60).toString().padStart(2,'0'))
                    color: "#31403d"
                    font.pixelSize: 15
                    verticalAlignment: Text.AlignVCenter
                    elide: Text.ElideRight
                }

                FancyButton {
                    text: "Reset bacino errori (" + quizBridge.wrongCount + ")"
                    Layout.preferredHeight: 40
                    Layout.preferredWidth: 260
                    Layout.minimumWidth: 260
                    Layout.maximumWidth: 260
                    Layout.fillWidth: false
                    enabled: !inQuizMode && Number(quizBridge.wrongCount || 0) > 0
                    font.pixelSize: 13
                    font.bold: true
                    onClicked: {
                        const scope = quizBridge.cloudLogged
                            ? "Verranno cancellati i dati locali e quelli cloud."
                            : "Verranno cancellati solo i dati locali (non sei loggato al cloud)."
                        confirmDialog.message = "Azzerare il bacino errori (" + quizBridge.wrongCount + " domande)?\n" + scope
                        confirmDialog.onConfirmed = function() { quizBridge.clearWrongQuestions() }
                        confirmDialog.open()
                    }
                }
            }
        }

        Dialog {
            id: quizCountDialog
            modal: true
            anchors.centerIn: Overlay.overlay
            width: 360
            height: 230
            standardButtons: Dialog.NoButton
            background: Rectangle { radius: 12; color: "#ffffff"; border.width: 1; border.color: "#c7d2fe" }

            ColumnLayout {
                anchors.fill: parent
                anchors.margins: 12
                spacing: 10

                Text {
                    text: quizLaunchMode === "wrong" ? "Avvia Quiz errori" : "Avvia Quiz"
                    font.pixelSize: 18
                    font.bold: true
                    color: "#1b2d2b"
                }

                CheckBox {
                    id: allQuestionsCheck
                    text: "Tutte le domande"
                    checked: quizRequestAll
                    onToggled: quizRequestAll = checked
                    font.pixelSize: 14
                }

                RowLayout {
                    Layout.fillWidth: true
                    spacing: 8
                    Text { text: "Numero domande"; font.pixelSize: 14; color: "#334" }
                    SpinBox {
                        id: quizCountSpin
                        from: 1
                        to: Math.max(1, quizLaunchMode === "wrong" ? Number(quizBridge.wrongCount || 1) : Number(quizBridge.poolTotal || 1))
                        value: Math.max(1, Math.min(to, quizRequestCount))
                        enabled: !quizRequestAll
                        editable: true
                    }
                    Item { Layout.fillWidth: true }
                }

                RowLayout {
                    Layout.fillWidth: true
                    Item { Layout.fillWidth: true }
                    FancyButton { text: "Annulla"; onClicked: quizCountDialog.close() }
                    FancyButton {
                        text: "Avvia"
                        font.bold: true
                        onClicked: {
                            const count = quizRequestAll
                                ? (quizLaunchMode === "wrong" ? Number(quizBridge.wrongCount || 0) : Number(quizBridge.poolTotal || 0))
                                : Number(quizCountSpin.value || 1)
                            correctionMode = false
                            correctionDetails = []
                            if (quizLaunchMode === "wrong")
                                quizBridge.generateWrongQuiz(Math.max(1, count))
                            else
                                quizBridge.generateQuiz(Math.max(1, count))
                            quizElapsedSec = 0
                            quizRemainingSec = 1800
                            examNextCheckSec = 120
                            examModeLocked = true
                            quizTimer.start()
                            quizCountDialog.close()
                        }
                    }
                }
            }
        }

        // ─── Dialog conferma riutilizzabile ───────────────────────────────────────
        Dialog {
            id: confirmDialog
            modal: true
            anchors.centerIn: Overlay.overlay
            width: Math.min(root.width - 80, 400)
            standardButtons: Dialog.NoButton
            closePolicy: Popup.CloseOnEscape

            property string message: ""
            property var onConfirmed: null   // callback () => void
            property bool _accepted: false

            onClosed: {
                console.log("confirmDialog.onClosed: _accepted=" + _accepted + " onConfirmed=" + typeof onConfirmed)
                if (_accepted && typeof onConfirmed === "function") {
                    var cb = onConfirmed
                    onConfirmed = null
                    _accepted = false
                    console.log("confirmDialog: eseguo callback")
                    cb()
                } else {
                    console.log("confirmDialog: callback NON eseguito (_accepted=" + _accepted + ")")
                    onConfirmed = null
                    _accepted = false
                }
            }

            Column {
                width: parent.width
                spacing: 18
                topPadding: 4

                Text {
                    width: parent.width
                    text: confirmDialog.message
                    wrapMode: Text.WordWrap
                    font.pixelSize: 15
                    color: "#374151"
                    horizontalAlignment: Text.AlignHCenter
                }

                RowLayout {
                    width: parent.width
                    spacing: 12

                    FancyButton {
                        text: "Annulla"
                        Layout.fillWidth: true
                        implicitHeight: 40
                        font.pixelSize: 14
                        onClicked: confirmDialog.close()
                        background: Rectangle {
                            radius: 10
                            color: confirmDialogAnnullaHover.containsMouse ? "#e5e7eb" : "#f3f4f6"
                            border.color: "#d1d5db"
                            HoverHandler { id: confirmDialogAnnullaHover }
                        }
                        contentItem: Text {
                            text: "Annulla"
                            font: parent.font
                            color: "#374151"
                            horizontalAlignment: Text.AlignHCenter
                            verticalAlignment: Text.AlignVCenter
                        }
                    }

                    FancyButton {
                        text: "Conferma"
                        Layout.fillWidth: true
                        implicitHeight: 40
                        font.pixelSize: 14
                        onClicked: {
                            console.log("confirmDialog: pulsante Conferma cliccato, setto _accepted=true")
                            confirmDialog._accepted = true
                            confirmDialog.close()
                        }
                        background: Rectangle {
                            radius: 10
                            gradient: Gradient {
                                orientation: Gradient.Vertical
                                GradientStop { position: 0.0; color: "#ef4444" }
                                GradientStop { position: 1.0; color: "#dc2626" }
                            }
                            border.color: "#b91c1c"
                        }
                    }
                }
            }
        }
        // ──────────────────────────────────────────────────────────────────────

        Dialog {
            id: loadingOverlay
            modal: true
            focus: true
            visible: isLoading
            closePolicy: Popup.NoAutoClose
            anchors.centerIn: Overlay.overlay
            width: 320
            height: 170
            standardButtons: Dialog.NoButton
            background: Rectangle {
                radius: 12
                color: "#1f2937ee"
                border.color: "#4b5563"
            }
            contentItem: Column {
                anchors.fill: parent
                anchors.margins: 18
                spacing: 12
                BusyIndicator { running: loadingOverlay.visible; width: 64; height: 64; anchors.horizontalCenter: parent.horizontalCenter }
                Text {
                    text: "Caricamento in corso..."
                    color: "white"
                    font.pixelSize: 17
                    font.bold: true
                    horizontalAlignment: Text.AlignHCenter
                    width: parent.width
                }
            }
        }

        Dialog {
            id: cloudLoginDialog
            modal: true
            anchors.centerIn: Overlay.overlay
            width: Math.min(root.width - 80, 350)
            height: 420
            standardButtons: Dialog.NoButton

            onOpened: {
                loginUser.text = quizBridge.cloudLastUser || ""
                loginPass.text = ""
                loginError.text = ""
            }

            background: Rectangle {
                radius: 14
                color: "#ffffff"
                border.color: "#c7d2fe"
            }

            ColumnLayout {
                anchors.fill: parent
                anchors.margins: 16
                spacing: 10

                Text { text: "Accesso Cloud Picker"; font.pixelSize: 26; font.bold: true; color: "#1b2d2b" }
                Text { text: "Username o email"; font.pixelSize: 16; font.bold: true; color: "#1f2f2d" }
                TextField { id: loginUser; Layout.fillWidth: true; Layout.preferredHeight: 52; font.pixelSize: 18; placeholderText: "es. enzo oppure enzo@dominio.it" }
                Text { text: "Password"; font.pixelSize: 16; font.bold: true; color: "#1f2f2d" }
                TextField { id: loginPass; Layout.fillWidth: true; Layout.preferredHeight: 52; font.pixelSize: 18; echoMode: TextInput.Password; placeholderText: "Inserisci la password" }
                Text {
                    id: loginError
                    Layout.fillWidth: true
                    text: ""
                    wrapMode: Text.Wrap
                    color: "#b42318"
                    font.pixelSize: 12
                    font.bold: true
                }
                RowLayout {
                    Layout.fillWidth: true
                    Item { Layout.fillWidth: true }
                    FancyButton { text: "Esci"; onClicked: cloudLoginDialog.close() }
                    FancyButton {
                        text: "Login"
                        onClicked: quizBridge.cloudLogin(loginUser.text, loginPass.text)
                    }
                }
            }

            Connections {
                target: quizBridge
                function onCloudAuthChanged() {
                    if (!cloudLoginDialog.visible)
                        return
                    if (quizBridge.cloudLogged) {
                        cloudLoginDialog.close()
                        cloudPickerDialog.cloudEntries = []
                        cloudPickerDialog.open()
                        isLoading = true
                                quizBridge.cloudLoadEntries(quizBridge.cloudManifestUrl || "")
                    }
                }
                function onStatusChanged() {
                    if (!cloudLoginDialog.visible)
                        return
                    var msg = quizBridge.status || ""
                    if (msg.toLowerCase().indexOf("errore") >= 0 || msg.toLowerCase().indexOf("fallito") >= 0) {
                        loginError.text = msg
                    }
                }
            }
        }

        Dialog {
            id: cloudPickerDialog
            modal: true
            anchors.centerIn: Overlay.overlay
            width: Math.min(root.width - 40, 760)
            height: Math.min(root.height - 40, 620)
            standardButtons: Dialog.NoButton

            property var cloudEntries: []

            background: Rectangle {
                radius: 14
                color: "#ffffff"
                border.color: "#c7d2fe"
            }

            ColumnLayout {
                anchors.fill: parent
                anchors.margins: 16
                spacing: 10

                Text { text: "Seleziona JSON da GitHub"; font.pixelSize: 22; font.bold: true; color: "#1b2d2b" }
                Rectangle {
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    radius: 10
                    color: "#fff"
                    border.color: "#c7d2fe"

                    ListView {
                        id: cloudList
                        anchors.fill: parent
                        anchors.margins: 8
                        model: cloudPickerDialog.cloudEntries
                        clip: true
                        delegate: Rectangle {
                            width: cloudList.width
                            height: 46
                            radius: 8
                            property bool isSelected: (modelData.name || "") === (quizBridge.datasetName || "")
                            color: isSelected ? "#dff4ff" : (cloudRowMouse.containsMouse ? "#eef6ff" : (index % 2 === 0 ? "#f7f7f7" : "#f2f2f2"))
                            border.color: isSelected ? "#1d9bf0" : (cloudRowMouse.containsMouse ? "#8bb8ef" : "#e6edf8")
                            RowLayout {
                                anchors.fill: parent
                                anchors.margins: 8
                                Text { Layout.fillWidth: true; text: modelData.name || "JSON"; font.pixelSize: 14; font.bold: true; color: "#183a5a" }
                            }
                            MouseArea {
                                id: cloudRowMouse
                                anchors.fill: parent
                                hoverEnabled: true
                                cursorShape: Qt.PointingHandCursor
                                onClicked: {
                                    isLoading = true
                                    userInitiatedLoad = true
                                    datasetSelectionInProgress = true
                                    showMainArea = false
                                    quizBridge.cloudLoadSelected(modelData.name || "JSON", modelData.url || "")
                                    cloudPickerDialog.close()
                                }
                            }
                        }
                    }
                }

                RowLayout {
                    Layout.fillWidth: true
                    Item { Layout.fillWidth: true }
                    FancyButton { text: "Chiudi"; onClicked: cloudPickerDialog.close() }
                }
            }

            Connections {
                target: quizBridge
                function onCloudEntriesChanged() {
                    try {
                        cloudPickerDialog.cloudEntries = JSON.parse(quizBridge.cloudEntriesJson || "[]")
                    } catch(e) {
                        cloudPickerDialog.cloudEntries = []
                    }
                }
            }
        }


        Timer {
            id: quizTimer
            interval: 1000
            repeat: true
            running: false
            onTriggered: {
                quizElapsedSec += 1
                if (quizRemainingSec > 0) quizRemainingSec -= 1
                if (examModeEnabled && quizElapsedSec >= examNextCheckSec) {
                    examCheckDialog.open()
                    examNextCheckSec = quizElapsedSec + 120
                }
            }
        }

        Dialog {
            id: easterDialog
            modal: true
            anchors.centerIn: Overlay.overlay
            width: Math.min(root.width - 60, 760)
            height: 430
            standardButtons: Dialog.NoButton
            background: Rectangle { radius: 14; color: "#ffffff"; border.width: 1; border.color: "#c7d2fe" }

            ColumnLayout {
                anchors.fill: parent
                anchors.margins: 16
                spacing: 10
                Text { text: "Easter Egg"; font.pixelSize: 24; font.bold: true; color: "#1b2d2b" }
                Text { text: "Manifest Cloud URL"; font.pixelSize: 14; font.bold: true; color: "#1f2f2d" }
                TextField { id: easterManifest; Layout.fillWidth: true; text: quizBridge.cloudManifestUrl || ""; placeholderText: "https://.../manifest_full.json" }
                Text { text: "AI Key"; font.pixelSize: 14; font.bold: true; color: "#1f2f2d" }
                TextField { id: easterAi; Layout.fillWidth: true; text: quizBridge.aiKey || ""; echoMode: TextInput.Password; placeholderText: "sk-..." }
                Text { text: "Nuova password account"; font.pixelSize: 14; font.bold: true; color: "#1f2f2d" }
                TextField { id: easterNewPass; Layout.fillWidth: true; echoMode: TextInput.Password; placeholderText: "Minimo 6 caratteri" }
                TextField { id: easterNewPass2; Layout.fillWidth: true; echoMode: TextInput.Password; placeholderText: "Conferma nuova password" }
                Item { Layout.fillHeight: true }
                RowLayout {
                    Layout.fillWidth: true
                    Item { Layout.fillWidth: true }
                    FancyButton { text: "Chiudi"; onClicked: easterDialog.close() }
                    FancyButton {
                        text: "Salva"
                        onClicked: {
                            quizBridge.setCloudManifestUrl(easterManifest.text)
                            quizBridge.setAiKey((easterAi.text || "").trim())
                            if ((easterNewPass.text || "").length > 0 || (easterNewPass2.text || "").length > 0) {
                                if ((easterNewPass.text || "") !== (easterNewPass2.text || "")) {
                                    quizBridge.setAiKey((easterAi.text || "").trim())
                                    return
                                }
                                quizBridge.changePassword((easterNewPass.text || "").trim())
                            }
                            easterDialog.close()
                        }
                    }
                }
            }
        }


        Dialog {
            id: examCheckDialog
            parent: Overlay.overlay
            modal: true
            focus: true
            dim: true
            closePolicy: Popup.NoAutoClose
            x: Math.round((root.width - width) / 2)
            y: Math.round((root.height - height) / 2)
            z: 9999
            width: 500
            height: 240
            standardButtons: Dialog.NoButton
            background: Rectangle { radius: 14; color: "#ffffff"; border.width: 1; border.color: "#c7d2fe" }
            ColumnLayout {
                anchors.fill: parent
                anchors.margins: 16
                spacing: 12
                Text { text: "Check presenza"; font.pixelSize: 22; font.bold: true; color: "#0b7285"; Layout.alignment: Qt.AlignHCenter }
                Text { text: "Clicca per continuare il questionario"; font.pixelSize: 14; color: "#183a5a"; Layout.alignment: Qt.AlignHCenter }
                Item { Layout.fillHeight: true }
                FancyButton {
                    text: "Conferma e continua"
                    Layout.alignment: Qt.AlignHCenter
                    onClicked: examCheckDialog.close()
                }
            }
        }

        Dialog {
            id: resultDialog
            modal: true
            anchors.centerIn: Overlay.overlay
            width: 420
            height: 260
            standardButtons: Dialog.NoButton
            property string summary: ""
            background: Rectangle { radius: 14; color: "#ffffff"; border.width: 1; border.color: "#c7d2fe" }
            ColumnLayout {
                anchors.fill: parent
                anchors.margins: 16
                spacing: 10
                Text { text: "Resoconto"; font.pixelSize: 22; font.bold: true; color: "#1b2d2b" }
                Text { text: resultDialog.summary; wrapMode: Text.Wrap; font.pixelSize: 14; color: "#183a5a" }
                Item { Layout.fillHeight: true }
                FancyButton { text: "OK"; Layout.alignment: Qt.AlignRight; onClicked: resultDialog.close() }
            }
        }

        Connections {
            target: quizBridge
            function onResultChanged() {
                try {
                    var r = JSON.parse(quizBridge.lastResultJson || "{}")
                    correctionDetails = Array.isArray(r.detail) ? r.detail : []
                    correctionMode = correctionDetails.length > 0
                    var c = Number(r.correct || 0)
                    var t = Number(r.total || 0)
                    var p = Number(r.pct || 0)
                    resultDialog.summary = "Risultato: " + c + "/" + t + " (" + p + "%)"
                    resultDialog.open()
                    quizTimer.stop()
                    examModeLocked = false
                } catch(e) {}
            }
        }


        Dialog {
            id: snapshotDialog
            modal: true
            anchors.centerIn: Overlay.overlay
            width: Math.min(root.width - 20, 1320)
            height: Math.min(root.height - 24, 860)
            standardButtons: Dialog.NoButton
            background: Rectangle { radius: 10; color: "#ffffff"; border.width: 1; border.color: "#c7d2fe" }

            ColumnLayout {
                anchors.fill: parent
                anchors.margins: 10
                spacing: 8
                RowLayout {
                    Layout.fillWidth: true
                    spacing: 10
                    FancyButton { text: "Chiudi"; onClicked: snapshotDialog.close() }
                    Text { text: "Snapshot questionario"; font.pixelSize: 20; font.bold: true; color: "#1b2d2b" }
                    Item { Layout.fillWidth: true }
                }

                ScrollView {
                    id: snapshotScroll
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    clip: true
                    Column {
                        width: Math.max(420, (snapshotScroll.availableWidth > 0 ? snapshotScroll.availableWidth : snapshotScroll.width) - 8)
                        spacing: 10
                        Repeater {
                            model: currentSnapshotItems
                            delegate: Rectangle {
                                width: parent.width
                                radius: 10
                                color: "#fff"
                                border.color: "#c7d2fe"
                                implicitHeight: snapCol.implicitHeight + 12

                                ColumnLayout {
                                    id: snapCol
                                    anchors.left: parent.left
                                    anchors.right: parent.right
                                    anchors.margins: 10
                                    spacing: 4

                                    property var choiceRows: (modelData.choices || modelData.risposte || [])
                                    property int selIdx: {
                                        if (typeof modelData.selected === "number") return Number(modelData.selected)
                                        if (typeof modelData.selectedIndex === "number") return Number(modelData.selectedIndex)
                                        const arr = choiceRows || []
                                        for (var i = 0; i < arr.length; i++) {
                                            var r = arr[i] || {}
                                            if (r.selected || r.checked || r.isSelected) return i
                                        }
                                        return -1
                                    }
                                    property int corIdx: {
                                        if (typeof modelData.correctIndex === "number") return Number(modelData.correctIndex)
                                        if (typeof modelData.answerIndex === "number") return Number(modelData.answerIndex)
                                        const arr = choiceRows || []
                                        for (var i = 0; i < arr.length; i++) {
                                            var r = arr[i] || {}
                                            if (r.correct || r.isCorrect || r.ok) return i
                                        }
                                        return -1
                                    }
                                    property bool wrongSnap: {
                                        if (selIdx >= 0 && corIdx >= 0) return selIdx !== corIdx
                                        if (typeof modelData.isWrong === "boolean") return !!modelData.isWrong
                                        if (typeof modelData.wrong === "boolean") return !!modelData.wrong
                                        if (typeof modelData.isCorrect === "boolean") return !modelData.isCorrect
                                        if (typeof modelData.correct === "boolean") return !modelData.correct
                                        if (root.snapshotFromWrongContext) return true
                                        return true
                                    }
                                    property string correctText: {
                                        const arr = choiceRows || []
                                        if (corIdx >= 0 && corIdx < arr.length) {
                                            var r = arr[corIdx]
                                            if (typeof r === "string") return String(r)
                                            return String((r && (r.text || r.answer || r.risposta)) || "")
                                        }
                                        return String(modelData.correctText || "")
                                    }

                                    Rectangle {
                                        Layout.fillWidth: true
                                        radius: 10
                                        color: (snapCol.wrongSnap ? "#e06b6b" : "#8f959c")
                                        border.color: "#6f7780"
                                        implicitHeight: qBox.implicitHeight + 16

                                        RowLayout {
                                            id: qBox
                                            anchors.fill: parent
                                            anchors.margins: 10
                                            spacing: 10
                                            Rectangle {
                                                radius: 8
                                                color: "#b8bec5"
                                                implicitWidth: 52
                                                implicitHeight: 48
                                                Text {
                                                    anchors.centerIn: parent
                                                    text: String(index + 1)
                                                    color: "white"
                                                    font.pixelSize: 20
                                                    font.bold: true
                                                }
                                            }
                                            ColumnLayout {
                                                Layout.fillWidth: true
                                                spacing: 2
                                                Text {
                                                    text: (modelData.question || modelData.domanda || modelData.questionText || modelData.q || "[Domanda non disponibile]")
                                                    wrapMode: Text.WordWrap
                                                    color: "white"
                                                    font.pixelSize: 18
                                                    font.bold: true
                                                    Layout.fillWidth: true
                                                }
                                                Text {
                                                    text: "Paragrafo di riferimento - " + (modelData.chapter || modelData.capitolo || "")
                                                    color: "#6d7775"
                                                    font.pixelSize: 12
                                                    font.bold: true
                                                }
                                            }
                                        }
                                    }
                                    Image {
                                        property string imgSrc: modelData.image_url || ""
                                        Layout.fillWidth: true
                                        Layout.maximumHeight: 220
                                        fillMode: Image.PreserveAspectFit
                                        source: imgSrc
                                        visible: imgSrc !== ""
                                        smooth: true
                                        mipmap: true
                                    }

                                    Repeater {
                                        model: (snapCol.choiceRows || [])
                                        delegate: Rectangle {
                                            Layout.fillWidth: true
                                            implicitHeight: Math.max(34, choiceTxt.paintedHeight + 10)
                                            radius: 8
                                            property bool c: (typeof modelData === "object") && !!(modelData.correct || modelData.isCorrect)
                                            property bool s: (typeof modelData === "object") && !!(modelData.selected || modelData.checked)
                                            color: c ? "#d8ebe5" : (s ? "#ead8dd" : "#fbf7ef")
                                            border.color: "#eadfcd"
                                            RowLayout {
                                                anchors.fill: parent
                                                anchors.margins: 6
                                                spacing: 10
                                                Text {
                                                    text: "•"
                                                    color: "#5c5c5c"
                                                    font.pixelSize: 24
                                                    font.bold: true
                                                    Layout.preferredWidth: 20
                                                    horizontalAlignment: Text.AlignHCenter
                                                }
                                                Text {
                                                    id: choiceTxt
                                                    Layout.fillWidth: true
                                                    text: (typeof modelData === "string") ? modelData : (modelData.text || modelData.answer || modelData.risposta || "")
                                                    wrapMode: Text.WordWrap
                                                    color: "#18202f"
                                                    font.pixelSize: 15
                                                    font.bold: true
                                                }
                                            }
                                        }
                                    }

                                    Text {
                                        Layout.fillWidth: true
                                        visible: wrongSnap
                                        text: "Errata — Risposta: " + correctText
                                        color: "#c51616"
                                        font.pixelSize: 15
                                        font.bold: true
                                    }

                                    RowLayout {
                                        Layout.fillWidth: true
                                        visible: wrongSnap
                                        spacing: 8
                                        FancyButton {
                                            text: "Apri PDF"
                                            enabled: !!(modelData.hasPdfPage)
                                            visible: !!(modelData.hasPdfPage)
                                            onClicked: quizBridge.openPdfForChapter(modelData.chapter || "")
                                        }
                                        FancyButton {
                                            text: "Interroga A.I."
                                            enabled: ((quizBridge.aiKey || "").trim().length > 0)
                                            visible: ((quizBridge.aiKey || "").trim().length > 0)
                                            onClicked: quizBridge.askAiSnapshot(JSON.stringify(modelData || {}))
                                        }
                                        Item { Layout.fillWidth: true }
                                    }
                                }
                            }
                        }
                    }
                }

            }
        }


        Dialog {
            id: wrongPayloadDialog
            modal: true
            anchors.centerIn: Overlay.overlay
            width: Math.min(root.width - 60, 980)
            height: Math.min(root.height - 60, 700)
            standardButtons: Dialog.NoButton
            background: Rectangle { radius: 12; color: "#ffffff"; border.width: 1; border.color: "#c7d2fe" }
            ColumnLayout {
                anchors.fill: parent
                anchors.margins: 12
                spacing: 8
                Text { text: "Anteprima JSON errori"; font.pixelSize: 20; font.bold: true; color: "#1b2d2b" }
                ScrollView {
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    clip: true
                    TextArea {
                        readOnly: true
                        selectByMouse: true
                        wrapMode: TextArea.NoWrap
                        text: quizBridge.wrongPayloadJson || "[]"
                        font.family: "Menlo"
                        font.pixelSize: 12
                    }
                }
                RowLayout {
                    Layout.fillWidth: true
                    Item { Layout.fillWidth: true }
                    FancyButton { text: "Chiudi"; onClicked: wrongPayloadDialog.close() }
                }
            }
        }

        Dialog {
            id: snapshotAiDialog
            modal: true
            anchors.centerIn: Overlay.overlay
            width: Math.min(root.width - 80, 760)
            height: Math.min(root.height - 80, 520)
            standardButtons: Dialog.NoButton
            background: Rectangle { radius: 14; color: "#ffffff"; border.width: 1; border.color: "#c7d2fe" }
            ColumnLayout {
                anchors.fill: parent
                anchors.margins: 14
                spacing: 8
                Text { text: "Approfondimento A.I."; font.pixelSize: 20; font.bold: true; color: "#1b2d2b" }
                ScrollView {
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    clip: true
                    TextArea {
                        id: aiAnswerText
                        width: parent.width
                        readOnly: true
                        text: snapshotAiText
                        wrapMode: TextArea.Wrap
                        selectByMouse: true
                        font.pixelSize: 14
                    }
                }
                RowLayout {
                    Layout.fillWidth: true
                    Item { Layout.fillWidth: true }
                    FancyButton { text: "Chiudi"; onClicked: snapshotAiDialog.close() }
                }
            }
        }


        Dialog {
            id: percentDialog
            modal: true
            anchors.centerIn: Overlay.overlay
            width: Math.min(root.width - 80, 620)
            height: Math.min(root.height - 80, 620)
            standardButtons: Dialog.NoButton

            ListModel { id: percentModel }
            property var pendingProfileChapters: []

            onOpened: {
                percentDialog.pendingProfileChapters = []
                percentModel.clear()
                var base = selectedChapters.length > 0 ? selectedChapters : chapters
                base = base.slice(0).sort(function(a, b) {
                    function n(v) {
                        var m = /(?:lezione|lesson)\s*(\d+)/i.exec(String(v || ""))
                        if (m) return parseInt(m[1])
                        var m2 = /(\d+)/.exec(String(v || ""))
                        return m2 ? parseInt(m2[1]) : 999999
                    }
                    var na = n(a), nb = n(b)
                    if (na !== nb) return na - nb
                    return String(a).localeCompare(String(b))
                })
                var def = Math.round(100 / Math.max(1, base.length))
                for (var i = 0; i < base.length; i++) {
                    var ch = base[i]
                    var v = Number(percentMapData[ch])
                    if (isNaN(v) || v < 0) v = def
                    percentModel.append({ chapter: ch, pct: v })
                }
            }

            background: Rectangle { radius: 14; color: "#ffffff"; border.width: 1; border.color: "#c7d2fe" }

            ColumnLayout {
                anchors.fill: parent
                anchors.margins: 16
                spacing: 10

                Text { text: "Distribuzione % per capitolo"; font.pixelSize: 22; font.bold: true; color: "#1b2d2b" }

                RowLayout {
                    Layout.fillWidth: true
                    spacing: 8
                    ComboBox {
                        id: profileCombo
                        Layout.fillWidth: true
                        model: percentProfiles
                    }
                    TextField {
                        id: profileNameField
                        Layout.preferredWidth: 180
                        placeholderText: "Nome profilo"
                    }
                    FancyButton { text: "Salva"; onClicked: quizBridge.savePercentProfile(profileNameField.text || profileCombo.currentText || "") }
                    FancyButton {
                        text: "Carica"
                        enabled: (profileCombo.currentText || "").length > 0
                        onClicked: {
                            var name = profileCombo.currentText || ""
                            quizBridge.loadPercentProfile(name)
                            Qt.callLater(function() {
                                var base = selectedChapters.length > 0 ? selectedChapters.slice(0) : chapters.slice(0)
                                base = base.slice(0).sort(function(a, b) {
                                    function n(v) {
                                        var m = /(?:lezione|lesson)\s*(\d+)/i.exec(String(v || ""))
                                        if (m) return parseInt(m[1])
                                        var m2 = /(\d+)/.exec(String(v || ""))
                                        return m2 ? parseInt(m2[1]) : 999999
                                    }
                                    var na = n(a), nb = n(b)
                                    if (na !== nb) return na - nb
                                    return String(a).localeCompare(String(b))
                                })
                                percentDialog.pendingProfileChapters = base.slice(0)
                                percentModel.clear()
                                var def = Math.round(100 / Math.max(1, base.length))
                                for (var i = 0; i < base.length; i++) {
                                    var ch = base[i]
                                    var v = Number(percentMapData[ch])
                                    if (isNaN(v) || v < 0) v = def
                                    percentModel.append({ chapter: ch, pct: v })
                                }
                            })
                        }
                    }
                    FancyButton {
                        text: "Elimina"
                        enabled: (profileCombo.currentText || "").length > 0
                        onClicked: {
                            const profileName = profileCombo.currentText || ""
                            if (!profileName) return
                            confirmDialog.message = 'Eliminare il profilo "' + profileName + '"?'
                            confirmDialog.onConfirmed = function() { quizBridge.deletePercentProfile(profileName) }
                            confirmDialog.open()
                        }
                    }
                }

                ListView {
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    model: percentModel
                    clip: true
                    delegate: Rectangle {
                        width: parent ? parent.width : 560
                        height: 44
                        radius: 8
                        color: "#fff"
                        border.color: "#c7d2fe"
                        RowLayout {
                            anchors.fill: parent
                            anchors.margins: 8
                            Text { Layout.fillWidth: true; text: chapter; font.pixelSize: 14; color: "#183a5a" }
                            SpinBox {
                                from: 0
                                to: 100
                                value: Number(pct)
                                onValueModified: percentModel.setProperty(index, "pct", value)
                            }
                        }
                    }
                }

                RowLayout {
                    Layout.fillWidth: true
                    Text {
                        Layout.fillWidth: true
                        text: {
                            var t = 0
                            for (var i = 0; i < percentModel.count; i++) t += Number(percentModel.get(i).pct || 0)
                            return "Totale: " + t + "%"
                        }
                        color: "#586765"
                    }
                    FancyButton { text: "Annulla"; onClicked: percentDialog.close() }
                    FancyButton {
                        text: "Applica"
                        onClicked: {
                            var obj = {}
                            for (var i = 0; i < percentModel.count; i++) {
                                var row = percentModel.get(i)
                                obj[row.chapter] = Number(row.pct || 0)
                            }
                            if (percentDialog.pendingProfileChapters && percentDialog.pendingProfileChapters.length > 0) {
                                selectedChapters = percentDialog.pendingProfileChapters.slice(0)
                                quizBridge.setSelectedChapters(JSON.stringify(selectedChapters))
                            }
                            quizBridge.setPercentMap(JSON.stringify(obj))
                            percentDialog.pendingProfileChapters = []
                            percentDialog.close()
                        }
                    }
                }
            }
        }

    }
}
