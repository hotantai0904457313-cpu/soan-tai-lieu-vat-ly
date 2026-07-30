Set WshShell = CreateObject("WScript.Shell")
appDir = "C:\Users\HO TAN TAI\web soan tai lieu\vatly-app"
WshShell.CurrentDirectory = appDir
pyw = "C:\Users\HO TAN TAI\AppData\Local\Python\pythoncore-3.14-64\pythonw.exe"
WshShell.Run Chr(34) & pyw & Chr(34) & " app.py", 0, False
