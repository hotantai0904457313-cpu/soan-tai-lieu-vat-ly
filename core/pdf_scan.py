"""
Nhận diện trang PDF là BẢN SCAN (ảnh chụp) hay trang chữ thật — chống watermark.

Vì sao cần module riêng: cách cũ chỉ đếm số ký tự trong lớp text của trang
(`len(page.get_text()) >= 60`). Nhiều PDF scan tải trên mạng có watermark dán
vào lớp text ở MỌI trang (vd 'Tài Liệu Ôn Thi Group\\nhttps://TaiLieuOnThi.Net\\n
TAILIEUONTHI.NET\\n' = đúng 63 ký tự) → vượt ngưỡng 60 → cả quyển sách scan bị
coi là PDF chữ, OCR bị tắt, nội dung thật mất sạch.

Cách làm ở đây:
  1. Tìm các dòng LẶP LẠI ở hầu hết các trang (watermark / header / footer /
     số trang) rồi TRỪ chúng ra trước khi đếm chữ.
  2. Đo ĐỘ PHỦ của ảnh raster trên trang: scan là ảnh phủ gần hết trang.
  3. Bắt thêm scan "ghép mảnh": nhiều ảnh vụn hoặc nhiều nét vẽ mà không có chữ.

Chỉ dùng PyMuPDF (fitz) — không numpy, không cv2. Mọi hàm ở mức module để
test được độc lập, không phụ thuộc Flask/ODL.
"""

from __future__ import annotations

import re
import unicodedata
from pathlib import Path

# ── Ngưỡng (đã hiệu chỉnh trên ~70 file thật trong data/uploads/originals) ──

BOILER_LINE_MAX = 80        # watermark/header/footer luôn là dòng NGẮN; dài hơn = nội dung
BOILER_PAGE_FRAC = 0.80     # dòng phải lặp ở ≥80% trang mẫu mới coi là boilerplate
                            # (0.60 gây báo nhầm: file có vài trang gần trùng nhau
                            #  sẽ bị xoá sạch nội dung thật)
BOILER_SAMPLE = 24          # số trang lấy mẫu để tìm boilerplate

SCAN_TEXT_MAX = 200         # ký tự THỰC (đã trừ boilerplate) ít hơn → nghi là scan
SCAN_RAW_TEXT_MAX = 800     # CHỐT AN TOÀN: text thô nhiều hơn mức này thì KHÔNG
                            # BAO GIỜ coi là scan, dù lọc boilerplate có sai
SCAN_IMG_COVER_MIN = 0.70   # độ phủ HỢP (union) của ảnh raster → scan ảnh nguyên trang
SCAN_INK_DRAW_MIN = 40      # số nét vẽ → scan ghép mảnh / scan dạng vector outline
SCAN_EMPTY_TEXT_MAX = 20    # "trang không có chữ" cho luật ghép mảnh

COVER_GRID = 32             # lưới lấy mẫu tâm ô khi đo độ phủ (1024 điểm/trang)

_DIGITAL_COVERAGE = 0.80    # tỉ lệ trang có chữ để coi cả tài liệu là digital

# Dòng chỉ gồm số / số La Mã / dấu câu → số trang, luôn là boilerplate
_PAGENUM_RE = re.compile(r'^[\s\-–—.|]*(?:\d{1,4}|[ivxlcdmIVXLCDM]{1,7})[\s\-–—.|]*$')
_WS_RE = re.compile(r'\s+')


def norm_line(s: str) -> str:
    """Chuẩn hoá 1 dòng để so khớp lặp: NFC + bỏ phân biệt hoa thường + gộp khoảng trắng."""
    s = unicodedata.normalize("NFC", s or "").strip()
    s = _WS_RE.sub(" ", s)
    return s.casefold()


