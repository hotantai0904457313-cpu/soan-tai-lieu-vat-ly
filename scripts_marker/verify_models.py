"""
Xác minh model đã tải ĐỦ chưa: so dung lượng từng file local với Content-Length
trên server. File nào thiếu/lệch -> tải tiếp (resume). Dùng lại logic resumable.
"""
import sys
import json
import requests
from pathlib import Path
from platformdirs import user_cache_dir

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(Path(__file__).resolve().parent))
from download_models import download_file_resumable, S3_BASE, MODELS  # noqa

CACHE_DIR = Path(user_cache_dir("datalab")) / "models"
TIMEOUT = 30


def server_size(url):
    for _ in range(5):
        try:
            h = requests.head(url, timeout=TIMEOUT, allow_redirects=True)
            if h.status_code == 200:
                return int(h.headers.get("Content-Length", 0))
        except Exception:
            pass
    return None


def main():
    all_ok = True
    for model in MODELS:
        local_dir = CACHE_DIR / model
        manifest = local_dir / "manifest.json"
        if not manifest.exists():
            print(f"[{model}] THIẾU manifest -> cần tải lại")
            all_ok = False
            continue
        files = json.loads(manifest.read_text())["files"]
        for f in files:
            if f == "manifest.json":
                continue
            dest = local_dir / f
            url = f"{S3_BASE}/{model}/{f}"
            local = dest.stat().st_size if dest.exists() else 0
            remote = server_size(url)
            if remote is None:
                print(f"  [{model}/{f}] không HEAD được (bỏ qua)")
                continue
            if local == remote:
                # chỉ in cho file lớn
                if remote > 5_000_000:
                    print(f"  ✓ {model}/{f}  {local/1024/1024:.0f}MB khớp")
            else:
                print(f"  ✗ {model}/{f}  local={local} server={remote} -> tải tiếp")
                ok = download_file_resumable(url, dest)
                all_ok = all_ok and ok
    print("\n" + ("TẤT CẢ MODEL ĐỦ ✓" if all_ok else "ĐÃ SỬA / VẪN CÒN THIẾU ✗"))


if __name__ == "__main__":
    main()
