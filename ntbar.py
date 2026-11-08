import signal
import sys
import ctypes
import json
import os
import subprocess
import uuid
import winreg
from datetime import datetime

from PySide6.QtCore import Qt, QTimer, QSize, QUrl, QSettings
from PySide6.QtGui import QFont, QDesktopServices, QFontMetrics, QIcon
from PySide6.QtWidgets import (
    QApplication, QWidget, QHBoxLayout, QLabel, QToolButton, QMenu,
    QScrollArea, QFrame, QStyle, QMessageBox, QSizePolicy, QDialog,
    QFormLayout, QComboBox, QSpinBox, QDialogButtonBox, QCheckBox,
    QListWidget, QListWidgetItem, QPushButton, QVBoxLayout, QInputDialog,
    QColorDialog, QFileDialog, QLineEdit, QFontComboBox
)

from win32_api import (
    enumerate_windows, activate_or_minimize, close_window, monitor_list,
    register_appbar, remove_appbar, reposition_appbar, load_window_icon
)


if sys.platform != "win32":
    raise SystemExit("NtBar supports Windows 10 and 11.")


class TaskButton(QToolButton):
    def __init__(self, window, panel_height, config, button_color, text_color, parent=None):
        super().__init__(parent)
        self.hwnd = window.hwnd
        self.show_label = bool(config.get("show_labels", True))
        self.original_title = window.title
        self.margin_x = max(0, int(config.get("margin_x", 4)))
        self.margin_y = max(0, int(config.get("margin_y", 2)))
        self.corner_radius = max(0, int(config.get("corner_radius", 0)))
        background = SecondaryTaskbar.item_background(config, button_color)
        hover_background = SecondaryTaskbar.item_background(config, self._shade(button_color, 1.18))
        active_background = SecondaryTaskbar.item_background(config, self._shade(button_color, 0.82))
        self.setFont(QFont(str(config.get("font_family", "Segoe UI")), max(6, int(config.get("font_size", 9)))))
        self.scroll_offset = 0
        self.marquee_timer = QTimer(self)
        self.marquee_timer.setInterval(180)
        self.marquee_timer.timeout.connect(self.advance_marquee)
        self.setToolButtonStyle(
            Qt.ToolButtonTextBesideIcon if self.show_label else Qt.ToolButtonIconOnly
        )
        self.setText(window.title if self.show_label else "")
        self.setToolTip(window.title)
        icon = load_window_icon(window.hwnd)
        self.setIcon(icon or QApplication.style().standardIcon(QStyle.SP_ComputerIcon))
        button_height = max(1, panel_height)
        width = max(32, int(config.get("button_width", 96)))
        if not self.show_label:
            width = button_height
        icon_size = max(8, int(config.get("icon_size", 20)))
        self.setIconSize(QSize(icon_size, icon_size))
        self.setFixedSize(width, button_height)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self.setCursor(Qt.PointingHandCursor)
        self.setProperty("active", window.active)
        self.setProperty("minimized", window.minimized)
        self.setStyleSheet(f"""
            QToolButton {{ color:{text_color}; background:{background}; border:1px solid {background};
                border-radius:{self.corner_radius}px; padding:{self.margin_y}px {self.margin_x}px; text-align:left; }}
            QToolButton:hover {{ background:{hover_background}; }}
            QToolButton[active="true"] {{ background:{active_background}; border-bottom:2px solid #79a9e8; }}
            QToolButton[minimized="true"] {{ color:{text_color}; }}
        """)
        self.clicked.connect(lambda: activate_or_minimize(self.hwnd))

    @staticmethod
    def _shade(color, factor):
        from PySide6.QtGui import QColor
        shade = QColor(color)
        return shade.lighter(int(factor * 100)).name() if factor >= 1 else shade.darker(int(100 / factor)).name()

    def marquee_text(self):
        if not self.show_label or not self.original_title:
            return
        available = max(1, self.width() - self.iconSize().width() - 2 * self.margin_x - 28)
        metrics = QFontMetrics(self.font())
        if metrics.horizontalAdvance(self.original_title) <= available:
            self.setText(self.original_title)
            return
        stream = self.original_title + "     " + self.original_title
        start = self.scroll_offset % (len(self.original_title) + 5)
        visible = ""
        for char in stream[start:]:
            candidate = visible + char
            if metrics.horizontalAdvance(candidate) > available:
                break
            visible = candidate
        self.setText(visible)

    def advance_marquee(self):
        self.scroll_offset += 1
        self.marquee_text()

    def enterEvent(self, event):
        super().enterEvent(event)
        if self.show_label:
            self.scroll_offset = 0
            self.marquee_text()
            self.marquee_timer.start()

    def leaveEvent(self, event):
        self.marquee_timer.stop()
        self.scroll_offset = 0
        self.setText(self.original_title if self.show_label else "")
        super().leaveEvent(event)

    def contextMenuEvent(self, event):
        menu = QMenu(self)
        menu.addAction("Close window", lambda: close_window(self.hwnd))
        menu.exec(event.globalPos())


