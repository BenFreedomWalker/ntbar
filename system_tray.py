import os
import ctypes
from ctypes import wintypes
from dataclasses import dataclass
from PySide6.QtCore import QObject, Signal, Qt, QSize
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QWidget, QHBoxLayout, QToolButton, QMenu, QFileIconProvider, QSizePolicy
from PySide6.QtCore import QFileInfo

try:
    from comtypes import COMObject, GUID, HRESULT, COMMETHOD, IUnknown
    from comtypes.automation import VARIANT
    from comtypes.client import CreateObject
    from ctypes import POINTER, c_ulong, c_void_p
    COMTYPES_AVAILABLE = True
except ImportError:
    COMTYPES_AVAILABLE = False

CLSID_TRAY_NOTIFY = "{25DEAD04-1EAC-4911-9E3A-AD0A4AB560FD}"
IID_TRAY_NOTIFY = GUID("{D133CE13-3537-48BA-93A7-AFCD5D2053B4}")
IID_TRAY_NOTIFY_LEGACY = GUID("{FB852B2C-6BAD-4605-9551-F15F87830935}")
IID_NOTIFICATION_CB = GUID("{D782CCBA-AFB0-43F1-94DB-FDA3779EACCB}")

class NOTIFYITEM(ctypes.Structure):
    _fields_ = [
        ("pszExeName", wintypes.LPWSTR),
        ("pszTip", wintypes.LPWSTR),
        ("hIcon", wintypes.HICON),
        ("hWnd", wintypes.HWND),
        ("dwPreference", wintypes.DWORD),
        ("uID", wintypes.UINT),
        ("guidItem", ctypes.c_byte * 16),
    ]

if COMTYPES_AVAILABLE:
    class INotificationCB(IUnknown):
        _iid_ = IID_NOTIFICATION_CB
        _methods_ = [
            COMMETHOD([], HRESULT, "Notify",
                      (["in"], wintypes.ULONG, "event"),
                      (["in"], ctypes.POINTER(NOTIFYITEM), "item"))
        ]

    class ITrayNotify(IUnknown):
        _iid_ = IID_TRAY_NOTIFY
        _methods_ = [
            COMMETHOD([], HRESULT, "RegisterCallback",
                      (["in"], POINTER(INotificationCB), "callback"),
                      (["out"], POINTER(c_ulong), "cookie")),
            COMMETHOD([], HRESULT, "UnregisterCallback",
                      (["in"], c_ulong, "cookie")),
            COMMETHOD([], HRESULT, "SetPreference",
                      (["in"], POINTER(NOTIFYITEM), "item")),
            COMMETHOD([], HRESULT, "EnableAutoTray", (["in"], wintypes.BOOL, "enabled")),
            COMMETHOD([], HRESULT, "DoAction", (["in"], wintypes.BOOL, "enabled")),
        ]

    class ITrayNotifyLegacy(IUnknown):
        _iid_ = IID_TRAY_NOTIFY_LEGACY
        _methods_ = [
            COMMETHOD([], HRESULT, "RegisterCallback", (["in"], POINTER(INotificationCB), "callback")),
            COMMETHOD([], HRESULT, "SetPreference", (["in"], POINTER(NOTIFYITEM), "item")),
            COMMETHOD([], HRESULT, "EnableAutoTray", (["in"], wintypes.BOOL, "enabled")),
        ]

@dataclass
class TrayEntry:
    key: str
    hwnd: int
    uid: int
    guid: bytes
    exe: str
    tip: str

class TrayEvents(QObject):
    changed = Signal(int, object)

class NotificationCallback(COMObject if COMTYPES_AVAILABLE else object):
    if COMTYPES_AVAILABLE:
        _com_interfaces_ = [INotificationCB]

    def __init__(self, events):
        if COMTYPES_AVAILABLE:
            super().__init__()
        self.events = events

    def Notify(self, event, item):
        try:
            data = item.contents
            hwnd = int(data.hWnd or 0)
            uid = int(data.uID)
            guid = bytes(data.guidItem)
            exe = data.pszExeName or ""
            tip = data.pszTip or ""
            key = guid.hex() if any(guid) else f"{hwnd}:{uid}"
            self.events.changed.emit(int(event), {
                "key": key, "hwnd": hwnd, "uid": uid, "guid": guid,
                "exe": exe, "tip": tip
            })
        except Exception:
            pass
        return 0

class WindowsNotificationArea:
    """Read-only mirror of Explorer's notification area using its ITrayNotify COM interface."""
    def __init__(self, events):
        self.events = events
        self.callback = None
        self.service = None
        self.cookie = None
        self.legacy = False
        self.error = ""

    def start(self):
        if not COMTYPES_AVAILABLE:
            self.error = "The comtypes package is not installed."
            return False
        try:
            self.callback = NotificationCallback(self.events)
            try:
                self.service = CreateObject(CLSID_TRAY_NOTIFY, interface=ITrayNotify)
                self.cookie = c_ulong()
                self.service.RegisterCallback(self.callback, ctypes.byref(self.cookie))
            except Exception:
                self.service = CreateObject(CLSID_TRAY_NOTIFY, interface=ITrayNotifyLegacy)
                self.legacy = True
                self.service.RegisterCallback(self.callback)
            return True
        except Exception as exc:
            self.error = str(exc)
            self.service = None
            self.callback = None
            return False

    def stop(self):
        if self.service is not None:
            try:
                if not self.legacy and self.cookie is not None:
                    self.service.UnregisterCallback(self.cookie.value)
                else:
                    self.service.RegisterCallback(None)
            except Exception:
                pass
        self.service = None
        self.callback = None

