"""
GĐ3 — Hình cho trang SCAN. Offline, 0 tốn API.

Chạy:  python test_scan_figures.py

[1] Lấy ảnh con trang scan (collect_page_figures scan=True) trên trang thật:
    đúng số hình, đúng kích thước, bỏ ảnh nền, PNG 288 DPI.
[2] Neo hình trang scan trong md giả lập (mốc trang + json Gemini): thay đúng
    chỗ thẻ Gemini, thay dòng "[Hình: …]", trang không có Gemini thì đặt theo Y
    TRONG trang — không hình nào dồn xuống cuối tài liệu; hình Gemini được cắt
    lại từ PDF ở 288 DPI.
[3] Đọc theo nửa trang: 2 nửa không ghi đè file hình của nhau, bbox quy về %
    cả trang, DPI ghi đúng, json lần đọc bị bỏ được dọn.
[4] Ghép 2 nửa: bỏ dòng/hình lặp ở dải chồng lấn; hình không neo được bằng
    chữ thì bám về câu (sau đề, trước phương án A) và thay dòng "*[Hình…]*".
"""

import sys
import os
import io
import json
import re
import shutil
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.chdir(os.path.dirname(os.path.abspath(__file__)))

import fitz
from PIL import Image

import core.pdf_to_word as p2w
import core.pdf_word_figures as wf

ORIG = "data/uploads/originals"
EBOOK = f"{ORIG}/09812ec05ef44c4c80e871317c0791ef.pdf"   # 436 trang scan + watermark
AD51 = f"{ORIG}/ad51cbaf92b946f1812c95a9a0e0a122.pdf"    # 20 trang scan ghép mảnh

fails, npass = [], 0


def check(cond, label, detail=""):
    global npass
    if cond:
        npass += 1
        print(f"  PASS  {label}")
    else:
        fails.append(f"{label} — {detail}")
        print(f"  FAIL  {label}  {detail}")


def near(sizes, want, tol=4):
    """Mỗi kích thước mong đợi khớp đúng 1 hình (sai số tol pt)."""
    left = list(sizes)
    for w, h in want:
        hit = next((s for s in left if abs(s[0] - w) <= tol and abs(s[1] - h) <= tol), None)
        if hit is None:
            return False
        left.remove(hit)
    return True


# ── 1. Lấy ảnh con trang scan ───────────────────────────────────────
print("\n[1] Lấy ảnh con trang scan (scan=True)")
CASES = [
    (EBOOK, 1, []),
    (EBOOK, 110, [(171, 112), (253, 177)]),
    (EBOOK, 118, [(292, 190)]),
    (AD51, 1, [(182, 91), (193, 112)]),
    (AD51, 4, []),
    (AD51, 12, [(105, 68), (119, 102), (116, 89), (218, 147)]),
]
for path, pno, want in CASES:
    tag = f"{Path(path).stem[:8]} tr.{pno}"
    if not os.path.exists(path):
        check(False, f"{tag}: có file", path)
        continue
    doc = fitz.open(path)
    page = doc[pno - 1]
    d = Path(tempfile.mkdtemp())
    try:
        figs, _, _ = wf.collect_page_figures(page, d, pno, scan=True)
        sizes = [(round(f['rect'].width), round(f['rect'].height)) for f in figs]
        check(len(figs) == len(want) and near(sizes, want),
              f"{tag}: {len(want)} hình đúng kích thước", f"được {sizes}")
        area = page.rect.width * page.rect.height
        check(all(f['rect'].width * f['rect'].height <= 0.70 * area for f in figs),
              f"{tag}: không hình nào là ảnh nền (> 70% trang)")
        if figs:
            dpi = Image.open(figs[0]['png']).info.get('dpi', (0, 0))
            check(round(dpi[0]) == 288, f"{tag}: PNG ghi 288 DPI", f"dpi={dpi}")
    finally:
        doc.close()
        shutil.rmtree(d, ignore_errors=True)


# ── 2. Neo hình trang scan ──────────────────────────────────────────
print("\n[2] Neo hình trang scan trong md (mốc trang + json Gemini)")


def pct(rect, page):
    W, H = page.rect.width, page.rect.height
    return [rect.x0 / W * 100, rect.y0 / H * 100, rect.x1 / W * 100, rect.y1 / H * 100]


