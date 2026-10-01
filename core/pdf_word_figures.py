"""
core/pdf_word_figures.py — Trích HÌNH VẼ xác định (deterministic) cho đường PDF→Word
và neo vào đúng câu trong Markdown. Dùng chung cho MỌI method (odl / hybrid / gemini).

Bối cảnh (10/09/2026): đường lai/Gemini chỉ có hình khi AI tự khai báo bbox → sót
gần hết (Đề 4: 0/6 hình). Đường ODL chỉ lấy ảnh nhúng theo toạ độ Y ước lượng,
không lấy hình vector; ở đường lai, ảnh ODL nằm trong seg_X_odl/ nên đường dẫn
tương đối gãy → Pandoc lặng lẽ bỏ. Module này:

  1. Ảnh NHÚNG (raster) qua page.get_image_rects — bỏ watermark/nền (>60% trang),
     icon (<30pt), ảnh trong bảng, vùng header/footer.
  2. Hình VECTOR qua core.pdf_figures.extract_vector_figures (cùng bộ với luồng
     nhập đề — "quy tắc lấy hình vẽ trong mọi chức năng").
  3. Gộp hình chồng nhau; gộp hình CÙNG HÀNG (H.1 H.2 H.3 H.4) thành 1 ảnh; bỏ hình
     nằm trong vùng Gemini đã cắt (p{n}_gemini_boxes.json).
  4. Cắt ảnh từ trang (zoom 3, ghi DPI 216 để Pandoc/Word hiện đúng cỡ gốc).
  5. Neo vào Markdown: chọn DÒNG CHỮ cạnh hình (chữ bên hông với hình nổi phải,
     dòng ngay trên với hình giữa trang) → tìm dòng đó trong md (so khớp chuỗi chữ,
     bỏ qua công thức/định dạng) → chèn sau đoạn. Dự phòng: nhãn "Câu N" gần nhất
     phía trên; cuối cùng: cuối tài liệu.

Không bao giờ raise ra ngoài: lỗi → trả 0 và giữ nguyên md.
"""
from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path
from typing import List, Optional

try:
    import fitz  # PyMuPDF
except Exception:  # pragma: no cover
    fitz = None

MIN_PT = 30.0          # cạnh nhỏ nhất (pt) của ảnh nhúng — dưới là icon/bullet/công thức nhỏ
MAX_AREA_FRAC = 0.60   # ảnh/hình chiếm hơn → nền, watermark, trang scan → bỏ
HEADER_FRAC = 0.07     # vùng header (logo, dòng kẻ) — bỏ qua
FOOTER_FRAC = 0.93     # vùng footer (tên thầy, số trang) — bỏ qua
ZOOM = 3.0
DPI = int(72 * ZOOM)   # 216 — PNG ghi DPI để Pandoc dựng đúng kích thước gốc
ROW_GAP_PT = 60.0      # 2 hình cùng hàng cách nhau ≤ ngưỡng → gộp 1 ảnh
MIN_ANCHOR_LEN = 12    # dòng chữ ngắn hơn (nhãn trục "x", "O", "H.1") không làm mỏ neo

# ── Trang SCAN: bộ ngưỡng riêng ──────────────────────────────────────────
# Trước đây trang ít chữ bị bỏ hẳn ("Gemini lo") → hình của trang scan chỉ có
# MỘT nguồn là bbox do Gemini tự khai; Gemini không khai thì mất hình, không
# cảnh báo. Thực tế trang scan vẫn có ẢNH CON thật lấy được bằng fitz (vd sách
# 436 trang: trang 110 có 2 hình 171×112 và 253×177 pt bên cạnh ảnh nền).
SCAN_BG_COVER = 0.70      # ảnh phủ ≥ 70% trang = ảnh nền của bản scan → bỏ
SCAN_FIG_MIN_SIDE = 50.0  # pt — loại dải công thức (306×46), nhãn (43×27)
SCAN_FIG_MIN_AREA = 6000.0  # pt² — giữ 105×68 (7140), loại 88×24
SCAN_FIG_MAX_RATIO = 6.0  # quá dài/dẹt = dòng công thức, không phải hình
ZOOM_SCAN = 4.0           # crop trang scan ở 288 DPI (scan gốc thường 300 DPI)
DPI_SCAN = int(72 * ZOOM_SCAN)

_QNUM_RE = re.compile(r'^\s*(?:\*\*)?\s*(?:Câu|Bài)\s*(\d+)', re.IGNORECASE)
_ODL_IMG_RE = re.compile(r'!\[[^\]]*\]\(<?[^)<>]*?_images[/\\]imageFile\d+\.[A-Za-z]+>?\)')
_BLOCK_START_RE = re.compile(
    r'^\s*(?:\*\*)?\s*(?:Câu\s*\d|Bài\s*\d|PHẦN|Phần\s|[A-D][.)]|[a-d][.)]|\||!\[|#|-{3,})',
    re.IGNORECASE)
_WORD_RUN_RE = re.compile(r'[^\W\d_]+(?:\s+[^\W\d_]+)+')   # chuỗi ≥ 2 từ chỉ gồm chữ cái


# ── Chuẩn hoá chuỗi để so khớp ───────────────────────────────────────────

