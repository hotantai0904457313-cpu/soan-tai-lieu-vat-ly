import subprocess, time, urllib.request, os

PYW        = r"C:\Users\HO TAN TAI\AppData\Local\Python\pythoncore-3.14-64\pythonw.exe"
APP_DIR    = r"C:\Users\HO TAN TAI\web soan tai lieu\vatly-app"
CHROME     = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
DETACHED   = 0x00000008

def flask_ok():
    try:
        urllib.request.urlopen("http://127.0.0.1:5000/api/status", timeout=2)
        return True
    except:
        return False

def router_ok():
    try:
        urllib.request.urlopen("http://127.0.0.1:20128/v1/models", timeout=1)
        return True
    except:
        return False

# 9Router kiem tra rieng, KHONG phu thuoc Flask (truoc day Flask song -> bo qua 9Router)
if not router_ok():
    subprocess.Popen(["wscript.exe", os.path.join(APP_DIR, "launch-router.vbs")],
                     creationflags=DETACHED)

if not flask_ok():
    subprocess.Popen([PYW, "app.py"], cwd=APP_DIR, creationflags=DETACHED)
    for _ in range(20):
        time.sleep(1)
        if flask_ok():
            break

# 127.0.0.1 thay vi localhost: Flask chi bind IPv4, localhost thu IPv6 truoc -> khung ~2s
subprocess.Popen([CHROME, "http://127.0.0.1:5000"])
