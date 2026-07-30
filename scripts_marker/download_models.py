"""
Tải model cho Marker/Surya với HỖ TRỢ RESUME (tải tiếp khi đứt mạng).

Vấn đề: surya tự tải từ models.datalab.to nhưng không resume — đứt mạng là
xóa file dở, tải lại từ 0 -> với mạng VN không ổn định thì không bao giờ xong.

Script này: đọc manifest.json của từng model, tải từng file với HTTP Range,
đứt thì nối tiếp từ byte đang dở. Đặt file vào đúng cache dir của surya rồi
ghi manifest.json sau cùng -> Marker thấy "đã tải đủ" và bỏ qua bước tự tải.
"""
import os
import sys
import time
import json
import requests
from pathlib import Path
from platformdirs import user_cache_dir

sys.stdout.reconfigure(encoding="utf-8")

S3_BASE = "https://models.datalab.to"
CACHE_DIR = Path(user_cache_dir("datalab")) / "models"

# Các model Marker cần (khớp surya/settings.py)
MODELS = [
    "text_detection/2025_05_07",
    "text_recognition/2025_09_23",
    "layout/2025_09_23",
    "table_recognition/2025_02_18",
    "ocr_error_detection/2025_02_18",
]

MAX_RETRY_PER_FILE = 200   # đủ lớn để vượt qua các lần đứt mạng
TIMEOUT = 60


def human(n):
    for unit in ["B", "KB", "MB", "GB"]:
        if abs(n) < 1024:
            return f"{n:.1f}{unit}"
        n /= 1024
    return f"{n:.1f}TB"


def download_file_resumable(url, dest: Path):
    """Tải 1 file, tự nối tiếp nếu đứt. Trả về True khi hoàn tất."""
    # Lấy tổng kích thước
    total = None
    for _ in range(10):
        try:
            h = requests.head(url, timeout=TIMEOUT, allow_redirects=True)
            if h.status_code == 200:
                total = int(h.headers.get("Content-Length", 0))
                break
        except Exception:
            time.sleep(2)
    dest.parent.mkdir(parents=True, exist_ok=True)

    attempt = 0
    while attempt < MAX_RETRY_PER_FILE:
        have = dest.stat().st_size if dest.exists() else 0
        if total and have >= total:
            return True  # xong
        headers = {"Range": f"bytes={have}-"} if have else {}
        try:
            r = requests.get(url, headers=headers, stream=True, timeout=TIMEOUT, allow_redirects=True)
            # 206 = tải tiếp; 200 = tải từ đầu (server bỏ qua Range)
            mode = "ab" if (have and r.status_code == 206) else "wb"
            if mode == "wb":
                have = 0
            with open(dest, mode) as f:
                last = time.time()
                for chunk in r.iter_content(chunk_size=1024 * 256):
                    if chunk:
                        f.write(chunk)
                        have += len(chunk)
                        if time.time() - last > 3:
                            pct = f"{have/total*100:5.1f}%" if total else "?"
                            print(f"    {dest.name}: {human(have)} / {human(total or 0)} ({pct})", flush=True)
                            last = time.time()
            r.close()
            if total and have >= total:
                print(f"    {dest.name}: DONE {human(have)}", flush=True)
                return True
            if not total:
                # không biết tổng -> coi như xong nếu tải trọn vẹn không lỗi
                return True
        except Exception as e:
            attempt += 1
            print(f"    [đứt] {dest.name} tại {human(have)} — nối tiếp (lần {attempt}): {type(e).__name__}", flush=True)
            time.sleep(3)
    return False


def download_model(model_path):
    print(f"\n=== Model: {model_path} ===", flush=True)
    local_dir = CACHE_DIR / model_path
    local_dir.mkdir(parents=True, exist_ok=True)

    # Nếu đã có manifest đầy đủ -> bỏ qua
    manifest_local = local_dir / "manifest.json"
    if manifest_local.exists():
        try:
            files = json.loads(manifest_local.read_text())["files"]
            if all((local_dir / f).exists() for f in files):
                print("  Đã có đủ -> bỏ qua", flush=True)
                return True
        except Exception:
            pass

    # Tải manifest (tạm, chưa ghi vào local_dir để tránh surya tưởng đã xong)
    manifest_url = f"{S3_BASE}/{model_path}/manifest.json"
    manifest = None
    for _ in range(20):
        try:
            mr = requests.get(manifest_url, timeout=TIMEOUT)
            mr.raise_for_status()
            manifest = mr.json()
            break
        except Exception as e:
            print(f"  [manifest lỗi] thử lại: {e}", flush=True)
            time.sleep(3)
    if not manifest:
        print("  KHÔNG tải được manifest", flush=True)
        return False

    files = manifest["files"]
    print(f"  {len(files)} file cần tải", flush=True)
    for i, fname in enumerate(files, 1):
        if fname == "manifest.json":
            continue
        print(f"  [{i}/{len(files)}] {fname}", flush=True)
        url = f"{S3_BASE}/{model_path}/{fname}"
        dest = local_dir / fname
        ok = download_file_resumable(url, dest)
        if not ok:
            print(f"  THẤT BẠI ở {fname}", flush=True)
            return False

    # Ghi manifest.json SAU CÙNG -> đánh dấu hoàn tất
    manifest_local.write_text(json.dumps(manifest))
    print(f"  ✓ HOÀN TẤT {model_path}", flush=True)
    return True


def main():
    print(f"Cache dir: {CACHE_DIR}", flush=True)
    all_ok = True
    for m in MODELS:
        ok = download_model(m)
        all_ok = all_ok and ok
    print("\n" + ("=" * 40), flush=True)
    print("TẤT CẢ XONG ✓" if all_ok else "CÓ MODEL CHƯA XONG ✗", flush=True)
    sys.exit(0 if all_ok else 1)


if __name__ == "__main__":
    main()
