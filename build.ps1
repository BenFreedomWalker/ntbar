$ErrorActionPreference = "Stop"

$dotnet = Get-Command dotnet -ErrorAction SilentlyContinue
if (-not $dotnet) {
    throw "dotnet was not found. Install the .NET 8 SDK for Windows x64 from https://dotnet.microsoft.com/download/dotnet/8.0, then reopen PowerShell. The Runtime alone is not enough to build ntbar."
}

$sdks = & dotnet --list-sdks
if ($LASTEXITCODE -ne 0 -or -not $sdks) {
    throw "No .NET SDK was found. Install the .NET 8 SDK for Windows x64 from https://dotnet.microsoft.com/download/dotnet/8.0, then reopen PowerShell. The Runtime alone is not enough to build ntbar."
}

$project = Join-Path $PSScriptRoot "src\NtBar\NtBar.csproj"
$output = Join-Path $PSScriptRoot "dist"

& dotnet publish $project -c Release -r win-x64 --self-contained true -p:PublishSingleFile=true -p:IncludeNativeLibrariesForSelfExtract=true -o $output

if ($LASTEXITCODE -ne 0) {
    throw "dotnet publish failed with exit code $LASTEXITCODE"
}

Write-Host ""
Write-Host "Build complete: $output\NtBar.exe"
