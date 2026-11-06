# Run after npm run build. Uses Windows' actual Desktop folder, including OneDrive.
[CmdletBinding()]
param(
    [string]$DesktopPath = [Environment]::GetFolderPath('Desktop')
)

$ErrorActionPreference = 'Stop'
$dashboardPath = Split-Path -Parent $PSScriptRoot
$electronPath = Join-Path $dashboardPath 'node_modules/electron/dist/electron.exe'
$entryPath = Join-Path $dashboardPath 'out/main/main.js'
$iconPath = Join-Path $dashboardPath 'assets/ira.ico'

foreach ($required in @($electronPath, $entryPath, $iconPath)) {
    if (-not (Test-Path -LiteralPath $required -PathType Leaf)) {
        throw "Missing $required. Run npm ci and npm run build in dashboard first."
    }
}
if (-not (Test-Path -LiteralPath $DesktopPath -PathType Container)) {
    throw "Desktop folder not found: $DesktopPath"
}

$shortcutPath = Join-Path $DesktopPath 'IRA.lnk'
$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($shortcutPath)
# Launch Electron directly: no terminal, npm process, or development server.
# Electron's existing single-instance lock focuses IRA on a second launch.
$shortcut.TargetPath = $electronPath
$shortcut.Arguments = '"' + $dashboardPath + '"'
$shortcut.WorkingDirectory = $dashboardPath
$shortcut.IconLocation = "$iconPath,0"
$shortcut.Description = 'Launch IRA voice assistant'
$shortcut.WindowStyle = 1
$shortcut.Save()

Write-Output "Created $shortcutPath"