def _norm(s: str) -> str:
    s = unicodedata.normalize('NFC', s or '')
    s = s.casefold()
    s = re.sub(r'[^\w\s]+', ' ', s)     # bỏ *, $, \, {, }, dấu câu — giữ chữ/số
    s = s.replace('_', ' ')
    return re.sub(r'\s+', ' ', s).strip()


def _anchor_keys(text: str) -> List[str]:
    """Các biến thể chuỗi tìm kiếm cho 1 dòng chữ: chuỗi chữ dài nhất, rồi cắt ngắn dần."""
    runs = _WORD_RUN_RE.findall(text or '')
    key = _norm(max(runs, key=len)) if runs else ''
    if len(key) < MIN_ANCHOR_LEN:
        key = _norm(text)
    if len(key) < MIN_ANCHOR_LEN:
        return []
    keys = [key]
    for cut in (30, 18):
        if len(key) > cut:
            k = key[:cut].rsplit(' ', 1)[0] if ' ' in key[:cut] else key[:cut]
            if len(k) >= MIN_ANCHOR_LEN and k not in keys:
                keys.append(k)
    return keys


# ── Hình học ─────────────────────────────────────────────────────────────

def _inter_area(a, b) -> float:
    r = fitz.Rect(a) & fitz.Rect(b)
    return 0.0 if r.is_empty else r.width * r.height


def _area(r) -> float:
    r = fitz.Rect(r)
    return max(0.0, r.width) * max(0.0, r.height)


def _mostly_inside(r, containers, frac: float) -> bool:
    ar = _area(r)
    if ar <= 0:
        return False
    return any(_inter_area(r, c) >= frac * ar for c in containers)


def _page_lines(page) -> List[dict]:
    """Dòng chữ trên trang (bỏ header/footer), sắp theo (y, x)."""
    H = page.rect.height
    out = []
    try:
        d = page.get_text('dict')
    except Exception:
        return out
    for b in d.get('blocks', []):
        for ln in b.get('lines', []):
            t = ''.join(s.get('text', '') for s in ln.get('spans', [])).strip()
            if not t:
                continue
            r = fitz.Rect(ln['bbox'])
            if r.y1 < H * HEADER_FRAC or r.y0 > H * FOOTER_FRAC:
                continue
            out.append({'rect': r, 'text': t, 'y': (r.y0 + r.y1) / 2.0})
    out.sort(key=lambda l: (l['y'], l['rect'].x0))
    return out


def is_figure_like_table(table) -> bool:
    """find_tables nhận nhầm ĐỒ THỊ CÓ LƯỚI Ô VUÔNG là bảng (77–390 ô mà chỉ vài ô
    có số trục). Bảng thật (số liệu, Đúng-Sai, khung tiêu đề) có chữ ở phần lớn ô.
    → ≥ 12 ô và ≤ 25 % ô có chữ = đồ thị → xử lý như HÌNH."""
    try:
        rows = table.extract() or []
    except Exception:
        return False
    cells = [c for row in rows for c in row]
    if not cells:
        return False
    n_text = sum(1 for c in cells if c and str(c).strip())
    # ô "có chữ thật": ≥ 3 ký tự chữ/số — nhãn trục "Y", "−A", "O", "x" không tính
    n_wordy = sum(1 for c in cells
                  if c and sum(ch.isalnum() for ch in str(c)) >= 3)
    if len(cells) >= 12 and n_text <= 0.25 * len(cells):
        return True          # lưới ô vuông của đồ thị
    return n_wordy == 0      # khung/trục đồ thị chỉ có vài nhãn 1–2 ký tự (parabol Câu 12)


def _table_rects(page) -> tuple:
    """→ (bảng thật, vùng đồ-thị-bị-tưởng-là-bảng)."""
    tables, figure_like = [], []
    try:
        tabs = page.find_tables()
        for t in (getattr(tabs, 'tables', None) or []):
            (figure_like if is_figure_like_table(t) else tables).append(fitz.Rect(t.bbox))
    except Exception:
        pass
    return tables, figure_like


def _text_cover(rect, lines) -> float:
    """Tỉ lệ diện tích vùng bị DÒNG CHỮ (≥ 3 ký tự) phủ lên. Hình thật: nhãn trục nhỏ
    → thấp; ảnh trong suốt/rỗng đè lên đoạn văn → cao."""
    ar = _area(rect)
    if ar <= 0:
        return 1.0
    cov = 0.0
    for l in lines:
        if len(l['text']) < 3:
            continue
        cov += _inter_area(rect, l['rect'])
    return cov / ar


def _raster_transparent(page, smask_xref: int) -> bool:
    """Ảnh có mặt nạ alpha gần như trong suốt hoàn toàn (sticker ẩn) → không phải hình."""
    if not smask_xref:
        return False
    try:
        import io
        from PIL import Image as PILImage
        base = page.parent.extract_image(smask_xref)
        m = PILImage.open(io.BytesIO(base['image'])).convert('L')
        hist = m.histogram()
        visible = sum(hist[24:]) / float(m.width * m.height)
        return visible < 0.05
    except Exception:
        return False


