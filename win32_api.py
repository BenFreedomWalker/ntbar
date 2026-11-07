import ctypes
from ctypes import wintypes
from dataclasses import dataclass
from PySide6.QtGui import QIcon

user32 = ctypes.WinDLL("user32", use_last_error=True)
shell32 = ctypes.WinDLL("shell32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

user32.GetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int]
user32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
user32.GetWindowTextLengthW.restype = ctypes.c_int
user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
user32.IsWindow.argtypes = [wintypes.HWND]
user32.IsWindowVisible.argtypes = [wintypes.HWND]
user32.IsIconic.argtypes = [wintypes.HWND]
user32.ShowWindowAsync.argtypes = [wintypes.HWND, ctypes.c_int]
user32.BringWindowToTop.argtypes = [wintypes.HWND]
user32.SetForegroundWindow.argtypes = [wintypes.HWND]
user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.EnumWindows.restype = wintypes.BOOL
user32.EnumDisplayMonitors.restype = wintypes.BOOL
user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                                ctypes.c_int, ctypes.c_int, wintypes.UINT]
user32.SetWindowPos.restype = wintypes.BOOL
user32.GetForegroundWindow.restype = wintypes.HWND
user32.MonitorFromWindow.restype = wintypes.HMONITOR
kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
kernel32.OpenProcess.restype = wintypes.HANDLE
kernel32.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
shell32.SHAppBarMessage.restype = ctypes.c_size_t

GWL_EXSTYLE = -20
WS_EX_TOOLWINDOW = 0x80
WS_EX_APPWINDOW = 0x40000
SW_MINIMIZE = 6
SW_RESTORE = 9
WM_CLOSE = 0x10
ABM_NEW, ABM_REMOVE, ABM_QUERYPOS, ABM_SETPOS = 0, 1, 2, 3
ABE_BOTTOM = 3

class RECT(ctypes.Structure):
    _fields_ = [("left", wintypes.LONG), ("top", wintypes.LONG), ("right", wintypes.LONG), ("bottom", wintypes.LONG)]

class APPBARDATA(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("hWnd", wintypes.HWND), ("uCallbackMessage", wintypes.UINT),
                ("uEdge", wintypes.UINT), ("rc", RECT), ("lParam", wintypes.LPARAM)]

class MONITORINFOEXW(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", RECT), ("rcWork", RECT),
                ("dwFlags", wintypes.DWORD), ("szDevice", wintypes.WCHAR * 32)]

user32.GetMonitorInfoW.argtypes = [wintypes.HMONITOR, ctypes.POINTER(MONITORINFOEXW)]

class SHFILEINFOW(ctypes.Structure):
    _fields_ = [("hIcon", wintypes.HICON), ("iIcon", ctypes.c_int), ("dwAttributes", wintypes.DWORD),
                ("szDisplayName", wintypes.WCHAR * 260), ("szTypeName", wintypes.WCHAR * 80)]

@dataclass(frozen=True)
class WindowInfo:
    hwnd: int
    title: str
    minimized: bool
    active: bool

def _title(hwnd):
    length = user32.GetWindowTextLengthW(hwnd)
    if length <= 0:
        return ""
    buffer = ctypes.create_unicode_buffer(min(length + 1, 2048))
    user32.GetWindowTextW(hwnd, buffer, len(buffer))
    return buffer.value.strip()

def enumerate_windows(own_hwnd=0):
    found = []
    active = user32.GetForegroundWindow()
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def callback(hwnd, _):
        if hwnd == own_hwnd or not user32.IsWindowVisible(hwnd):
            return True
        style = user32.GetWindowLongPtrW(hwnd, GWL_EXSTYLE)
        if (style & WS_EX_TOOLWINDOW) and not (style & WS_EX_APPWINDOW):
            return True
        title = _title(hwnd)
        if title:
            found.append(WindowInfo(int(hwnd), title, bool(user32.IsIconic(hwnd)), hwnd == active))
        return True
    user32.EnumWindows(callback_type(callback), 0)
    return found

def load_window_icon(hwnd):
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    process = kernel32.OpenProcess(0x1000, False, pid.value)
    if not process:
        return None
    try:
        size = wintypes.DWORD(32768)
        path = ctypes.create_unicode_buffer(size.value)
        if not kernel32.QueryFullProcessImageNameW(process, 0, path, ctypes.byref(size)):
            return None
        icon = QIcon(path.value)
        return icon if not icon.isNull() else None
    finally:
        kernel32.CloseHandle(process)

def activate_or_minimize(hwnd):
    if not user32.IsWindow(hwnd):
        return
    if hwnd == user32.GetForegroundWindow() and not user32.IsIconic(hwnd):
        user32.ShowWindowAsync(hwnd, SW_MINIMIZE)
    else:
        user32.ShowWindowAsync(hwnd, SW_RESTORE)
        user32.BringWindowToTop(hwnd)
        user32.SetForegroundWindow(hwnd)

def close_window(hwnd):
    if user32.IsWindow(hwnd):
        user32.PostMessageW(hwnd, WM_CLOSE, 0, 0)

def monitor_list():
    result = []
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HMONITOR, wintypes.HDC, ctypes.POINTER(RECT), wintypes.LPARAM)
    def callback(handle, _hdc, _rect, _data):
        info = MONITORINFOEXW()
        info.cbSize = ctypes.sizeof(info)
        if user32.GetMonitorInfoW(handle, ctypes.byref(info)):
            r = info.rcMonitor
            result.append({"handle": int(handle), "device": info.szDevice, "left": r.left, "top": r.top,
                           "right": r.right, "bottom": r.bottom, "width": r.right-r.left,
                           "height": r.bottom-r.top, "primary": bool(info.dwFlags & 1)})
        return True
    user32.EnumDisplayMonitors(0, 0, callback_type(callback), 0)
    return result

def register_appbar(hwnd):
    data = APPBARDATA()
    data.cbSize = ctypes.sizeof(data)
    data.hWnd = hwnd
    data.uCallbackMessage = 0x8000 + 41
    return bool(shell32.SHAppBarMessage(ABM_NEW, ctypes.byref(data)))

def remove_appbar(hwnd):
    data = APPBARDATA()
    data.cbSize = ctypes.sizeof(data)
    data.hWnd = hwnd
    shell32.SHAppBarMessage(ABM_REMOVE, ctypes.byref(data))

def reposition_appbar(hwnd, monitor_handle, height):
    info = MONITORINFOEXW()
    info.cbSize = ctypes.sizeof(info)
    if not user32.GetMonitorInfoW(monitor_handle, ctypes.byref(info)):
        return
    data = APPBARDATA()
    data.cbSize = ctypes.sizeof(data)
    data.hWnd = hwnd
    data.uEdge = ABE_BOTTOM
    data.rc = RECT(info.rcMonitor.left, info.rcMonitor.bottom-height, info.rcMonitor.right, info.rcMonitor.bottom)
    shell32.SHAppBarMessage(ABM_QUERYPOS, ctypes.byref(data))
    data.rc.top = data.rc.bottom-height
    shell32.SHAppBarMessage(ABM_SETPOS, ctypes.byref(data))
    user32.SetWindowPos(hwnd, -1, data.rc.left, data.rc.top, data.rc.right-data.rc.left,
                        data.rc.bottom-data.rc.top, 0x0010 | 0x0040)
