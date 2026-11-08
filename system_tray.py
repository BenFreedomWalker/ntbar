"""Windows notification-area mirror, following ManagedShell's tray architecture.

The provider combines ITrayNotify live notifications with Explorer toolbar
enumeration for icons that existed before NtBar started. Icon interaction is
forwarded to the owning application's callback window, not simulated against
Explorer's taskbar.
"""
import ctypes
import os
import struct
import sys
import time
from ctypes import wintypes
from dataclasses import dataclass

from PySide6.QtCore import QObject, Signal, Qt, QSize, QEvent, QTimer
from PySide6.QtGui import QIcon, QImage, QPixmap
from PySide6.QtWidgets import (
    QWidget, QHBoxLayout, QToolButton, QSizePolicy, QMenu,
)

try:
    from comtypes import COMObject, GUID, HRESULT, COMMETHOD, IUnknown
    from comtypes.client import CreateObject
    from ctypes import POINTER, c_ulong, c_ulonglong
    COMTYPES_AVAILABLE = True
except ImportError:
    COMTYPES_AVAILABLE = False


user32 = ctypes.WinDLL("user32", use_last_error=True) if sys.platform == "win32" else None
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True) if sys.platform == "win32" else None
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True) if sys.platform == "win32" else None

if sys.platform == "win32":
    # Explicit pointer-sized signatures are essential on 64-bit Windows.
    user32.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
    user32.FindWindowW.restype = wintypes.HWND
    user32.FindWindowExW.argtypes = [wintypes.HWND, wintypes.HWND, wintypes.LPCWSTR, wintypes.LPCWSTR]
    user32.FindWindowExW.restype = wintypes.HWND
    user32.SendMessageW.argtypes = [wintypes.HWND, wintypes.UINT, ctypes.c_size_t, ctypes.c_ssize_t]
    user32.SendMessageW.restype = ctypes.c_ssize_t
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    user32.IsWindow.argtypes = [wintypes.HWND]
    user32.IsWindow.restype = wintypes.BOOL
    user32.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
    user32.GetCursorPos.restype = wintypes.BOOL
    user32.SendNotifyMessageW.argtypes = [wintypes.HWND, wintypes.UINT, ctypes.c_size_t, ctypes.c_ssize_t]
    user32.SendNotifyMessageW.restype = wintypes.BOOL
    user32.AllowSetForegroundWindow.argtypes = [wintypes.DWORD]
    user32.GetDoubleClickTime.restype = wintypes.UINT
    user32.GetDC.argtypes = [wintypes.HWND]
    user32.GetDC.restype = ctypes.c_void_p
    user32.ReleaseDC.argtypes = [wintypes.HWND, ctypes.c_void_p]
    user32.ReleaseDC.restype = ctypes.c_int
    user32.DrawIconEx.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int, wintypes.HICON,
                                  ctypes.c_int, ctypes.c_int, wintypes.UINT, ctypes.c_void_p, wintypes.UINT]
    user32.DrawIconEx.restype = wintypes.BOOL

    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = ctypes.c_void_p
    kernel32.VirtualAllocEx.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t,
                                        wintypes.DWORD, wintypes.DWORD]
    kernel32.VirtualAllocEx.restype = ctypes.c_void_p
    kernel32.ReadProcessMemory.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
                                           ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t)]
    kernel32.ReadProcessMemory.restype = wintypes.BOOL
    kernel32.VirtualFreeEx.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t, wintypes.DWORD]
    kernel32.VirtualFreeEx.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    kernel32.CloseHandle.restype = wintypes.BOOL

    gdi32.CreateCompatibleDC.argtypes = [ctypes.c_void_p]
    gdi32.CreateCompatibleDC.restype = ctypes.c_void_p
    gdi32.CreateDIBSection.argtypes = [ctypes.c_void_p, ctypes.c_void_p, wintypes.UINT,
                                       ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p, wintypes.DWORD]
    gdi32.CreateDIBSection.restype = ctypes.c_void_p
    gdi32.SelectObject.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    gdi32.SelectObject.restype = ctypes.c_void_p
    gdi32.PatBlt.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                             ctypes.c_int, wintypes.DWORD]
    gdi32.DeleteObject.argtypes = [ctypes.c_void_p]
    gdi32.DeleteDC.argtypes = [ctypes.c_void_p]

