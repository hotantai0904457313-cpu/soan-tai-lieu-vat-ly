"""
GĐ4 — Sách scan lớn. Offline, 0 tốn API (giả lập lời gọi Vision).

Chạy:  python test_scan_large.py

[1] scan_size_check: EBOOK 436 trang → cảnh báo, gợi ý dải 50 trang, ước lượng
    thời gian; chọn dải ≤ 80 trang hoặc file digital → không cảnh báo; < 5 s.
[2] Endpoint /api/pdf-to-word: tài liệu lớn trả needs_confirm, KHÔNG xếp hàng,
    dọn thư mục job.
[3] Bộ nhớ đệm từng trang (đường Gemini, render theo yêu cầu): lần 2 không gọi
    AI lại trang đã đọc, kết quả + hình giống hệt; trang lỗi không được lưu nên
    lần sau đọc lại; khoá theo nội dung PDF (2 file khác nhau → khác khoá).
"""

import sys
import os
import json
import time
import shutil
import tempfile
import threading
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.chdir(os.path.dirname(os.path.abspath(__file__)))

import core.pdf_to_word as p2w

ORIG = "data/uploads/originals"
EBOOK = f"{ORIG}/09812ec05ef44c4c80e871317c0791ef.pdf"    # 436 trang scan
SCAN6 = f"{ORIG}/77a8272e3494468ea29d8c2b3f9e5e63.pdf"    # 6/6 trang scan
DIGITAL = f"{ORIG}/24794017ca6a48cb9804e1e3aec703f9.pdf"  # 4 trang digital

fails, npass = [], 0


def check(cond, label, detail=""):
    global npass
    if cond:
        npass += 1
        print(f"  PASS  {label}")
    else:
        fails.append(f"{label} — {detail}")
        print(f"  FAIL  {label}  {detail}")


# ── 1. scan_size_check ──────────────────────────────────────────────
print("\n[1] scan_size_check")
t = time.time()
r = p2w.scan_size_check(EBOOK)
dt = time.time() - t
check(r["large"] and r["scan_pages"] == 436 and r["pages"] == 436,
      "EBOOK 436 trang scan → cảnh báo", f"{r}")
check(r["ranges"][:2] == ["1-50", "51-100"] and r["ranges"][-1] == "401-436"
      and len(r["ranges"]) == 9, "gợi ý 9 dải 50 trang", f"{r['ranges']}")
check(r["est_min"] == round(436 * p2w.SCAN_SEC_PER_PAGE / 60),
      "ước lượng theo số đo thật (~10 s/trang)", f"{r['est_min']} phút")
check(dt < 5, "phân tích 436 trang < 5 s", f"{dt:.1f}s")
r2 = p2w.scan_size_check(EBOOK, "101-150")
check(not r2["large"] and r2["scan_pages"] == 50 and r2["ranges"] == ["101-150"],
      "chọn dải 50 trang → không cảnh báo", f"{r2}")
check(not p2w.scan_size_check(DIGITAL)["large"], "file digital → không cảnh báo")
check(not p2w.scan_size_check("khong_ton_tai.pdf")["large"],
      "lỗi đọc file → không chặn việc chuyển")


# ── 2. Endpoint ─────────────────────────────────────────────────────
print("\n[2] /api/pdf-to-word với tài liệu scan lớn")
import app as flask_app  # noqa: E402

client = flask_app.app.test_client()
before = set(os.listdir(os.path.join(flask_app.UPLOAD_DIR, "p2w"))) \
    if os.path.isdir(os.path.join(flask_app.UPLOAD_DIR, "p2w")) else set()
jobs_before = set(flask_app._p2w_jobs)
with open(EBOOK, "rb") as f:
    resp = client.post("/api/pdf-to-word",
                       data={"files": (f, "sach_scan.pdf"), "method": "hybrid"},
                       content_type="multipart/form-data")
d = resp.get_json() or {}
check(resp.status_code == 200 and d.get("needs_confirm") is True,
      "trả needs_confirm", f"{resp.status_code} {str(d)[:120]}")
w = (d.get("warnings") or [{}])[0]
check(w.get("name") == "sach_scan.pdf" and w.get("scan_pages") == 436
      and w.get("ranges", [None])[0] == "1-50", "kèm tên file, số trang scan, dải gợi ý")
check(set(flask_app._p2w_jobs) == jobs_before, "KHÔNG tạo job / không xếp hàng")
after = set(os.listdir(os.path.join(flask_app.UPLOAD_DIR, "p2w")))
check(after == before, "dọn thư mục job vừa tạo", f"thừa {after - before}")


# ── 3. Bộ nhớ đệm từng trang ────────────────────────────────────────
print("\n[3] Bộ nhớ đệm từng trang (đường Gemini, AI giả lập)")
MODEL = "test/khong-phai-model-that"
calls = {"n": 0, "fail_page": None}
lock = threading.Lock()