def _raster_rects(page, table_rects, lines) -> list:
    W, H = page.rect.width, page.rect.height
    area = W * H
    rects, seen = [], set()
    try:
        images = page.get_images(full=True)
    except Exception:
        images = []
    for im in images:
        try:
            placed = page.get_image_rects(im[0])
        except Exception:
            continue
        for r in placed:
            r = fitz.Rect(r) & page.rect
            if r.is_empty or r.width < MIN_PT or r.height < MIN_PT:
                continue
            if r.width * r.height > MAX_AREA_FRAC * area:
                continue                      # nền / watermark / ảnh trang scan
            if r.y1 < H * HEADER_FRAC or r.y0 > H * FOOTER_FRAC:
                continue                      # logo header / footer
            if _mostly_inside(r, table_rects, 0.8):
                continue                      # logo trong khung tiêu đề, ảnh trong bảng
            if _text_cover(r, lines) >= 0.35:
                continue                      # ảnh rỗng/trong suốt đè lên đoạn văn
            if _raster_transparent(page, im[1] if len(im) > 1 else 0):
                continue
            key = (round(r.x0), round(r.y0), round(r.x1), round(r.y1))
            if key in seen:
                continue
            seen.add(key)
            rects.append(r)
    return rects


def _scan_raster_rects(page) -> list:
    """Ảnh con trên TRANG SCAN (bỏ ảnh nền phủ gần hết trang).

    Khác _raster_rects: KHÔNG áp ngưỡng text-cover và không loại ảnh nằm trong
    "bảng" — trang scan không có lớp text nên hai bộ lọc đó vô nghĩa hoặc gây
    hại (mọi bbox đều bị chữ OCR phủ, find_tables thì bắt bừa).
    """
    W, H = page.rect.width, page.rect.height
    area = W * H or 1.0
    rects, seen = [], set()
    try:
        images = page.get_images(full=True)
    except Exception:
        images = []
    for im in images:
        try:
            placed = page.get_image_rects(im[0])
        except Exception:
            continue
        for r in placed:
            r = fitz.Rect(r) & page.rect
            if r.is_empty:
                continue
            if r.width * r.height >= SCAN_BG_COVER * area:
                continue                      # ảnh nền của bản scan
            if min(r.width, r.height) < SCAN_FIG_MIN_SIDE:
                continue                      # nhãn, mẩu vụn
            if r.width * r.height < SCAN_FIG_MIN_AREA:
                continue
            long_side, short_side = max(r.width, r.height), min(r.width, r.height)
            if short_side > 0 and long_side / short_side > SCAN_FIG_MAX_RATIO:
                continue                      # dải dài dẹt = dòng công thức
            if r.y1 < H * HEADER_FRAC or r.y0 > H * FOOTER_FRAC:
                continue
            key = (round(r.x0), round(r.y0), round(r.x1), round(r.y1))
            if key in seen:
                continue
            seen.add(key)
            rects.append(r)
    return rects


def _vector_rects(page, table_rects, lines) -> list:
    try:
        from core.pdf_figures import extract_vector_figures
    except Exception:
        return []
    W, H = page.rect.width, page.rect.height
    line_rects = [tuple(l['rect']) for l in lines]
    out = []
    try:
        figs = extract_vector_figures(page, table_rects=table_rects, text_lines=line_rects)
    except Exception:
        figs = []
    for f in figs or []:
        try:
            r = fitz.Rect(f['rect']) & page.rect
        except Exception:
            continue
        if r.is_empty or r.width < MIN_PT or r.height < MIN_PT:
            continue
        if r.y1 < H * HEADER_FRAC or r.y0 > H * FOOTER_FRAC:
            continue
        if r.width * r.height > MAX_AREA_FRAC * W * H:
            continue
        if _text_cover(r, lines) >= 0.5:
            continue                          # khung kẻ quanh đoạn văn, không phải hình
        out.append(r)
    return out


def _merge_overlaps(rects: list) -> list:
    """Gộp các hình chồng nhau (ảnh nhúng có khung/trục vector vẽ đè)."""
    rects = [fitz.Rect(r) for r in rects]
    changed = True
    while changed and len(rects) > 1:
        changed = False
        for i in range(len(rects)):
            for j in range(i + 1, len(rects)):
                a, b = rects[i], rects[j]
                inter = _inter_area(a, b)
                if inter <= 0:
                    continue
                if inter >= 0.3 * min(_area(a), _area(b)):
                    rects[i] = a | b
                    del rects[j]
                    changed = True
                    break
            if changed:
                break
    return rects


def _long_text_between(left, right, lines) -> bool:
    """Có dòng chữ dài nằm GIỮA 2 hình (theo chiều ngang) và cùng dải Y không?"""
    y0, y1 = min(left.y0, right.y0), max(left.y1, right.y1)
    for l in lines:
        if len(l['text']) < 20:
            continue
        lr = l['rect']
        if lr.y1 < y0 or lr.y0 > y1:
            continue
        if lr.x0 >= left.x1 - 2 and lr.x1 <= right.x0 + 2:
            return True
    return False


def _group_rows(rects: list, lines) -> list:
    """Gộp hình CÙNG HÀNG (dải Y chồng ≥ 50 %, cách nhau ≤ ROW_GAP_PT, không có chữ
    dài xen giữa) thành 1 ảnh — giữ bố cục H.1 H.2 H.3 H.4 trên 1 dòng."""
    rects = sorted((fitz.Rect(r) for r in rects), key=lambda r: (r.y0, r.x0))
    changed = True
    while changed and len(rects) > 1:
        changed = False
        for i in range(len(rects)):
            for j in range(i + 1, len(rects)):
                a, b = rects[i], rects[j]
                vo = min(a.y1, b.y1) - max(a.y0, b.y0)
                if vo < 0.5 * min(a.height, b.height):
                    continue
                left, right = (a, b) if a.x0 <= b.x0 else (b, a)
                gap = right.x0 - left.x1
                if gap > ROW_GAP_PT:
                    continue
                if _long_text_between(left, right, lines):
                    continue
                rects[i] = a | b
                del rects[j]
                changed = True
                break
            if changed:
                break
    return sorted(rects, key=lambda r: (r.y0, r.x0))