class SecondaryTaskbar(QWidget):
    DEFAULT_HEIGHT = 44
    ITEM_LABELS = {
        "start": "Start menu",
        "tasks": "Tasklist",
        "clock": "Clock",
        "separator": "Separator",
        "launcher": "Launcher",
    }

    def __init__(self):
        super().__init__()
        self.settings = QSettings("BenFreedomWalker", "NtBar")
        self.panel_height = self.settings.value("panel_height", self.DEFAULT_HEIGHT, type=int)
        self.panel_edge = self.settings.value("panel_edge", "bottom")
        self.panel_length = self.settings.value("panel_length", 100, type=int)
        self.panel_alignment = self.settings.value("panel_alignment", "center")
        self.panel_color = self.settings.value("panel_color", "#202329")
        self.button_color = self.settings.value("button_color", "#292e36")
        self.text_color = self.settings.value("text_color", "#f1f1f1")
        self.items = self.load_items()
        self.setWindowTitle("NtBar")
        self.setWindowFlags(Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setObjectName("NtBar")
        self.setFixedHeight(self.panel_height)
        self.apply_panel_style()

        self.task_hosts = []
        self.module_widgets = []
        self.signature = None
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.rebuild_modules()

        self.target = None
        self.registered = False
        self.show()
        self.target = self.default_monitor()
        self.registered = register_appbar(int(self.winId()))
        self.place_bar()
        self.refresh_windows()
        self.update_clocks()

        self.refresh_timer = QTimer(self)
        self.refresh_timer.timeout.connect(self.refresh_windows)
        self.refresh_timer.start(900)
        self.clock_timer = QTimer(self)
        self.clock_timer.timeout.connect(self.update_clocks)
        self.clock_timer.start(1000)
        self.monitor_timer = QTimer(self)
        self.monitor_timer.timeout.connect(self.check_monitors)
        self.monitor_timer.start(2500)

    def default_config(self, kind):
        if kind == "clock":
            return {"show_date": True, "show_seconds": False, "font_size": 9, "font_family": "Segoe UI", "corner_radius": 0, "time_format": "24h", "show_am_pm": True, "show_date_on_hover": True}
        if kind == "tasks":
            return {"show_labels": True, "button_width": 96, "margin_x": 4, "margin_y": 2, "icon_size": 20, "font_family": "Segoe UI", "font_size": 9, "corner_radius": 0}
        if kind == "separator":
            return {"visible": True, "expand": False, "width": 8}
        if kind == "start":
            return {"label": "Start", "icon": "", "margin_x": 10, "margin_y": 2, "icon_size": 24, "font_family": "Segoe UI", "font_size": 9, "corner_radius": 0}
        if kind == "launcher":
            return {"name": "Launcher", "icon": "", "command": "", "margin_x": 10, "margin_y": 2, "icon_size": 24, "font_family": "Segoe UI", "font_size": 9, "corner_radius": 0}
        return {}

    def load_items(self):
        raw = self.settings.value("panel_items", "")
        try:
            parsed = json.loads(raw) if raw else None
        except (TypeError, ValueError):
            parsed = None
        if not isinstance(parsed, list):
            parsed = [
                {"id": uuid.uuid4().hex, "type": "start", "config": self.default_config("start")},
                {"id": uuid.uuid4().hex, "type": "tasks", "config": self.default_config("tasks")},
                {"id": uuid.uuid4().hex, "type": "separator", "config": self.default_config("separator")},
                {"id": uuid.uuid4().hex, "type": "clock", "config": self.default_config("clock")},
            ]
        normalized = []
        for entry in parsed:
            if isinstance(entry, str):
                kind = "separator" if entry == "expand" else entry
                config = self.default_config(kind)
                if entry == "expand":
                    config["expand"] = True
            elif isinstance(entry, dict):
                kind = entry.get("type")
                config = self.default_config(kind) if kind in self.ITEM_LABELS else {}
                config.update(entry.get("config", {}) if isinstance(entry.get("config"), dict) else {})
            else:
                continue
            if kind not in self.ITEM_LABELS:
                continue
            normalized.append({
                "id": str(entry.get("id") if isinstance(entry, dict) else uuid.uuid4().hex),
                "type": kind,
                "config": config,
            })
        return normalized

    def save_items(self):
        self.settings.setValue("panel_items", json.dumps(self.items))

    @staticmethod
    def item_background(config, color):
        # Panel items use opaque backgrounds; transparency and acrylic are disabled.
        from PySide6.QtGui import QColor
        return QColor(color).name()

    def apply_panel_style(self):
        self.setStyleSheet(f"""
            QWidget#NtBar {{ background:{self.panel_color}; color:{self.text_color}; }}
            QToolButton#panelItem {{ color:{self.text_color}; border:0; }}
            QLabel#clockItem {{ color:{self.text_color}; padding:0 6px; }}
            QScrollArea {{ border:0; background:transparent; }}
        """)

    def persist_panel_settings(self):
        for key, value in (
            ("panel_edge", self.panel_edge),
            ("panel_height", self.panel_height),
            ("panel_length", self.panel_length),
            ("panel_alignment", self.panel_alignment),
            ("panel_color", self.panel_color),
            ("button_color", self.button_color),
            ("text_color", self.text_color),
        ):
            self.settings.setValue(key, value)

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
        self.show_panel_context_menu(event.globalPos())

    def show_panel_context_menu(self, global_pos):
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
        menu.addAction("Panel Settings…", self.show_settings)
        menu.addAction("Refresh windows", self.refresh_windows)
        menu.addAction("About NtBar", self.show_about)
        menu.addSeparator()
        menu.addAction("Exit NtBar", self.close)
        menu.exec(global_pos)

    def make_tasklist(self, config):
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(False)
        scroll.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setMinimumWidth(0)
        host = QWidget()
        host.setAttribute(Qt.WA_StyledBackground, True)
        host.setStyleSheet("background:transparent;")
        buttons = QHBoxLayout(host)
        buttons.setContentsMargins(0, 0, 0, 0)
        buttons.setSpacing(2)
        buttons.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        scroll.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        scroll.setWidget(host)
        self.task_hosts.append({"scroll": scroll, "host": host, "layout": buttons, "config": config})
        return scroll

    def make_panel_item(self, entry):
        kind = entry["type"]
        config = entry["config"]
        if kind == "tasks":
            return self.make_tasklist(config)
        if kind == "clock":
            label = QLabel(self)
            label.setObjectName("clockItem")
            label.setAlignment(Qt.AlignCenter)
            label.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
            label.setFont(QFont(str(config.get("font_family", "Segoe UI")), max(6, int(config.get("font_size", 9)))))
            label.setStyleSheet(f"color:{self.text_color}; background:{self.item_background(config, self.button_color)}; border-radius:{max(0, int(config.get('corner_radius', 0)))}px; padding:0 6px;")
            label.setToolTipDuration(5000)
            label.setProperty("item_id", entry["id"])
            return label
        if kind == "separator":
            if config.get("expand", False):
                spacer = QWidget(self)
                spacer.setMinimumWidth(0)
                spacer.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
                return spacer
            separator = QFrame(self)
            if config.get("visible", True):
                separator.setFrameShape(QFrame.VLine)
                separator.setFrameShadow(QFrame.Plain)
                separator.setStyleSheet("color:#555b65;")
            else:
                separator.setFrameShape(QFrame.NoFrame)
                separator.setStyleSheet("background:transparent;")
            separator.setFixedWidth(max(0, int(config.get("width", 8))))
            return separator
        if kind == "start":
            button = QToolButton(self)
            button.setObjectName("panelItem")
            button.setFont(QFont(str(config.get("font_family", "Segoe UI")), max(6, int(config.get("font_size", 9)))))
            start_label = str(config.get("label", "")).strip()
            button.setText(start_label)
            button.setToolButtonStyle(Qt.ToolButtonTextBesideIcon if start_label else Qt.ToolButtonIconOnly)
            icon_path = str(config.get("icon", ""))
            if icon_path and os.path.isfile(icon_path):
                button.setIcon(QIcon(icon_path))
                icon_size = max(8, int(config.get("icon_size", 24)))
                button.setIconSize(QSize(icon_size, icon_size))
            button.setStyleSheet(f"background:{self.item_background(config, self.button_color)}; padding:{int(config.get('margin_y', 2))}px {int(config.get('margin_x', 10))}px; border-radius:{max(0, int(config.get('corner_radius', 0)))}px;")
            button.setFixedHeight(max(1, self.panel_height))
            button.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
            button.clicked.connect(self.open_start_menu)
            return button
        if kind == "launcher":
            button = QToolButton(self)
            button.setObjectName("panelItem")
            button.setFont(QFont(str(config.get("font_family", "Segoe UI")), max(6, int(config.get("font_size", 9)))))
            launcher_name = str(config.get("name", "")).strip()
            button.setText(launcher_name)
            button.setToolButtonStyle(
                Qt.ToolButtonTextBesideIcon if launcher_name else Qt.ToolButtonIconOnly
            )
            button.setStyleSheet(f"padding:{int(config.get('margin_y', 2))}px {int(config.get('margin_x', 10))}px; border-radius:{max(0, int(config.get('corner_radius', 0)))}px;")
            icon_path = str(config.get("icon", ""))
            if icon_path and os.path.isfile(icon_path):
                button.setIcon(QIcon(icon_path))
                icon_size = max(8, int(config.get("icon_size", 24)))
                button.setIconSize(QSize(icon_size, icon_size))
            button.setFixedHeight(max(1, self.panel_height))
            button.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
            command = str(config.get("command", "")).strip()
            button.setEnabled(bool(command))
            button.setToolTip(command or "Set a launch command in item settings")
            button.clicked.connect(lambda checked=False, cmd=command: self.launch_command(cmd))
            return button
        return QWidget(self)

    def rebuild_modules(self):
        layout = self.layout()
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self.task_hosts = []
        self.module_widgets = []
        for entry in self.items:
            widget = self.make_panel_item(entry)
            self.module_widgets.append(widget)
            layout.addWidget(widget, 1 if entry["type"] == "separator" and entry["config"].get("expand", False) else 0)
        self.update_clocks()
        self.signature = None
        self.refresh_windows()

    def find_open_shell(self):
        candidates = []
        for base in (os.environ.get("ProgramFiles"), os.environ.get("ProgramFiles(x86)")):
            if base:
                candidates.append(os.path.join(base, "Open-Shell", "StartMenu.exe"))
        uninstall_roots = (
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
            (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
        )
        for hive, root in uninstall_roots:
            try:
                with winreg.OpenKey(hive, root) as key:
                    for index in range(winreg.QueryInfoKey(key)[0]):
                        try:
                            with winreg.OpenKey(key, winreg.EnumKey(key, index)) as subkey:
                                name = winreg.QueryValueEx(subkey, "DisplayName")[0]
                                if "open-shell" in name.lower():
                                    location = winreg.QueryValueEx(subkey, "InstallLocation")[0]
                                    if location:
                                        candidates.append(os.path.join(location, "StartMenu.exe"))
                        except OSError:
                            continue
            except OSError:
                continue
        for path in candidates:
            if path and os.path.isfile(path):
                if os.path.basename(os.path.dirname(path)).lower() == "open-shell":
                    return path
        return None

    def open_windows_start_menu(self):
        user32 = ctypes.windll.user32
        user32.keybd_event(0x5B, 0, 0, 0)
        user32.keybd_event(0x5B, 0, 0x0002, 0)

    def open_start_menu(self):
        executable = self.find_open_shell()
        if executable:
            try:
                subprocess.Popen([executable, "-togglenew"], close_fds=True)
                return
            except OSError:
                pass
        self.open_windows_start_menu()

    def launch_command(self, command):
        if not command:
            return
        try:
            subprocess.Popen(command, shell=True, cwd=os.path.expanduser("~"))
        except OSError as error:
            QMessageBox.warning(self, "Launcher", f"Could not start the command:\n{error}")

    def item_title(self, entry):
        kind = entry["type"]
        config = entry["config"]
        title = self.ITEM_LABELS[kind]
        if kind == "launcher":
            name = str(config.get("name", "")).strip()
            if name:
                title += f" — {name}"
        elif kind == "start":
            title += f' — {config.get("label", "Start")}'
        return title

    def show_panel_items(self):
        dialog = QDialog(self)
        dialog.setWindowTitle("Panel Items")
        dialog.setMinimumSize(440, 390)
        outer = QVBoxLayout(dialog)
        listing = QListWidget(dialog)
        listing.setSelectionMode(QListWidget.SingleSelection)
        for entry in self.items:
            row = QListWidgetItem(self.item_title(entry))
            row.setData(Qt.UserRole, entry["id"])
            listing.addItem(row)
        outer.addWidget(listing)

        controls = QHBoxLayout()
        add_button = QPushButton("Add item…", dialog)
        settings_button = QPushButton("Settings…", dialog)
        remove_button = QPushButton("Remove", dialog)
        up_button = QPushButton("Move up", dialog)
        down_button = QPushButton("Move down", dialog)
        for button in (add_button, settings_button, remove_button, up_button, down_button):
            controls.addWidget(button)
        outer.addLayout(controls)

        def selected_index():
            row = listing.currentRow()
            return row if 0 <= row < len(self.items) else -1

        def refresh_list(select_row=-1):
            listing.clear()
            for entry in self.items:
                row = QListWidgetItem(self.item_title(entry))
                row.setData(Qt.UserRole, entry["id"])
                listing.addItem(row)
            if listing.count():
                listing.setCurrentRow(max(0, min(select_row, listing.count() - 1)))

        def add_item():
            choices = list(self.ITEM_LABELS.values())
            selected, accepted = QInputDialog.getItem(dialog, "Add panel item", "Item type:", choices, 0, False)
            if not accepted:
                return
            kind = next(key for key, value in self.ITEM_LABELS.items() if value == selected)
            entry = {"id": uuid.uuid4().hex, "type": kind, "config": self.default_config(kind)}
            self.items.append(entry)
            refresh_list(len(self.items) - 1)
            self.save_items()
            self.rebuild_modules()

        def move_item(delta):
            index = selected_index()
            target = index + delta
            if index < 0 or not 0 <= target < len(self.items):
                return
            entry = self.items.pop(index)
            self.items.insert(target, entry)
            refresh_list(target)
            self.save_items()
            self.rebuild_modules()

        def remove_item():
            index = selected_index()
            if index < 0:
                return
            self.items.pop(index)
            refresh_list(min(index, len(self.items) - 1))
            self.save_items()
            self.rebuild_modules()

        def edit_selected():
            index = selected_index()
            if index < 0:
                return
            if self.edit_item_settings(self.items[index]):
                refresh_list(index)
                self.save_items()
                self.rebuild_modules()

        add_button.clicked.connect(add_item)
        settings_button.clicked.connect(edit_selected)
        listing.itemDoubleClicked.connect(lambda _item: edit_selected())
        remove_button.clicked.connect(remove_item)
        up_button.clicked.connect(lambda: move_item(-1))
        down_button.clicked.connect(lambda: move_item(1))

        buttons = QDialogButtonBox(QDialogButtonBox.Close, parent=dialog)
        buttons.rejected.connect(dialog.reject)
        buttons.accepted.connect(dialog.accept)
        outer.addWidget(buttons)
        dialog.exec()

    def edit_item_settings(self, entry):
        kind = entry["type"]
        config = dict(entry["config"])
        dialog = QDialog(self)
        dialog.setWindowTitle(f"{self.ITEM_LABELS[kind]} Settings")
        form = QFormLayout(dialog)

        if kind != "separator":
            font_family = QFontComboBox(dialog)
            font_family.setCurrentFont(QFont(str(config.get("font_family", "Segoe UI"))))
            form.addRow("Font", font_family)
            font_size = QSpinBox(dialog)
            font_size.setRange(6, 32)
            font_size.setSuffix(" pt")
            font_size.setValue(int(config.get("font_size", 9)))
            form.addRow("Font size", font_size)
            corner_radius = QSpinBox(dialog)
            corner_radius.setRange(0, 32)
            corner_radius.setSuffix(" px")
            corner_radius.setValue(int(config.get("corner_radius", 0)))
            form.addRow("Corner radius", corner_radius)

        if kind == "clock":
            show_date = QCheckBox("Show date on the panel", dialog)
            show_date.setChecked(bool(config.get("show_date", True)))
            form.addRow("Date", show_date)
            show_seconds = QCheckBox("Show seconds", dialog)
            show_seconds.setChecked(bool(config.get("show_seconds", False)))
            form.addRow("Time", show_seconds)
            time_format = QComboBox(dialog)
            time_format.addItem("24-hour (14:30)", "24h")
            time_format.addItem("12-hour (2:30)", "12h")
            time_format.setCurrentIndex(0 if config.get("time_format", "24h") == "24h" else 1)
            form.addRow("Time format", time_format)
            show_am_pm = QCheckBox("Show AM / PM", dialog)
            show_am_pm.setChecked(bool(config.get("show_am_pm", True)))
            show_am_pm.setEnabled(time_format.currentData() == "12h")
            time_format.currentIndexChanged.connect(lambda _index: show_am_pm.setEnabled(time_format.currentData() == "12h"))
            form.addRow("12-hour clock", show_am_pm)
            hover_date = QCheckBox("Show full date near cursor on hover", dialog)
            hover_date.setChecked(bool(config.get("show_date_on_hover", True)))
            form.addRow("Hover", hover_date)
        elif kind == "tasks":
            show_labels = QCheckBox("Show window titles", dialog)
            show_labels.setChecked(bool(config.get("show_labels", True)))
            form.addRow("Labels", show_labels)
            width = QSpinBox(dialog)
            width.setRange(48, 320)
            width.setSingleStep(8)
            width.setSuffix(" px")
            width.setValue(int(config.get("button_width", 96)))
            form.addRow("Button width", width)
            margin_x = QSpinBox(dialog)
            margin_x.setRange(0, 24)
            margin_x.setSuffix(" px")
            margin_x.setValue(int(config.get("margin_x", 4)))
            form.addRow("Horizontal padding", margin_x)
            margin_y = QSpinBox(dialog)
            margin_y.setRange(0, 16)
            margin_y.setSuffix(" px")
            margin_y.setValue(int(config.get("margin_y", 2)))
            form.addRow("Vertical padding", margin_y)
            task_icon_size = QSpinBox(dialog)
            task_icon_size.setRange(8, 64)
            task_icon_size.setSuffix(" px")
            task_icon_size.setValue(int(config.get("icon_size", 20)))
            form.addRow("Icon size", task_icon_size)
        elif kind == "separator":
            visible = QComboBox(dialog)
            visible.addItem("Visible line", True)
            visible.addItem("Transparent", False)
            visible.setCurrentIndex(0 if config.get("visible", True) else 1)
            form.addRow("Appearance", visible)
            expand = QCheckBox("Expand to fill available space", dialog)
            expand.setChecked(bool(config.get("expand", False)))
            form.addRow("Size", expand)
            width = QSpinBox(dialog)
            width.setRange(0, 64)
            width.setSuffix(" px")
            width.setValue(int(config.get("width", 8)))
            form.addRow("Width", width)
        elif kind == "start":
            label = QLineEdit(str(config.get("label", "")), dialog)
            label.setPlaceholderText("Optional — leave empty for an icon-only button")
            form.addRow("Name (optional)", label)
            icon_path = {"value": str(config.get("icon", ""))}
            icon_row = QHBoxLayout()
            icon_edit = QLineEdit(icon_path["value"], dialog)
            browse = QPushButton("Browse…", dialog)
            def choose_start_icon():
                path, _ = QFileDialog.getOpenFileName(dialog, "Choose Start button icon", "", "Icons and images (*.ico *.png *.bmp *.jpg *.jpeg);;All files (*)")
                if path:
                    icon_path["value"] = path
                    icon_edit.setText(path)
            browse.clicked.connect(choose_start_icon)
            icon_edit.textChanged.connect(lambda value: icon_path.update(value=value))
            icon_row.addWidget(icon_edit)
            icon_row.addWidget(browse)
            form.addRow("Icon", icon_row)
            start_margin_x = QSpinBox(dialog)
            start_margin_x.setRange(0, 32)
            start_margin_x.setSuffix(" px")
            start_margin_x.setValue(int(config.get("margin_x", 10)))
            form.addRow("Horizontal padding", start_margin_x)
            start_margin_y = QSpinBox(dialog)
            start_margin_y.setRange(0, 16)
            start_margin_y.setSuffix(" px")
            start_margin_y.setValue(int(config.get("margin_y", 2)))
            form.addRow("Vertical padding", start_margin_y)
            start_icon_size = QSpinBox(dialog)
            start_icon_size.setRange(8, 64)
            start_icon_size.setSuffix(" px")
            start_icon_size.setValue(int(config.get("icon_size", 24)))
            form.addRow("Icon size", start_icon_size)
        elif kind == "launcher":
            name = QLineEdit(str(config.get("name", "")), dialog)
            name.setPlaceholderText("Optional — leave empty for an icon-only launcher")
            form.addRow("Name (optional)", name)
            icon_path = {"value": str(config.get("icon", ""))}
            icon_row = QHBoxLayout()
            icon_edit = QLineEdit(icon_path["value"], dialog)
            browse = QPushButton("Browse…", dialog)
            def choose_icon():
                path, _ = QFileDialog.getOpenFileName(
                    dialog, "Choose launcher icon", "",
                    "Icons and images (*.ico *.png *.bmp *.jpg *.jpeg);;All files (*)"
                )
                if path:
                    icon_path["value"] = path
                    icon_edit.setText(path)
            browse.clicked.connect(choose_icon)
            icon_edit.textChanged.connect(lambda value: icon_path.update(value=value))
            icon_row.addWidget(icon_edit)
            icon_row.addWidget(browse)
            form.addRow("Icon", icon_row)
            command = QLineEdit(str(config.get("command", "")), dialog)
            command.setPlaceholderText(r'C:\Program Files\App\app.exe or a command')
            form.addRow("Launch command", command)
            launcher_margin_x = QSpinBox(dialog)
            launcher_margin_x.setRange(0, 32)
            launcher_margin_x.setSuffix(" px")
            launcher_margin_x.setValue(int(config.get("margin_x", 10)))
            form.addRow("Horizontal padding", launcher_margin_x)
            launcher_margin_y = QSpinBox(dialog)
            launcher_margin_y.setRange(0, 16)
            launcher_margin_y.setSuffix(" px")
            launcher_margin_y.setValue(int(config.get("margin_y", 2)))
            form.addRow("Vertical padding", launcher_margin_y)
            launcher_icon_size = QSpinBox(dialog)
            launcher_icon_size.setRange(8, 64)
            launcher_icon_size.setSuffix(" px")
            launcher_icon_size.setValue(int(config.get("icon_size", 24)))
            form.addRow("Icon size", launcher_icon_size)
            note = QLabel("The command is started as entered. Arguments and quoted paths are supported.", dialog)
            note.setWordWrap(True)
            form.addRow("", note)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel, parent=dialog)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        form.addRow(buttons)
        if dialog.exec() != QDialog.Accepted:
            return False

        if kind != "separator":
            config.update(font_family=font_family.currentFont().family(), font_size=font_size.value(), corner_radius=corner_radius.value())
        if kind == "clock":
            config.update(show_date=show_date.isChecked(), show_seconds=show_seconds.isChecked(),
                          time_format=time_format.currentData(), show_am_pm=show_am_pm.isChecked(),
                          show_date_on_hover=hover_date.isChecked())
        elif kind == "tasks":
            config.update(show_labels=show_labels.isChecked(), button_width=width.value(),
                          margin_x=margin_x.value(), margin_y=margin_y.value(),
                          icon_size=task_icon_size.value())
        elif kind == "separator":
            config.update(visible=visible.currentData(), expand=expand.isChecked(), width=width.value())
        elif kind == "start":
            config.update(label=label.text().strip(), icon=icon_path["value"],
                          margin_x=start_margin_x.value(), margin_y=start_margin_y.value(),
                          icon_size=start_icon_size.value())
        elif kind == "launcher":
            if not command.text().strip():
                QMessageBox.warning(dialog, "Launcher", "Enter a launch command before saving.")
                return False
            config.update(name=name.text().strip(), icon=icon_path["value"], command=command.text().strip(),
                          margin_x=launcher_margin_x.value(), margin_y=launcher_margin_y.value(),
                          icon_size=launcher_icon_size.value())

        entry["config"] = config
        return True

    def show_settings(self):
        dialog = QDialog(self)
        dialog.setWindowTitle("Panel Settings")
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

        def color_picker(label, initial):
            state = {"value": initial}
            button = QPushButton(f"Choose… ({initial})", dialog)
            def choose():
                color = QColorDialog.getColor(state["value"], dialog, label)
                if color.isValid():
                    state["value"] = color.name()
                    button.setText(f"Choose… ({color.name()})")
            button.clicked.connect(choose)
            form.addRow(label, button)
            return state

        panel_color_value = color_picker("Panel color", self.panel_color)
        button_color_value = color_picker("Button color", self.button_color)
        text_color_value = color_picker("Text color", self.text_color)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel, parent=dialog)
        buttons.button(QDialogButtonBox.Ok).setText("Apply")
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        form.addRow(buttons)

        if dialog.exec() == QDialog.Accepted:
            if monitor.currentData() is not None:
                self.target = monitor.currentData()
                self.settings.setValue("monitor_device", self.target["device"])
            self.panel_edge = edge.currentData()
            self.panel_height = height.value()
            self.panel_length = length.value()
            self.panel_alignment = alignment.currentData()
            self.panel_color = panel_color_value["value"]
            self.button_color = button_color_value["value"]
            self.text_color = text_color_value["value"]
            self.persist_panel_settings()
            self.setFixedHeight(self.panel_height)
            self.apply_panel_style()
            self.rebuild_modules()
            self.place_bar()

    def refresh_windows(self):
        windows = enumerate_windows(int(self.winId()))
        signature = tuple((w.hwnd, w.title, w.minimized, w.active) for w in windows)
        if signature == self.signature:
            return
        self.signature = signature

        for host_info in self.task_hosts:
            buttons = host_info["layout"]
            while buttons.count():
                item = buttons.takeAt(0)
                widget = item.widget()
                if widget is not None:
                    widget.deleteLater()

            config = host_info["config"]
            if windows:
                for window in windows:
                    buttons.addWidget(TaskButton(window, self.panel_height, config, self.button_color, self.text_color, host_info["host"]))
            else:
                empty = QLabel("No open windows", host_info["host"])
                empty.setAlignment(Qt.AlignVCenter | Qt.AlignLeft)
                empty.setStyleSheet(f"color:{self.text_color}; padding:0 10px;")
                empty.setFixedHeight(max(1, self.panel_height))
                buttons.addWidget(empty)

            buttons.activate()
            content_width = sum(
                buttons.itemAt(i).widget().width()
                for i in range(buttons.count())
                if buttons.itemAt(i).widget() is not None
            )
            if len(windows) > 1:
                content_width += buttons.spacing() * (len(windows) - 1)
            content_width = max(content_width, 1)
            host_info["host"].setFixedSize(content_width, max(1, self.panel_height))
            host_info["scroll"].setFixedSize(min(content_width, max(1, self.width())), max(1, self.panel_height))

    def update_clocks(self):
        now = datetime.now()
        for index, entry in enumerate(self.items):
            if entry["type"] != "clock":
                continue
            if index >= len(self.module_widgets):
                continue
            label = self.module_widgets[index]
            config = entry["config"]
            parts = []
            if config.get("show_date", True):
                parts.append(now.strftime("%a %d %b"))
            if config.get("time_format", "24h") == "12h":
                time_format = "%I:%M:%S" if config.get("show_seconds", False) else "%I:%M"
                clock_text = now.strftime(time_format).lstrip("0")
                if config.get("show_am_pm", True):
                    clock_text += now.strftime(" %p")
            else:
                clock_text = now.strftime("%H:%M:%S" if config.get("show_seconds", False) else "%H:%M")
            parts.append(clock_text)
            label.setText("   ".join(parts))
            label.setToolTip(now.strftime("%A, %B %d, %Y") if config.get("show_date_on_hover", True) else "")
            label.adjustSize()
            label.updateGeometry()

    def check_monitors(self):
        monitors = monitor_list()
        if not monitors:
            return
        if not any(m["device"] == (self.target or {}).get("device") for m in monitors):
            self.target = self.default_monitor()
            self.place_bar()

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
