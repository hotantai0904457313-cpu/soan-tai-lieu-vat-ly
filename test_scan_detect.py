"""
Test nhận diện PDF bản SCAN (core/pdf_scan.py) — GĐ 0.

Chạy:  python test_scan_detect.py
Offline hoàn toàn, KHÔNG tốn API.

Bối cảnh: cách cũ đếm `len(page.get_text()) >= 60` nên PDF scan có watermark
63 ký tự/trang bị coi là PDF chữ → tắt OCR → mất sạch nội dung (quyển 436 trang
ra file Word chỉ 1 885 ký tự). Bộ test này chốt 2 điều:
  - mọi file scan đã biết phải được nhận ra ở MỌI trang
  - KHÔNG file digital nào bị gắn nhãn scan (báo nhầm sẽ đẩy trang chữ sang
    Gemini, vừa chậm vừa đổi định dạng bảng)
"""

import sys
import os
import time
import glob

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.chdir(os.path.dirname(os.path.abspath(__file__)))

from core.pdf_scan import (analyze_pdf, boilerplate_lines, norm_line,
                           page_is_scan, page_image_cover)

ORIG = "data/uploads/originals"
EBOOK = f"{ORIG}/09812ec05ef44c4c80e871317c0791ef.pdf"   # 436 trang scan + watermark

# Ground truth: file scan (đã xác minh bằng tay — mọi trang là ảnh chụp)
SCAN_FILES = {
    "09812ec05ef44c4c80e871317c0791ef.pdf",  # 436 tr, watermark TaiLieuOnThi 63 ký tự
    "12fc2912419a496b9dbdfd33e95accab.pdf",
    "1ef88a0b16174ed0963797c75964c227.pdf",  # scan GHÉP MẢNH: ảnh vụn, độ phủ chỉ 0,03-0,17
    "21886883c1b94393b9907940f18bc4b8.pdf",
    "23aa6157047b41d0b79cb1d9debef4c3.pdf",
    "56da084e27e348f5ae31be5695e7a10c.pdf",
    "656d5e84ae064c43bdf68b98a4a3314f.pdf",
    "6ff55751f76a4dff9ad51894fea05d3a.pdf",
    "77a8272e3494468ea29d8c2b3f9e5e63.pdf",
    "78735f360279462d8faa38608113f6fb.pdf",
    "8612013141174ec391af017ca38c9559.pdf",
    "89ce138647bf4760ad426f26034cda15.pdf",
    "ad51cbaf92b946f1812c95a9a0e0a122.pdf",
    "dd94bfd864da44aaae85d2ca73e0b7cb.pdf",
    "dfafed1c23fe453daeefd2849870c3a5.pdf",
}

fails = []
npass = 0


def check(cond, label, detail=""):
    global npass
    if cond:
        npass += 1
        print(f"  PASS  {label}")
    else:
        fails.append(f"{label} — {detail}")
        print(f"  FAIL  {label}  {detail}")


# ── 1. Hàm đơn lẻ ────────────────────────────────────────────────
print("\n[1] Hàm đơn lẻ")
check(norm_line("  TAILIEUONTHI.NET  ") == "tailieuonthi.net",
      "norm_line bỏ khoảng trắng + hoa thường")
check(norm_line("Tài  Liệu   Ôn Thi") == "tài liệu ôn thi",
      "norm_line gộp khoảng trắng, giữ dấu tiếng Việt")

# ── 2. EBOOK 436 trang — ca hỏng nặng nhất ───────────────────────
print("\n[2] EBOOK 436 trang (watermark 63 ký tự/trang)")
if not os.path.exists(EBOOK):
    check(False, "tìm thấy file EBOOK", EBOOK)