def _gemini_boxes(fig_dir: Path, page_no: int, page) -> list:
    """Mọi hình Gemini Vision khai cho trang này (json do _gemini_one_page ghi:
    p{N}_gemini_boxes.json, hoặc _h1/_h2 khi trang đọc theo nửa) → list dict
    {id, rect (Rect|None), placed, caption, anchor}. Json kiểu cũ chỉ chứa hình
    ĐÃ chèn và không có khoá 'placed' → coi là placed."""
    W, H = page.rect.width, page.rect.height
    out = []
    for p in sorted(fig_dir.glob(f"p{page_no}_gemini_boxes*.json")):
        try:
            boxes = json.loads(p.read_text(encoding='utf-8'))
        except Exception:
            continue
        for item in boxes:
            try:
                if isinstance(item, dict):
                    fid, b = item.get('id'), item.get('bbox')
                    placed = bool(item.get('placed', True))
                    cap = (item.get('caption') or '').strip()
                    anc = (item.get('anchor_text') or '').strip()
                else:
                    fid, b, placed, cap, anc = None, item, True, '', ''
                rect = None
                if b and len(b) == 4:
                    rect = fitz.Rect(b[0] / 100 * W, b[1] / 100 * H,
                                     b[2] / 100 * W, b[3] / 100 * H)
                    if rect.is_empty:
                        rect = None
                out.append({'id': fid, 'rect': rect, 'placed': placed,
                            'caption': cap, 'anchor': anc})
            except Exception:
                pass
    return out


def _gemini_rects(fig_dir: Path, page_no: int, page) -> list:
    """Vùng Gemini Vision ĐÃ cắt hình cho trang này → list (id, Rect). Hình Gemini
    khai mà không cắt được KHÔNG tính — nó không có trong tài liệu nên không được
    phép thay thế (bỏ) hình xác định."""
    return [(b['id'], b['rect']) for b in _gemini_boxes(fig_dir, page_no, page)
            if b['placed'] and b['rect'] is not None]


def _recrop_gemini(page, fig_dir: Path, page_no: int, boxes: list) -> int:
    """Trang SCAN: cắt lại hình Gemini đã chèn TỪ TRANG PDF ở zoom 4 (288 DPI,
    khớp scan gốc ~300 DPI), đè lên bản cắt từ ảnh render 216 DPI. Tên file giữ
    nguyên nên thẻ ảnh trong md không đổi. Trả số hình đã cắt lại."""
    n = 0
    for b in boxes:
        if not b['placed'] or b['rect'] is None or b['id'] is None:
            continue
        png = fig_dir / f"p{page_no}_fig{b['id']}.png"
        if png.exists() and _render(page, b['rect'], png, zoom=ZOOM_SCAN):
            n += 1
    return n


def _dedup_with_gemini(rects: list, gem: list, tables: list) -> tuple:
    """Hình xác định (cắt chính xác) THẮNG khi chồng lấn với hình Gemini (bbox AI
    thường lỏng/lệch) → trả (rects giữ, id Gemini cần bỏ, id Gemini được bảo vệ).
    - Khung Gemini bao trọn ≥ 2 hình xác định → đó là các mảnh của 1 sơ đồ lớn →
      giữ Gemini, bỏ các mảnh.
    - Khung Gemini nằm trên BẢNG SỐ LIỆU thật (Gemini cắt bảng thành ảnh theo quy
      tắc 7) → bảo vệ, không bao giờ bỏ (bộ trích xác định không cắt bảng)."""
    keep, drop_ids, protected = list(rects), [], []
    for fid, g in gem:
        if any(_inter_area(g, t) >= 0.3 * _area(g) for t in tables):
            if fid is not None:
                protected.append(fid)
            continue
        contained = [r for r in keep if _inter_area(r, g) >= 0.8 * _area(r)]
        if len(contained) >= 2:
            keep = [r for r in keep if r not in contained]
            continue
        hit = [r for r in keep if _inter_area(r, g) >= 0.15 * min(_area(r), _area(g))]
        if hit and fid is not None:
            drop_ids.append(fid)
    return keep, drop_ids, protected


# ── Chọn mỏ neo ──────────────────────────────────────────────────────────

