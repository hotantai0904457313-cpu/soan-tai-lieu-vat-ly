"""
marker_server.py — Sidecar Marker thường trú (chạy trong venv_marker / Python 3.12).

Nạp model 1 LẦN lúc khởi động rồi giữ nóng trong RAM. Mở HTTP server nội bộ
nhận job convert từ app chính (Python 3.14). Tránh nạp lại ~3GB model mỗi file.

Giao thức:
  GET  /health             -> {"ready": true}
  POST /convert            -> body JSON {pdf_path, work_dir, disable_ocr, fname_base?}
                              -> {"ok": true, "md_path": "...", "seconds": N}
                              hoặc {"ok": false, "error": "..."}
  POST /shutdown           -> tắt sidecar

Tự tắt khi rảnh quá --idle-timeout giây (mặc định 900s = 15 phút) để giải phóng RAM.

Chạy:  venv_marker\Scripts\python.exe scripts_marker\marker_server.py --port 17923
"""
import sys
import os
import io
import json
import time
import argparse
import threading
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")

# ── Nạp model 1 lần ───────────────────────────────────────────────
MODELS = None
_convert_lock = threading.Lock()      # serialize convert -> tránh tràn RAM
_last_activity = time.monotonic()
_active_jobs = 0                      # số job ĐANG convert (để watchdog không giết giữa chừng)
_active_lock = threading.Lock()


def load_models():
    global MODELS
    t0 = time.time()
    print("[sidecar] Đang nạp model Marker vào RAM...", flush=True)
    from marker.models import create_model_dict
    MODELS = create_model_dict()
    print(f"[sidecar] Nạp model xong sau {time.time()-t0:.1f}s", flush=True)


def do_convert(pdf_path: str, work_dir: str, disable_ocr: bool,
               fname_base: str = "doc") -> dict:
    """Convert 1 PDF -> markdown trong work_dir. Trả về md_path."""
    from marker.converters.pdf import PdfConverter
    from marker.config.parser import ConfigParser
    from marker.output import save_output

    os.makedirs(work_dir, exist_ok=True)
    config = {
        "output_format": "markdown",
        "disable_ocr": bool(disable_ocr),
    }
    config_parser = ConfigParser(config)
    converter = PdfConverter(
        config=config_parser.generate_config_dict(),
        artifact_dict=MODELS,                       # <-- model nóng, không nạp lại
        processor_list=config_parser.get_processors(),
        renderer=config_parser.get_renderer(),
        llm_service=config_parser.get_llm_service(),
    )
    rendered = converter(pdf_path)
    save_output(rendered, work_dir, fname_base)
    md_path = os.path.join(work_dir, fname_base + ".md")
    if not os.path.exists(md_path):
        raise RuntimeError("Không tạo được file Markdown")
    return {"md_path": md_path}


# ── HTTP handler ──────────────────────────────────────────────────

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass  # tắt log mặc định

    def _send(self, code, obj):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        global _last_activity
        if self.path == "/health":
            _last_activity = time.monotonic()
            self._send(200, {"ready": MODELS is not None})
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self):
        global _last_activity
        _last_activity = time.monotonic()
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length) if length else b"{}"
        try:
            data = json.loads(raw.decode("utf-8"))
        except Exception:
            self._send(400, {"ok": False, "error": "JSON không hợp lệ"})
            return

        if self.path == "/shutdown":
            self._send(200, {"ok": True})
            threading.Thread(target=lambda: (time.sleep(0.3), os._exit(0)),
                             daemon=True).start()
            return

        if self.path != "/convert":
            self._send(404, {"ok": False, "error": "not found"})
            return

        pdf_path = data.get("pdf_path")
        work_dir = data.get("work_dir")
        disable_ocr = bool(data.get("disable_ocr", False))
        fname_base = data.get("fname_base", "doc")
        if not pdf_path or not work_dir:
            self._send(400, {"ok": False, "error": "Thiếu pdf_path/work_dir"})
            return

        global _active_jobs
        t0 = time.time()
        with _active_lock:
            _active_jobs += 1            # đánh dấu đang bận -> watchdog KHÔNG tắt
        try:
            with _convert_lock:                  # 1 file 1 lúc -> an toàn RAM
                try:
                    r = do_convert(pdf_path, work_dir, disable_ocr, fname_base)
                    self._send(200, {"ok": True, "md_path": r["md_path"],
                                     "seconds": round(time.time() - t0, 1)})
                except Exception as e:
                    tb = traceback.format_exc()[-1500:]
                    print(f"[sidecar] LỖI convert: {tb}", flush=True)
                    self._send(200, {"ok": False, "error": str(e)})
        finally:
            with _active_lock:
                _active_jobs -= 1
            _last_activity = time.monotonic()    # reset đồng hồ rảnh sau khi xong


def idle_watchdog(timeout: int):
    while True:
        time.sleep(30)
        # CHỈ tắt khi KHÔNG có job nào đang chạy + rảnh quá lâu
        if _active_jobs == 0 and time.monotonic() - _last_activity > timeout:
            print(f"[sidecar] Rảnh quá {timeout}s -> tự tắt để giải phóng RAM.",
                  flush=True)
            os._exit(0)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=17923)
    ap.add_argument("--idle-timeout", type=int, default=900)
    args = ap.parse_args()

    os.environ.setdefault("TORCH_DEVICE", "cpu")
    load_models()

    if args.idle_timeout > 0:
        threading.Thread(target=idle_watchdog, args=(args.idle_timeout,),
                         daemon=True).start()

    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"SIDECAR_READY port={args.port}", flush=True)  # tín hiệu cho app cha
    server.serve_forever()


if __name__ == "__main__":
    main()
