<#
    Creates Desktop and Start Menu shortcuts for Jarvis, and optionally starts
    it at login.

    All shortcuts point at Jarvis.vbs, which launches without a console window
    and without elevation. Nothing here writes outside your user profile, so it
    needs no admin rights.

        .\Install-Shortcuts.ps1            # Desktop + Start Menu
        .\Install-Shortcuts.ps1 -AtLogin   # ...and start with Windows
        .\Install-Shortcuts.ps1 -Remove    # undo everything
#>

param(
    [switch]$AtLogin,
    [switch]$Remove
)

$ErrorActionPreference = 'Stop'
$root     = Split-Path -Parent $PSScriptRoot
$launcher = Join-Path $PSScriptRoot 'Jarvis.vbs'

$targets = @{
    'Desktop'   = Join-Path ([Environment]::GetFolderPath('Desktop')) 'Jarvis.lnk'
    'StartMenu' = Join-Path ([Environment]::GetFolderPath('StartMenu')) 'Programs\Jarvis.lnk'
    'Startup'   = Join-Path ([Environment]::GetFolderPath('Startup')) 'Jarvis.lnk'
}

if ($Remove) {
    foreach ($entry in $targets.GetEnumerator()) {
        if (Test-Path $entry.Value) {
            Remove-Item $entry.Value -Force
            Write-Host "  removed  $($entry.Key)" -ForegroundColor DarkGray
        }
    }
    Write-Host "`n  Shortcuts removed.`n" -ForegroundColor Green
    exit 0
}

if (-not (Test-Path $launcher)) { throw "Launcher not found at $launcher" }

# Build an icon so the shortcut isn't a generic script glyph.
$iconPath = Join-Path $root 'data\jarvis.ico'
if (-not (Test-Path $iconPath)) {
    Add-Type -AssemblyName System.Drawing
    $size = 64
    $bmp  = New-Object System.Drawing.Bitmap $size, $size
    $g    = [System.Drawing.Graphics]::FromImage($bmp)
    $g.SmoothingMode = 'AntiAlias'
    $g.Clear([System.Drawing.Color]::Transparent)
    $rings = @(@(30, 60), @(23, 130), @(15, 255))
    foreach ($ring in $rings) {
        $r = $ring[0]; $a = $ring[1]
        $brush = New-Object System.Drawing.SolidBrush(
            [System.Drawing.Color]::FromArgb($a, 34, 211, 238))
        $g.FillEllipse($brush, ($size/2 - $r), ($size/2 - $r), (2*$r), (2*$r))
        $brush.Dispose()
    }
    $g.Dispose()
    $icon = [System.Drawing.Icon]::FromHandle($bmp.GetHicon())
    $stream = [System.IO.File]::Create($iconPath)
    $icon.Save($stream)
    $stream.Close(); $bmp.Dispose()
}

$shell = New-Object -ComObject WScript.Shell

function New-JarvisShortcut([string]$path, [string]$label) {
    $parent = Split-Path -Parent $path
    if (-not (Test-Path $parent)) { New-Item -ItemType Directory -Path $parent -Force | Out-Null }

    $link = $shell.CreateShortcut($path)
    $link.TargetPath       = "$env:SystemRoot\System32\wscript.exe"
    $link.Arguments        = """$launcher"""
    $link.WorkingDirectory = $root
    $link.Description      = 'Jarvis - local desktop assistant'
    $link.IconLocation     = "$iconPath,0"
    $link.Save()
    Write-Host "  created  $label" -ForegroundColor Green
}

New-JarvisShortcut $targets['Desktop']   'Desktop shortcut'
New-JarvisShortcut $targets['StartMenu'] 'Start Menu entry'

if ($AtLogin) {
    New-JarvisShortcut $targets['Startup'] 'Start with Windows'
} elseif (Test-Path $targets['Startup']) {
    Remove-Item $targets['Startup'] -Force
    Write-Host "  removed  start-with-Windows entry" -ForegroundColor DarkGray
}

Write-Host "`n  Done. Launch Jarvis from the desktop or Start menu.`n" -ForegroundColor Cyan