def _choose_anchor(rect, lines) -> Optional[dict]:
    """Dòng chữ làm mỏ neo cho hình:
    (1) dòng BÊN HÔNG (tâm dòng trong dải Y của hình, nằm ngoài hình) — hình nổi
        phải/trái; ưu tiên dòng bắt đầu "Câu N", không thì dòng trên cùng;
    (2) dòng gần nhất PHÍA TRÊN hình — hình đặt giữa trang dưới câu dẫn;
    (3) dòng gần nhất phía dưới."""
    y0, y1 = rect.y0, rect.y1
    cands = [l for l in lines if len(l['text']) >= MIN_ANCHOR_LEN]
    side = []
    for l in cands:
        lr = l['rect']
        if y0 - 2 <= l['y'] <= y1 + 2:
            ov = max(0.0, min(lr.x1, rect.x1) - max(lr.x0, rect.x0))
            if ov < 0.5 * lr.width:
                side.append(l)
    if side:
        side.sort(key=lambda l: l['y'])
        for l in side:
            if _QNUM_RE.match(l['text']):
                return l
        return side[0]
    above = [l for l in cands if l['y'] < y0]
    if above:
        return max(above, key=lambda l: l['y'])
    below = [l for l in cands if l['y'] > y1]
    if below:
        return min(below, key=lambda l: l['y'])
    return None


def _qnum_above(rect, lines) -> Optional[str]:
    best = None
    for l in lines:
        if l['y'] <= rect.y1:
            m = _QNUM_RE.match(l['text'])
            if m:
                best = m.group(1)
    return best


# ── Chèn vào Markdown ────────────────────────────────────────────────────

def _para_end(md_lines: List[str], i: int) -> int:
    """Chỉ số dòng ngay SAU đoạn chứa dòng i (dừng ở dòng trống hoặc dòng mở khối
    mới: Câu/Bài/PHẦN, phương án A./a), bảng, ảnh, heading)."""
    j = i + 1
    while j < len(md_lines):
        s = md_lines[j]
        if not s.strip() or _BLOCK_START_RE.match(s):
            break
        j += 1
    return j


def _find_line(md_norm: List[str], keys: List[str], cursor: int) -> int:
    n = len(md_norm)
    for k in keys:
        order = list(range(cursor, n)) + list(range(0, cursor))
        for i in order:
            if k and k in md_norm[i]:
                return i
    return -1


def _find_qnum(md_lines: List[str], num: str, cursor: int) -> int:
    pat = re.compile(r'^\s*(?:\*\*)?\s*(?:Câu|Bài)\s*' + re.escape(num) + r'(?!\d)', re.IGNORECASE)
    n = len(md_lines)
    for i in list(range(cursor, n)) + list(range(0, cursor)):
        if pat.match(md_lines[i]):
            return i
    return -1


_PAGE_MARK_RE = re.compile(r'^\s*<!--\s*p2w:page=(\d+)\s*-->\s*$')


def _page_markers(md_lines: List[str]) -> list:
    """Mốc <!-- p2w:page=N --> (đường lai/Gemini ghi trước nội dung mỗi trang
    hoặc mỗi DẢI trang ODL) → list (chỉ số dòng, N) theo thứ tự tài liệu."""
    out = []
    for i, s in enumerate(md_lines):
        m = _PAGE_MARK_RE.match(s)
        if m:
            out.append((i, int(m.group(1))))
    return out


def _range_for(markers: list, page_no: int, n_lines: int):
    """Khoảng dòng [lo, hi) chứa nội dung trang page_no: từ mốc gần nhất có số
    trang ≤ page_no (dải ODL nhiều trang chỉ có 1 mốc ở trang đầu) tới mốc kế.
    Không có mốc → None (đường ODL thuần: giữ cách tìm toàn tài liệu như cũ)."""
    best = None
    for k, (li, pn) in enumerate(markers):
        if pn <= page_no:
            best = k
    if best is None:
        return None
    lo = markers[best][0] + 1
    hi = markers[best + 1][0] if best + 1 < len(markers) else n_lines
    return lo, hi


def _scan_order(lo: int, hi: int, cursor: int) -> list:
    """Duyệt [lo, hi) bắt đầu từ cursor (vị trí hình trước) rồi vòng lại — như
    _find_line: số câu reset mỗi PHẦN nên "Câu 2" có thể xuất hiện 2 lần trong
    cùng một trang, lần nằm SAU hình trước mới là lần đúng."""
    c = min(max(cursor, lo), hi)
    return list(range(c, hi)) + list(range(lo, c))


def _find_line_in(md_norm: List[str], keys: List[str], lo: int, hi: int,
                  cursor: int = 0) -> int:
    for k in keys:
        if not k:
            continue
        for i in _scan_order(lo, hi, cursor):
            if k in md_norm[i]:
                return i
    return -1


def _find_qnum_in(md_lines: List[str], num: str, lo: int, hi: int,
                  cursor: int = 0) -> int:
    pat = re.compile(r'^\s*(?:\*\*)?\s*(?:Câu|Bài)\s*' + re.escape(num) + r'(?!\d)',
                     re.IGNORECASE)
    for i in _scan_order(lo, hi, cursor):
        if pat.match(md_lines[i]):
            return i
    return -1


