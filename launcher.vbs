Set WshShell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")

appDir    = "C:\Users\HO TAN TAI\web soan tai lieu\vatly-app"
pyw       = "C:\Users\HO TAN TAI\AppData\Local\Python\pythoncore-3.14-64\pythonw.exe"
routerDir = "C:\Users\HO TAN TAI\web soan tai lieu\9router-master\9router-master"

Function FlaskRunning()
    On Error Resume Next
    Dim http
    Set http = CreateObject("MSXML2.XMLHTTP")
    http.Open "GET", "http://127.0.0.1:5000/api/status", False
    http.send
    FlaskRunning = (http.status = 200)
    On Error GoTo 0
End Function

Function RouterRunning()
    On Error Resume Next
    Dim http
    Set http = CreateObject("MSXML2.XMLHTTP")
    http.Open "GET", "http://127.0.0.1:20128/v1/models", False
    http.send
    RouterRunning = (http.status = 200)
    On Error GoTo 0
End Function

' Neu Flask da chay: mo browser ngay
If FlaskRunning() Then
    WshShell.Run "rundll32 url.dll,FileProtocolHandler http://localhost:5000"
    WScript.Quit
End If

' Khoi dong 9Router neu chua chay
If Not RouterRunning() Then
    If fso.FolderExists(routerDir) Then
        WshShell.CurrentDirectory = routerDir
        WshShell.Run "cmd /c npm run dev", 0, False
    End If
End If

' Khoi dong Flask
WshShell.CurrentDirectory = appDir
WshShell.Run Chr(34) & pyw & Chr(34) & " app.py", 0, False

' Doi Flask san sang (toi da 20 giay)
Dim i
For i = 1 To 20
    WScript.Sleep 1000
    If FlaskRunning() Then Exit For
Next

' Mo browser
WshShell.Run "rundll32 url.dll,FileProtocolHandler http://localhost:5000"
