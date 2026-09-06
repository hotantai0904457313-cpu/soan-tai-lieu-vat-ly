"""Phát hiện & cắt HÌNH VẼ VECTƠ (đồ thị, sơ đồ) trong PDF ra ảnh PNG.

Bối cảnh: đề thi soạn bằng Word/LaTeX vẽ đồ thị bằng **đường vẽ vectơ** (paths),
KHÔNG phải ảnh bitmap nhúng → `page.get_images()` trả rỗng → hình bị mất khi
import. Module này gom các nét vẽ thành cụm (cluster), lọc bỏ khung trang /
gạch chân / bảng / đoạn văn đóng khung, rồi render từng cụm còn lại ra PNG.

Chỉ phụ thuộc: PyMuPDF (fitz) + stdlib. Không sửa file nào khác.
"""

from __future__ import annotations

from typing import Any, Iterable, List, Optional, Sequence, Tuple

try:  # fitz là bắt buộc, nhưng không được để lỗi import làm chết app
    import fitz  # type: ignore
except Exception:  # pragma: no cover
    fitz = None  # type: ignore


__all__ = ['extract_vector_figures', 'figure_anchor_line']

Box = Tuple[float, float, float, float]


# ── Tiện ích hình học (dùng tuple thay fitz.Rect để tránh bẫy "empty rect") ──
# LƯU Ý: fitz.Rect.intersects() trả False với rect suy biến (đường thẳng ngang
# có height = 0) → mọi phép giao/hợp ở đây tự cài bằng tuple số thực.

def _as_box(obj: Any) -> Optional[Box]:
    """Chuẩn hoá về (x0, y0, x1, y1). Nhận fitz.Rect, tuple/list 4 số,
    hoặc dict (khoá x0/y0/x1/y1, hoặc 'bbox', hoặc kiểu line dict của importer
    với 'x'/'x1'/'y')."""
    if obj is None:
        return None
    try:
        if isinstance(obj, dict):
            if 'bbox' in obj and obj['bbox'] is not None:
                return _as_box(obj['bbox'])
            if 'rect' in obj and obj['rect'] is not None:
                return _as_box(obj['rect'])
            if 'x0' in obj:
                return (float(obj['x0']), float(obj['y0']),
                        float(obj['x1']), float(obj['y1']))
            if 'x' in obj and 'y' in obj:
                x0 = float(obj.get('x', 0.0))
                x1 = float(obj.get('x1', x0))
                yc = float(obj.get('y', 0.0))
                h = float(obj.get('h', obj.get('height', 0.0)) or 0.0)
                y0 = float(obj['y0']) if 'y0' in obj else yc - h / 2.0
                y1 = float(obj['y1']) if 'y1' in obj else yc + h / 2.0
                return (x0, y0, x1, y1)
            return None
        # fitz.Rect / IRect / Quad-like
        if hasattr(obj, 'x0') and hasattr(obj, 'y1'):
            return (float(obj.x0), float(obj.y0), float(obj.x1), float(obj.y1))
        seq = list(obj)
        if len(seq) >= 4:
            return (float(seq[0]), float(seq[1]), float(seq[2]), float(seq[3]))
    except Exception:
        return None
    return None


def _norm(b: Box) -> Box:
    x0, y0, x1, y1 = b
    if x1 < x0:
        x0, x1 = x1, x0
    if y1 < y0:
        y0, y1 = y1, y0
    return (x0, y0, x1, y1)


def _union(a: Box, b: Box) -> Box:
    return (min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3]))


def _area(b: Box) -> float:
    return max(0.0, b[2] - b[0]) * max(0.0, b[3] - b[1])


def _inter_area(a: Box, b: Box) -> float:
    w = min(a[2], b[2]) - max(a[0], b[0])
    h = min(a[3], b[3]) - max(a[1], b[1])
    return (w * h) if (w > 0 and h > 0) else 0.0


def _gap_xy(a: Box, b: Box) -> Tuple[float, float]:
    """Khoảng hở theo trục X và Y (0 nếu chồng nhau trên trục đó)."""
    gx = max(0.0, max(a[0], b[0]) - min(a[2], b[2]))
    gy = max(0.0, max(a[1], b[1]) - min(a[3], b[3]))
    return gx, gy