class NotificationIconButton(QToolButton):
    def __init__(self, entry, icon_size, parent=None):
        super().__init__(parent)
        self.entry = entry
        self.setAutoRaise(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolButtonStyle(Qt.ToolButtonIconOnly)
        self.setFixedSize(icon_size + 8, max(icon_size + 6, 24))
        self.setIconSize(QSize(icon_size, icon_size))
        self.setToolTip(entry.tip or os.path.basename(entry.exe) or "Notification icon")
        if entry.exe and os.path.isfile(entry.exe):
            icon = QFileIconProvider().icon(QFileInfo(entry.exe))
            if not icon.isNull():
                self.setIcon(icon)
        self.clicked.connect(lambda: activate_notification_icon(entry, False))
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(
            lambda point: activate_notification_icon(entry, True)
        )

def activate_notification_icon(entry, right_click=False):
    """Click the corresponding icon in Explorer so its original app receives the event."""
    shell32 = ctypes.WinDLL("shell32", use_last_error=True)
    user32 = ctypes.WinDLL("user32", use_last_error=True)

    class NOTIFYICONIDENTIFIER(ctypes.Structure):
        _fields_ = [
            ("cbSize", wintypes.DWORD),
            ("hWnd", wintypes.HWND),
            ("uID", wintypes.UINT),
            ("guidItem", ctypes.c_byte * 16),
        ]

    class RECT(ctypes.Structure):
        _fields_ = [("left", wintypes.LONG), ("top", wintypes.LONG),
                    ("right", wintypes.LONG), ("bottom", wintypes.LONG)]

    identifier = NOTIFYICONIDENTIFIER()
    identifier.cbSize = ctypes.sizeof(identifier)
    identifier.hWnd = entry.hwnd
    identifier.uID = entry.uid
    if entry.guid and len(entry.guid) == 16:
        identifier.guidItem = (ctypes.c_byte * 16).from_buffer_copy(entry.guid)
    rect = RECT()
    get_rect = shell32.Shell_NotifyIconGetRect
    get_rect.argtypes = [ctypes.POINTER(NOTIFYICONIDENTIFIER), ctypes.POINTER(RECT)]
    get_rect.restype = ctypes.c_long
    if get_rect(ctypes.byref(identifier), ctypes.byref(rect)) != 0:
        return False

    x = (rect.left + rect.right) // 2
    y = (rect.top + rect.bottom) // 2
    user32.SetCursorPos(x, y)
    # Send the click to Explorer's real notification icon. This preserves the
    # owning application's own callback/menu rather than imitating its UI.
    MOUSEEVENTF_RIGHTDOWN = 0x0008
    MOUSEEVENTF_RIGHTUP = 0x0010
    MOUSEEVENTF_LEFTDOWN = 0x0002
    MOUSEEVENTF_LEFTUP = 0x0004
    flags = (MOUSEEVENTF_RIGHTDOWN | MOUSEEVENTF_RIGHTUP) if right_click else (
        MOUSEEVENTF_LEFTDOWN | MOUSEEVENTF_LEFTUP
    )
    user32.mouse_event(flags, 0, 0, 0, 0)
    return True

_shared_events = None
_shared_provider = None
_shared_users = 0

class SystemTrayWidget(QWidget):
    def __init__(self, config, panel_height, parent=None):
        super().__init__(parent)
        self.config = config
        self.panel_height = panel_height
        self.icon_size = max(8, min(48, int(config.get("icon_size", 18))))
        self.entries = {}
        global _shared_events, _shared_provider, _shared_users
        if _shared_events is None:
            _shared_events = TrayEvents()
            _shared_provider = WindowsNotificationArea(_shared_events)
            _shared_provider.start()
        self.events = _shared_events
        self.events.changed.connect(self.on_notification_changed)
        self._connected = True
        _shared_users += 1
        self.setToolTip(_shared_provider.error or "Windows notification area")

        self.row = QHBoxLayout(self)
        self.row.setContentsMargins(4, 0, 4, 0)
        self.row.setSpacing(max(0, int(config.get("spacing", 2))))
        self.row.setAlignment(Qt.AlignVCenter | Qt.AlignLeft)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self.setFixedHeight(max(1, panel_height))

        self.provider.start()

    def on_notification_changed(self, event, data):
        key = data["key"]
        if event == 2 or not data["hwnd"]:
            self.entries.pop(key, None)
        else:
            self.entries[key] = TrayEntry(
                key, data["hwnd"], data["uid"], data["guid"],
                data["exe"], data["tip"]
            )
        self.rebuild_icons()

    def rebuild_icons(self):
        while self.row.count():
            item = self.row.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        for entry in self.entries.values():
            self.row.addWidget(NotificationIconButton(entry, self.icon_size, self))
        self.adjustSize()
        self.updateGeometry()

    def sizeHint(self):
        return QSize(max(1, len(self.entries) * (self.icon_size + 8) + 8), self.panel_height)

    def shutdown(self):
        global _shared_events, _shared_provider, _shared_users
        if not self._connected:
            return
        self._connected = False
        try:
            self.events.changed.disconnect(self.on_notification_changed)
        except (RuntimeError, TypeError):
            pass
        _shared_users = max(0, _shared_users - 1)
        if _shared_users == 0 and _shared_provider is not None:
            _shared_provider.stop()
            _shared_provider = None
            _shared_events = None

    def closeEvent(self, event):
        self.shutdown()
        super().closeEvent(event)
