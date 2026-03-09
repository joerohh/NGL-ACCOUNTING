Set fso = CreateObject("Scripting.FileSystemObject")
agentDir = fso.GetParentFolderName(WScript.ScriptFullName)

Set WshShell = CreateObject("WScript.Shell")
WshShell.CurrentDirectory = agentDir
WshShell.Run "cmd /c call venv\Scripts\activate.bat && python main.py", 0, False
