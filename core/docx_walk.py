"""
Duyệt file Word (.docx) theo ĐÚNG thứ tự tài liệu — dùng chung cho phần
Import Word và tính năng "Tách hình theo câu".

Vì sao cần module này:
  * `doc.paragraphs` của python-docx CHỈ thấy đoạn văn ở cấp thân bài
    → mất toàn bộ đoạn nằm trong ô bảng và trong content control (w:sdt).
  * Hình VML cũ (`v:imagedata` trong `w:pict` / `w:object`, thường là WMF/EMF
    do MathType hoặc Word 2003 chèn) bị bỏ qua nếu chỉ tìm `a:blip`.
  * `mc:AlternateContent` chứa CẢ hai bản (mc:Choice = DrawingML mới,
    mc:Fallback = VML cũ) của cùng một hình → đếm hai lần nếu duyệt thô.

Module này chỉ phụ thuộc python-docx + lxml + re + io (KHÔNG import
core.importer để tránh vòng lặp import).

API:
    iter_block_items(parent)            -> ('p', Paragraph) | ('tbl', Table)
    paragraph_images(para, part)        -> [imgdict, ...]
    classify_table(cells, n_rows, n_cols) -> 'option'|'ds'|'data'|'plain'
    walk_docx(docx_obj, include_tables=True) -> iterator các item
    table_option_cells(item)            -> [(letter, text, images), ...]
    OPTION_CELL_RE                      -> regex nhãn đáp án A./B)/(C)...
"""
import re

# ── Namespace OOXML ──────────────────────────────────────────────────
_NS_W = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'
_NS_R = '{http://schemas.openxmlformats.org/officeDocument/2006/relationships}'
_NS_A = '{http://schemas.openxmlformats.org/drawingml/2006/main}'
_NS_WP = '{http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing}'
_NS_WPG = '{http://schemas.microsoft.com/office/word/2010/wordprocessingGroup}'
_NS_WPG2 = '{http://schemas.openxmlformats.org/drawingml/2006/wordprocessingGroup}'
_NS_PIC = '{http://schemas.openxmlformats.org/drawingml/2006/picture}'
_NS_MC = '{http://schemas.openxmlformats.org/markup-compatibility/2006}'
_NS_V = '{urn:schemas-microsoft-com:vml}'
_NS_SVG = '{http://schemas.microsoft.com/office/drawing/2016/SVG/main}'

W_P = _NS_W + 'p'
W_TBL = _NS_W + 'tbl'
W_TR = _NS_W + 'tr'
W_TC = _NS_W + 'tc'
W_SDT = _NS_W + 'sdt'
W_SDT_CONTENT = _NS_W + 'sdtContent'
W_CUSTOM_XML = _NS_W + 'customXml'
W_TXBX_CONTENT = _NS_W + 'txbxContent'

R_EMBED = _NS_R + 'embed'
R_LINK = _NS_R + 'link'
R_ID = _NS_R + 'id'

A_BLIP = _NS_A + 'blip'
A_EXT = _NS_A + 'ext'
A_CHEXT = _NS_A + 'chExt'
A_XFRM = _NS_A + 'xfrm'
SVG_BLIP = _NS_SVG + 'svgBlip'

WP_INLINE = _NS_WP + 'inline'
WP_ANCHOR = _NS_WP + 'anchor'
WP_EXTENT = _NS_WP + 'extent'

PIC_PIC = _NS_PIC + 'pic'
PIC_SPPR = _NS_PIC + 'spPr'

MC_ALT = _NS_MC + 'AlternateContent'
MC_CHOICE = _NS_MC + 'Choice'
MC_FALLBACK = _NS_MC + 'Fallback'

V_IMAGEDATA = _NS_V + 'imagedata'
V_TEXTBOX = _NS_V + 'textbox'