def _y_fallback(md_lines: List[str], lo: int, hi: int, yfrac: float) -> int:
    """Không neo được bằng chữ → chèn theo vị trí dọc của hình TRONG TRANG: chia
    nội dung trang thành các khối (đoạn / câu / phương án), hình ở độ cao yfrac
    thì đặt trước khối thứ round(yfrac × số khối). Có thể lệch vài dòng nhưng
    luôn đúng trang, đúng vùng — không bao giờ dồn xuống cuối tài liệu."""
    blocks = []
    for i in range(lo, hi):
        s = md_lines[i].strip()
        if not s or s == '---' or s.startswith('<!--'):
            continue
        prev = md_lines[i - 1].strip() if i > lo else ''
        if (i == lo or not prev or prev == '---' or prev.startswith('<!--')
                or _BLOCK_START_RE.match(md_lines[i])):
            blocks.append(i)
    if not blocks:
        return lo
    k = int(round(max(0.0, min(1.0, yfrac)) * len(blocks)))
    pos = _para_end(md_lines, blocks[-1]) if k >= len(blocks) else blocks[k]
    # Bám về CÂU: hình thuộc câu gần nhất bắt đầu phía trên nó (hình thường nằm
    # bên phải/bên dưới đề) → đặt sau phần đề, trước phương án A — không chen
    # giữa B và C như khi chỉ chia đều theo độ cao.
    for i in range(min(pos, hi) - 1, lo - 1, -1):
        if _QBLOCK_RE.match(md_lines[i]):
            return _stem_end(md_lines, i, hi)
    return pos


# Đầu một câu/ví dụ (để bám hình vào) và dòng mở đầu phương án / ý a) b)
_QBLOCK_RE = re.compile(r'^\s*(?:\*\*)?\s*(?:Câu|Bài|Ví\s*dụ|Minh\s*họa)\s*\d+',
                        re.IGNORECASE)
_OPTION_RE = re.compile(r'^\s*(?:\*\*)?\s*(?:[A-D]|[a-d])\s*(?:\*\*)?\s*[.)]')
_LEFTOVER_RE = re.compile(r'^\s*\*\[Hình(?::[^\]]*| trong trang gốc)\]\*\s*$')


def _stem_end(md_lines: List[str], q: int, hi: int) -> int:
    """Vị trí ngay trước phương án/ý đầu tiên của câu bắt đầu ở dòng q (hoặc
    trước câu kế / cuối khoảng nếu câu không có phương án)."""
    i = q + 1
    while i < hi:
        s = md_lines[i]
        if _OPTION_RE.match(s) or _QBLOCK_RE.match(s) or s.strip() == '---' \
                or s.lstrip().startswith('<!--'):
            break
        i += 1
    return i


def _leftover_near(md_lines: List[str], j: int, lo: int, hi: int,
                   used: set, radius: int = 6) -> int:
    """Dòng chú thích "*[Hình…]*" (hình Gemini khai mà không cắt được) gần vị trí
    chèn j nhất, trong khoảng dòng của trang → hình xác định thay vào đó thay vì
    để thừa dòng chữ cạnh hình. Không có → -1."""
    best, dist = -1, radius + 1
    for i in range(max(lo, j - radius), min(hi, j + radius + 1)):
        if i not in used and _LEFTOVER_RE.match(md_lines[i]) and abs(i - j) < dist:
            best, dist = i, abs(i - j)
    return best


def _render(page, rect, out_path: Path, zoom: float = ZOOM) -> bool:
    try:
        clip = fitz.Rect(rect.x0 - 2, rect.y0 - 2, rect.x1 + 2, rect.y1 + 2) & page.rect
        pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), clip=clip, alpha=False)
        try:
            dpi = int(72 * zoom)
            pix.set_dpi(dpi, dpi)
        except Exception:
            pass
        pix.save(str(out_path))
        return True
    except Exception:
        return False


def strip_odl_images(md_text: str) -> str:
    """Bỏ thẻ ảnh do ODL tự xuất (<stem>_images/imageFileN.png) — trên trang digital
    bộ trích xác định thay thế trọn (ODL chỉ lấy được một phần, đặt sai chỗ, và ở
    đường lai đường dẫn tương đối gãy)."""
    return _ODL_IMG_RE.sub('', md_text or '')


# ── API chính ────────────────────────────────────────────────────────────

def collect_page_figures(page, fig_dir: Path, page_no: int, lines=None,
                         scan: bool = False) -> tuple:
    """Trích hình của 1 trang → (figs, drop_gemini_ids, protected): figs là list
    {'rect', 'png' (Path), 'anchor' (dict|None), 'qnum' (str|None)} theo thứ tự
    (y, x); drop_gemini_ids là id hình Gemini cùng trang bị hình xác định thay thế.

    scan=True → trang là BẢN SCAN: lấy ảnh con bằng _scan_raster_rects (bỏ ảnh
    nền), crop ở DPI cao hơn, và không dùng find_tables (không có lớp text nên
    nó bắt bừa). Trước đây trang scan bị bỏ hẳn → mất hình.
    """
    if fitz is None:
        return [], [], []
    try:
        txt = page.get_text('text') or ''
    except Exception:
        txt = ''
    lines = lines if lines is not None else _page_lines(page)
    zoom = ZOOM

    if scan:
        zoom = ZOOM_SCAN
        tabs = []
        rects = _scan_raster_rects(page)
    else:
        # Trang digital không có chữ → không phải việc của bộ trích xác định
        if len(txt.strip()) < 50:
            return [], [], []
        tabs, grid_figs = _table_rects(page)
        H = page.rect.height
        grid_figs = [r for r in grid_figs
                     if not (r.y1 < H * HEADER_FRAC or r.y0 > H * FOOTER_FRAC)
                     and _text_cover(r, lines) < 0.35]
        rects = (_raster_rects(page, tabs, lines) + _vector_rects(page, tabs, lines)
                 + grid_figs)
    if not rects:
        return [], [], []
    rects = _merge_overlaps(rects)
    rects = _group_rows(rects, lines)
    drop_ids: list = []
    protected: list = []
    gem = _gemini_rects(fig_dir, page_no, page)
    if gem:
        rects, drop_ids, protected = _dedup_with_gemini(rects, gem, tabs)
    out = []
    for k, r in enumerate(rects, 1):
        png = fig_dir / f"p{page_no}_det{k}.png"
        if not _render(page, r, png, zoom=zoom):
            continue
        out.append({'rect': r, 'png': png,
                    'anchor': _choose_anchor(r, lines),
                    'qnum': _qnum_above(r, lines)})
    return out, drop_ids, protected


