Set WshShell = CreateObject("WScript.Shell")
strDir = CreateObject("Scripting.FileSystemObject").GetParentFolderName(WScript.ScriptFullName)
WshShell.CurrentDirectory = strDir
WshShell.Run chr(34) & strDir & "\.venv\Scripts\pythonw.exe" & Chr(34) & " """ & strDir & "\main.py""", 0
Set WshShell = Nothing
