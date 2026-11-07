import sys
from datetime import datetime
from PySide6.QtCore import Qt, QTimer, QSize
from PySide6.QtGui import QFont, QDesktopServices
from PySide6.QtWidgets import QApplication, QWidget, QHBoxLayout, QLabel, QToolButton, QMenu, QScrollArea, QFrame, QStyle, QMessageBox
from PySide6.QtCore import QUrl
from win32_api import enumerate_windows, activate_or_minimize, close_window, monitor_list, register_appbar, remove_appbar, reposition_appbar, load_window_icon

if sys.platform != "win32":
    raise SystemExit("NtBar supports Windows 10 and 11.")

class TaskButton(QToolButton):
    def __init__(self, window, parent=None):
        super().__init__(parent)
        self.hwnd = window.hwnd
        self.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.setText(window.title)
        self.setToolTip(window.title)
        icon = load_window_icon(window.hwnd)
        self.setIcon(icon or QApplication.style().standardIcon(QStyle.SP_ComputerIcon))
        self.setIconSize(QSize(20, 20))
        self.setFixedHeight(36)
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
    HEIGHT = 44

    def __init__(self):
        super().__init__()
        self.setWindowTitle("NtBar")
        self.setWindowFlags(Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.setObjectName("NtBar")
        self.setFixedHeight(self.HEIGHT)
        self.setStyleSheet("""
            QWidget#NtBar { background:#202329; color:#f1f1f1; }
            QToolButton#menuButton { background:#292e36; color:#f1f1f1;
                border:1px solid #3c424c; border-radius:5px; padding:5px 11px; }
            QToolButton#menuButton:hover { background:#3b424d; }
            QLabel#clock { color:#e2e5e9; padding:0 12px; }
            QScrollArea { border:0; background:transparent; }
        """)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(7, 4, 7, 4)
        layout.setSpacing(7)

        self.menu_button = QToolButton(self)
        self.menu_button.setObjectName("menuButton")
        self.menu_button.setText("NtBar")
        self.menu_button.clicked.connect(self.show_menu)
        layout.addWidget(self.menu_button)
        layout.addWidget(self.make_separator())

        self.scroll = QScrollArea(self)
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.host = QWidget()
        self.buttons = QHBoxLayout(self.host)
        self.buttons.setContentsMargins(0, 0, 0, 0)
        self.buttons.setSpacing(5)
        self.scroll.setWidget(self.host)
        layout.addWidget(self.scroll, 1)
        layout.addWidget(self.make_separator())

        self.clock = QLabel(self)
        self.clock.setObjectName("clock")
        self.clock.setFont(QFont("Segoe UI", 9))
        layout.addWidget(self.clock)

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

    def make_separator(self):
        separator = QFrame(self)
        separator.setFrameShape(QFrame.VLine)
        separator.setFrameShadow(QFrame.Plain)
        separator.setFixedWidth(1)
        separator.setStyleSheet("QFrame { color:#414750; background:#414750; max-width:1px; }")
        return separator

    def default_monitor(self):
        monitors = monitor_list()
        return next((m for m in monitors if not m["primary"]), monitors[0] if monitors else None)

    def place_bar(self):
        if self.target:
            reposition_appbar(int(self.winId()), self.target["handle"], self.HEIGHT)
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
        menu.addAction("Refresh windows", self.refresh_windows)
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
        self.place_bar()

    def refresh_windows(self):
        windows = enumerate_windows(int(self.winId()))
        signature = tuple((w.hwnd, w.title, w.minimized, w.active) for w in windows)
        if signature == self.signature:
            return
        self.signature = signature
        while self.buttons.count():
            item = self.buttons.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        for window in windows:
            self.buttons.addWidget(TaskButton(window, self.host))
        self.buttons.addStretch(1)

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
        event.accept()

def main():
    app = QApplication(sys.argv)
    app.setApplicationName("NtBar")
    bar = SecondaryTaskbar()
    sys.exit(app.exec())

if __name__ == "__main__":
    main()