W_DRAWING = _NS_W + 'drawing'
W_PICT = _NS_W + 'pict'
W_OBJECT = _NS_W + 'object'
# Mỗi "khối hình" độc lập — khử trùng rId trong phạm vi này (xem paragraph_images)
_IMG_CONTAINERS = (W_DRAWING, W_PICT, W_OBJECT, MC_ALT)

_EMU_PER_CM = 360000.0

# Nhãn đáp án trong ô bảng: "A.", "**B)**", "(C)", "D:", "A\." (đã escape)
OPTION_CELL_RE = re.compile(r'^\**\s*\(?([A-D])\**\s*\\?[.):]')

# Nhận định Đúng-Sai: "a)", "**b.**", "c)"
_DS_ITEM_RE = re.compile(r'^\s*\**\(?[a-d][.)]')

_ALLOWED_OPTION_GRIDS = {(2, 2), (1, 4), (4, 1), (1, 2), (2, 1)}


# ── 1. Duyệt block theo thứ tự tài liệu ──────────────────────────────

def _parent_element(parent):
    """Lấy phần tử XML gốc để duyệt con (Document → w:body, _Cell → w:tc)."""
    elm = getattr(parent, 'element', None)
    body = getattr(elm, 'body', None)
    if body is not None:
        return body
    tc = getattr(parent, '_tc', None)
    if tc is not None:
        return tc
    if elm is not None:
        return elm
    # Đã là phần tử lxml
    if hasattr(parent, 'iterchildren'):
        return parent
    raise ValueError('iter_block_items: parent phải là Document hoặc _Cell')


def _iter_children_blocks(elm, parent):
    """Duyệt con trực tiếp; chui vào w:sdt / w:customXml (content control)."""
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    for child in elm.iterchildren():
        tag = child.tag
        if tag == W_P:
            yield 'p', Paragraph(child, parent)
        elif tag == W_TBL:
            yield 'tbl', Table(child, parent)
        elif tag == W_SDT:
            # Content control: nội dung thật nằm trong w:sdtContent
            for inner in child.iterchildren():
                if inner.tag == W_SDT_CONTENT:
                    for item in _iter_children_blocks(inner, parent):
                        yield item
        elif tag == W_CUSTOM_XML:
            # w:customXml bọc trực tiếp nội dung block
            for item in _iter_children_blocks(child, parent):
                yield item


def iter_block_items(parent):
    """Sinh ('p', Paragraph) hoặc ('tbl', Table) theo ĐÚNG thứ tự tài liệu.

    `parent` là docx Document hoặc _Cell của bảng. Có chui vào
    w:sdt / w:sdtContent (content control) để không sót đoạn/bảng bị bọc.
    """
    elm = _parent_element(parent)
    for item in _iter_children_blocks(elm, parent):
        yield item


# ── 2. Ảnh trong một đoạn văn ────────────────────────────────────────

def _emu_to_cm(v):
    try:
        return int(v) / _EMU_PER_CM
    except (TypeError, ValueError):
        return None


def _ext_of(elm, tag=A_EXT):
    """(cx, cy) từ phần tử a:ext / a:chExt con của a:xfrm."""
    if elm is None:
        return None
    e = elm.find(tag)
    if e is None:
        return None
    try:
        return int(e.get('cx')), int(e.get('cy'))
    except (TypeError, ValueError):
        return None


_LEN_RE = re.compile(r'^\s*(-?[\d.]+)\s*(pt|in|cm|mm|px|pc|em|ex)?\s*$', re.I)
_UNIT_CM = {'pt': 2.54 / 72, 'in': 2.54, 'cm': 1.0, 'mm': 0.1,
            'px': 2.54 / 96, 'pc': 12 * 2.54 / 72}


def _css_len_cm(txt):
    if not txt:
        return None
    m = _LEN_RE.match(txt)
    if not m:
        return None
    try:
        val = float(m.group(1))
    except ValueError:
        return None
    unit = (m.group(2) or 'pt').lower()
    f = _UNIT_CM.get(unit)
    if f is None or val <= 0:
        return None
    return val * f