def _v_overlap_ratio(a: Box, b: Box) -> float:
    """Tỉ lệ chồng lấn theo phương DỌC so với chiều cao nhỏ hơn."""
    ov = min(a[3], b[3]) - max(a[1], b[1])
    if ov <= 0:
        return 0.0
    hmin = min(a[3] - a[1], b[3] - b[1])
    if hmin <= 0:
        return 1.0
    return ov / hmin


# ── Nhận diện CHỮ VẼ THÀNH ĐƯỜNG (glyph outline) ─────────────────────────
# Nhiều PDF đề thi (bản in ra PDF / chuyển từ ảnh) vẽ CHỮ bằng đường vectơ chứ
# không dùng font → mỗi con chữ là 1 path nhỏ dạng FILL. Cả đoạn văn khi đó gộp
# thành một "cụm" to đùng và bị nhầm là hình vẽ (đã gặp thật: 1692/1730 nét là
# glyph 3-12pt). Dấu hiệu: RẤT NHIỀU nét, gần như toàn bộ đều bé, không có nét
# dài làm khung/trục. Đồ thị thật luôn có trục/đường cong dài (nét lớn).
# Đo thực tế trên đề "text-as-outline": dải chữ n_rect=119..1335, tỉ lệ nét bé
# 0.95–0.99; còn đồ thị/sơ đồ thật chỉ 8–40 nét và LUÔN có trục/đường dài
# (tỉ lệ nét bé tụt hẳn xuống). Nên chốt: rất nhiều nét + gần như toàn nét bé.
_TINY_PT = 12.0           # nét "bé" = cả 2 chiều < 12pt (cỡ một con chữ)
_OUTLINE_MIN_RECTS = 60   # dưới ngưỡng này mẫu quá nhỏ, không đủ căn cứ
_OUTLINE_TINY_FRAC = 0.90


def _looks_like_text_outline(n_rect: int, n_tiny: int) -> bool:
    if n_rect < _OUTLINE_MIN_RECTS:
        return False
    return (n_tiny / float(n_rect)) >= _OUTLINE_TINY_FRAC


# ── Nét MỜ / VÔ HÌNH (watermark, chữ chìm) ───────────────────────────────
# Watermark "TÀI LIỆU..." chạy chéo cả trang: nếu tính vào cụm thì nó NỐI mọi
# hình rời rạc thành một khối to (đã gặp: 3 hình thật + văn bản dính làm một).
# Đo thực tế: watermark là fill đen `fill_opacity=0.10`, hoặc xám 0.75
# `fill_opacity=0.50` → độ sáng khi chồng lên nền trắng ≈ 0.88-0.90. Hình thật
# đậm hơn hẳn (nét đen ≈ 0, nền xanh nhạt của hình ≈ 0.82).
_LIGHT_CUTOFF = 0.85      # sáng hơn mức này (trên nền trắng) = coi như vô hình


def _luma(c) -> float:
    try:
        if not c:
            return 0.0
        v = list(c)
        if len(v) >= 3:
            return 0.2126 * float(v[0]) + 0.7152 * float(v[1]) + 0.0722 * float(v[2])
        return float(v[0])
    except Exception:
        return 0.0


def _is_faint(d) -> bool:
    """True nếu nét vẽ gần như không nhìn thấy trên nền trắng."""
    try:
        if not isinstance(d, dict):
            return False
        best = 1.0          # càng nhỏ càng đậm
        seen = False
        for ck, ok in (('color', 'stroke_opacity'), ('fill', 'fill_opacity')):
            c = d.get(ck)
            if not c:
                continue
            seen = True
            op = d.get(ok)
            op = 1.0 if op is None else max(0.0, min(1.0, float(op)))
            best = min(best, _luma(c) * op + (1.0 - op))   # trộn với nền trắng
        return seen and best >= _LIGHT_CUTOFF
    except Exception:
        return False


# ── Hàm chính ────────────────────────────────────────────────────────────

