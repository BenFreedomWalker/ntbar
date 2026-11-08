import signal
import sys
import ctypes
import json
import os
import subprocess
import winreg
from datetime import datetime
from PySide6.QtCore import Qt, QTimer, QSize
from PySide6.QtGui import QFont, QDesktopServices
from PySide6.QtWidgets import QApplication, QWidget, QHBoxLayout, QLabel, QToolButton, QMenu, QScrollArea, QFrame, QStyle, QMessageBox, QSizePolicy, QDialog, QFormLayout, QComboBox, QSpinBox, QDialogButtonBox, QCheckBox, QListWidget, QPushButton, QVBoxLayout, QInputDialog
from PySide6.QtCore import QUrl, QSettings
from win32_api import enumerate_windows, activate_or_minimize, close_window, monitor_list, register_appbar, remove_appbar, reposition_appbar, load_window_icon

if sys.platform != "win32":
    raise SystemExit("NtBar supports Windows 10 and 11.")

class TaskButton(QToolButton):
    BUTTON_WIDTH = 112

    def __init__(self, window, panel_height=44, button_width=96, show_label=True, parent=None):
        super().__init__(parent)
        self.hwnd = window.hwnd
        self.setToolButtonStyle(Qt.ToolButtonTextBesideIcon if show_label else Qt.ToolButtonIconOnly)
        self.setText(window.title if show_label else "")
        self.setToolTip(window.title)
        icon = load_window_icon(window.hwnd)
        self.setIcon(icon or QApplication.style().standardIcon(QStyle.SP_ComputerIcon))
        button_height = max(1, panel_height - 8)
        icon_size = max(8, min(20, button_height - 6))
        self.setIconSize(QSize(icon_size, icon_size))
        self.setFixedSize(max(32, button_width) if show_label else button_height, button_height)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self.setCursor(Qt.PointingHandCursor)
        self.setProperty("active", window.active)
        self.setProperty("minimized", window.minimized)
        self.setStyleSheet("""
            QToolButton { color:#e8e8e8; background:#30343b; border:1px solid #414750;
                border-radius:0; padding:4px 9px; text-align:left; }
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
    DEFAULT_PANEL_ITEMS = ["start", "tasks", "separator", "clock"]
    ITEM_LABELS = {
        "start": "Start menu (Open-Shell / Windows)",
        "tasks": "Tasklist",
        "clock": "Clock",
        "separator": "Separator",
        "expand": "Expanded separator",
    }

    def __init__(self):
        super().__init__()
        self.settings = QSettings("BenFreedomWalker", "NtBar")
        self.panel_height = self.settings.value("panel_height", self.DEFAULT_HEIGHT, type=int)
        self.panel_edge = self.settings.value("panel_edge", "bottom")
        self.panel_length = self.settings.value("panel_length", 100, type=int)
        self.panel_alignment = self.settings.value("panel_alignment", "center")
        self.show_task_labels = self.settings.value("show_task_labels", True, type=bool)
        self.task_button_width = self.settings.value("task_button_width", 96, type=int)
        try:
            self.panel_items = json.loads(self.settings.value("panel_items", json.dumps(self.DEFAULT_PANEL_ITEMS)))
            if not isinstance(self.panel_items, list) or not all(x in self.ITEM_LABELS for x in self.panel_items):
                raise ValueError("Invalid panel item list")
        except (TypeError, ValueError, json.JSONDecodeError):
            self.panel_items = list(self.DEFAULT_PANEL_ITEMS)
        self.setWindowTitle("NtBar")
        self.setWindowFlags(Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.setObjectName("NtBar")
        self.setFixedHeight(self.panel_height)
        self.setStyleSheet("""
            QWidget#NtBar { background:#202329; color:#f1f1f1; }
            QToolButton#panelItem { background:#292e36; color:#f1f1f1;
                border:1px solid #3c424c; border-radius:0; padding:4px 10px; }
            QToolButton#panelItem:hover { background:#3b424d; }
            QLabel#clock { color:#e2e5e9; padding:0 8px; }
            QScrollArea { border:0; background:transparent; }
        """)
        layout = QHBoxLayout(self)
        vertical_margin = min(4, max(0, (self.panel_height - 1) // 2))
        layout.setContentsMargins(7, vertical_margin, 7, vertical_margin)
        layout.setSpacing(7)

        self.task_scroll = QScrollArea(self)
        self.task_scroll.setWidgetResizable(False)
        self.task_scroll.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self.task_scroll.setFrameShape(QFrame.NoFrame)
        self.task_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.task_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.task_scroll.setMinimumWidth(0)
        self.task_host = QWidget()
        self.task_host.setAttribute(Qt.WA_StyledBackground, True)
        self.task_host.setStyleSheet("background:transparent;")
        self.buttons = QHBoxLayout(self.task_host)
        self.buttons.setContentsMargins(0, 0, 0, 0)
        self.buttons.setSpacing(2)
        self.buttons.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self.task_scroll.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self.task_scroll.setWidget(self.task_host)

        self.clock = QLabel(self)
        self.clock.setObjectName("clock")
        self.clock.setFont(QFont("Segoe UI", 9))
        self.clock.setAlignment(Qt.AlignCenter)
        self.rebuild_modules()

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
            geometry = reposition_appbar(
                int(self.winId()), self.target["handle"], self.panel_height,
                self.panel_edge, self.panel_length, self.panel_alignment
            )
            if geometry:
                self.setGeometry(*geometry)
            self.raise_()

    def contextMenuEvent(self, event):
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
        menu.addAction("Panel Items…", self.show_panel_items)
        menu.addAction("Settings…", self.show_settings)
        menu.addAction("Refresh windows", self.refresh_windows)
        menu.addAction("About NtBar", self.show_about)
        menu.addSeparator()
        menu.addAction("Exit NtBar", self.close)
        menu.exec(event.globalPos())

    def make_panel_item(self, item):
        if item == "tasks":
            return self.task_scroll
        if item == "clock":
            return self.clock
        if item == "separator":
            line = QFrame(self)
            line.setFrameShape(QFrame.VLine)
            line.setFrameShadow(QFrame.Plain)
            line.setStyleSheet("color:#555b65;")
            line.setFixedWidth(7)
            return line
        if item == "expand":
            spacer = QWidget(self)
            spacer.setMinimumWidth(0)
            spacer.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
            return spacer
        if item == "start":
            button = QToolButton(self)
            button.setObjectName("panelItem")
            button.setText("Start")
            button.setFixedHeight(max(1, self.panel_height - 8))
            button.clicked.connect(self.open_start_menu)
            return button
        return QWidget(self)

    def rebuild_modules(self):
        layout = self.layout()
        while layout.count():
            entry = layout.takeAt(0)
            widget = entry.widget()
            if widget is not None and widget not in (self.task_scroll, self.clock):
                widget.deleteLater()
        self.module_widgets = []
        for item in self.panel_items:
            widget = self.make_panel_item(item)
            self.module_widgets.append(widget)
            layout.addWidget(widget, 1 if item == "expand" else 0)
        self.update_clock()

    def find_open_shell(self):
        candidates = []
        for base in (os.environ.get("ProgramFiles"), os.environ.get("ProgramFiles(x86)")):
            if base:
                candidates.append(os.path.join(base, "Open-Shell", "StartMenu.exe"))

        uninstall_roots = (
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Uninstall"),
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\\WOW6432Node\\Microsoft\\Windows\\CurrentVersion\\Uninstall"),
            (winreg.HKEY_CURRENT_USER, r"SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Uninstall"),
        )
        for hive, root in uninstall_roots:
            try:
                with winreg.OpenKey(hive, root) as key:
                    count = winreg.QueryInfoKey(key)[0]
                    for index in range(count):
                        try:
                            subkey_name = winreg.EnumKey(key, index)
                            with winreg.OpenKey(key, subkey_name) as subkey:
                                display_name = winreg.QueryValueEx(subkey, "DisplayName")[0]
                                if "open-shell" not in display_name.lower():
                                    continue
                                install_dir = winreg.QueryValueEx(subkey, "InstallLocation")[0]
                                if install_dir:
                                    candidates.append(os.path.join(install_dir, "StartMenu.exe"))
                        except OSError:
                            continue
            except OSError:
                continue

        seen = set()
        for path in candidates:
            if not path:
                continue
            path = os.path.abspath(os.path.expandvars(path))
            normalized = os.path.normcase(path)
            if normalized in seen:
                continue
            seen.add(normalized)
            if not os.path.isfile(path):
                continue
            # Avoid unrelated StartMenu.exe files: accept only the known
            # Open-Shell install directory or a registered Open-Shell location.
            if os.path.basename(os.path.dirname(path)).lower() == "open-shell":
                return path
            for hive, root in uninstall_roots:
                try:
                    with winreg.OpenKey(hive, root) as key:
                        for index in range(winreg.QueryInfoKey(key)[0]):
                            try:
                                with winreg.OpenKey(key, winreg.EnumKey(key, index)) as subkey:
                                    name = winreg.QueryValueEx(subkey, "DisplayName")[0]
                                    location = winreg.QueryValueEx(subkey, "InstallLocation")[0]
                                    if "open-shell" in name.lower() and location and os.path.normcase(os.path.abspath(location)) == os.path.normcase(os.path.dirname(path)):
                                        return path
                            except OSError:
                                continue
                except OSError:
                    continue
        return None

    def open_windows_start_menu(self):
        user32 = ctypes.windll.user32
        vk_lwin = 0x5B
        keyeventf_keyup = 0x0002
        user32.keybd_event(vk_lwin, 0, 0, 0)
        user32.keybd_event(vk_lwin, 0, keyeventf_keyup, 0)

    def open_start_menu(self):
        executable = self.find_open_shell()
        if executable:
            try:
                subprocess.Popen([executable, "-togglenew"], close_fds=True)
                return
            except OSError:
                pass

        # Open-Shell is unavailable or could not be started: use Windows' own menu.
        self.open_windows_start_menu()

    def show_panel_items(self):
        dialog = QDialog(self)
        dialog.setWindowTitle("Panel Items")
        dialog.setMinimumSize(360, 330)
        outer = QVBoxLayout(dialog)
        listing = QListWidget(dialog)
        for item in self.panel_items:
            listing.addItem(self.ITEM_LABELS[item])
        outer.addWidget(listing)

        controls = QHBoxLayout()
        add_button = QPushButton("Add item…", dialog)
        remove_button = QPushButton("Remove", dialog)
        up_button = QPushButton("Move up", dialog)
        down_button = QPushButton("Move down", dialog)
        for button in (add_button, remove_button, up_button, down_button):
            controls.addWidget(button)
        outer.addLayout(controls)

        def add_item():
            unique_items = {"start", "tasks", "clock"}
            label_keys = {value: key for key, value in self.ITEM_LABELS.items()}
            present = {
                label_keys[listing.item(i).text()]
                for i in range(listing.count())
                if label_keys[listing.item(i).text()] in unique_items
            }
            choices = [value for key, value in self.ITEM_LABELS.items() if key not in present]
            if not choices:
                return
            selected, accepted = QInputDialog.getItem(dialog, "Add panel item", "Item:", choices, 0, False)
            if accepted:
                key = next(k for k, value in self.ITEM_LABELS.items() if value == selected)
                listing.addItem(self.ITEM_LABELS[key])

        def move_item(delta):
            row = listing.currentRow()
            target = row + delta
            if row < 0 or not 0 <= target < listing.count():
                return
            item = listing.takeItem(row)
            listing.insertItem(target, item)
            listing.setCurrentRow(target)

        add_button.clicked.connect(add_item)
        remove_button.clicked.connect(lambda: listing.takeItem(listing.currentRow()) if listing.currentRow() >= 0 else None)
        up_button.clicked.connect(lambda: move_item(-1))
        down_button.clicked.connect(lambda: move_item(1))

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel, parent=dialog)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        outer.addWidget(buttons)
        if dialog.exec() == QDialog.Accepted:
            reverse = {value: key for key, value in self.ITEM_LABELS.items()}
            self.panel_items = [reverse[listing.item(i).text()] for i in range(listing.count())]
            self.settings.setValue("panel_items", json.dumps(self.panel_items))
            self.rebuild_modules()

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
                self.buttons.addWidget(TaskButton(window, self.panel_height, self.task_button_width, self.show_task_labels, self.task_host))
        else:
            empty = QLabel("No open windows", self.task_host)
            empty.setAlignment(Qt.AlignVCenter | Qt.AlignLeft)
            empty.setStyleSheet("color:#858d98; padding:0 10px;")
            empty.setFixedHeight(max(1, self.panel_height - 8))
            self.buttons.addWidget(empty)

        self.buttons.activate()
        content_width = sum(
            self.buttons.itemAt(i).widget().width()
            for i in range(self.buttons.count())
            if self.buttons.itemAt(i).widget() is not None
        )
        if len(windows) > 1:
            content_width += self.buttons.spacing() * (len(windows) - 1)

        content_width = max(content_width, 1)
        inner_height = max(1, self.panel_height - min(8, self.panel_height - 1))
        self.task_host.setFixedSize(content_width, inner_height)

        self.task_scroll.setMinimumWidth(0)
        available_width = max(1, self.width() - 14)
        self.task_scroll.setFixedSize(min(content_width, available_width), inner_height)

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

        button_width = QSpinBox(dialog)
        button_width.setRange(48, 240)
        button_width.setSingleStep(8)
        button_width.setSuffix(" px")
        button_width.setValue(self.task_button_width)
        form.addRow("Task button width", button_width)

        show_labels = QCheckBox("Show window titles on task buttons", dialog)
        show_labels.setChecked(self.show_task_labels)
        form.addRow("Task buttons", show_labels)

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
            self.show_task_labels = show_labels.isChecked()
            self.task_button_width = button_width.value()
            if self.target:
                self.settings.setValue("monitor_device", self.target["device"])
            self.settings.setValue("panel_edge", self.panel_edge)
            self.settings.setValue("panel_height", self.panel_height)
            self.settings.setValue("panel_length", self.panel_length)
            self.settings.setValue("panel_alignment", self.panel_alignment)
            self.settings.setValue("show_task_labels", self.show_task_labels)
            self.settings.setValue("task_button_width", self.task_button_width)
            self.setFixedHeight(self.panel_height)
            margins = self.layout().contentsMargins()
            vertical_margin = min(4, max(0, (self.panel_height - 1) // 2))
            self.layout().setContentsMargins(margins.left(), vertical_margin, margins.right(), vertical_margin)
            self.rebuild_modules()
            self.place_bar()
            self.signature = None
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
    app.setQuitOnLastWindowClosed(False)
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