def boilerplate_lines(doc, sample: int = BOILER_SAMPLE) -> set:
    """
    Tìm các dòng xuất hiện ở hầu hết các trang → watermark / header / footer / số trang.

    Chỉ xét dòng NGẮN (≤ BOILER_LINE_MAX ký tự) để một câu hoặc một đoạn nội dung
    không bao giờ bị coi là watermark, kể cả khi tài liệu có nhiều trang gần giống nhau.
    """
    n = doc.page_count
    if n <= 0:
        return set()
    # Lấy mẫu rải đều toàn tài liệu (sách 436 trang cũng chỉ đọc 24 trang)
    if n <= sample:
        idxs = list(range(n))
    else:
        step = n / float(sample)
        idxs = sorted({int(i * step) for i in range(sample)})

    counts: dict[str, int] = {}
    checked = 0
    for i in idxs:
        try:
            page = doc[i]
            txt = page.get_text("text") or ""
        except Exception:
            continue
        checked += 1
        seen = set()
        for raw in txt.splitlines():
            line = norm_line(raw)
            if not line or len(line) > BOILER_LINE_MAX:
                continue
            if line in seen:          # đếm 1 lần/trang
                continue
            seen.add(line)
            counts[line] = counts.get(line, 0) + 1

    if not checked:
        return set()
    need = max(2, int(round(checked * BOILER_PAGE_FRAC)))
    boiler = {line for line, c in counts.items() if c >= need}
    # Số trang: luôn là boilerplate dù mỗi trang một con số khác nhau
    boiler |= {line for line in counts if _PAGENUM_RE.match(line)}
    return boiler


def page_texts(page, boiler: set) -> tuple:
    """
    Trả về (raw, effective):
      raw       — toàn bộ text của trang, đã strip
      effective — đã bỏ các dòng boilerplate (watermark/header/footer/số trang)
    """
    txt = page.get_text("text") or ""
    raw = txt.strip()
    if not boiler:
        return raw, raw
    keep = []
    for line in txt.splitlines():
        nl = norm_line(line)
        if not nl or nl in boiler or _PAGENUM_RE.match(nl):
            continue
        keep.append(line.strip())
    return raw, "\n".join(keep).strip()


def _image_rects(page) -> list:
    """
    Vị trí các ảnh raster trên trang.

    Dùng page.get_bboxlog() vì page.get_image_rects() phải dò lại toàn trang cho
    từng ảnh: đo thực tế 0,14–0,64 s/trang, tức 206 s cho quyển sách 436 trang,
    trong khi get_bboxlog() gần như miễn phí và cho cùng kết quả phân loại.
    Chỉ lùi về get_image_rects khi bboxlog không thấy gì mà trang vẫn có ảnh.
    """
    rects = []
    try:
        import fitz
        for kind, bbox in page.get_bboxlog():
            if "image" in kind:
                r = fitz.Rect(bbox)
                if not r.is_empty:
                    rects.append(r)
    except Exception:
        pass
    if rects:
        return rects
    try:
        if not page.get_images(full=True):
            return []
        for info in page.get_images(full=True):
            try:
                rects += [r for r in page.get_image_rects(info[0]) if not r.is_empty]
            except Exception:
                continue
    except Exception:
        pass
    return rects


def page_image_cover(page, grid: int = COVER_GRID) -> float:
    """
    Tỉ lệ diện tích trang bị ảnh raster che (phép HỢP, không cộng dồn chồng lấn).

    Lấy mẫu tâm các ô của lưới grid×grid rồi đếm ô nằm trong ít nhất một ảnh —
    đủ chính xác cho việc phân loại và không cần numpy. Trang scan ghép từ nhiều
    dải ảnh cũng được tính đúng vì các dải cộng lại thành độ phủ lớn.
    """
    try:
        rect = page.rect
        if rect.is_empty or rect.width <= 0 or rect.height <= 0:
            return 0.0
        rects = _image_rects(page)
        if not rects:
            return 0.0
        # Lối tắt cho ca phổ biến nhất: MỘT ảnh phủ gần hết trang (scan nguyên trang)
        page_area = rect.get_area() or 1.0
        big = max(r.get_area() for r in rects) / page_area
        if big >= SCAN_IMG_COVER_MIN:
            return min(big, 1.0)
        hit = 0
        total = grid * grid
        for gy in range(grid):
            cy = rect.y0 + (gy + 0.5) * rect.height / grid
            for gx in range(grid):
                cx = rect.x0 + (gx + 0.5) * rect.width / grid
                for r in rects:
                    if r.x0 <= cx <= r.x1 and r.y0 <= cy <= r.y1:
                        hit += 1
                        break
        return hit / float(total)
    except Exception:
        return 0.0