def extract_vector_figures(page, table_rects=(), text_lines=None, zoom=3.0,
                           min_size=40.0, min_paths=4, max_area_frac=0.60,
                           gap=6.0, pad=2.0, drop_text_outlines=True,
                           drop_faint=True):
    """Tìm các vùng HÌNH VẼ VECTƠ trên `page` và render ra PNG.

    Trả về list dict: {'rect': fitz.Rect, 'png': bytes, 'n_paths': int,
                       'row': int, 'col': int}
    sắp xếp theo (row, col) — trên xuống dưới, rồi trái sang phải
    (một "row" là cụm các hình có dải Y chồng nhau ≥ 50%).

    `drop_text_outlines`: loại cụm là CHỮ VẼ THÀNH ĐƯỜNG (glyph outline) — xem
    _looks_like_text_outline. Đặt False để tắt (trả đúng thuật toán gốc).

    Không bao giờ raise: mọi lỗi → trả [].
    """
    out: List[dict] = []
    if page is None or fitz is None:
        return out
    try:
        page_rect = _as_box(page.rect) or (0.0, 0.0, 595.0, 842.0)
        page_area = max(_area(page_rect), 1e-6)

        # ── 1. Thu các nét vẽ ────────────────────────────────────────
        try:
            drawings = page.get_drawings() or []
        except Exception:
            drawings = []

        prims: List[Tuple[Box, int, int]] = []   # (bbox, số primitive, là nét bé?)
        for d in drawings:
            try:
                box = _as_box(d.get('rect') if isinstance(d, dict) else getattr(d, 'rect', None))
            except Exception:
                box = None
            if box is None:
                continue
            box = _norm(box)
            w, h = box[2] - box[0], box[3] - box[1]
            # bỏ chấm/điểm nhỏ (cả 2 chiều đều bé) — GIỮ đường mảnh (trục toạ độ)
            if w < 3.0 and h < 3.0:
                continue
            # bỏ nét mờ/vô hình (watermark) — nếu không nó nối các hình lại làm một
            if drop_faint and isinstance(d, dict) and _is_faint(d):
                continue
            # KHUNG TRANG / clip path to bằng cả trang: phải loại NGAY (trước khi
            # gộp cụm) vì bbox của nó bao trùm mọi hình → gộp vào là mất sạch hình.
            if _area(box) > max_area_frac * page_area:
                continue
            try:
                items = d.get('items') if isinstance(d, dict) else None
                n = len(items) if items else 0
            except Exception:
                n = 0
            tiny = 1 if max(w, h) < _TINY_PT else 0
            prims.append((box, max(1, n), tiny))

        if not prims:
            return out

        # ── 2. Gộp cụm tham lam (fixpoint, tối đa 20 lượt) ───────────
        #    cluster = [bbox, n_primitive, n_rect, n_rect_bé]
        clusters: List[List[Any]] = [[b, n, 1, t] for (b, n, t) in prims]
        for _ in range(20):
            merged_any = False
            i = 0
            while i < len(clusters):
                j = i + 1
                while j < len(clusters):
                    a, b = clusters[i][0], clusters[j][0]
                    gx, gy = _gap_xy(a, b)
                    if gx <= gap and gy <= gap:      # giao nhau HOẶC hở ≤ gap cả 2 trục
                        clusters[i][0] = _union(a, b)
                        clusters[i][1] += clusters[j][1]
                        clusters[i][2] += clusters[j][2]
                        clusters[i][3] += clusters[j][3]
                        clusters.pop(j)
                        merged_any = True
                    else:
                        j += 1
                i += 1
            if not merged_any:
                break

        # ── 3. Lọc cụm ───────────────────────────────────────────────
        tabs = [t for t in (_as_box(t) for t in (table_rects or ())) if t]
        tlines = [t for t in (_as_box(t) for t in (text_lines or ())) if t]

        keep: List[Tuple[Box, int]] = []
        for box, n, n_rect, n_tiny in clusters:
            w, h = box[2] - box[0], box[3] - box[1]
            if n < min_paths:
                continue
            if w < min_size or h < min_size:
                continue                       # gạch chân / đường kẻ lẻ
            a = _area(box)
            if a > max_area_frac * page_area:
                continue                       # khung viền trang
            if any(_inter_area(box, t) >= 0.5 * a for t in tabs):
                continue                       # đã xử lý như BẢNG
            if tlines and a > 0:
                cov = sum(_inter_area(box, t) for t in tlines)
                if min(cov, a) / a >= 0.60:
                    continue                   # đoạn văn đóng khung, không phải hình
            if drop_text_outlines and _looks_like_text_outline(n_rect, n_tiny):
                continue                       # chữ vẽ thành đường, không phải hình
            keep.append((box, n))

        if not keep:
            return out

        # ── 4. Sắp xếp theo hàng / cột rồi render PNG ────────────────
        keep.sort(key=lambda kv: (kv[0][1], kv[0][0]))
        rows: List[List[Tuple[Box, int]]] = []
        for item in keep:
            placed = False
            for row in rows:
                if any(_v_overlap_ratio(item[0], m[0]) >= 0.5 for m in row):
                    row.append(item)
                    placed = True
                    break
            if not placed:
                rows.append([item])
        rows.sort(key=lambda r: min(m[0][1] for m in r))

        for ri, row in enumerate(rows):
            row.sort(key=lambda kv: kv[0][0])
            for ci, (box, n) in enumerate(row):
                clip = fitz.Rect(max(box[0] - pad, page_rect[0]),
                                 max(box[1] - pad, page_rect[1]),
                                 min(box[2] + pad, page_rect[2]),
                                 min(box[3] + pad, page_rect[3]))
                try:
                    pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom),
                                          clip=clip, alpha=False)
                    png = pix.tobytes('png')
                except Exception:
                    continue
                if not png:
                    continue
                out.append({'rect': fitz.Rect(box[0], box[1], box[2], box[3]),
                            'png': png, 'n_paths': int(n),
                            'row': ri, 'col': ci})
        return out
    except Exception:
        return []


