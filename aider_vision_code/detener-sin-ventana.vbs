Option Explicit
Dim shell, base, command
Set shell = CreateObject("WScript.Shell")
base = CreateObject("Scripting.FileSystemObject").GetParentFolderName(WScript.ScriptFullName)
command = "powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -WindowStyle Hidden -File """ & base & "\AVC-Service-Progress.ps1"" -Action Stop"
shell.Run command, 0, False