def _vml_dims(elm):
    """(w_cm, h_cm) từ thuộc tính style của v:shape / v:rect ..."""
    style = elm.get('style')
    if not style:
        return None
    w = h = None
    for chunk in style.split(';'):
        if ':' not in chunk:
            continue
        k, _, v = chunk.partition(':')
        k = k.strip().lower()
        if k == 'width':
            w = _css_len_cm(v)
        elif k == 'height':
            h = _css_len_cm(v)
    if w is None and h is None:
        return None
    return w, h


def _rid_info(part, rid):
    """(blob, ext) của quan hệ rId. Ảnh liên kết ngoài → blob None."""
    blob, ext = None, ''
    try:
        rel_parts = part.related_parts
    except Exception:
        rel_parts = {}
    img_part = None
    try:
        img_part = rel_parts[rid]
    except Exception:
        img_part = None
    if img_part is not None:
        try:
            blob = img_part.blob
        except Exception:
            blob = None
        name = str(getattr(img_part, 'partname', '') or '')
        if '.' in name:
            ext = name.rsplit('.', 1)[-1].lower()
        if not ext:
            ct = str(getattr(img_part, 'content_type', '') or '')
            if '/' in ct:
                ext = ct.rsplit('/', 1)[-1].lower()
    else:
        # Ảnh liên kết ngoài (r:link) — lấy đuôi từ URL/đường dẫn
        try:
            rel = part.rels[rid]
            target = str(getattr(rel, 'target_ref', '') or '')
        except Exception:
            target = ''
        target = target.split('?')[0].split('#')[0]
        if '.' in target:
            ext = target.rsplit('.', 1)[-1].lower()
    if ext == 'jpg':
        ext = 'jpeg'
    if ext == 'tif':
        ext = 'tiff'
    return blob, ext


def _add_image(out, seen, part, rid, ctx):
    if not rid:
        return
    if seen is not None:
        if rid in seen:
            return
        seen.add(rid)
    blob, ext = _rid_info(part, rid)

    w_cm = h_cm = None
    size = None
    if ctx.get('in_group') and ctx.get('pic_ext'):
        cx, cy = ctx['pic_ext']
        sx, sy = ctx.get('grp_scale') or (1.0, 1.0)
        size = (cx * sx, cy * sy)
    elif ctx.get('extent'):
        size = ctx['extent']
    elif ctx.get('pic_ext'):
        size = ctx['pic_ext']
    if size:
        w_cm = _emu_to_cm(size[0])
        h_cm = _emu_to_cm(size[1])
    elif ctx.get('vml'):
        w_cm, h_cm = ctx['vml']

    out.append({
        'rid': rid,
        'blob': blob,
        'ext': ext,
        'width_cm': w_cm,
        'height_cm': h_cm,
        'anchored': bool(ctx.get('anchored')),
        'in_textbox': bool(ctx.get('in_textbox')),
    })