def _overlapping(rect, boxes: list) -> list:
    """Hình Gemini có bbox chồng lấn ≥ 15% (theo hình nhỏ hơn) với rect, chồng
    nhiều nhất trước."""
    hits = []
    for b in boxes:
        g = b['rect']
        if g is None:
            continue
        ov = _inter_area(rect, g)
        if ov >= 0.15 * min(_area(rect), _area(g)):
            hits.append((ov, b))
    hits.sort(key=lambda t: -t[0])
    return [b for _, b in hits]


def _place_scan_inline(f, tag: str, boxes: list, gem_lines: dict, used_lines: set,
                       md_lines: List[str], md_norm: List[str], lo: int, hi: int) -> bool:
    """Trang scan: đặt hình xác định VÀO ĐÚNG DÒNG của hình Gemini cùng vùng —
    thẻ ảnh Gemini đã chèn, hoặc dòng chú thích "*[Hình: …]*" khi Gemini khai
    hình mà không cắt được. Gemini đọc cả trang nên chỗ nó đặt hình là chỗ đúng
    nhất trong bài. Trả True nếu đã đặt."""
    for b in _overlapping(f['rect'], boxes):
        li = -1
        if b['placed'] and b['id'] is not None:
            try:
                li = gem_lines.get(int(b['id']), -1)
            except (TypeError, ValueError):
                li = -1
        if li < 0 and not b['placed']:
            want = f"*[Hình: {b['caption']}]*" if b['caption'] else "*[Hình trong trang gốc]*"
            for i in range(lo, hi):
                if i not in used_lines and md_lines[i].strip() == want:
                    li = i
                    break
        if li >= 0 and li not in used_lines:
            md_lines[li] = tag
            md_norm[li] = ''
            used_lines.add(li)
            return True
    return False


