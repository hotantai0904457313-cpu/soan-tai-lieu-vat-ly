"""
Cổng chống hồi quy cho PDF DIGITAL — chạy TRƯỚC mỗi lần commit.

Chạy:  python test_digital_regression.py
Mục [1]+[2] offline. Mục [3] chuyển đổi thật 1 file digital (đi ODL thuần, không
gọi AI nên tất định).

Vì sao cần: việc nhận diện scan mới (core/pdf_scan.py) và prompt/zoom riêng cho
trang scan tuyệt đối KHÔNG được làm đổi kết quả của PDF chữ — đường đó đang chạy
tốt. Báo nhầm một trang digital thành scan sẽ đẩy nó sang Gemini: chậm hơn và
bảng bị đổi định dạng.

Ghi chú về "bản vàng": không so sánh byte-đối-byte với một bản chụp trước, vì
HEAD của repo chưa có phần việc trích hình (10/09) — kho đang có nhiều thay đổi
chưa commit, nên HEAD không phải mốc so sánh hợp lệ. Thay vào đó kiểm 2 thứ chắc
chắn hơn: (a) việc phân loại trang KHÔNG đổi so với luật cũ, (b) các bất biến về
chất lượng của file Word xuất ra.
"""

import sys
import os
import re
import glob

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.chdir(os.path.dirname(os.path.abspath(__file__)))

import fitz
import core.pdf_to_word as p
from core.pdf_to_word import _classify_pages, detect_text_layer, _MATH_DENSITY_CHARS

ORIG = "data/uploads/originals"
SCAN_FILES = {
    "09812ec05ef44c4c80e871317c0791ef", "12fc2912419a496b9dbdfd33e95accab",
    "1ef88a0b16174ed0963797c75964c227", "21886883c1b94393b9907940f18bc4b8",
    "23aa6157047b41d0b79cb1d9debef4c3", "56da084e27e348f5ae31be5695e7a10c",
    "656d5e84ae064c43bdf68b98a4a3314f", "6ff55751f76a4dff9ad51894fea05d3a",
    "77a8272e3494468ea29d8c2b3f9e5e63", "78735f360279462d8faa38608113f6fb",
    "8612013141174ec391af017ca38c9559", "89ce138647bf4760ad426f26034cda15",
    "ad51cbaf92b946f1812c95a9a0e0a122", "dd94bfd864da44aaae85d2ca73e0b7cb",
    "dfafed1c23fe453daeefd2849870c3a5",
}
# File digital 4 trang, 0 trang phức tạp → đi ODL thuần, không gọi AI, tất định
DIGITAL_E2E = f"{ORIG}/24794017ca6a48cb9804e1e3aec703f9.pdf"

fails, npass = [], 0


def check(cond, label, detail=""):
    global npass
    if cond:
        npass += 1
        print(f"  PASS  {label}")
    else:
        fails.append(f"{label} — {detail}")
        print(f"  FAIL  {label}  {detail}")


def old_classify(pdf_path, idxs):
    """Bản sao LUẬT CŨ (trước khi thay bộ nhận diện scan), gồm luật 2 cũ:
    'text < 50 ký tự + có ảnh → trang phức tạp'."""
    cs = set()
    doc = fitz.open(str(pdf_path))
    try:
        for idx in idxs:
            page = doc[idx]
            cx = False
            txt = page.get_text("text") or ""
            try:
                if len(getattr(page.find_tables(), "tables", []) or []) > 0:
                    cx = True
            except Exception:
                pass
            if not cx and len(txt.strip()) < 50:          # luật 2 CŨ
                try:
                    if page.get_images(full=True):
                        cx = True
                except Exception:
                    pass
            if not cx:
                try:
                    if len(page.get_drawings()) > 40:
                        cx = True
                except Exception:
                    pass
            if not cx:
                n_it = sum(1 for c in txt if 0x1D400 <= ord(c) <= 0x1D7FF)
                n_sym = sum(txt.count(c) for c in _MATH_DENSITY_CHARS)
                hf = bool(re.search(r'\b(cos|sin|tan|cot|log|ln|sqrt)\b', txt)) or '√' in txt
                if (n_sym + n_it) >= 8 or (hf and n_it >= 2):
                    cx = True
            if not cx and len(re.findall(
                    r'[\dπλμαβγθωφ]\s*/\s*[\dπλμαβγθωφ]', txt)) >= 2:
                cx = True
            if not cx and re.search(r'[.,xX×·]\s*10\s*-?\d', txt):
                cx = True
            if cx:
                cs.add(idx)
    finally:
        doc.close()
    return cs


