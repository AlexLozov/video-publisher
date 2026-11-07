<#
.SYNOPSIS
  Starts Video Publisher. Used by the launcher .bat in the repository root and the desktop shortcut.

.DESCRIPTION
  1. First run: creates .venv and installs the app (pip install -e .).
     Reinstalls when pyproject.toml is newer than the last install.
  2. Installs UI packages if ui\node_modules is missing or package-lock.json changed.
  3. Rebuilds the UI (npm run build) when ui\dist is missing or older than the UI sources.
  4. Creates a desktop shortcut once (deleting it is respected; -Shortcut recreates it).
  5. Starts the app without a console window (pythonw). Errors go to
     %USERPROFILE%\.video-publisher\launch.log.

.PARAMETER Launcher   Path of the .bat to put into the shortcut.
.PARAMETER Shortcut   Recreate the desktop shortcut even if it was created before.
.PARAMETER NoStart    Prepare everything but do not start the app (for checks).
#>
param(
    # Full path of the .bat that started us: the shortcut points to it (kept out of this
    # file so the script stays ASCII; Windows PowerShell misreads UTF-8 without BOM).
    [string]$Launcher = '',
    [switch]$Shortcut,
    [switch]$NoStart
)

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
$Venv = Join-Path $Root '.venv'
$Python = Join-Path $Venv 'Scripts\python.exe'
$PythonW = Join-Path $Venv 'Scripts\pythonw.exe'
$Ui = Join-Path $Root 'ui'
$DataDir = Join-Path $env:USERPROFILE '.video-publisher'
New-Item -ItemType Directory -Force $DataDir | Out-Null

function Step($text) { Write-Host "-> $text" -ForegroundColor Cyan }

function Test-Newer([string]$Marker, [string[]]$Sources) {
    # True when the marker file is missing or any source file is newer than it.
    if (-not (Test-Path $Marker)) { return $true }
    $stamp = (Get-Item $Marker).LastWriteTimeUtc
    foreach ($src in $Sources) {
        if (-not (Test-Path $src)) { continue }
        $item = Get-Item $src
        $files = if ($item.PSIsContainer) { Get-ChildItem $src -Recurse -File } else { @($item) }
        if ($files | Where-Object { $_.LastWriteTimeUtc -gt $stamp } | Select-Object -First 1) {
            return $true
        }
    }
    return $false
}

function Invoke-Checked([string]$What, [scriptblock]$Command) {
    & $Command
    if ($LASTEXITCODE -ne 0) { throw "$What failed (exit code $LASTEXITCODE)" }
}

# 1. Python environment
$PipMarker = Join-Path $Venv '.vp-installed'
if (-not (Test-Path $Python)) {
    Step 'Creating Python environment (first run, takes a minute)'
    $py = Get-Command py -ErrorAction SilentlyContinue
    if ($py) { Invoke-Checked 'venv' { py -3 -m venv $Venv } }
    else { Invoke-Checked 'venv' { python -m venv $Venv } }
}
if (Test-Newer $PipMarker @((Join-Path $Root 'pyproject.toml'))) {
    Step 'Installing Python packages'
    Push-Location $Root
    try { Invoke-Checked 'pip install' { & $Python -m pip install --disable-pip-version-check -q -e . } }
    finally { Pop-Location }
    Set-Content -Path $PipMarker -Value (Get-Date -Format o)
}

# 2-3. Interface
$NodeMarker = Join-Path $Ui 'node_modules\.vp-installed'
$BuiltIndex = Join-Path $Ui 'dist\index.html'
$needInstall = Test-Newer $NodeMarker @((Join-Path $Ui 'package-lock.json'))
$uiSources = @('src', 'index.html', 'package.json', 'vite.config.ts', 'tsconfig.json') |
    ForEach-Object { Join-Path $Ui $_ }
$needBuild = Test-Newer $BuiltIndex $uiSources
if ($needInstall -or $needBuild) {
    if (-not (Get-Command npm -ErrorAction SilentlyContinue)) {
        throw 'Node.js (npm) is not installed: https://nodejs.org'
    }
    Push-Location $Ui
    try {
        if ($needInstall) {
            Step 'Installing interface packages'
            Invoke-Checked 'npm install' { npm install --no-audit --no-fund --loglevel=error }
            Set-Content -Path $NodeMarker -Value (Get-Date -Format o)
        }
        Step 'Building the interface'
        Invoke-Checked 'npm run build' { npm run build --silent }
    }
    finally { Pop-Location }
}

# 4. Desktop shortcut (once)
$ShortcutMarker = Join-Path $DataDir 'shortcut-created'
if ($Launcher -and ($Shortcut -or -not (Test-Path $ShortcutMarker))) {
    Step 'Creating desktop shortcut'
    $desktop = [Environment]::GetFolderPath('Desktop')
    $shell = New-Object -ComObject WScript.Shell
    $lnk = $shell.CreateShortcut((Join-Path $desktop 'Video Publisher.lnk'))
    $lnk.TargetPath = $Launcher
    $lnk.WorkingDirectory = $Root
    $lnk.IconLocation = (Join-Path $Root 'desktop\icon.ico') + ',0'
    $lnk.WindowStyle = 7  # minimized: the console only flashes in the taskbar
    $lnk.Description = 'Video Publisher'
    $lnk.Save()
    Set-Content -Path $ShortcutMarker -Value (Get-Date -Format o)
}

# 5. Start
if ($NoStart) { Step 'Ready (not started: -NoStart)'; exit 0 }
Step 'Starting Video Publisher'
$log = Join-Path $DataDir 'launch.log'
Start-Process -FilePath $PythonW -ArgumentList "`"$(Join-Path $Root 'desktop\run.py')`"" `
    -WorkingDirectory $Root -RedirectStandardError $log