def fake_vision(model, img_bytes, prompt, **kw):
    with lock:
        calls["n"] += 1
    # Mỗi trang 1 nội dung riêng: nhận ra trang qua kích thước ảnh render
    from PIL import Image
    import io
    w, h = Image.open(io.BytesIO(img_bytes)).size
    if calls["fail_page"] and calls["fail_page"](w, h):
        raise p2w.VisionBlocked("giả lập bị chặn")
    return json.dumps({
        "markdown": f"**Câu 1.** Nội dung trang ảnh {w}x{h} đủ dài để không bị coi là "
                    f"trang rỗng.\n\n{{{{FIGURE_1}}}}\n\n**A.** 1 cm",
        "figures": [{"id": 1, "box_2d": [100, 100, 400, 500], "caption": "Hình",
                     "anchor_text": "Câu 1"}]})


orig = p2w._vision_call
p2w._vision_call = fake_vision
cache_dirs = []
try:
    key6 = p2w._page_cache_key(Path(SCAN6), MODEL)
    keyE = p2w._page_cache_key(Path(DIGITAL), MODEL)
    cache_dirs.append(p2w._PAGE_CACHE_DIR / key6)
    shutil.rmtree(cache_dirs[0], ignore_errors=True)
    check(key6 != keyE and key6 == p2w._page_cache_key(Path(SCAN6), MODEL)
          and key6 != p2w._page_cache_key(Path(SCAN6), MODEL + "x"),
          "khoá theo nội dung PDF + model (ổn định, khác file → khác khoá)")

    def run():
        wd = Path(tempfile.mkdtemp())
        md, st = p2w._convert_via_gemini(Path(SCAN6), wd, "", model=MODEL)
        figs = sorted(f.name for f in (wd / "figures").glob("p*_fig*.png"))
        return md.read_text(encoding="utf-8"), st, figs, wd

    calls["n"] = 0
    md1, st1, figs1, wd1 = run()
    n1 = calls["n"]
    check(n1 == 6 and st1["failed_pages"] == [], "lần 1: gọi AI 6 trang, 0 trang lỗi",
          f"calls={n1} failed={st1['failed_pages']}")
    check(len(list(cache_dirs[0].glob("p*.md"))) == 6, "lưu đủ 6 trang vào bộ nhớ đệm")

    calls["n"] = 0
    md2, st2, figs2, wd2 = run()
    check(calls["n"] == 0, "lần 2: KHÔNG gọi AI lại trang đã đọc", f"calls={calls['n']}")
    check(md2 == md1 and figs2 == figs1 and len(figs1) == 6,
          "kết quả + hình giống hệt lần 1", f"{len(figs1)} / {len(figs2)} hình")

    # Trang lỗi không được lưu → lần sau đọc lại đúng trang đó
    shutil.rmtree(cache_dirs[0], ignore_errors=True)
    import fitz
    # Chặn trang 3 (nhận ra qua kích thước ảnh render zoom 3; trang cùng cỡ cũng bị chặn)
    sizes = {}
    with fitz.open(SCAN6) as d6:
        for i in range(d6.page_count):
            sizes[i] = (round(d6[i].rect.width * 3), round(d6[i].rect.height * 3))
    target = sizes[2]
    same = [i for i, s in sizes.items() if s == target]
    calls["fail_page"] = lambda w, h: (w, h) == target
    calls["n"] = 0
    md3, st3, _, wd3 = run()
    failed = set(st3["failed_pages"])
    check(failed == {i + 1 for i in same}, "trang bị chặn → trang lỗi (ảnh trang gốc)",
          f"{sorted(failed)} vs {[i + 1 for i in same]}")
    cached = {int(f.stem[1:]) for f in cache_dirs[0].glob("p*.md")}
    check(not (cached & failed) and len(cached) == 6 - len(failed),
          "trang lỗi KHÔNG được lưu vào bộ nhớ đệm", f"cached={sorted(cached)}")
    calls["fail_page"] = None
    calls["n"] = 0
    md4, st4, _, wd4 = run()
    check(calls["n"] == len(failed) and st4["failed_pages"] == [],
          "lần sau chỉ đọc lại đúng trang từng lỗi", f"calls={calls['n']}")

    old = p2w._PAGE_CACHE_DIR / "zz_test_cu"
    old.mkdir(parents=True, exist_ok=True)
    os.utime(old, (time.time() - 9 * 86400,) * 2)
    p2w.cleanup_page_cache()
    check(not old.exists() and cache_dirs[0].exists(),
          "dọn bộ nhớ đệm > 7 ngày, giữ bộ mới")
    for wd in (wd1, wd2, wd3, wd4):
        shutil.rmtree(wd, ignore_errors=True)
finally:
    p2w._vision_call = orig
    for c in cache_dirs:
        shutil.rmtree(c, ignore_errors=True)


print(f"\n{npass} PASS, {len(fails)} FAIL")
if fails:
    for f in fails:
        print("  -", f)
    sys.exit(1)