def page_is_scan(page, boiler: set) -> bool:
    """
    Trang này là ảnh chụp/scan (cần đọc bằng Vision) hay trang chữ thật?

    Thứ tự xét đặt chốt an toàn lên trước để không bao giờ gắn nhãn scan cho
    một trang chữ thật, và để thoát sớm cho rẻ.
    """
    try:
        raw, eff = page_texts(page, boiler)
    except Exception:
        return False

    # 1. Có chữ thật đáng kể → KHÔNG phải scan. `raw` là chốt phòng trường hợp
    #    bộ lọc boilerplate cắt oan nội dung của tài liệu nhiều trang giống nhau.
    if len(eff) >= SCAN_TEXT_MAX or len(raw) >= SCAN_RAW_TEXT_MAX:
        return False

    # 2. Ảnh phủ gần hết trang → scan ảnh nguyên trang (ca phổ biến nhất)
    if page_image_cover(page) >= SCAN_IMG_COVER_MIN:
        return True

    # 3. Scan ghép mảnh: hầu như không có chữ nhưng trang đầy ảnh vụn hoặc nét vẽ.
    #    Đòi `eff` gần như rỗng để trang trắng thật (không ảnh, không nét) không bị tính.
    if len(eff) <= SCAN_EMPTY_TEXT_MAX:
        try:
            if page.get_images(full=True):
                return True
        except Exception:
            pass
        try:
            if len(page.get_drawings()) >= SCAN_INK_DRAW_MIN:
                return True
        except Exception:
            pass
    return False


def analyze_pdf(pdf_path, page_indices: list | None = None,
                sample: int = BOILER_SAMPLE) -> dict:
    """
    Phân tích 1 PDF: trang nào là scan, tài liệu có phải digital không.

    page_indices = None → xét MỌI trang (dùng khi cần định tuyến từng trang).
    Trả về dict giữ nguyên các khoá mà detect_text_layer() cũ đã trả, cộng thêm
    scan_pages / boilerplate / is_scan_doc.
    """
    try:
        import fitz  # PyMuPDF
    except Exception:
        # Không có fitz → không dám khẳng định → coi như scan (an toàn: vẫn OCR)
        return {"pages": 0, "pages_with_text": 0, "coverage": 0.0,
                "is_digital": False, "scan_pages": [], "boilerplate": [],
                "is_scan_doc": False, "note": "no_fitz"}

    try:
        doc = fitz.open(str(pdf_path))
    except Exception as e:
        return {"pages": 0, "pages_with_text": 0, "coverage": 0.0,
                "is_digital": False, "scan_pages": [], "boilerplate": [],
                "is_scan_doc": False, "note": f"open_failed: {str(e)[:80]}"}

    try:
        n = doc.page_count
        boiler = boilerplate_lines(doc, sample=sample)
        idxs = list(range(n)) if page_indices is None else [
            i for i in page_indices if 0 <= i < n]

        scan_pages = []
        for i in idxs:
            try:
                if page_is_scan(doc[i], boiler):
                    scan_pages.append(i)
            except Exception:
                continue

        checked = len(idxs)
        with_text = checked - len(scan_pages)
        coverage = (with_text / checked) if checked else 0.0
        return {
            "pages": n,
            "checked": checked,
            "pages_with_text": with_text,
            "coverage": round(coverage, 3),
            "is_digital": coverage >= _DIGITAL_COVERAGE,
            "scan_pages": scan_pages,
            "is_scan_doc": bool(checked) and len(scan_pages) == checked,
            "boilerplate": sorted(boiler)[:12],
        }
    finally:
        try:
            doc.close()
        except Exception:
            pass
