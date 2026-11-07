import signal
import sys
from datetime import datetime
from PySide6.QtCore import Qt, QTimer, QSize
from PySide6.QtGui import QFont, QDesktopServices
from PySide6.QtWidgets import QApplication, QWidget, QHBoxLayout, QLabel, QToolButton, QMenu, QScrollArea, QFrame, QStyle, QMessageBox, QSizePolicy, QDialog, QFormLayout, QComboBox, QSpinBox, QDialogButtonBox
from PySide6.QtCore import QUrl, QSettings
from win32_api import enumerate_windows, activate_or_minimize, close_window, monitor_list, register_appbar, remove_appbar, reposition_appbar, load_window_icon

if sys.platform != "win32":
    raise SystemExit("NtBar supports Windows 10 and 11.")

class TaskButton(QToolButton):
    def __init__(self, window, panel_height=44, parent=None):
        super().__init__(parent)
        self.hwnd = window.hwnd
        self.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.setText(window.title)
        self.setToolTip(window.title)
        icon = load_window_icon(window.hwnd)
        self.setIcon(icon or QApplication.style().standardIcon(QStyle.SP_ComputerIcon))
        button_height = max(1, panel_height - 8)
        icon_size = max(8, min(20, button_height - 6))
        self.setIconSize(QSize(icon_size, icon_size))
        self.setFixedHeight(button_height)
        self.setMinimumWidth(112)
        self.setMaximumWidth(210)
        self.setCursor(Qt.PointingHandCursor)
        self.setProperty("active", window.active)
        self.setProperty("minimized", window.minimized)
        self.setStyleSheet("""
            QToolButton { color:#e8e8e8; background:#30343b; border:1px solid #414750;
                border-radius:5px; padding:4px 9px; text-align:left; }
            QToolButton:hover { background:#414852; border-color:#626d7b; }
            QToolButton[active="true"] { background:#46566b; border-bottom:2px solid #79a9e8; }
            QToolButton[minimized="true"] { color:#aeb5bf; }
        """)
        self.clicked.connect(lambda: activate_or_minimize(self.hwnd))

    def contextMenuEvent(self, event):
        menu = QMenu(self)
        action = menu.addAction("Close window")
        action.triggered.connect(lambda: close_window(self.hwnd))
        menu.exec(event.globalPos())