else:
    t0 = time.time()
    r = analyze_pdf(EBOOK)
    dt = time.time() - t0
    check(r["checked"] == 436, "kiểm đủ 436 trang", f"checked={r['checked']}")
    check(len(r["scan_pages"]) == r["checked"],
          "MỌI trang được nhận là scan",
          f"{len(r['scan_pages'])}/{r['checked']}")
    check(r["is_digital"] is False,
          "is_digital = False (trước đây là True → tắt OCR, mất nội dung)",
          f"is_digital={r['is_digital']}, coverage={r['coverage']}")
    check(r["is_scan_doc"] is True, "is_scan_doc = True")
    check(any("tailieuonthi" in b for b in r["boilerplate"]),
          "watermark bị nhận là boilerplate",
          f"boilerplate={r['boilerplate'][:3]}")
    check(dt < 5.0, f"quét 436 trang dưới 5s (thực tế {dt:.1f}s)",
          f"{dt:.1f}s — get_image_rects từng làm mất 206s")

# ── 3. Toàn bộ kho file: scan nhận đúng, digital không báo nhầm ──
print("\n[3] Toàn kho data/uploads/originals")
files = sorted(glob.glob(f"{ORIG}/*.pdf"))
check(len(files) >= 70, f"tìm thấy {len(files)} file PDF", f"chỉ có {len(files)}")

miss_scan, false_pos, partial = [], [], []
t0 = time.time()
for p in files:
    b = os.path.basename(p)
    r = analyze_pdf(p)
    n, sp = r["checked"], len(r["scan_pages"])
    if b in SCAN_FILES:
        if sp != n:
            miss_scan.append(f"{b} chỉ {sp}/{n}")
    elif sp == n and n > 0:
        false_pos.append(f"{b} ({n} trang)")
    elif sp > 0:
        partial.append(f"{b} {sp}/{n} trang {r['scan_pages'][:6]}")
dt_all = time.time() - t0

check(not miss_scan, f"{len(SCAN_FILES)} file scan: nhận đúng 100% trang",
      "; ".join(miss_scan[:4]))
check(not false_pos, "0 file digital bị gắn nhãn scan toàn bộ",
      "; ".join(false_pos[:4]))
check(not partial, "0 file digital có trang bị báo nhầm là scan",
      "; ".join(partial[:4]))
check(dt_all < 90, f"quét cả {len(files)} file dưới 90s (thực tế {dt_all:.0f}s)",
      f"{dt_all:.0f}s")

# ── 4. Bẫy đã gặp: tài liệu có nhiều trang GẦN TRÙNG nhau ────────
# Lọc boilerplate quá lỏng sẽ coi nội dung lặp là watermark rồi xoá sạch →
# trang chữ thật biến thành "trang scan". Chốt SCAN_RAW_TEXT_MAX chặn việc đó.
print("\n[4] Bẫy trang gần trùng nhau (chống báo nhầm)")
for trap in ("2f1b5590", "a93584e2"):
    hit = glob.glob(f"{ORIG}/{trap}*.pdf")
    if not hit:
        print(f"  BỎ QUA  không có file {trap}* trên máy này")
        continue
    r = analyze_pdf(hit[0])
    check(len(r["scan_pages"]) == 0,
          f"{trap}*: trang nội dung lặp KHÔNG bị coi là scan",
          f"scan_pages={r['scan_pages'][:6]}")

# ── 5. Trang trắng thật không phải scan ──────────────────────────
print("\n[5] Trang trắng (không ảnh, không nét vẽ) không được coi là scan")
try:
    import fitz
    d = fitz.open()
    d.new_page()                      # trang trắng tinh
    check(page_is_scan(d[0], set()) is False, "trang trắng → không phải scan")
    check(page_image_cover(d[0]) == 0.0, "độ phủ ảnh của trang trắng = 0")
    d.close()
except Exception as e:
    check(False, "dựng được PDF trắng trong bộ nhớ", str(e)[:80])

# ── Kết luận ─────────────────────────────────────────────────────
print(f"\n{'='*58}")
if fails:
    print(f"THẤT BẠI {len(fails)} / {npass + len(fails)} tiêu chí:")
    for f in fails:
        print(f"  - {f}")
    sys.exit(1)
print(f"TẤT CẢ {npass} TIÊU CHÍ ĐỀU PASS")
