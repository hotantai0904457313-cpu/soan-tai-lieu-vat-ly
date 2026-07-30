$appDir = "C:\Users\HO TAN TAI\web soan tai lieu\vatly-app"
$pyw    = "C:\Users\HO TAN TAI\AppData\Local\Python\pythoncore-3.14-64\pythonw.exe"
$router = "C:\Users\HO TAN TAI\web soan tai lieu\9router-master\9router-master"

function Test-Flask {
    try {
        $r = Invoke-WebRequest "http://127.0.0.1:5000/api/status" -UseBasicParsing -TimeoutSec 2 -ErrorAction Stop
        return $r.StatusCode -eq 200
    } catch { return $false }
}

# Flask dang chay: mo browser ngay
if (Test-Flask) {
    Start-Process "http://localhost:5000"
    exit
}

# Khoi dong 9Router neu chua chay
try {
    Invoke-WebRequest "http://127.0.0.1:20128/v1/models" -UseBasicParsing -TimeoutSec 1 -ErrorAction Stop | Out-Null
} catch {
    if (Test-Path "$router\package.json") {
        Start-Process "cmd.exe" -ArgumentList "/c cd /d `"$router`" && npm run dev" -WindowStyle Hidden
    }
}

# Khoi dong Flask
Start-Process -FilePath $pyw -ArgumentList "app.py" -WorkingDirectory $appDir -WindowStyle Hidden

# Doi Flask san sang (toi da 20 giay)
for ($i = 0; $i -lt 20; $i++) {
    Start-Sleep 1
    if (Test-Flask) { break }
}

# Mo browser
Start-Process "http://localhost:5000"