def _walk_img_elm(elm, part, ctx, out, seen, per_container=True):
    tag = elm.tag

    # Mỗi w:drawing / w:pict / w:object là MỘT hình riêng → khử trùng rId trong
    # phạm vi khối, không phải cả đoạn (cùng 1 ảnh có thể lặp lại nhiều lần
    # trong một dòng — vd ký hiệu độ chèn 8 lần).
    if per_container and seen is not None and tag in _IMG_CONTAINERS:
        seen = set()

    # mc:AlternateContent → CHỈ lấy nhánh mc:Choice (tránh đếm trùng VML fallback)
    if tag == MC_ALT:
        branch = None
        for ch in elm.iterchildren():
            if ch.tag == MC_CHOICE:
                branch = ch
                break
        if branch is None:
            for ch in elm.iterchildren():
                if ch.tag == MC_FALLBACK:
                    branch = ch
                    break
        if branch is not None:
            for ch in branch.iterchildren():
                _walk_img_elm(ch, part, ctx, out, seen, per_container)
        return

    # Ảnh DrawingML — KHÔNG chui vào con (a:extLst chứa asvg:svgBlip trùng ảnh)
    if tag == A_BLIP:
        _add_image(out, seen, part,
                   elm.get(R_EMBED) or elm.get(R_LINK), ctx)
        return
    if tag == SVG_BLIP:
        _add_image(out, seen, part,
                   elm.get(R_EMBED) or elm.get(R_LINK), ctx)
        return
    # Ảnh VML (w:pict / w:object — thường là WMF/EMF)
    if tag == V_IMAGEDATA:
        _add_image(out, seen, part,
                   elm.get(R_ID) or elm.get(R_EMBED) or elm.get(R_LINK), ctx)
        return

    # Cập nhật ngữ cảnh khi đi xuống
    new_ctx = ctx
    if tag == WP_INLINE or tag == WP_ANCHOR:
        new_ctx = dict(ctx)
        new_ctx['anchored'] = (tag == WP_ANCHOR)
        e = elm.find(WP_EXTENT)
        if e is not None:
            try:
                new_ctx['extent'] = (int(e.get('cx')), int(e.get('cy')))
            except (TypeError, ValueError):
                pass
    elif tag in (_NS_WPG + 'wgp', _NS_WPG2 + 'wgp', _NS_A + 'grpSp'):
        new_ctx = dict(ctx)
        new_ctx['in_group'] = True
        scale = None
        for sp in elm.iterchildren():
            if sp.tag.endswith('grpSpPr'):
                xf = sp.find(A_XFRM)
                ext = _ext_of(xf, A_EXT)
                chext = _ext_of(xf, A_CHEXT)
                if ext and chext and chext[0] and chext[1]:
                    scale = (ext[0] / chext[0], ext[1] / chext[1])
                break
        new_ctx['grp_scale'] = scale or (1.0, 1.0)
    elif tag == PIC_PIC:
        new_ctx = dict(ctx)
        for sp in elm.iterchildren():
            if sp.tag == PIC_SPPR or sp.tag.endswith('spPr'):
                pe = _ext_of(sp.find(A_XFRM), A_EXT)
                if pe:
                    new_ctx['pic_ext'] = pe
                break
    elif tag == W_TXBX_CONTENT or tag == V_TEXTBOX:
        new_ctx = dict(ctx)
        new_ctx['in_textbox'] = True
    elif tag.startswith(_NS_V) and elm.get('style'):
        dims = _vml_dims(elm)
        if dims:
            new_ctx = dict(ctx)
            new_ctx['vml'] = dims

    for ch in elm.iterchildren():
        _walk_img_elm(ch, part, new_ctx, out, seen, per_container)


def paragraph_images(para, part, dedupe='container'):
    """Danh sách ảnh của một đoạn văn, theo thứ tự tài liệu, khử trùng theo rId.

    Mỗi phần tử:
      {'rid', 'blob' (bytes|None), 'ext', 'width_cm', 'height_cm',
       'anchored' (wp:anchor), 'in_textbox' (nằm trong w:txbxContent)}

    Nhận diện: a:blip@r:embed, a:blip@r:link (ảnh ngoài → blob None nhưng vẫn
    liệt kê), v:imagedata@r:id (VML), asvg:svgBlip. Với mc:AlternateContent chỉ
    lấy nhánh mc:Choice. Nhóm hình (wpg:wgp) → liệt kê từng hình con.

    dedupe:
      'container' (mặc định) — khử trùng rId trong TỪNG khối hình
          (w:drawing / w:pict / w:object / mc:AlternateContent). Đủ để chặn
          đếm hai lần mc:Choice + mc:Fallback, mà vẫn giữ đúng số lần MỘT ảnh
          được chèn lặp lại trong cùng một đoạn (gặp thật: ký hiệu độ chèn 8
          lần trong một dòng — dùng chung 1 rId).
      'paragraph' — khử trùng theo rId trên cả đoạn (mỗi rId chỉ 1 lần).
      'none' — liệt kê mọi lần xuất hiện.
    """
    p = getattr(para, '_p', para)
    out, seen = [], set()
    ctx = {'anchored': False, 'in_textbox': False}
    per_container = (dedupe == 'container')
    if dedupe == 'none':
        seen = None
    for ch in p.iterchildren():
        _walk_img_elm(ch, part, ctx, out, seen, per_container=per_container)
    return out


