@echo off
cd /d "C:\Users\HO TAN TAI\web soan tai lieu\vatly-app"

REM Khoi dong 9Router truoc (luon kiem tra, ke ca khi Flask da chay)
netstat -an 2>nul | findstr ":20128 " | findstr "LISTENING" >nul 2>&1
if not %errorlevel%==0 (
    cscript //nologo "C:\Users\HO TAN TAI\web soan tai lieu\vatly-app\launch-router.vbs"
)

REM Khoi dong Flask (bo qua neu da chay)
netstat -an 2>nul | findstr ":5000 " | findstr "LISTENING" >nul 2>&1
if not %errorlevel%==0 (
    cscript //nologo "C:\Users\HO TAN TAI\web soan tai lieu\vatly-app\launch-flask.vbs"
)