# ── 1. Không file digital nào bị gắn nhãn scan ───────────────────
print("\n[1] Nhận diện scan không báo nhầm trên file digital")
digital = [f for f in sorted(glob.glob(f"{ORIG}/*.pdf"))
           if os.path.basename(f)[:-4] not in SCAN_FILES]
bad = []
for f in digital:
    r = detect_text_layer(f, page_indices=None)
    if r.get("scan_pages"):
        bad.append(f"{os.path.basename(f)[:14]} trang {r['scan_pages'][:5]}")
check(len(digital) >= 50, f"có {len(digital)} file digital để kiểm")
check(not bad, "0 trang digital bị gắn nhãn scan", "; ".join(bad[:5]))

# ── 2. Phân loại trang y hệt luật cũ ─────────────────────────────
print("\n[2] _classify_pages không đổi so với luật cũ (không bớt trang gửi Gemini)")
lost = gain = 0
diffs = []
for f in digital:
    try:
        doc = fitz.open(f)
        n = min(doc.page_count, 40)
        doc.close()
        idxs = list(range(n))
        old = old_classify(f, idxs)
        det = detect_text_layer(f, page_indices=idxs)
        new = _classify_pages(f, idxs, scan_set=set(det.get("scan_pages") or ()))
        if old - new or new - old:
            diffs.append(f"{os.path.basename(f)[:14]} mất {sorted(old-new)[:4]} thêm {sorted(new-old)[:4]}")
        lost += len(old - new)
        gain += len(new - old)
    except Exception as e:
        diffs.append(f"{os.path.basename(f)[:14]} LỖI {str(e)[:50]}")
check(lost == 0, f"0 trang bị BỚT khỏi Gemini (thực tế {lost})", "; ".join(diffs[:4]))
check(gain == 0, f"0 trang bị THÊM vào Gemini (thực tế {gain})", "; ".join(diffs[:4]))

# ── 3. E2E 1 file digital: bất biến chất lượng ───────────────────
print("\n[3] E2E file digital 4 trang (ODL thuần, không gọi AI)")
if not (p.odl_available() and p.java_available() and p.find_pandoc()):
    print("  BỎ QUA  máy này thiếu ODL/Java/Pandoc")
else:
    out = p.EXPORT_DIR / "_test_digital_regression.docx"
    try:
        r = p.convert_pdf_to_word(DIGITAL_E2E, out_docx=out, method="hybrid",
                                  ocr_mode="auto", gemini_api_key="",
                                  niner_url="", niner_key="")
        from docx import Document
        d = Document(r["docx_path"])
        txt = "\n".join(x.text for x in d.paragraphs)
        chars = len(re.sub(r"\s+", " ", txt).strip())
        # Số đo tham chiếu đo được 01/10/2026 trên chính file này
        check(4700 <= chars <= 5300, f"số ký tự trong khoảng tham chiếu (được {chars})",
              f"{chars} — tham chiếu ~4965")
        check(len(d.inline_shapes) == 4, f"đúng 4 hình như tham chiếu",
              f"được {len(d.inline_shapes)}")
        check(len(re.findall(r"Câu\s*\d+", txt)) == 20, "đủ 20 'Câu N'",
              f"được {len(re.findall(r'Câu\s*\d+', txt))}")
        check(txt.count("$") % 2 == 0, "dấu $ cân bằng", f"{txt.count('$')} dấu")
        check("⚠️" not in txt, "KHÔNG có cảnh báo trang lỗi trên file digital")
        check("p2w:page" not in txt, "mốc trang không lọt vào Word")
        check(r.get("failed_pages") == [], "failed_pages rỗng",
              f"{r.get('failed_pages')}")
        check(r.get("ocr_used") is False, "ocr_used = False (file digital)",
              f"ocr_used={r.get('ocr_used')}")
    except Exception as e:
        check(False, "chuyển đổi chạy xong không lỗi",
              f"{type(e).__name__}: {str(e)[:130]}")

print(f"\n{'='*58}")
if fails:
    print(f"THẤT BẠI {len(fails)} / {npass + len(fails)} tiêu chí:")
    for f in fails:
        print(f"  - {f}")
    sys.exit(1)
print(f"TẤT CẢ {npass} TIÊU CHÍ ĐỀU PASS")