CLSID_TRAY_NOTIFY = "{25DEAD04-1EAC-4911-9E3A-AD0A4AB560FD}"
if COMTYPES_AVAILABLE:
    IID_TRAY_NOTIFY = GUID("{D133CE13-3537-48BA-93A7-AFCD5D2053B4}")
    IID_TRAY_NOTIFY_LEGACY = GUID("{FB852B2C-6BAD-4605-9551-F15F87830935}")
    IID_NOTIFICATION_CB = GUID("{D782CCBA-AFB0-43F1-94DB-FDA3779EACCB}")


class NOTIFYITEM(ctypes.Structure):
    _fields_ = [
        ("pszExeName", wintypes.LPWSTR),
        ("pszIconText", wintypes.LPWSTR),
        ("hIcon", wintypes.HICON),
        ("hWnd", wintypes.HWND),
        ("dwUserPref", wintypes.DWORD),
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
                      (["out"], POINTER(c_ulonglong), "cookie")),
            COMMETHOD([], HRESULT, "UnregisterCallback",
                      (["in"], c_ulonglong, "cookie")),
            COMMETHOD([], HRESULT, "SetPreference", (["in"], POINTER(NOTIFYITEM), "item")),
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
    exe: str = ""
    tip: str = ""
    icon_pixels: bytes = b""
    callback_message: int = 0
    version: int = 0
    hidden: bool = False


class TrayEvents(QObject):
    changed = Signal(int, object)


def extract_icon_pixels(hicon, size=32):
    """Copy an HICON to owned BGRA bytes before the shell releases the handle."""
    if not hicon or sys.platform != "win32":
        return b""

    class BITMAPINFOHEADER(ctypes.Structure):
        _fields_ = [
            ("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG),
            ("biHeight", wintypes.LONG), ("biPlanes", wintypes.WORD),
            ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
            ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG),
            ("biYPelsPerMeter", wintypes.LONG), ("biClrUsed", wintypes.DWORD),
            ("biClrImportant", wintypes.DWORD),
        ]

    class BITMAPINFO(ctypes.Structure):
        _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", wintypes.DWORD * 3)]

    dc = user32.GetDC(None)
    memory_dc = gdi32.CreateCompatibleDC(dc)
    info = BITMAPINFO()
    info.bmiHeader = BITMAPINFOHEADER(
        ctypes.sizeof(BITMAPINFOHEADER), size, -size, 1, 32, 0,
        size * size * 4, 0, 0, 0, 0
    )
    bits = ctypes.c_void_p()
    bitmap = gdi32.CreateDIBSection(
        memory_dc, ctypes.byref(info), 0, ctypes.byref(bits), None, 0
    )
    if not bitmap or not bits.value:
        if memory_dc:
            gdi32.DeleteDC(memory_dc)
        if dc:
            user32.ReleaseDC(None, dc)
        return b""
    old = gdi32.SelectObject(memory_dc, bitmap)
    gdi32.PatBlt(memory_dc, 0, 0, size, size, 0x00000042)
    ok = user32.DrawIconEx(memory_dc, 0, 0, hicon, size, size, 0, None, 0x0003)
    pixels = ctypes.string_at(bits.value, size * size * 4) if ok else b""
    if old:
        gdi32.SelectObject(memory_dc, old)
    gdi32.DeleteObject(bitmap)
    gdi32.DeleteDC(memory_dc)
    user32.ReleaseDC(None, dc)
    return pixels


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
            key = guid.hex() if any(guid) else f"{hwnd}:{uid}"
            self.events.changed.emit(int(event), {
                "key": key, "hwnd": hwnd, "uid": uid, "guid": guid,
                "exe": data.pszExeName or "",
                "tip": data.pszIconText or "",
                "icon_pixels": extract_icon_pixels(data.hIcon),
            })
        except Exception:
            # COM callbacks must never let Python exceptions cross the ABI.
            pass
        return 0