# ── 3. Phân loại bảng ────────────────────────────────────────────────

def _rows_of(cells, n_rows, n_cols):
    rows = []
    for r in range(n_rows):
        rows.append(cells[r * n_cols:(r + 1) * n_cols])
    return rows


def _cell_text(c):
    return (c.get('text') or '').strip()


def _cell_imgs(c):
    return c.get('images') or []


def classify_table(cells, n_rows, n_cols):
    """'option' | 'ds' | 'data' | 'plain'.

    cells: danh sách phẳng theo hàng (row-major) của
           {'text': str, 'images': [imgdict, ...]}
    """
    cells = list(cells or [])
    if n_rows <= 0 or n_cols <= 0 or not cells:
        return 'plain'

    non_empty = [c for c in cells if _cell_text(c) or _cell_imgs(c)]
    with_text = [c for c in cells if _cell_text(c)]
    with_img = [c for c in cells if _cell_imgs(c)]

    # ── option: bảng 4 phương án A/B/C/D ──
    if (n_rows, n_cols) in _ALLOWED_OPTION_GRIDS and 2 <= len(non_empty) <= 4:
        letters, ok = [], True
        for c in with_text:
            m = OPTION_CELL_RE.match(_cell_text(c))
            if not m:
                ok = False
                break
            letters.append(m.group(1).upper())
        if ok and len(letters) == len(set(letters)):
            img_only = [c for c in non_empty
                        if not _cell_text(c) and _cell_imgs(c)]
            if not img_only or len(letters) >= 2:
                if letters:
                    return 'option'

    # ── ds: bảng Đúng-Sai (mirror importer._is_ds_table_rows) ──
    rows = _rows_of(cells, n_rows, n_cols)
    for r in rows[:2]:
        txts = [_cell_text(c) for c in r]
        if (('Đ' in txts or 'Đúng' in txts) and ('S' in txts or 'Sai' in txts)):
            return 'ds'
    n_ds = sum(1 for r in rows if r and _DS_ITEM_RE.match(_cell_text(r[0])))
    if n_ds >= 2:
        return 'ds'

    # ── data: bảng số liệu ──
    if n_rows >= 2 and n_cols >= 2 and len(with_text) >= 2 and not with_img:
        return 'data'

    return 'plain'


# ── 4. Duyệt toàn tài liệu ───────────────────────────────────────────

def _collect_cell(cell):
    """Gộp text + ảnh của một ô (bảng lồng nhau bị làm phẳng vào ô cha)."""
    texts, imgs = [], []
    part = None
    try:
        part = cell.part
    except Exception:
        part = None
    for kind, obj in iter_block_items(cell):
        if kind == 'p':
            texts.append(obj.text or '')
            if part is not None:
                imgs.extend(paragraph_images(obj, part))
        else:
            for row in _table_cells(obj):
                for sub in row:
                    if sub['text']:
                        texts.append(sub['text'])
                    imgs.extend(sub['images'])
    return {'text': '\n'.join(texts).strip(), 'images': imgs}


def _table_cells(table):
    """[[cell, ...] mỗi hàng] — đi thẳng theo w:tr/w:tc (ô gộp không nhân bản)."""
    from docx.table import _Cell

    rows = []
    for tr in table._tbl.iterchildren():
        if tr.tag != W_TR:
            continue
        row = []
        for tc in tr.iterchildren():
            if tc.tag != W_TC:
                continue
            row.append(_collect_cell(_Cell(tc, table)))
        rows.append(row)
    return rows