def attach_figures(pdf_path, work_dir, md_path, page_range: str | None = None,
                   progress_cb=None, scan_pages=None) -> int:
    """Trích hình xác định rồi chèn vào md (ghi đè tại chỗ). Trả về số hình đã
    chèn. Không raise.

    scan_pages: tập chỉ số trang (từ 0) là BẢN SCAN. Trang scan:
      - hình Gemini đã chèn được cắt lại từ trang PDF ở 288 DPI;
      - lấy ảnh con bằng _scan_raster_rects (bỏ ảnh nền);
      - neo hình KHÔNG dựa lớp text (thường không có/toàn watermark) mà theo thứ
        tự: thay đúng chỗ thẻ Gemini cùng vùng → dòng chú thích "[Hình: …]" của
        hình Gemini không cắt được → anchor_text Gemini khai → chữ OCR của trang
        → vị trí dọc trong trang. Mọi phép tìm giới hạn trong khoảng dòng của
        trang (mốc <!-- p2w:page=N -->) nên hình không bao giờ nhảy trang."""
    scan_set = set(scan_pages or ())
    if fitz is None:
        return 0
    pdf_path, work_dir, md_path = Path(pdf_path), Path(work_dir), Path(md_path)
    try:
        doc = fitz.open(str(pdf_path))
    except Exception:
        return 0
    try:
        n = doc.page_count
        if page_range:
            try:
                from core.pdf_to_word import _parse_page_range
                idxs = _parse_page_range(page_range, n)
            except Exception:
                idxs = list(range(n))
        else:
            idxs = list(range(n))
        idxs = [i for i in idxs if 0 <= i < n]

        fig_dir = work_dir / 'figures'
        fig_dir.mkdir(parents=True, exist_ok=True)

        try:
            md_text = md_path.read_text(encoding='utf-8', errors='replace')
        except Exception:
            return 0
        md_text = strip_odl_images(md_text)
        md_lines = md_text.split('\n')
        md_norm = [_norm(s) for s in md_lines]
        markers = _page_markers(md_lines)
        used_lines: set = set()

        inserts: dict = {}       # vị trí dòng → list thẻ ảnh chèn TRƯỚC dòng đó
        tail: List[str] = []     # không neo được → cuối tài liệu
        cursor = 0
        total = 0

        for idx in idxs:
            try:
                page = doc[idx]
            except Exception:
                continue
            lines = _page_lines(page)
            scan = idx in scan_set
            rng = _range_for(markers, idx + 1, len(md_lines))
            lo, hi = rng if rng else (0, len(md_lines))
            boxes = _gemini_boxes(fig_dir, idx + 1, page) if scan else []
            if scan and boxes:
                _recrop_gemini(page, fig_dir, idx + 1, boxes)
            figs, drop_ids, protected = collect_page_figures(page, fig_dir, idx + 1, lines,
                                                             scan=scan)
            gem_prefix = f"figures/p{idx + 1}_fig"

            def _blank_gemini(li: int) -> None:
                """Xoá nội dung 1 dòng thẻ Gemini (giữ chỉ số dòng để các vị trí
                chèn đã tính không lệch)."""
                md_lines[li] = ''
                md_norm[li] = ''

            def _gemini_id(s: str):
                m = re.search(re.escape(gem_prefix) + r'(\d+)\.png', s)
                return int(m.group(1)) if m else None

            # Vị trí thẻ Gemini của trang (trước khi bỏ) — trang scan dùng làm chỗ
            # đặt hình xác định cùng vùng: Gemini đã đặt thẻ đúng chỗ trong bài.
            gem_lines: dict = {}
            if scan:
                for li in range(lo, hi):
                    s = md_lines[li]
                    if s.lstrip().startswith('![') and gem_prefix in s:
                        gid = _gemini_id(s)
                        if gid is not None:
                            gem_lines.setdefault(gid, li)

            # (a) Thẻ Gemini có bbox chồng lấn hình xác định → bỏ
            for li, s in enumerate(md_lines):
                if s.lstrip().startswith('![') and gem_prefix in s:
                    gid = _gemini_id(s)
                    if gid is not None and gid in drop_ids:
                        _blank_gemini(li)
            for f in figs:
                rel = f"figures/{f['png'].name}"
                tag = f"![]({rel})"
                pos = -1
                if scan and _place_scan_inline(f, tag, boxes, gem_lines, used_lines,
                                               md_lines, md_norm, lo, hi):
                    total += 1
                    continue
                a = f['anchor']
                keys = _anchor_keys(a['text']) if a is not None else []
                if scan:
                    # anchor_text Gemini khai cho hình cùng vùng — trang scan không
                    # có lớp text đáng tin để chọn dòng neo
                    for b in _overlapping(f['rect'], boxes):
                        if b['anchor']:
                            keys = _anchor_keys(b['anchor']) + keys
                            break
                if rng and keys:
                    pos = _find_line_in(md_norm, keys, lo, hi, cursor)
                if pos < 0 and keys and not (scan and rng):
                    pos = _find_line(md_norm, keys, cursor)
                if pos < 0 and f['qnum']:
                    if rng:
                        pos = _find_qnum_in(md_lines, f['qnum'], lo, hi, cursor)
                    if pos < 0 and not (scan and rng):
                        pos = _find_qnum(md_lines, f['qnum'], cursor)
                if pos < 0 and rng:
                    H = page.rect.height or 1.0
                    yfrac = ((f['rect'].y0 + f['rect'].y1) / 2) / H
                    j = _y_fallback(md_lines, lo, hi, yfrac)
                    lv = _leftover_near(md_lines, j, lo, hi, used_lines) if scan else -1
                    if lv >= 0:
                        md_lines[lv] = tag
                        md_norm[lv] = ''
                        used_lines.add(lv)
                    else:
                        inserts.setdefault(j, []).append(tag)
                    total += 1
                    continue
                if pos < 0:
                    tail.append(f"![Hình trang {idx + 1}]({rel})")
                    total += 1
                    continue
                j = _para_end(md_lines, pos)
                lv = _leftover_near(md_lines, j, lo, hi, used_lines) if scan else -1
                if lv >= 0:
                    md_lines[lv] = tag
                    md_norm[lv] = ''
                    used_lines.add(lv)
                else:
                    inserts.setdefault(j, []).append(tag)
                cursor = pos
                total += 1
                # (b) Cùng CÂU đã có hình xác định → thẻ Gemini cùng trang trong
                # khối câu đó là cùng một hình (bbox AI lệch nên không chồng lấn)
                # → bỏ, trừ thẻ được bảo vệ (bảng số liệu). KHÔNG áp cho trang
                # scan: ảnh con chỉ là một phần hình của trang (phần còn lại nằm
                # trong ảnh nền), thẻ Gemini khác vùng là hình KHÁC.
                if scan:
                    continue
                qend = pos + 1
                while qend < len(md_lines) and not _QNUM_RE.match(md_lines[qend]) \
                        and not re.match(r'^\s*(?:\*\*)?\s*(?:PHẦN|Phần)\s', md_lines[qend]):
                    qend += 1
                for li in range(pos, qend):
                    s = md_lines[li]
                    if s.lstrip().startswith('![') and gem_prefix in s:
                        gid = _gemini_id(s)
                        if gid is None or gid not in protected:
                            _blank_gemini(li)
            if progress_cb and figs:
                try:
                    progress_cb('img', f"Trang {idx + 1}: {len(figs)} hình")
                except Exception:
                    pass

        if total == 0:
            if md_text != md_path.read_text(encoding='utf-8', errors='replace'):
                md_path.write_text(md_text, encoding='utf-8')
            return 0

        out: List[str] = []
        for j, line in enumerate(md_lines):
            if j in inserts:
                out.append('')
                for t in inserts[j]:
                    out.append(t)
                    out.append('')
            out.append(line)
        if len(md_lines) in inserts:
            out.append('')
            for t in inserts[len(md_lines)]:
                out.append(t)
                out.append('')
        if tail:
            out.append('')
            for t in tail:
                out.append(t)
                out.append('')
        md_path.write_text('\n'.join(out), encoding='utf-8')
        return total
    except Exception:
        return 0
    finally:
        try:
            doc.close()
        except Exception:
            pass