class WindowsNotificationArea:
    """ManagedShell-style provider: live shell callbacks plus Explorer's seed list."""
    def __init__(self, events):
        self.events = events
        self.callback = None
        self.service = None
        self.cookie = None
        self.legacy = False
        self.error = ""
        self._started = False

    def start(self):
        if sys.platform != "win32":
            self.error = "The Windows notification area is available only on Windows."
            return False
        if not COMTYPES_AVAILABLE:
            self.error = "Install comtypes to enable notification-area integration."
            return False
        try:
            self.callback = NotificationCallback(self.events)
            try:
                self.service = CreateObject(CLSID_TRAY_NOTIFY, interface=ITrayNotify)
                self.cookie = c_ulonglong()
                self.service.RegisterCallback(self.callback, ctypes.byref(self.cookie))
            except Exception:
                self.service = CreateObject(CLSID_TRAY_NOTIFY, interface=ITrayNotifyLegacy)
                self.legacy = True
                self.service.RegisterCallback(self.callback)

            # ExplorerTrayService seeds icons that were registered before NtBar.
            # When Windows auto-hides inactive icons, temporarily disable that
            # filtering while reading Explorer's toolbar, then restore it.
            auto_tray = self._auto_tray_enabled()
            if auto_tray:
                try:
                    self.service.EnableAutoTray(False)
                except Exception:
                    pass
            try:
                self._enumerate_explorer_icons()
            finally:
                if auto_tray:
                    try:
                        self.service.EnableAutoTray(True)
                    except Exception:
                        pass
            self._started = True
            return True
        except Exception as exc:
            self.error = str(exc)
            self.stop()
            return False

    def _auto_tray_enabled(self):
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                    r"SOFTWARE\Microsoft\Windows\CurrentVersion\Explorer") as key:
                value, _ = winreg.QueryValueEx(key, "EnableAutoTray")
                return int(value) == 1
        except OSError:
            return True

    def _enumerate_explorer_icons(self):
        hwnd = user32.FindWindowW("Shell_TrayWnd", None)
        if not hwnd:
            return
        notify = user32.FindWindowExW(hwnd, None, "TrayNotifyWnd", None)
        pager = user32.FindWindowExW(notify, None, "SysPager", None) if notify else 0
        toolbar = user32.FindWindowExW(pager, None, "ToolbarWindow32", None) if pager else 0
        if not toolbar:
            # Windows configurations without SysPager.
            toolbar = user32.FindWindowExW(notify, None, "ToolbarWindow32", None) if notify else 0
        if not toolbar:
            return

        class TBBUTTON(ctypes.Structure):
            _fields_ = [
                ("iBitmap", ctypes.c_int), ("idCommand", ctypes.c_int),
                ("fsState", ctypes.c_ubyte), ("fsStyle", ctypes.c_ubyte),
                ("bReserved", ctypes.c_ubyte * (6 if ctypes.sizeof(ctypes.c_void_p) == 8 else 2)),
                ("dwData", ctypes.c_size_t), ("iString", ctypes.c_ssize_t),
            ]

        class TRAYITEM(ctypes.Structure):
            _fields_ = [
                ("hWnd", ctypes.c_void_p), ("uID", wintypes.UINT),
                ("uCallbackMessage", wintypes.UINT), ("dwState", wintypes.UINT),
                ("uVersion", wintypes.UINT), ("hIcon", ctypes.c_void_p),
                ("uIconDemoteTimerID", ctypes.c_void_p), ("dwUserPref", wintypes.UINT),
                ("dwLastSoundTime", wintypes.UINT),
                ("szExeName", wintypes.WCHAR * 260),
                ("szIconText", wintypes.WCHAR * 260),
                ("uNumSeconds", wintypes.UINT), ("guidItem", ctypes.c_byte * 16),
            ]

        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(toolbar, ctypes.byref(pid))
        PROCESS_ACCESS = 0x0008 | 0x0010 | 0x0020 | 0x0400
        process = kernel32.OpenProcess(PROCESS_ACCESS, False, pid.value)
        if not process:
            return
        remote_size = max(ctypes.sizeof(TBBUTTON), ctypes.sizeof(TRAYITEM))
        remote = kernel32.VirtualAllocEx(process, None, remote_size, 0x1000 | 0x2000, 0x04)
        if not remote:
            kernel32.CloseHandle(process)
            return

        try:
            count = user32.SendMessageW(toolbar, 0x0418, 0, 0)  # TB_BUTTONCOUNT
            for index in range(min(int(count), 512)):
                if not user32.SendMessageW(toolbar, 0x0417, index, remote):  # TB_GETBUTTON
                    continue
                button = TBBUTTON()
                read = ctypes.c_size_t()
                if not kernel32.ReadProcessMemory(process, remote, ctypes.byref(button),
                                                  ctypes.sizeof(button), ctypes.byref(read)):
                    continue
                if not button.dwData:
                    continue
                item = TRAYITEM()
                if not kernel32.ReadProcessMemory(process, button.dwData, ctypes.byref(item),
                                                  ctypes.sizeof(item), ctypes.byref(read)):
                    continue
                if not item.hWnd or not user32.IsWindow(item.hWnd):
                    continue
                guid = bytes(item.guidItem)
                key = guid.hex() if any(guid) else f"{int(item.hWnd)}:{item.uID}"
                self.events.changed.emit(1, {
                    "key": key, "hwnd": int(item.hWnd), "uid": int(item.uID),
                    "guid": guid, "exe": item.szExeName, "tip": item.szIconText,
                    "icon_pixels": extract_icon_pixels(item.hIcon),
                    "callback_message": int(item.uCallbackMessage),
                    "version": int(item.uVersion), "hidden": bool(button.fsState & 0x08),
                })
        finally:
            kernel32.VirtualFreeEx(process, remote, 0, 0x8000)
            kernel32.CloseHandle(process)

    def stop(self):
        if self.service is not None:
            try:
                if not self.legacy and self.cookie is not None:
                    self.service.UnregisterCallback(int(self.cookie.value))
                elif self.legacy:
                    self.service.RegisterCallback(None)
            except Exception:
                pass
        self.service = None
        self.callback = None
        self.cookie = None
        self._started = False