class SecondaryTaskbar(QWidget):
    DEFAULT_HEIGHT = 44

    def __init__(self):
        super().__init__()
        self.settings = QSettings("BenFreedomWalker", "NtBar")
        self.panel_height = self.settings.value("panel_height", self.DEFAULT_HEIGHT, type=int)
        self.panel_edge = self.settings.value("panel_edge", "bottom")
        self.panel_length = self.settings.value("panel_length", 100, type=int)
        self.panel_alignment = self.settings.value("panel_alignment", "center")
        self.setWindowTitle("NtBar")
        self.setWindowFlags(Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.setObjectName("NtBar")
        self.setFixedHeight(self.panel_height)
        self.setStyleSheet("""
            QWidget#NtBar { background:#202329; color:#f1f1f1; }
            QToolButton#menuButton { background:#292e36; color:#f1f1f1;
                border:1px solid #3c424c; border-radius:5px; padding:5px 11px; }
            QToolButton#menuButton:hover { background:#3b424d; }
            QLabel#clock { color:#e2e5e9; padding:0 12px; }
            QScrollArea { border:0; background:transparent; }
        """)
        layout = QHBoxLayout(self)
        vertical_margin = min(4, max(0, (self.panel_height - 1) // 2))
        layout.setContentsMargins(7, vertical_margin, 7, vertical_margin)
        layout.setSpacing(7)

        self.menu_button = QToolButton(self)
        self.menu_button.setObjectName("menuButton")
        self.menu_button.setText("NtBar")
        self.menu_button.setFixedHeight(max(1, self.panel_height - vertical_margin * 2))
        self.menu_button.clicked.connect(self.show_menu)
        layout.addWidget(self.menu_button)
        layout.addSpacing(8)

        self.scroll = QScrollArea(self)
        self.scroll.setWidgetResizable(False)
        self.scroll.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll.setMinimumWidth(160)
        self.host = QWidget()
        self.buttons = QHBoxLayout(self.host)
        self.buttons.setContentsMargins(0, 0, 0, 0)
        self.buttons.setSpacing(5)
        self.scroll.setWidget(self.host)
        layout.addWidget(self.scroll)
        layout.addSpacing(8)

        self.clock = QLabel(self)
        self.clock.setObjectName("clock")
        self.clock.setFont(QFont("Segoe UI", 9))
        layout.addWidget(self.clock)
        layout.addStretch(1)

        self.target = None
        self.signature = None
        self.registered = False

        self.show()
        self.target = self.default_monitor()
        self.registered = register_appbar(int(self.winId()))
        self.place_bar()
        self.refresh_windows()
        self.update_clock()

        self.refresh_timer = QTimer(self)
        self.refresh_timer.timeout.connect(self.refresh_windows)
        self.refresh_timer.start(900)
        self.clock_timer = QTimer(self)
        self.clock_timer.timeout.connect(self.update_clock)
        self.clock_timer.start(1000)
        self.monitor_timer = QTimer(self)
        self.monitor_timer.timeout.connect(self.check_monitors)
        self.monitor_timer.start(2500)

    def default_monitor(self):
        monitors = monitor_list()
        saved_device = self.settings.value("monitor_device", "")
        return (
            next((m for m in monitors if m["device"] == saved_device), None)
            or next((m for m in monitors if not m["primary"]), monitors[0] if monitors else None)
        )

    def place_bar(self):
        if self.target:
            reposition_appbar(int(self.winId()), self.target["handle"], self.panel_height, self.panel_edge, self.panel_length, self.panel_alignment)
            self.raise_()

    def show_menu(self):
        menu = QMenu(self)
        monitor_menu = menu.addMenu("Move taskbar to monitor")
        for number, monitor in enumerate(monitor_list(), 1):
            label = f'Monitor {number} — {monitor["width"]}×{monitor["height"]}'
            if monitor["primary"]:
                label += " (primary)"
            action = monitor_menu.addAction(label)
            action.setCheckable(True)
            action.setChecked(bool(self.target and self.target["device"] == monitor["device"]))
            action.triggered.connect(lambda checked=False, m=monitor: self.select_monitor(m))
        menu.addSeparator()
        menu.addAction("Settings…", self.show_settings)
        menu.addAction("Refresh windows", self.refresh_windows)
        menu.addAction("About NtBar", self.show_about)
        menu.addSeparator()
        menu.addAction("Exit NtBar", self.close)
        menu.exec(self.menu_button.mapToGlobal(self.menu_button.rect().bottomLeft()))

    def show_about(self):
        QMessageBox.about(
            self, "About NtBar",
            "<b>NtBar</b><br>Secondary taskbar for Windows<br><br>"
            "Built with Python, PySide6 and native Windows APIs.<br>"
            "Project: github.com/BenFreedomWalker/ntbar"
        )

    def select_monitor(self, monitor):
        self.target = monitor
        self.settings.setValue("monitor_device", monitor["device"])
        self.place_bar()

    def refresh_windows(self):
        windows = enumerate_windows(int(self.winId()))
        signature = tuple((w.hwnd, w.title, w.minimized, w.active) for w in windows)
        if signature == self.signature:
            return
        self.signature = signature

        while self.buttons.count():
            item = self.buttons.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

        if windows:
            for window in windows:
                self.buttons.addWidget(TaskButton(window, self.panel_height, self.host))
        else:
            empty = QLabel("No open windows", self.host)
            empty.setAlignment(Qt.AlignVCenter | Qt.AlignLeft)
            empty.setStyleSheet("color:#858d98; padding:0 10px;")
            empty.setFixedHeight(max(1, self.panel_height - 8))
            self.buttons.addWidget(empty)

        self.buttons.activate()
        content_width = sum(
            self.buttons.itemAt(i).widget().sizeHint().width()
            for i in range(self.buttons.count())
            if self.buttons.itemAt(i).widget() is not None
        )
        if len(windows) > 1:
            content_width += self.buttons.spacing() * (len(windows) - 1)

        content_width = max(content_width, 160)
        inner_height = max(1, self.panel_height - min(8, self.panel_height - 1))
        self.host.setFixedSize(content_width, inner_height)

        monitor_width = (self.target or {}).get("width", 1920)
        panel_width = int(monitor_width * self.panel_length / 100)
        max_tasklist_width = max(1, min(int(monitor_width * 0.55), panel_width - 250))
        self.scroll.setFixedWidth(min(content_width, max_tasklist_width))
        self.scroll.setFixedHeight(inner_height)

    def show_settings(self):
        dialog = QDialog(self)
        dialog.setWindowTitle("NtBar Settings")
        dialog.setMinimumWidth(320)
        form = QFormLayout(dialog)

        monitor = QComboBox(dialog)
        monitors = monitor_list()
        for index, item in enumerate(monitors, 1):
            label = f'Monitor {index} — {item["width"]}×{item["height"]}'
            if item["primary"]:
                label += " (primary)"
            monitor.addItem(label, item)
            if self.target and item["device"] == self.target["device"]:
                monitor.setCurrentIndex(monitor.count() - 1)
        form.addRow("Display", monitor)

        edge = QComboBox(dialog)
        edge.addItem("Top", "top")
        edge.addItem("Bottom", "bottom")
        edge.setCurrentIndex(0 if self.panel_edge == "top" else 1)
        form.addRow("Position", edge)

        height = QSpinBox(dialog)
        height.setRange(1, 512)
        height.setSuffix(" px")
        height.setValue(self.panel_height)
        form.addRow("Panel thickness", height)

        length = QSpinBox(dialog)
        length.setRange(25, 100)
        length.setSuffix(" %")
        length.setValue(self.panel_length)
        form.addRow("Panel length", length)

        alignment = QComboBox(dialog)
        for value, label in (("left", "Left"), ("center", "Center"), ("right", "Right")):
            alignment.addItem(label, value)
        alignment.setCurrentIndex({"left": 0, "center": 1, "right": 2}.get(self.panel_alignment, 1))
        form.addRow("Alignment", alignment)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel, parent=dialog)
        buttons.button(QDialogButtonBox.Ok).setText("Apply")
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        form.addRow(buttons)

        if dialog.exec() == QDialog.Accepted:
            if monitor.currentData() is not None:
                self.target = monitor.currentData()
            self.panel_edge = edge.currentData()
            self.panel_height = height.value()
            self.panel_length = length.value()
            self.panel_alignment = alignment.currentData()
            if self.target:
                self.settings.setValue("monitor_device", self.target["device"])
            self.settings.setValue("panel_edge", self.panel_edge)
            self.settings.setValue("panel_height", self.panel_height)
            self.settings.setValue("panel_length", self.panel_length)
            self.settings.setValue("panel_alignment", self.panel_alignment)
            self.setFixedHeight(self.panel_height)
            margins = self.layout().contentsMargins()
            vertical_margin = min(4, max(0, (self.panel_height - 1) // 2))
            self.layout().setContentsMargins(margins.left(), vertical_margin, margins.right(), vertical_margin)
            self.menu_button.setFixedHeight(max(1, self.panel_height - vertical_margin * 2))
            self.place_bar()
            self.refresh_windows()

    def update_clock(self):
        self.clock.setText(datetime.now().strftime("%a %d %b   %H:%M"))

    def check_monitors(self):
        monitors = monitor_list()
        if not monitors:
            return
        if not any(m["device"] == (self.target or {}).get("device") for m in monitors):
            self.target = self.default_monitor()
            self.place_bar()

    def closeEvent(self, event):
        for timer in (self.refresh_timer, self.clock_timer, self.monitor_timer):
            timer.stop()
        if self.registered:
            remove_appbar(int(self.winId()))
            self.registered = False
        event.accept()
        app = QApplication.instance()
        if app is not None:
            app.quit()

def main():
    app = QApplication(sys.argv)
    app.setApplicationName("NtBar")
    signal.signal(signal.SIGINT, lambda *_: app.quit())
    interrupt_timer = QTimer()
    interrupt_timer.timeout.connect(lambda: None)
    interrupt_timer.start(200)
    app._interrupt_timer = interrupt_timer

    bar = SecondaryTaskbar()
    return_code = app.exec()
    if bar.registered:
        remove_appbar(int(bar.winId()))
        bar.registered = False
    sys.exit(return_code)

if __name__ == "__main__":
    main()
