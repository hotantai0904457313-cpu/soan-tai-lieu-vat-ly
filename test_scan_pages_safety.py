"""
Test lưới an toàn cho trang scan (GĐ 1 + GĐ 2): không bao giờ mất trang im lặng.

Chạy:  python test_scan_pages_safety.py
Phần lớn offline. Chỉ mục [6] gọi API thật (bỏ qua nếu không có key/mạng).

Bối cảnh: trước đây Gemini lỗi ở một trang scan thì hybrid lùi về _odl_text_only,
hàm này xoá ảnh nguyên trang rồi TRẢ CHUỖI RỖNG mà không raise → trang mất trắng,
không marker, không log, không báo UI.
"""

import sys
import os
import re

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.chdir(os.path.dirname(os.path.abspath(__file__)))

import core.pdf_to_word as p

SCAN6 = "data/uploads/originals/77a8272e3494468ea29d8c2b3f9e5e63.pdf"  # 6/6 trang scan

fails, npass = [], 0


def check(cond, label, detail=""):
    global npass
    if cond:
        npass += 1
        print(f"  PASS  {label}")
    else:
        fails.append(f"{label} — {detail}")
        print(f"  FAIL  {label}  {detail}")


# ── 1. Đếm chữ thực sự ───────────────────────────────────────────
print("\n[1] _meaningful_text_len")
check(p._meaningful_text_len("") == 0, "chuỗi rỗng = 0")
check(p._meaningful_text_len("![x](figures/a.png)") == 0,
      "chỉ có thẻ ảnh = 0 (trang scan ODL xử lý ra đúng thế này)")
check(p._meaningful_text_len("<!-- p2w:page=3 -->\n---\n") == 0,
      "mốc trang + dấu phân cách = 0")
check(p._meaningful_text_len("Câu 1. Một vật dao động") > 15, "có chữ thật > 15")

# ── 2. Phát hiện bị cắt giữa đường ───────────────────────────────
print("\n[2] _looks_truncated")
for s, exp, why in [
    ("Câu 1. Vật dao động với biên độ 5 cm.", False, "câu hoàn chỉnh"),
    ("**A.** 30", False, "dòng phương án"),
    ("| a | b |", False, "dòng bảng"),
    ("## Năng lượng dao động", False, "tiêu đề"),
    ("Một vật chuyển động thẳng đều với vận tốc ban đầu rất lớn và gia tốc",
     True, "câu dài không có dấu kết thúc"),
    ("Ta có $v = 2", True, "hở một dấu $"),
    ("", False, "rỗng thì không coi là cắt"),
]:
    check(p._looks_truncated(s) is exp, f"{why}", f"chuỗi={s[:40]!r}")

# ── 3. Chia nửa trang ────────────────────────────────────────────
print("\n[3] _png_halves (đọc lại khi hết hạn mức output)")
try:
    import fitz
    d = fitz.open(SCAN6)
    png = d[0].get_pixmap(matrix=fitz.Matrix(2, 2)).tobytes("png")
    d.close()
    halves = p._png_halves(png)
    check(len(halves) == 2, "chia được 2 nửa", f"được {len(halves)}")
    if len(halves) == 2:
        from PIL import Image
        import io
        with Image.open(io.BytesIO(png)) as im0:
            h0 = im0.size[1]
        hs = []
        for hb in halves:
            with Image.open(io.BytesIO(hb)) as im:
                hs.append(im.size[1])
        check(all(0.5 * h0 < x < 0.8 * h0 for x in hs),
              "mỗi nửa cao ~65% trang (có chồng lấn 15%)", f"cao {hs} / {h0}")
        check(sum(hs) > h0, "tổng 2 nửa > cả trang → có chồng lấn, không mất dòng giữa")
except Exception as e:
    check(False, "dựng được ảnh trang để chia nửa", str(e)[:90])

# ── 4. ODL trả rỗng phải RAISE, không im lặng ────────────────────
print("\n[4] _odl_text_only trên trang scan phải raise PageLost")
if not (p.odl_available() and p.java_available()):
    print("  BỎ QUA  máy này không có ODL/Java")
else:
    from pathlib import Path
    wd = p.MARKER_OUT / "_test_pagelost"
    import shutil
    shutil.rmtree(wd, ignore_errors=True)
    try:
        txt = p._odl_text_only(Path(SCAN6), wd, "1")
        check(False, "raise PageLost cho trang scan",
              f"lại trả về {len(txt)} ký tự thay vì raise")
    except p.PageLost:
        check(True, "raise PageLost cho trang scan (trước đây trả '' im lặng)")
    except Exception as e:
        check(False, "raise ĐÚNG loại PageLost", f"raise {type(e).__name__}: {str(e)[:70]}")
    shutil.rmtree(wd, ignore_errors=True)