def figure_anchor_line(rect, text_lines):
    """Chọn DÒNG TEXT làm mỏ neo cho một hình.

    `text_lines`: list dict kiểu importer — khoá 'y' (tâm dòng), 'x', 'x1', 'text'
    (cũng chấp nhận fitz.Rect / tuple 4 số).

    Ưu tiên: dòng gần nhất NẰM TRÊN đỉnh hình (y <= rect.y0) và có chồng lấn
    ngang với hình; nếu không có → dòng trên gần nhất; nếu không có nữa →
    dòng gần nhất theo |y - tâm hình|. Trả None khi text_lines rỗng.
    """
    try:
        if not text_lines:
            return None
        rb = _as_box(rect)
        if rb is None:
            return None
        rb = _norm(rb)
        rcy = (rb[1] + rb[3]) / 2.0

        info = []   # (idx, y_centre, x0, x1)
        for i, ln in enumerate(text_lines):
            y = x0 = x1 = None
            if isinstance(ln, dict):
                if 'y' in ln:
                    try:
                        y = float(ln['y'])
                    except Exception:
                        y = None
                try:
                    if 'x' in ln:
                        x0 = float(ln['x'])
                    if 'x1' in ln:
                        x1 = float(ln['x1'])
                except Exception:
                    pass
            if y is None or x0 is None:
                b = _as_box(ln)
                if b is None:
                    continue
                b = _norm(b)
                if y is None:
                    y = (b[1] + b[3]) / 2.0
                if x0 is None:
                    x0 = b[0]
                if x1 is None:
                    x1 = b[2]
            if x1 is None:
                x1 = x0
            info.append((i, float(y), float(x0), float(x1)))

        if not info:
            return None

        above = [t for t in info if t[1] <= rb[1]]
        if above:
            over = [t for t in above if t[3] > rb[0] and t[2] < rb[2]]
            pool = over or above
            # gần nhất từ trên xuống = y lớn nhất
            best = max(pool, key=lambda t: (t[1], -abs(t[1] - rb[1])))
            return best[0]
        return min(info, key=lambda t: abs(t[1] - rcy))[0]
    except Exception:
        return None