if os.path.exists(EBOOK):
    work = Path(tempfile.mkdtemp())
    fig_dir = work / "figures"
    fig_dir.mkdir()
    doc = fitz.open(EBOOK)
    p110 = doc[109]
    r110 = wf._scan_raster_rects(p110)
    r110 = sorted(r110, key=lambda r: (r.y0, r.x0))
    # Hình 1: Gemini đã cắt + chèn thẻ (bbox lệch nhẹ như AI hay khai)
    g1 = fitz.Rect(r110[0].x0 + 6, r110[0].y0 - 5, r110[0].x1 + 8, r110[0].y1 + 4)
    # Hình 2: Gemini khai nhưng không cắt được → dòng chú thích
    g2 = r110[1]
    (fig_dir / "p110_gemini_boxes.json").write_text(json.dumps([
        {"id": 1, "bbox": pct(g1, p110), "placed": True,
         "caption": "Hình 1", "anchor_text": "Câu 5. Một vật dao động"},
        {"id": 2, "bbox": pct(g2, p110), "placed": False,
         "caption": "đồ thị vận tốc", "anchor_text": "Câu 6. Đồ thị"},
    ], ensure_ascii=False), encoding="utf-8")
    Image.new("RGB", (40, 30), "white").save(fig_dir / "p110_fig1.png", dpi=(216, 216))
    doc.close()

    md = "\n".join([
        "<!-- p2w:page=110 -->",
        "**Câu 5.** Một vật dao động điều hoà có đồ thị li độ như hình bên.",
        "",
        "![Hình 1](figures/p110_fig1.png)",
        "",
        "**A.** 2 cm  **B.** 4 cm",
        "",
        "**Câu 6.** Đồ thị vận tốc theo thời gian được cho như hình.",
        "",
        "*[Hình: đồ thị vận tốc]*",
        "",
        "**A.** 1 s",
        "",
        "---",
        "",
        "<!-- p2w:page=111 -->",
        "**Câu 7.** Trang này không có hình nào.",
        "",
        "---",
        "",
        "<!-- p2w:page=118 -->",
        "**Câu 9.** Một con lắc lò xo dao động điều hoà.",
        "",
        "**A.** 1 J",
        "",
        "**Câu 10.** Cơ năng của con lắc bằng bao nhiêu?",
        "",
        "**A.** 0,5 J",
        "",
        "**Câu 11.** Chu kì dao động của con lắc là bao nhiêu?",
        "",
        "**A.** 1 s",
    ])
    md_path = work / "x_hybrid.md"
    md_path.write_text(md, encoding="utf-8")
    n = wf.attach_figures(EBOOK, work, md_path, page_range="110,111,118",
                          scan_pages={109, 110, 117})
    out = md_path.read_text(encoding="utf-8")
    L = out.split("\n")

    def line_of(sub):
        return next((i for i, s in enumerate(L) if sub in s), -1)

    marks = [(i, int(m.group(1))) for i, l in enumerate(L)
             if (m := re.match(r"<!-- p2w:page=(\d+) -->", l))]
    det = [(i, int(m.group(1))) for i, l in enumerate(L)
           if (m := re.search(r"figures/p(\d+)_det\d+\.png", l))]
    misplaced = [(i, pg) for i, pg in det
                 if not any(mi < i and pn == pg and all(not (mi < mj < i) for mj, _ in marks)
                            for mi, pn in marks)]
    check(n == len(det) >= 3, "gắn đủ hình xác định (≥ 3)", f"n={n}, thẻ={len(det)}")
    check(not misplaced, "MỌI hình pN_det nằm giữa mốc trang N và mốc kế", f"{misplaced}")
    check("![Hình trang" not in out, "không hình nào dồn cuối tài liệu")
    i_det1 = line_of("figures/p110_det1.png")
    check(i_det1 >= 0 and "Câu 5" in L[i_det1 - 2] and "A." in L[i_det1 + 2],
          "hình 1 THAY ĐÚNG CHỖ thẻ Gemini (giữa đề Câu 5 và phương án)",
          f"dòng {i_det1}")
    check("p110_fig1.png" not in out, "thẻ Gemini trùng vùng đã bị thay, không lặp hình")
    i_det2 = line_of("figures/p110_det2.png")
    check(i_det2 >= 0 and "*[Hình:" not in out,
          "hình 2 thay dòng chú thích '[Hình: …]' của hình Gemini không cắt được",
          f"dòng {i_det2}")
    i_m118, i_det118 = line_of("p2w:page=118"), line_of("figures/p118_det1.png")
    check(i_det118 > i_m118 > 0,
          "trang không có Gemini → hình đặt TRONG khoảng dòng của trang 118",
          f"mốc {i_m118}, hình {i_det118}")
    check(i_det118 > line_of("Câu 9.") and i_det118 < len(L) - 1,
          "đặt theo vị trí dọc, không phải cuối trang/cuối tài liệu", f"dòng {i_det118}")
    dpi = Image.open(fig_dir / "p110_fig1.png").info.get("dpi", (0, 0))
    check(round(dpi[0]) == 288, "hình Gemini trang scan được cắt lại từ PDF ở 288 DPI",
          f"dpi={dpi}")
    shutil.rmtree(work, ignore_errors=True)
