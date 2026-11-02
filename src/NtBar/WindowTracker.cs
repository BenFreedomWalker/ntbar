using System.Diagnostics;
using System.Runtime.InteropServices;
using System.Text;

namespace NtBar;

internal sealed record TrackedWindow(nint Handle, string Title, Icon? Icon, bool IsMinimized);

internal sealed class WindowTracker
{
    private readonly nint _ownHandle;

    internal WindowTracker(nint ownHandle) => _ownHandle = ownHandle;

    internal IReadOnlyList<TrackedWindow> GetWindows()
    {
        var windows = new List<TrackedWindow>();

        NativeMethods.EnumWindows((handle, _) =>
        {
            if (handle == _ownHandle || !NativeMethods.IsWindowVisible(handle))
                return true;

            long style = NativeMethods.GetWindowLongPtr(handle, NativeMethods.GWL_EXSTYLE).ToInt64();
            if ((style & NativeMethods.WS_EX_TOOLWINDOW) != 0 &&
                (style & NativeMethods.WS_EX_APPWINDOW) == 0)
                return true;

            int length = NativeMethods.GetWindowTextLength(handle);
            if (length <= 0)
                return true;

            var text = new StringBuilder(Math.Min(length + 1, 2048));
            NativeMethods.GetWindowText(handle, text, text.Capacity);
            string title = text.ToString().Trim();
            if (string.IsNullOrWhiteSpace(title))
                return true;

            NativeMethods.GetWindowThreadProcessId(handle, out uint processId);
            Icon? icon = null;
            try
            {
                using Process process = Process.GetProcessById((int)processId);
                string? path = process.MainModule?.FileName;
                if (!string.IsNullOrWhiteSpace(path))
                    icon = Icon.ExtractAssociatedIcon(path);
            }
            catch
            {
                // Some elevated, protected, or already-exited processes expose no path.
            }

            windows.Add(new TrackedWindow(handle, title, icon, NativeMethods.IsIconic(handle)));
            return true;
        }, nint.Zero);

        return windows;
    }

    internal static void ActivateOrMinimize(TrackedWindow window)
    {
        if (!NativeMethods.IsWindow(window.Handle))
            return;

        if (NativeMethods.IsIconic(window.Handle))
        {
            NativeMethods.ShowWindowAsync(window.Handle, NativeMethods.SW_RESTORE);
            NativeMethods.BringWindowToTop(window.Handle);
            NativeMethods.SetForegroundWindow(window.Handle);
        }
        else
        {
            NativeMethods.ShowWindowAsync(window.Handle, NativeMethods.SW_MINIMIZE);
        }
    }
}