class NotificationIconButton(QToolButton):
    def __init__(self, entry, icon_size, parent=None):
        super().__init__(parent)
        self.entry = entry
        self.icon_size = icon_size
        self.setAutoRaise(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.NoFocus)
        self.setToolButtonStyle(Qt.ToolButtonIconOnly)
        self.setFixedSize(icon_size + 8, max(icon_size + 6, 24))
        self.setIconSize(QSize(icon_size, icon_size))
        self.setToolTip(entry.tip or os.path.basename(entry.exe) or "Notification icon")
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self._context_menu)
        self._last_pos = (0, 0)
        self._pressed_button = None
        self._last_click = {}
        self._refresh_icon()

    def _refresh_icon(self):
        pixels = self.entry.icon_pixels
        if pixels and len(pixels) == 32 * 32 * 4:
            image = QImage(pixels, 32, 32, 32 * 4, QImage.Format_ARGB32).copy()
            self.setIcon(QIcon(QPixmap.fromImage(image)))
        else:
            self.setIcon(QIcon())

    def update_entry(self, entry):
        self.entry = entry
        self.setToolTip(entry.tip or os.path.basename(entry.exe) or "Notification icon")
        self._refresh_icon()

    def _cursor_param(self):
        pt = wintypes.POINT()
        user32.GetCursorPos(ctypes.byref(pt))
        return ((int(pt.y) & 0xFFFF) << 16) | (int(pt.x) & 0xFFFF)

    def enterEvent(self, event):
        point = self._cursor_param()
        forward_icon_event(self.entry, 0x02A1, point)  # WM_MOUSEHOVER
        if self.entry.version > 3:
            forward_icon_event(self.entry, 0x0406, point)  # NIN_POPUPOPEN
        forward_icon_event(self.entry, 0x0200, point)  # WM_MOUSEMOVE
        super().enterEvent(event)

    def mouseMoveEvent(self, event):
        forward_icon_event(self.entry, 0x0200, self._cursor_param())
        super().mouseMoveEvent(event)

    def leaveEvent(self, event):
        point = self._cursor_param()
        forward_icon_event(self.entry, 0x02A3, point)  # WM_MOUSELEAVE
        if self.entry.version > 3:
            forward_icon_event(self.entry, 0x0407, point)  # NIN_POPUPCLOSE
        super().leaveEvent(event)

    def mousePressEvent(self, event):
        self._pressed_button = event.button()
        self._last_pos = self._cursor_param()
        down = {Qt.LeftButton: 0x0201, Qt.RightButton: 0x0204, Qt.MiddleButton: 0x0207}
        double = {Qt.LeftButton: 0x0203, Qt.RightButton: 0x0206, Qt.MiddleButton: 0x0209}
        button = event.button()
        now = time.monotonic()
        previous = self._last_click.get(button, 0.0)
        threshold = user32.GetDoubleClickTime() / 1000.0
        msg = double.get(button) if previous and now - previous <= threshold else down.get(button)
        if msg:
            forward_icon_event(self.entry, msg, self._last_pos)
        self._last_click[button] = now
        event.accept()

    def mouseReleaseEvent(self, event):
        msg = {Qt.LeftButton: 0x0202, Qt.RightButton: 0x0205, Qt.MiddleButton: 0x0208}.get(event.button())
        if msg:
            forward_icon_event(self.entry, msg, self._cursor_param())
            if self.entry.version >= 3:
                select_msg = {Qt.LeftButton: 0x0400, Qt.RightButton: 0x007B}.get(event.button())
                if select_msg:
                    forward_icon_event(self.entry, select_msg, self._cursor_param())
        event.accept()

    def _context_menu(self, point):
        # The owning app receives WM_CONTEXTMENU through its registered callback.
        forward_icon_event(self.entry, 0x007B, self._cursor_param())


