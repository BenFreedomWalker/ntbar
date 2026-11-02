$ErrorActionPreference = "Stop"

$project = Join-Path $PSScriptRoot "src\NtBar\NtBar.csproj"
$output = Join-Path $PSScriptRoot "dist"

dotnet publish $project -c Release -r win-x64 --self-contained true -p:PublishSingleFile=true -p:IncludeNativeLibrariesForSelfExtract=true -o $output

if ($LASTEXITCODE -ne 0) {
    throw "dotnet publish failed with exit code $LASTEXITCODE"
}

Write-Host ""
Write-Host "Build complete: $output\NtBar.exe"
