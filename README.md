# ntbar

A lightweight secondary Windows taskbar built with C# WinForms and built-in Windows APIs.

## Goals

- Independent taskbar window for a selected monitor.
- Track visible application windows and activate/minimize them.
- Keep dependencies to the .NET framework libraries and Windows APIs.
- Add notification-area support in a later milestone.

## Build

Requires the .NET 8 SDK on Windows.

```powershell
dotnet build .\src\NtBar\NtBar.csproj
```