else:
    check(False, "có file EBOOK", EBOOK)


# ── 3. Đọc theo nửa trang ───────────────────────────────────────────
print("\n[3] Đọc theo nửa trang: tên file, bbox, DPI, dọn json")
fig_dir = Path(tempfile.mkdtemp())
W, H = 1000, 1500
FAKE = {"markdown": "**Câu 1.** Đề bài.\n\n{{FIGURE_1}}\n\n**A.** 1\n\n{{FIGURE_2}}",
        "figures": [{"id": 1, "bbox": [10, 10, 50, 50], "caption": "Hình A",
                     "anchor_text": "Câu 1. Đề bài"},
                    {"id": 2, "bbox": [10, 60, 10.5, 60.5], "caption": "Hình nhỏ",
                     "anchor_text": "A. 1"}]}
orig_call = p2w._vision_call
p2w._vision_call = lambda *a, **k: json.dumps(FAKE, ensure_ascii=False)
try:
    buf = io.BytesIO()
    Image.new("RGB", (W, H), "white").save(buf, "PNG")
    halves = p2w._png_halves(buf.getvalue())
    check(len(halves) == 2, "chia được 2 nửa")
    md_parts = []
    for k, (hb, (y0, y1)) in enumerate(zip(halves, p2w._HALF_SPANS), 1):
        md_parts.append(p2w._gemini_one_page(hb, W, 0, 2, fig_dir, scan=True, part=k,
                                             y_off=y0, y_span=y1 - y0, dpi=216))
    f101, f201 = fig_dir / "p3_fig101.png", fig_dir / "p3_fig201.png"
    check(f101.exists() and f201.exists(), "2 nửa ghi 2 file hình riêng (không đè nhau)")
    check("figures/p3_fig101.png" in md_parts[0] and "figures/p3_fig201.png" in md_parts[1],
          "thẻ ảnh mỗi nửa trỏ đúng file của nửa đó")
    with Image.open(f201) as im:
        hh = int(H * (0.5 + p2w._HALF_OVERLAP))          # chiều cao thật của nửa ảnh
        exp = (int(0.5 * W) - int(0.1 * W), int(0.5 * hh) - int(0.1 * hh))
        check(abs(im.size[0] - exp[0]) <= 2 and abs(im.size[1] - exp[1]) <= 2,
              "khung cắt theo kích thước THẬT của nửa ảnh", f"{im.size} vs {exp}")
        check(round(im.info.get("dpi", (0, 0))[0]) == 216, "PNG nửa trang ghi 216 DPI")
    j2 = json.loads((fig_dir / "p3_gemini_boxes_h2.json").read_text(encoding="utf-8"))
    y_exp = (0.5 - p2w._HALF_OVERLAP) * 100 + 10 * (0.5 + p2w._HALF_OVERLAP)
    check(abs(j2[0]["bbox"][1] - y_exp) < 0.01, "bbox nửa dưới quy về % cả trang",
          f"{j2[0]['bbox'][1]:.2f} vs {y_exp:.2f}")
    check(j2[0]["placed"] and j2[0]["anchor_text"] == "Câu 1. Đề bài",
          "json giữ placed + anchor_text")
    check(j2[1]["placed"] is False and "*[Hình: Hình nhỏ]*" in md_parts[1],
          "hình không cắt được: placed=False + để lại chú thích, không xoá trắng")
    (fig_dir / "p3_gemini_boxes.json").write_text("[]", encoding="utf-8")
    p2w._use_gemini_boxes(fig_dir, 2, "halves")
    check(not (fig_dir / "p3_gemini_boxes.json").exists()
          and (fig_dir / "p3_gemini_boxes_h1.json").exists(),
          "chọn bản nửa trang → dọn json bản cả trang, giữ json 2 nửa")
    p2w._use_gemini_boxes(fig_dir, 2, "none")
    check(not list(fig_dir.glob("p3_gemini_boxes*.json")), "keep=none → dọn hết json")