def paragraph_numbering(para, part):
    """(numId, ilvl, numFmt) nếu đoạn có đánh số TỰ ĐỘNG của Word (w:numPr), else None.
    numFmt: 'decimal' | 'bullet' | 'upperLetter' | 'lowerLetter' | … | 'unknown' (không đọc
    được numbering.xml). Chữ số/chữ cái tự động KHÔNG nằm trong para.text — đề đánh số
    kiểu này từng bị import_docx coi là không có câu nào."""
    try:
        pPr = para._p.find(_NS_W + 'pPr')
        if pPr is None:
            return None
        numPr = pPr.find(_NS_W + 'numPr')
        if numPr is None:
            return None
        numId_el = numPr.find(_NS_W + 'numId')
        ilvl_el = numPr.find(_NS_W + 'ilvl')
        num_id = numId_el.get(_NS_W + 'val') if numId_el is not None else None
        ilvl = int(ilvl_el.get(_NS_W + 'val') or 0) if ilvl_el is not None else 0
        if not num_id or num_id == '0':
            return None
    except Exception:
        return None
    fmt = 'unknown'
    try:
        numbering = part.numbering_part.element
        num = numbering.find('%snum[@%snumId="%s"]' % (_NS_W, _NS_W, num_id))
        abs_id = num.find(_NS_W + 'abstractNumId').get(_NS_W + 'val')
        absn = numbering.find('%sabstractNum[@%sabstractNumId="%s"]' % (_NS_W, _NS_W, abs_id))
        lvl = absn.find('%slvl[@%silvl="%d"]' % (_NS_W, _NS_W, ilvl))
        fmt = lvl.find(_NS_W + 'numFmt').get(_NS_W + 'val') or 'unknown'
    except Exception:
        pass
    return (num_id, ilvl, fmt)


def walk_docx(docx_obj, include_tables=True):
    """Sinh các item theo ĐÚNG thứ tự tài liệu.

      {'kind': 'p', 'text': str, 'images': [...], 'style': str}
      {'kind': 'tbl', 'table': 'option'|'ds'|'data'|'plain',
       'n_rows': int, 'n_cols': int, 'cells': [[cell, ...] mỗi hàng]}
    """
    part = docx_obj.part
    for kind, obj in iter_block_items(docx_obj):
        if kind == 'p':
            try:
                style = obj.style.name if obj.style is not None else ''
            except Exception:
                style = ''
            yield {'kind': 'p', 'text': obj.text or '',
                   'images': paragraph_images(obj, part),
                   'style': style or '',
                   'num': paragraph_numbering(obj, part)}
        else:
            if not include_tables:
                continue
            rows = _table_cells(obj)
            n_rows = len(rows)
            n_cols = max((len(r) for r in rows), default=0)
            flat = []
            for r in rows:
                padded = list(r) + [{'text': '', 'images': []}] * (n_cols - len(r))
                flat.extend(padded)
            yield {'kind': 'tbl',
                   'table': classify_table(flat, n_rows, n_cols),
                   'n_rows': n_rows, 'n_cols': n_cols, 'cells': rows}


# ── 5. Tách 4 phương án từ bảng 'option' ─────────────────────────────

def table_option_cells(item):
    """[(letter, text_không_nhãn, images), ...] sắp theo A..D.

    Ô chỉ có hình (không chữ) được gán nốt chữ cái còn thiếu theo thứ tự đọc.
    """
    found = []
    for row in item.get('cells') or []:
        for c in row:
            txt = _cell_text(c)
            imgs = _cell_imgs(c)
            if not txt and not imgs:
                continue
            m = OPTION_CELL_RE.match(txt) if txt else None
            if m:
                found.append([m.group(1).upper(), txt[m.end():].strip(), imgs])
            else:
                found.append([None, txt, imgs])

    used = {f[0] for f in found if f[0]}
    spare = [ch for ch in 'ABCD' if ch not in used]
    for f in found:
        if f[0] is None:
            f[0] = spare.pop(0) if spare else ''
    found.sort(key=lambda f: f[0] or 'Z')
    return [(f[0], f[1], f[2]) for f in found]