# ── 5. Marker trang lỗi: luôn nhúng ảnh trang gốc ────────────────
print("\n[5] _page_failure_marker — bậc cuối, không để trang trắng")
from pathlib import Path
fig = p.MARKER_OUT / "_test_marker_fig"
fig.mkdir(parents=True, exist_ok=True)
note = p._page_failure_marker(2, "bị Google chặn", fig, Path(SCAN6), "")
check("⚠️" in note and "[Trang 3]" in note, "có cảnh báo nêu rõ số trang")
check("bị Google chặn" in note, "nêu lý do thất bại")
check("p3_fullpage.png" in note, "nhúng ảnh trang gốc vào markdown")
check((fig / "p3_fullpage.png").exists(), "ảnh trang gốc được ghi ra đĩa thật")
note2 = p._page_failure_marker(2, "x", fig, Path(SCAN6), "Câu 5. Phần đọc được dở dang")
check("Câu 5. Phần đọc được dở dang" in note2,
      "giữ lại phần chữ đã đọc được (thà ít hơn mất trắng)")
import shutil
shutil.rmtree(fig, ignore_errors=True)

# ── 6. Mốc trang bị xoá trước khi dựng Word ──────────────────────
print("\n[6] Mốc trang <!-- p2w:page=N --> không được lọt vào Word")
tmp = p.MARKER_OUT / "_test_marker.md"
tmp.parent.mkdir(parents=True, exist_ok=True)
tmp.write_text("<!-- p2w:page=1 -->\nCâu 1. Nội dung\n\n<!-- p2w:page=2 -->\nCâu 2. Nữa\n",
               encoding="utf-8")
p._strip_page_markers(tmp)
out = tmp.read_text(encoding="utf-8")
check("p2w:page" not in out, "mốc đã bị xoá sạch", out[:60])
check("Câu 1. Nội dung" in out and "Câu 2. Nữa" in out, "nội dung giữ nguyên")
tmp.unlink(missing_ok=True)

# ── 7. E2E: PDF scan 6 trang, không trang nào rỗng ───────────────
print("\n[7] E2E 6 trang scan (gọi API thật)")
import sqlite3
try:
    s = dict(sqlite3.connect("data/documents.db").execute("select key,value from settings"))
except Exception:
    s = {}
if not (s.get("gemini_api_key") or s.get("niner_router_key")):
    print("  BỎ QUA  không có API key")
elif os.environ.get("SKIP_API"):
    print("  BỎ QUA  đặt SKIP_API=1")
else:
    out = p.EXPORT_DIR / "_test_scan6.docx"
    try:
        r = p.convert_pdf_to_word(
            SCAN6, out_docx=out, method="hybrid", ocr_mode="auto",
            gemini_api_key=s.get("gemini_api_key", ""),
            niner_url=s.get("niner_router_url", "http://127.0.0.1:20128/v1"),
            niner_key=s.get("niner_router_key", ""))
        check(r["ocr_used"] is True,
              "ocr_used = True (trước đây hard-code False → UI ghi '· nhanh')",
              f"ocr_used={r['ocr_used']}")
        check(len(r.get("scan_pages") or []) == 6, "nhận đủ 6 trang scan",
              f"{r.get('scan_pages')}")
        check(r.get("failed_pages") == [], "0 trang thất bại",
              f"failed={r.get('failed_pages')}")
        from docx import Document
        doc = Document(r["docx_path"])
        txt = "\n".join(x.text for x in doc.paragraphs)
        chars = len(re.sub(r"\s+", " ", txt).strip())
        check(chars > 6 * 300, f"≥300 ký tự/trang (thực tế {chars} cho 6 trang)",
              f"{chars} ký tự")
        check("p2w:page" not in txt, "không có mốc trang trong Word")
        check(txt.count("$") % 2 == 0, "dấu $ cân bằng", f"{txt.count('$')} dấu")
    except Exception as e:
        check(False, "chuyển đổi chạy xong không lỗi", f"{type(e).__name__}: {str(e)[:120]}")

print(f"\n{'='*58}")
if fails:
    print(f"THẤT BẠI {len(fails)} / {npass + len(fails)} tiêu chí:")
    for f in fails:
        print(f"  - {f}")
    sys.exit(1)
print(f"TẤT CẢ {npass} TIÊU CHÍ ĐỀU PASS")