finally:
    p2w._vision_call = orig_call
    shutil.rmtree(fig_dir, ignore_errors=True)




# ── 4. Ghép 2 nửa trang + bám hình về câu ───────────────────────────
print("\n[4] Ghép 2 nửa trang, bám hình về câu, thay dòng '[Hình…]'")
TOP = "\n".join([
    "**Câu 1.** Một vật dao động điều hoà với biên độ 4 cm.",
    "**A.** 2 cm",
    "**B.** 4 cm",
    "### 2. Cảm ứng từ",
    "- Cảm ứng từ là một đại lượng vectơ, đặc trưng cho từ trường.",
    "- Kí hiệu: $\\vec{B}$.",
    "- Cảm ứng từ tại một điểm trong từ trường có:",
])
BOTTOM = "\n".join([
    "### 2. Cảm ứng từ",
    "- Cảm ứng từ là một đại lượng vec-tơ, đặc trưng cho từ trường.",   # OCR lệch nhẹ
    "- Kí hiệu: $\\vec{B}$.",
    "- Cảm ứng từ tại một điểm trong từ trường có:",
    "+ Điểm đặt: tại điểm đang xét trong từ trường đã cho.",
    "**Câu 2.** Một đoạn dây dẫn dài 20 cm đặt trong từ trường đều.",
    "**A.** 2 cm",
    "**B.** 4 cm",
])
joined = p2w._drop_overlap_lines(TOP, BOTTOM)
check(joined.count("Cảm ứng từ là một") == 0 and "Kí hiệu" not in joined
      and "### 2. Cảm ứng từ" not in joined,
      "bỏ các dòng của dải chồng lấn đã có ở nửa trên (kể cả OCR lệch nhẹ)", repr(joined[:80]))
check("+ Điểm đặt" in joined and "**Câu 2.**" in joined,
      "giữ nội dung mới của nửa dưới")
check(joined.count("**A.** 2 cm") == 1 and joined.count("**B.** 4 cm") == 1,
      "phương án ngắn trùng chữ của CÂU KHÁC không bị xoá")
check(p2w._drop_overlap_lines("Câu 1. Nội dung hoàn toàn khác nhau ở đây.",
                              BOTTOM) == BOTTOM.strip(),
      "không có dòng lặp → giữ nguyên nửa dưới")

fig_dir = Path(tempfile.mkdtemp())
try:
    (fig_dir / "p5_gemini_boxes_h1.json").write_text(json.dumps(
        [{"id": 101, "bbox": [10, 50, 40, 62], "placed": True}]), encoding="utf-8")
    (fig_dir / "p5_gemini_boxes_h2.json").write_text(json.dumps(
        [{"id": 201, "bbox": [11, 51, 40, 63], "placed": True},
         {"id": 202, "bbox": [10, 80, 40, 90], "placed": True}]), encoding="utf-8")
    bot = ("![](figures/p5_fig201.png)\n\n**Câu 3.** Đề.\n\n![](figures/p5_fig202.png)")
    out = p2w._drop_dup_half_figures(bot, fig_dir, 4)
    j2 = json.loads((fig_dir / "p5_gemini_boxes_h2.json").read_text(encoding="utf-8"))
    check("p5_fig201" not in out and "p5_fig202" in out and [e["id"] for e in j2] == [202],
          "hình Gemini cắt ở cả 2 nửa → bỏ bản nửa dưới (thẻ + json), giữ hình khác")
finally:
    shutil.rmtree(fig_dir, ignore_errors=True)

