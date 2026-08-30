' Silent launcher for Jarvis - starts with no console window.
' Point your desktop shortcut or Startup folder entry at this file.
'
' Runs at normal integrity: no elevation, no UAC prompt, by design.

Option Explicit

Dim shell, fso, scriptDir, projectRoot, uv, command
Set shell = CreateObject("WScript.Shell")
Set fso   = CreateObject("Scripting.FileSystemObject")

scriptDir   = fso.GetParentFolderName(WScript.ScriptFullName)
projectRoot = fso.GetParentFolderName(scriptDir)

' Locate uv the same way the PowerShell launcher does.
uv = ""
Dim candidates, candidate
candidates = Array( _
    shell.ExpandEnvironmentStrings("%LOCALAPPDATA%\hermes\bin\uv.exe"), _
    shell.ExpandEnvironmentStrings("%USERPROFILE%\.local\bin\uv.exe"), _
    shell.ExpandEnvironmentStrings("%LOCALAPPDATA%\Microsoft\WinGet\Links\uv.exe"))

For Each candidate In candidates
    If uv = "" And fso.FileExists(candidate) Then uv = candidate
Next

If uv = "" Then uv = "uv"   ' fall back to PATH

shell.CurrentDirectory = projectRoot
command = """" & uv & """ run python -m jarvis"

' 0 = hidden window, False = don't wait for it to exit.
shell.Run command, 0, False