def forward_icon_event(entry, mouse_message, point_param):
    """Forward notification icon input using the callback protocol from NotifyIcon.cs."""
    if not entry.hwnd or not entry.callback_message or not user32.IsWindow(entry.hwnd):
        return False
    if entry.version > 3:
        wparam = point_param
        lparam = ((entry.uid & 0xFFFF) << 16) | (mouse_message & 0xFFFF)
    else:
        wparam = entry.uid
        lparam = mouse_message
    user32.AllowSetForegroundWindow(-1)
    return bool(user32.SendNotifyMessageW(
        entry.hwnd, entry.callback_message, wparam, lparam
    ))


_shared_events = None
_shared_provider = None
_shared_users = 0
_shared_entries = {}
_shared_widgets = set()


class SystemTrayWidget(QWidget):
    def __init__(self, config, panel_height, parent=None):
        super().__init__(parent)
        self.config = config
        self.panel_height = panel_height
        self.icon_size = max(8, min(48, int(config.get("icon_size", 18))))
        self.spacing = max(0, int(config.get("spacing", 2)))
        global _shared_events, _shared_provider, _shared_users, _shared_entries, _shared_widgets
        if _shared_events is None:
            _shared_events = TrayEvents()
            _shared_provider = WindowsNotificationArea(_shared_events)
            _shared_entries = {}
            _shared_widgets = set()
        self.entries = dict(_shared_entries)
        self.buttons = {}
        self.events = _shared_events
        self.row = QHBoxLayout(self)
        self.row.setContentsMargins(4, 0, 4, 0)
        self.row.setSpacing(self.spacing)
        self.row.setAlignment(Qt.AlignVCenter | Qt.AlignLeft)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self.setFixedHeight(max(1, panel_height))
        self.events.changed.connect(self.on_notification_changed)
        self._connected = True
        _shared_widgets.add(self)
        if _shared_users == 0:
            _shared_provider.start()
        _shared_users += 1
        # ITrayNotify does not expose callback-message/version fields for newly
        # registered icons. Re-read Explorer's toolbar periodically to fill
        # those fields, just as ExplorerTrayService does during initialization.
        self._refresh_timer = QTimer(self)
        self._refresh_timer.setInterval(2000)
        self._refresh_timer.timeout.connect(self._refresh_explorer_icons)
        self._refresh_timer.start()
        self.rebuild_icons()
        self.setToolTip(_shared_provider.error or "Windows notification area")

    def _refresh_explorer_icons(self):
        if _shared_provider is not None:
            _shared_provider._enumerate_explorer_icons()

    def on_notification_changed(self, event, data):
        global _shared_entries
        key = data["key"]
        # ITrayNotify uses 2 for removal; Explorer enumeration emits 1 (add).
        if event == 2 or not data.get("hwnd"):
            _shared_entries.pop(key, None)
        else:
            old = _shared_entries.get(key)
            guid = data.get("guid", old.guid if old else b"")
            _shared_entries[key] = TrayEntry(
                key, int(data["hwnd"]), int(data.get("uid", old.uid if old else 0)),
                guid, data.get("exe", old.exe if old else ""),
                data.get("tip", old.tip if old else ""),
                data.get("icon_pixels") or (old.icon_pixels if old else b""),
                int(data.get("callback_message", old.callback_message if old else 0)),
                int(data.get("version", old.version if old else 0)),
                bool(data.get("hidden", old.hidden if old else False)),
            )
        self.entries = dict(_shared_entries)
        self.rebuild_icons()

    def rebuild_icons(self):
        visible = {key: entry for key, entry in self.entries.items() if not entry.hidden}
        for key in list(self.buttons):
            if key not in visible:
                button = self.buttons.pop(key)
                self.row.removeWidget(button)
                button.deleteLater()
        for key, entry in visible.items():
            if key in self.buttons:
                self.buttons[key].update_entry(entry)
            else:
                button = NotificationIconButton(entry, self.icon_size, self)
                self.buttons[key] = button
                self.row.addWidget(button)
        self.adjustSize()
        self.updateGeometry()

    def sizeHint(self):
        count = sum(1 for entry in self.entries.values() if not entry.hidden)
        return QSize(max(1, count * (self.icon_size + 8) + max(0, count - 1) * self.spacing + 8),
                     self.panel_height)

    def shutdown(self):
        global _shared_events, _shared_provider, _shared_users, _shared_entries, _shared_widgets
        if not self._connected:
            return
        self._connected = False
        self._refresh_timer.stop()
        _shared_widgets.discard(self)
        try:
            self.events.changed.disconnect(self.on_notification_changed)
        except (RuntimeError, TypeError):
            pass
        _shared_users = max(0, _shared_users - 1)
        if _shared_users == 0:
            if _shared_provider is not None:
                _shared_provider.stop()
            _shared_provider = None
            _shared_events = None
            _shared_entries = {}
            _shared_widgets = set()

    def closeEvent(self, event):
        self.shutdown()
        super().closeEvent(event)
