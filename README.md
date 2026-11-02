# ntbar

A lightweight secondary Windows taskbar built with C# WinForms and built-in Windows APIs. The project is in its first prototype stage; it is not yet a full DisplayFusion replacement.

## Current prototype

- Creates a bar on the first non-primary monitor (or the primary monitor if only one is connected).
- Lists visible top-level application windows with their icons and titles.
- Click a window button to restore/activate or minimize that window.
- Right-click a window button to close that window.
- Shows a clock and can move the bar between connected monitors.
- Reacts to display configuration changes.

## Planned work

1. Proper Windows AppBar registration so the bar reserves screen space instead of covering windows.
2. Better task-button behavior and active-window indication.
3. A genuine notification-area host, including icon callbacks and context menus.
4. Pinned shortcuts, startup options, and settings.

## Build

Requires the .NET 8 SDK on Windows.

Build for development:

```powershell
dotnet build .\src\NtBar\NtBar.csproj
```

Publish a self-contained single-file x64 executable:

```powershell
.\build.ps1
```

The publish output is written to `dist\NtBar.exe`. No third-party NuGet packages are used.