PAGE = [
    "<!-- p2w:page=3 -->",                                   # 0
    "**Câu 16.** Đặt một khung dây dẫn hình chữ nhật.",       # 1
    "Lực từ tác dụng lên cạnh",                               # 2
    "",                                                       # 3
    "**A.** AB ngược hướng",                                  # 4
    "**B.** BC cùng hướng",                                   # 5
    "**C.** CD cùng hướng",                                   # 6
    "**D.** DA ngược hướng",                                  # 7
    "",                                                       # 8
    "**Câu 17.** Hình vẽ nào dưới đây?",                      # 9
    "",                                                       # 10
    "**A.** Hình 1",                                          # 11
]
j = wf._y_fallback(PAGE, 1, len(PAGE), 0.35)
check(j == 4, "hình bên phải đề Câu 16 → đặt sau đề, TRƯỚC phương án A (không chen B/C)",
      f"j={j}")
page2 = list(PAGE)
page2[3] = "*[Hình trong trang gốc]*"
check(wf._leftover_near(page2, 4, 1, len(page2), set()) == 3,
      "có dòng '*[Hình…]*' của Gemini sát chỗ chèn → dùng đúng dòng đó")
check(wf._leftover_near(page2, 4, 1, len(page2), {3}) == -1,
      "dòng '*[Hình…]*' đã dùng thì không dùng lại")
check(wf._y_fallback(["<!-- p2w:page=1 -->", "Lý thuyết dòng 1.", "", "Lý thuyết dòng 2."],
                     1, 4, 0.9) == 4,
      "trang không có câu nào → giữ vị trí theo độ cao")


# ── 5. Toạ độ hình Gemini: tự nhận đơn vị ───────────────────────────
print("\n[5] Toạ độ hình Gemini (% / phần nghìn lẫn lộn, box_2d)")
# (Gemini trả, khung thật đo từ PDF theo %) — đề scan ad51cbaf, chuyển 01/10/2026
BBOX_CASES = [
    ([74, 99, 90, 186], [71, 10, 89, 18]),      # x theo %, y theo phần nghìn
    ([76, 198, 882, 298], [9, 20, 88, 30]),     # cả 2 trục phần nghìn
    ([655, 825, 94, 915], [60, 82, 97, 100]),   # lẫn trong CÙNG trục x
    ([732, 93, 935, 283], [73, 10, 93, 29]),
    ([79, 546, 91, 665], [79, 53, 92, 67]),
    ([10, 20, 50, 60], [10, 20, 50, 60]),       # % chuẩn → giữ nguyên
]
for raw, real in BBOX_CASES:
    got = p2w._fig_bbox_pct({"bbox": raw})
    # Sai số 10%: khung PDF gồm cả viền ảnh; mục đích là bắt SAI ĐƠN VỊ (lệch ≥ 50%)
    ok = len(got) == 4 and all(abs(g - r) <= 10 for g, r in zip(got, real))
    check(ok, f"bbox {raw} → gần khung thật {real}", f"được {[round(v, 1) for v in got]}")
check(p2w._fig_bbox_pct({"bbox": [50, 20, 10, 60]}) == [], "khung ngược (x0 > x1) → bỏ")
check(p2w._fig_bbox_pct({"bbox": [10, 20]}) == [], "thiếu toạ độ → bỏ")
check(p2w._fig_bbox_pct({"box_2d": [100, 710, 180, 890]}) == [71.0, 10.0, 89.0, 18.0],
      "box_2d [ymin, xmin, ymax, xmax] 0–1000 → [x0, y0, x1, y1] %")
check("box_2d" in p2w._SCAN_PROMPT and '"bbox"' not in p2w._SCAN_PROMPT
      and '"bbox"' in p2w._GEMINI_PROMPT,
      "prompt trang scan dùng box_2d, prompt trang digital giữ nguyên bbox %")
TOP2 = "\n".join(["**Minh họa 1.** Hình nào đúng?", "### 2. Cảm ứng từ",
                  r"- Kí hiệu: $\vec{B}$."])
BOT2 = "\n".join(["## 2. Cảm ứng từ", r"- Kí hiệu: $\vec{B}$.",
                  "+ Phương: trùng với phương nam châm thử."])
j2 = p2w._drop_overlap_lines(TOP2, BOT2)
check("Cảm ứng từ" not in j2 and "Kí hiệu" not in j2 and "+ Phương" in j2,
      "tiêu đề lặp khác cấp (### / ##) + dòng ngắn liền kề bị bỏ", repr(j2))


print(f"\n{npass} PASS, {len(fails)} FAIL")
if fails:
    for f in fails:
        print("  -", f)
    sys.exit(1)
