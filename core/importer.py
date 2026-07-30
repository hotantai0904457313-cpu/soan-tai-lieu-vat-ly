"""
Import PDF / Word → Document model.
Pipeline DOCX: Pandoc → Markdown (LaTeX $...$) → Gemini text → JSON  (công thức giữ nguyên 100%)
Pipeline PDF: Gemini Vision → JSON
Fallback: regex parser thông thường
"""
import os, re, uuid, json, sys, subprocess, shutil as _shutil
from typing import List, Tuple, Optional
from PIL import Image as PILImage, ImageFilter, ImageEnhance
import io

from core.document_model import Document, Section, Question, Image

IMG_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'data', 'uploads', 'images')
os.makedirs(IMG_DIR, exist_ok=True)

# ── Nhận diện cấu trúc (dùng cho import thông thường) ────────────

SECTION_PATTERNS = [
    ('trac_nghiem_lua_chon', re.compile(
        r'ph[àầa]n\s*i[^iv]|tr[áắa]c\s*nghi[eệe]m\s*l[uựua]\s*ch[oọo]n'
        r'|tr[áắa]c\s*nghi[eệe]m\b|trac\s*nghiem',
        re.IGNORECASE | re.UNICODE)),
    ('dung_sai', re.compile(
        r'ph[àầa]n\s*ii[^i]|[dđd][úuu]ng\s*[–—-]\s*sai|[dđd][úuu]ng\s*sai'
        r'|dung\s*[-–]\s*sai|dung\s*sai',
        re.IGNORECASE | re.UNICODE)),
    ('tra_loi_ngan', re.compile(
        r'ph[àầa]n\s*iii|tr[ảaa]\s*l[oờo]i\s*ng[aắa]n'
        r'|[dđd][eềe][nne]\s*s[oốo]|tra\s*loi\s*ngan',
        re.IGNORECASE | re.UNICODE)),
    ('tu_luan', re.compile(
        r'ph[àầa]n\s*iv|t[ựuu]\s*lu[aậa]n|tu\s*luan',
        re.IGNORECASE | re.UNICODE)),
    ('ly_thuyet', re.compile(
        r'l[yýy]\s*thuy[eếe]t|ki[eếe]n\s*th[uứu]c\s*c[oơo]\s*b[aảa]n'
        r'|t[oóo]m\s*t[aắa]t|ly\s*thuyet',
        re.IGNORECASE | re.UNICODE)),
]

# Có tiền tố "Câu" → chấp nhận mọi dấu phân cách. KHÔNG có tiền tố → lookahead
# bắt buộc số theo sau bởi . hoặc ) — nếu cho cả ':' và khoảng trắng thì dòng
# nối tiếp kiểu "10 m/s là vận tốc..." bị tách nhầm thành câu hỏi mới số 10.
QUESTION_PATTERN = re.compile(
    r'^(?=c[aâ]u\s*\d|\d+\s*[.)])(?:c[aâ]u\s*)?(\d+)\s*[.):\s]\s*(.+)',
    re.IGNORECASE | re.UNICODE | re.DOTALL)

OPTION_PATTERN = re.compile(
    r'^([A-D])\s*[.):\-]\s*(.+)', re.UNICODE | re.DOTALL)

SUB_ITEM_PATTERN = re.compile(
    r'^([a-d])\s*\\?[.)]\s*(.+)', re.UNICODE | re.DOTALL)  # \\? handles Pandoc \)

MULTI_OPTION_PATTERN = re.compile(
    r'([A-D])\s*[.):\-]\s*((?:(?![A-D]\s*[.):\-]).)+)',
    re.UNICODE)


def split_multi_options(line: str) -> List[str]:
    matches = list(MULTI_OPTION_PATTERN.finditer(line))
    if len(matches) < 2:
        return [line]
    opts = []
    for i, m in enumerate(matches):
        letter = m.group(1).upper()
        start = m.start(2)
        end = matches[i+1].start() if i+1 < len(matches) else len(line)
        text = line[start:end].strip()
        opts.append(f'{letter}. {text}')
    return opts


def detect_section_type(text: str) -> Optional[str]:
    for stype, pat in SECTION_PATTERNS:
        if pat.search(text):
            return stype
    return None


def sharpen_image(img_bytes: bytes, filename: str) -> str:
    out_path = os.path.join(IMG_DIR, filename)
    try:
        img = PILImage.open(io.BytesIO(img_bytes))
        img.save(out_path, 'PNG')
    except Exception:
        with open(out_path, 'wb') as f:
            f.write(img_bytes)
    return filename


def _parse_pages_1indexed(page_range: str, total: int) -> List[int]:
    """Parse '1-3', '1,3,5', '2', '1-3,5' (đếm từ 1) → list index 0-indexed hợp lệ."""
    pages = set()
    for part in (page_range or '').replace(' ', '').split(','):
        if not part:
            continue
        if '-' in part:
            a, b = part.split('-', 1)
            try:
                pages.update(range(int(a) - 1, int(b)))
            except ValueError:
                pass
        else:
            try:
                pages.add(int(part) - 1)
            except ValueError:
                pass
    return sorted(p for p in pages if 0 <= p < total)


def subset_pdf(src_path: str, dst_path: str, page_range: str) -> bool:
    """Tạo PDF mới chỉ gồm các trang được chọn. True nếu tạo được (page_range hợp lệ)."""
    import fitz
    src = fitz.open(src_path)
    out = None
    try:
        idxs = _parse_pages_1indexed(page_range, len(src))
        if not idxs or len(idxs) == len(src):
            return False   # rỗng hoặc chọn tất cả → khỏi cần cắt
        out = fitz.open()
        for i in idxs:
            out.insert_pdf(src, from_page=i, to_page=i)
        out.save(dst_path)
        return True
    finally:
        if out is not None:
            try:
                out.close()
            except Exception:
                pass
        src.close()


# ── Import Word (.docx) ───────────────────────────────────────────

def import_docx(filepath: str) -> Document:
    from docx import Document as DocxDoc
    import zipfile

    docx_obj = DocxDoc(filepath)
    doc = Document()
    doc.title = os.path.splitext(os.path.basename(filepath))[0]

    image_map: dict = {}
    try:
        with zipfile.ZipFile(filepath) as z:
            rels_path = 'word/_rels/document.xml.rels'
            if rels_path in z.namelist():
                import xml.etree.ElementTree as ET
                root = ET.fromstring(z.read(rels_path))
                ns = {'r': 'http://schemas.openxmlformats.org/package/2006/relationships'}
                for rel in root.findall('r:Relationship', ns):
                    if 'image' in rel.get('Type', '').lower():
                        rid = rel.get('Id')
                        target = rel.get('Target', '')
                        img_path = 'word/' + target.lstrip('/')
                        if img_path in z.namelist():
                            img_bytes = z.read(img_path)
                            fname = f'{uuid.uuid4().hex[:8]}.png'
                            saved = sharpen_image(img_bytes, fname)
                            image_map[rid] = saved
    except Exception:
        pass

    R_EMBED = '{http://schemas.openxmlformats.org/officeDocument/2006/relationships}embed'
    items: List[Tuple[str, List[str]]] = []
    for para in docx_obj.paragraphs:
        text = para.text.strip()
        img_refs: List[str] = []
        for elem in para._p.iter():
            if elem.tag.endswith('}blip'):
                rid = elem.get(R_EMBED)
                if rid and rid in image_map:
                    img_refs.append(image_map[rid])
        if text or img_refs:
            items.append((text, img_refs))

    _build_sections_from_items(doc, items)
    return doc


# ── Import PDF ────────────────────────────────────────────────────

def _is_ds_table_rows(rows) -> bool:
    """Bảng CÂU HỎI ĐÚNG-SAI (format 2025): có cột Đ/S (hoặc Đúng/Sai) ở hàng đầu,
    hoặc ≥2 hàng mà ô đầu bắt đầu bằng nhận định a)/b)/c)/d). Bảng này phải giữ
    TEXT (nhận định là nội dung câu hỏi), KHÔNG được cắt thành ảnh.
    (Mirror pdf_to_word._is_ds_table_rows — giữ 2 bản đồng bộ.)"""
    if not rows:
        return False
    for r in rows[:2]:
        cells = [str(c or '').strip() for c in (r or [])]
        if (('Đ' in cells or 'Đúng' in cells) and ('S' in cells or 'Sai' in cells)):
            return True
    n = sum(1 for r in rows
            if r and re.match(r'^\s*\**[a-d][.)]', str(r[0] or '')))
    return n >= 2


def _extract_table_images(page, page_num: int):
    """Phát hiện bảng có kẻ ô trên trang (PyMuPDF find_tables) và cắt bảng SỐ LIỆU
    thành ảnh PNG (scale 3x cho nét) — để bảng dữ kiện thí nghiệm đi theo câu hỏi
    dưới dạng hình thay vì text vỡ / mất hẳn.

    Bỏ qua: bảng Đúng-Sai (là câu hỏi — giữ text), bảng toàn ô trống (khung điền
    đáp án), "bảng" chiếm gần cả trang (thực chất là khung viền trang).

    Returns: (table_imgs: [{'y0': float, 'fname': str}], table_rects: [fitz.Rect])
    """
    import fitz
    table_imgs, table_rects = [], []
    try:
        tabs = page.find_tables()
        tables = list(getattr(tabs, 'tables', None) or [])
    except Exception:
        return table_imgs, table_rects

    page_area = max(page.rect.width * page.rect.height, 1e-6)
    for ti, t in enumerate(tables):
        try:
            rows = t.extract() or []
        except Exception:
            rows = []
        # Cần tối thiểu 2 hàng × 2 cột và ≥2 ô có nội dung — tránh khung trang,
        # khung trống điền đáp án
        filled = sum(1 for r in rows for c in (r or []) if str(c or '').strip())
        if len(rows) < 2 or max((len(r or []) for r in rows), default=0) < 2 or filled < 2:
            continue
        if _is_ds_table_rows(rows):
            continue   # bảng Đúng-Sai = nội dung câu hỏi → giữ text
        try:
            bbox = fitz.Rect(t.bbox)
            if bbox.width * bbox.height > 0.7 * page_area:
                continue   # to bất thường — khả năng là viền trang
            # Nới 2pt mỗi phía cho khỏi cắt mất viền bảng
            clip = fitz.Rect(max(bbox.x0 - 2, page.rect.x0),
                             max(bbox.y0 - 2, page.rect.y0),
                             min(bbox.x1 + 2, page.rect.x1),
                             min(bbox.y1 + 2, page.rect.y1))
            if clip.width < 40 or clip.height < 15:
                continue
            pix = page.get_pixmap(matrix=fitz.Matrix(3, 3), clip=clip)
            fname = sharpen_image(pix.tobytes('png'), f'p{page_num}_tbl{ti}.png')
            table_imgs.append({'y0': bbox.y0, 'fname': fname})
            table_rects.append(bbox)
        except Exception:
            pass
    return table_imgs, table_rects


def import_pdf(filepath: str) -> Document:
    import fitz

    doc = Document()
    doc.title = os.path.splitext(os.path.basename(filepath))[0]
    items: List[Tuple[str, List[str]]] = []

    pdf_doc = None
    try:
        pdf_doc = fitz.open(filepath)
        for page_num in range(len(pdf_doc)):
            page = pdf_doc[page_num]
            page_h = page.rect.height

            # Bảng số liệu → ảnh; text ruột bảng sẽ bị loại khỏi luồng text
            table_imgs, table_rects = _extract_table_images(page, page_num)

            def _inside_table(b):
                if not table_rects:
                    return False
                bx0, by0, bx1, by1 = b[0], b[1], b[2], b[3]
                area = max((bx1 - bx0) * (by1 - by0), 1e-6)
                for tr in table_rects:
                    iw = max(0.0, min(bx1, tr.x1) - max(bx0, tr.x0))
                    ih = max(0.0, min(by1, tr.y1) - max(by0, tr.y0))
                    if iw * ih > 0.5 * area:
                        return True
                return False

            blocks = page.get_text('blocks')
            text_blocks = []
            for b in blocks:
                if b[6] == 0:
                    if _inside_table(b):
                        continue   # đã thành ảnh bảng — bỏ text vỡ
                    lines = [l.strip() for l in b[4].split('\n') if l.strip()]
                    for line in lines:
                        text_blocks.append({'y': (b[1]+b[3])/2, 'text': line, 'imgs': []})
            text_blocks.sort(key=lambda x: x['y'])

            # Gắn ảnh bảng vào dòng text gần nhất PHÍA TRÊN bảng (câu dẫn)
            for tinfo in table_imgs:
                tbl_y = tinfo['y0']
                best_block = None
                best_dist = float('inf')
                for tb in text_blocks:
                    if tb['y'] <= tbl_y and tbl_y - tb['y'] < best_dist:
                        best_dist = tbl_y - tb['y']
                        best_block = tb
                if best_block is None and text_blocks:
                    best_block = min(text_blocks, key=lambda x: abs(x['y'] - tbl_y))
                if best_block:
                    best_block['imgs'].append(tinfo['fname'])
                else:
                    items.append(('', [tinfo['fname']]))

            img_list_raw = page.get_images(full=True)
            for img_index, img_info in enumerate(img_list_raw):
                xref = img_info[0]
                try:
                    base_img = pdf_doc.extract_image(xref)
                    img_bytes = base_img['image']
                    w = base_img.get('width', 0)
                    h = base_img.get('height', 0)
                    if w < 50 or h < 50:
                        continue
                    fname = f'p{page_num}_{img_index}.png'
                    saved = sharpen_image(img_bytes, fname)
                    img_y = page_h * (img_index + 1) / (len(img_list_raw) + 1)
                    try:
                        rects = page.get_image_rects(xref)
                        if rects:
                            img_y = (rects[0].y0 + rects[0].y1) / 2
                    except Exception:
                        pass
                    best_block = None
                    best_dist = float('inf')
                    for tb in text_blocks:
                        if tb['y'] <= img_y:
                            dist = img_y - tb['y']
                            if dist < best_dist:
                                best_dist = dist
                                best_block = tb
                    if best_block is None and text_blocks:
                        best_block = min(text_blocks, key=lambda x: abs(x['y'] - img_y))
                    if best_block:
                        best_block['imgs'].append(saved)
                    else:
                        items.append(('', [saved]))
                except Exception:
                    pass

            for tb in text_blocks:
                items.append((tb['text'], tb['imgs']))
    except Exception:
        # Lỗi giữa chừng → thử fallback pdfplumber, bỏ items dở của fitz
        items.clear()
        try:
            import pdfplumber
            with pdfplumber.open(filepath) as pdf:
                for page in pdf.pages:
                    text = page.extract_text() or ''
                    for line in text.split('\n'):
                        if line.strip():
                            items.append((line.strip(), []))
        except Exception:
            pass
    finally:
        # Đóng cả khi exception — handle mở giữ file trên Windows, chặn xoá uploads/
        if pdf_doc is not None:
            try:
                pdf_doc.close()
            except Exception:
                pass

    _build_sections_from_items(doc, items)
    return doc


# ── Xây dựng sections từ danh sách items (dùng cho import thông thường) ──

def _build_sections_from_items(doc: Document, items: List[Tuple[str, List[str]]]):
    current_section: Optional[Section] = None
    current_question: Optional[Question] = None
    q_number = 1

    def flush_question():
        nonlocal current_question
        if current_question and current_question.text and current_section:
            current_section.questions.append(current_question)
            current_question = None

    def flush_section():
        nonlocal current_section, q_number
        flush_question()
        if current_section and (current_section.questions or current_section.intro):
            doc.sections.append(current_section)
        current_section = None
        q_number = 1

    def attach_images(img_filenames, target, fallback_section):
        for fname in img_filenames:
            img_obj = Image(filename=fname)
            if target:
                target.images.append(img_obj)

    for raw_text, img_list in items:
        line = raw_text.strip()

        if not line and img_list:
            attach_images(img_list, current_question, current_section)
            continue

        if not line:
            continue

        stype = detect_section_type(line)
        if stype and len(line) < 120:
            flush_section()
            current_section = Section(type=stype, label=line,
                                      is_theory=(stype == 'ly_thuyet'))
            q_number = 1
            continue

        if current_section is None:
            current_section = Section(type='khac', label='')

        m_q = QUESTION_PATTERN.match(line)
        if m_q:
            flush_question()
            q_number = int(m_q.group(1))
            current_question = Question(number=q_number, text=m_q.group(2).strip())
            if img_list:
                attach_images(img_list, current_question, current_section)
            continue

        if current_question:
            m_opt = OPTION_PATTERN.match(line)
            if m_opt:
                parts = split_multi_options(line)
                if len(parts) > 1:
                    for part in parts:
                        mo = OPTION_PATTERN.match(part)
                        if mo:
                            current_question.options.append(
                                f'{mo.group(1).upper()}. {mo.group(2).strip()}')
                else:
                    current_question.options.append(
                        f'{m_opt.group(1).upper()}. {m_opt.group(2).strip()}')
                if img_list:
                    attach_images(img_list, current_question, current_section)
                continue

            multi = split_multi_options(line)
            if len(multi) >= 2 and all(OPTION_PATTERN.match(p) for p in multi):
                for part in multi:
                    mo = OPTION_PATTERN.match(part)
                    if mo:
                        current_question.options.append(
                            f'{mo.group(1).upper()}. {mo.group(2).strip()}')
                if img_list:
                    attach_images(img_list, current_question, current_section)
                continue

            m_sub = SUB_ITEM_PATTERN.match(line)
            if m_sub and current_section.type in ('dung_sai', 'khac'):
                current_question.sub_items.append(
                    f'{m_sub.group(1)}. {m_sub.group(2).strip()}')
                if img_list:
                    attach_images(img_list, current_question, current_section)
                continue

            current_question.text += ' ' + line
            if img_list:
                attach_images(img_list, current_question, current_section)
        else:
            current_section.intro += (' ' + line if current_section.intro else line)
            if img_list:
                attach_images(img_list, None, current_section)

    flush_section()

    if not doc.sections:
        sec = Section(type='khac', label='Nội dung')
        sec.intro = '\n'.join(t for t, _ in items[:50])
        doc.sections.append(sec)

    _infer_doc_type(doc)


def _infer_doc_type(doc: Document):
    types = {s.type for s in doc.sections}
    if 'trac_nghiem_lua_chon' in types or 'dung_sai' in types:
        doc.doc_type = 'de_thi'
    elif 'ly_thuyet' in types:
        doc.doc_type = 'phieu_bai_tap'
    else:
        doc.doc_type = 'khac'


def _build_sections_from_lines(doc: Document, lines: List[str], image_map: dict):
    items = [(line, []) for line in lines if line]
    _build_sections_from_items(doc, items)


# ── Gemini Vision → JSON import ───────────────────────────────────
#
# Thay vì: ảnh → free text → regex parser (brittle)
# Làm:     ảnh → Gemini Vision → JSON có cấu trúc → Document
#
# Lợi ích: không còn regex parser, không còn chuyện nhảy sai câu/mất đáp án.

VISION_JSON_PROMPT = (
    "Bạn là OCR chuyên đề thi Vật lý THPT Việt Nam. Hãy đọc toàn bộ nội dung trong ảnh.\n"
    "Trả về JSON hợp lệ DUY NHẤT — KHÔNG có text, markdown, hay ```json``` bên ngoài.\n\n"
    "Schema bắt buộc:\n"
    '{"sections":[{"type":"<loại>","label":"<tiêu đề phần>","intro":"<lý thuyết/dẫn đề nếu có>","questions":'
    '[{"number":<int>,"text":"<nội dung câu hỏi>","options":["A. ...","B. ...","C. ...","D. ..."],'
    '"sub_items":["a) ...","b) ...","c) ...","d) ..."]}]}]}\n\n'
    "Giá trị hợp lệ cho type: trac_nghiem_lua_chon | dung_sai | tra_loi_ngan | tu_luan | ly_thuyet | khac\n"
    '"options": chỉ có khi câu hỏi có lựa chọn A/B/C/D — điền đủ 4 đáp án, không bỏ qua.\n'
    '"sub_items": chỉ có khi câu hỏi có mục a/b/c/d (phần đúng-sai) — điền đủ.\n\n'
    "QUY TẮC BẢNG SỐ LIỆU (KHÔNG ĐƯỢC BỎ BẢNG — mất bảng là mất dữ kiện đề):\n"
    "Câu hỏi có BẢNG (bảng số liệu thí nghiệm, bảng giá trị đo, bảng dữ kiện cho đề):\n"
    '- Chép ĐẦY ĐỦ mọi hàng/ô thành bảng Markdown ngay trong "text" của câu đó,\n'
    "  mỗi hàng bảng là 1 dòng, xuống dòng bằng \\n trong JSON string. Ví dụ:\n"
    '  "text": "Kết quả đo được cho trong bảng:\\n| F (N) | 1,0 | 2,0 | 3,0 |\\n| a (m/s2) | 0,5 | 1,0 | 1,5 |"\n'
    "- Ô bảng ghi số/chữ trần, KHÔNG bọc $...$ trong ô bảng.\n"
    "- NGOẠI LỆ: bảng có cột Đ/S (Đúng/Sai) KHÔNG phải bảng số liệu — đó là câu hỏi\n"
    '  đúng-sai: đưa từng nhận định vào "sub_items", bỏ cột Đ/S trống.\n\n'
    "QUY TẮC LATEX (QUAN TRỌNG NHẤT):\n"
    "Tất cả công thức, ký hiệu toán học → LaTeX bọc trong $ $\n"
    "TRONG CHUỖI JSON, mọi dấu backslash \\ phải viết THÀNH \\\\\\\\ (hai dấu):\n"
    '  Đúng:  "text": "Vật có khối lượng $m = 2$ kg, tốc độ $v_0 = 3$ m/s"\n'
    '  Đúng:  "text": "Gia tốc $a = \\\\frac{F}{m}$, quãng đường $S = \\\\frac{v^2 - v_0^2}{2a}$"\n'
    '  Đúng:  "options": ["A. $v = \\\\sqrt{2aS}$", "B. $\\\\frac{1}{2}mv^2$", "C. 6 m/s", "D. 8 m/s"]\n'
    '  SAI:   "text": "\\frac{a}{b}"  ← thiếu backslash thứ hai → JSON lỗi\n\n'
    "Ký hiệu hay dùng (nhớ viết double-backslash):\n"
    "  Phân số: \\\\frac{tử}{mẫu}  Căn: \\\\sqrt{x}  Mũ: x^2  Chỉ số: v_0  x_{12}\n"
    "  Chữ Hy Lạp: \\\\alpha \\\\beta \\\\gamma \\\\delta \\\\theta \\\\lambda \\\\mu \\\\omega \\\\Omega\n"
    "  \\\\Delta \\\\Phi \\\\Psi  Vector: \\\\vec{v} \\\\vec{F}  Điểm chấm: \\\\cdot\n"
    "  Tích phân: \\\\int  Tổng: \\\\sum  Pi: \\\\pi  Vô cực: \\\\infty\n"
    "  Dấu ×: \\\\times  Dấu ÷: \\\\div  Dấu ±: \\\\pm  Dấu ≈: \\\\approx\n\n"
    "Đơn vị giữ nguyên NGOÀI $ $: m, kg, s, N, J, W, Hz, Ω, μF, kV, cm, mm, rad, mol, K\n\n"
    "Ví dụ đúng:\n"
    '{"sections":[\n'
    '{"type":"trac_nghiem_lua_chon","label":"PHẦN I. TRẮC NGHIỆM LỰA CHỌN","intro":"","questions":[\n'
    '{"number":1,"text":"Một vật có khối lượng $m = 2$ kg chuyển động với $v_0 = 3$ m/s. Sau khi đi quãng đường $S = 4$ m thì dừng lại. Lực ma sát có độ lớn:","options":["A. 1,5 N","B. 2,25 N","C. 3 N","D. 4,5 N"],"sub_items":[]},\n'
    '{"number":2,"text":"Gia tốc rơi tự do $g = 10$ m/s$^2$. Thời gian rơi từ độ cao $h = 20$ m:","options":["A. $\\\\sqrt{2}$ s","B. 2 s","C. $2\\\\sqrt{2}$ s","D. 4 s"],"sub_items":[]}\n'
    ']},\n'
    '{"type":"dung_sai","label":"PHẦN II. ĐÚNG – SAI","intro":"","questions":[\n'
    '{"number":1,"text":"Cho vật dao động điều hòa với $\\\\omega = 10$ rad/s, biên độ $A = 5$ cm.","options":[],"sub_items":["a) Chu kỳ $T = \\\\frac{2\\\\pi}{\\\\omega} = 0,628$ s","b) Vận tốc cực đại $v_{max} = \\\\omega A = 0,5$ m/s","c) Gia tốc cực đại $a_{max} = \\\\omega^2 A = 5$ m/s$^2$","d) Năng lượng dao động $E = \\\\frac{1}{2}m\\\\omega^2 A^2$"]}\n'
    ']}\n'
    "]}\n\n"
    "Trả về JSON hợp lệ, không có gì khác:"
)


def _render_pdf_pages(pdf_path: str, max_pages: int = 16, dpi: int = 200) -> List[PILImage.Image]:
    """Render từng trang PDF thành PIL Image. DPI cao hơn = nhận diện công thức tốt hơn."""
    import fitz
    images = []
    pdf_doc = None
    try:
        pdf_doc = fitz.open(pdf_path)
        scale = dpi / 72
        for i in range(min(len(pdf_doc), max_pages)):
            page = pdf_doc[i]
            mat = fitz.Matrix(scale, scale)
            pix = page.get_pixmap(matrix=mat)
            pil_img = PILImage.open(io.BytesIO(pix.tobytes('png')))
            images.append(pil_img)
    except Exception:
        pass
    finally:
        if pdf_doc is not None:
            try:
                pdf_doc.close()
            except Exception:
                pass
    return images


def _call_gemini_vision_json(page_images: List[PILImage.Image], gemini_key: str) -> str:
    """Gọi Gemini Vision, request JSON output. Thử mime_type trước, fallback text."""
    import google.generativeai as genai
    genai.configure(api_key=gemini_key)

    parts = [VISION_JSON_PROMPT] + page_images
    last_err = None
    # Thứ tự ưu tiên: flash (cân bằng chất lượng/tốc độ) → lite (nhanh hơn) → 2.5 (chậm nhất)
    for model_name in ('gemini-2.0-flash', 'gemini-2.5-flash', 'gemini-2.0-flash-lite'):
        for use_mime in (True, False):
            try:
                model = genai.GenerativeModel(model_name)
                cfg = {'max_output_tokens': 32768, 'temperature': 0.1}
                if use_mime:
                    cfg['response_mime_type'] = 'application/json'
                resp = model.generate_content(parts, generation_config=cfg)
                text = resp.text
                if text and len(text.strip()) > 10:
                    return text
            except Exception as e:
                last_err = e
                err_s = str(e)
                if '429' in err_s or 'quota' in err_s.lower():
                    import time; time.sleep(5)
                    break   # thử model tiếp theo
                if use_mime and ('mime' in err_s.lower() or 'unsupported' in err_s.lower()
                                 or 'response_mime_type' in err_s.lower()):
                    continue  # thử lại không có mime_type
                # Lỗi khác: thử model tiếp theo
                break
    raise RuntimeError(f'Gemini Vision thất bại: {last_err}')


def _repair_latex_backslashes(text: str) -> str:
    # Sửa backslash trong JSON string khi Gemini viết \frac thay vì \\frac.
    # Vấn đề: \f \n \r \t \b là JSON escape ĐỒNG THỜI là prefix LaTeX phổ biến.
    # Chiến lược: double ALL single backslashes (kể cả trong \"...\").
    # - \" → \\" (đây là vấn đề — sẽ break string delimiter)
    # - \\ → \\\\ (OK)
    # Cách an toàn: chỉ double backslash TRONG string values, giữ nguyên structural chars.
    # Simplified approach: replace ALL \ not followed by \ with \\,
    # EXCEPT \" (escaped quote — must keep) and \\ (already doubled).
    result = []
    i = 0
    while i < len(text):
        c = text[i]
        if c == '\\':
            if i + 1 < len(text):
                next_c = text[i + 1]
                if next_c == '\\':
                    # Already doubled backslash → keep as \\
                    result.append('\\\\')
                    i += 2
                elif next_c == '"':
                    # Escaped quote → keep as \"
                    result.append('\\"')
                    i += 2
                else:
                    # All other: single backslash → double it
                    # This covers \frac \nu \rho \tau \begin \n \t \r \f \b etc.
                    # For physics text: all these are LaTeX, not actual control chars
                    result.append('\\\\')
                    i += 1
            else:
                result.append('\\\\')
                i += 1
        else:
            result.append(c)
            i += 1
    return ''.join(result)


def _has_latex_damage(data: dict) -> bool:
    """Kiểm tra kết quả JSON có chứa control chars do LaTeX bị decode sai không.
    Ví dụ: \frac → form_feed + rac, \nu → newline + u (đều vô nghĩa trong text Vật lý).
    Các chars đáng ngờ: form_feed (0x0C), backspace (0x08), vertical tab (0x0B).
    """
    try:
        dump = json.dumps(data, ensure_ascii=False)
        # Form feed (\f=0x0C) and backspace (\b=0x08) are very suspicious in physics
        return '\x0c' in dump or '\x08' in dump or '\x0b' in dump
    except Exception:
        return False


def _extract_and_parse_json(raw: str) -> Optional[dict]:
    """
    Parse JSON từ response Gemini với 5 chiến lược repair.
    Phát hiện LaTeX bị decode sai (control chars) và thử repair lại.
    """
    if not raw or not raw.strip():
        return None

    # Bỏ wrapper ```json ... ``` nếu có
    m = re.search(r'```(?:json)?\s*([\s\S]+?)\s*```', raw)
    text = m.group(1) if m else raw

    # Tìm { ... } ngoài cùng
    start, end = text.find('{'), text.rfind('}')
    if start == -1 or end == -1:
        # Fallback: thử toàn bộ text
        start, end = raw.find('{'), raw.rfind('}')
        if start == -1 or end == -1:
            return None
        text = raw[start:end + 1]
    else:
        text = text[start:end + 1]

    # --- Chiến lược 1: Parse trực tiếp ---
    try:
        result = json.loads(text)
        # Kiểm tra nếu parse thành công nhưng có LaTeX bị hỏng
        if not _has_latex_damage(result):
            return result
        # Có damage → thử repair (fall through)
    except json.JSONDecodeError:
        pass

    # --- Chiến lược 2: Full repair (double ALL single backslashes) ---
    # Xử lý đúng các trường hợp: \frac \nu \rho \tau \begin \n \t \r \f \b
    try:
        fixed_full = _repair_latex_backslashes(text)
        result = json.loads(fixed_full)
        if not _has_latex_damage(result):
            return result
    except (json.JSONDecodeError, Exception):
        pass

    # --- Chiến lược 3: Regex repair (an toàn hơn, không sửa \f \n \r \t \b) ---
    try:
        fixed_regex = re.sub(r'(?<!\\)\\(?!["\\/bfnrtu])', r'\\\\', text)
        result = json.loads(fixed_regex)
        if not _has_latex_damage(result):
            return result
    except json.JSONDecodeError:
        pass

    # --- Chiến lược 4: Full repair + strip control chars ---
    try:
        cleaned = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]', '',
                         _repair_latex_backslashes(text))
        result = json.loads(cleaned)
        if result:
            return result
    except (json.JSONDecodeError, Exception):
        pass

    # --- Chiến lược 5: Strip control chars từ text gốc ---
    try:
        cleaned_orig = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]', '', text)
        result = json.loads(cleaned_orig)
        if result:
            return result
    except json.JSONDecodeError:
        return None

    return None


# Bảng Markdown trong text câu: ≥2 dòng liên tiếp dạng | ... |
_MD_TABLE_RE = re.compile(r'(?:^|\n)[ \t]*\|[^\n]*\|[ \t]*\n[ \t]*\|[^\n]*\|')
# Ảnh bảng cắt từ PDF (đặt tên trong _extract_table_images)
_TBL_IMG_RE = re.compile(r'_tbl\d+\.png$')


def _json_to_document(data: dict, title: str,
                      img_by_qnum: dict, img_by_order: List) -> Document:
    """Chuyển JSON dict từ Gemini thành Document model."""
    doc = Document()
    doc.title = data.get('title', title)
    attached = set()   # filename ảnh đã gắn vào câu (để biết ảnh nào còn sót)

    for sec_data in (data.get('sections') or []):
        sec = Section(
            type=sec_data.get('type', 'khac'),
            label=sec_data.get('label', ''),
            intro=sec_data.get('intro', '') or '',
            is_theory=(sec_data.get('type') == 'ly_thuyet'),
        )
        for qi, q_data in enumerate(sec_data.get('questions') or []):
            try:
                q = Question(
                    number=int(q_data.get('number') or (qi + 1)),
                    text=(q_data.get('text') or '').strip(),
                    options=[str(o) for o in (q_data.get('options') or [])],
                    sub_items=[str(s) for s in (q_data.get('sub_items') or [])],
                )
            except (ValueError, TypeError):
                continue
            if not q.text:
                continue
            # Gắn ảnh: theo số câu, fallback theo thứ tự
            if q.number in img_by_qnum:
                q.images = img_by_qnum[q.number]
            else:
                for (stype, order, imgs) in img_by_order:
                    if stype == sec.type and order == qi:
                        q.images = imgs
                        break
            # Chống trùng bảng: AI đã trả bảng Markdown trong text → bỏ ảnh bảng
            # cắt từ PDF của câu này (đánh dấu attached để không rơi vào leftovers)
            if q.images and _MD_TABLE_RE.search(q.text):
                kept = []
                for im in q.images:
                    if _TBL_IMG_RE.search(im.filename):
                        attached.add(im.filename)
                    else:
                        kept.append(im)
                q.images = kept
            for im in q.images:
                attached.add(im.filename)
            sec.questions.append(q)
        doc.sections.append(sec)

    # Ảnh đã trích từ PDF nhưng KHÔNG khớp câu nào (AI đánh số/chia phần khác bộ trích ảnh)
    # → KHÔNG để mất: đưa xuống cuối (giống hành vi cũ "ảnh ở chân trang").
    leftovers, seen = [], set()
    for imgs in img_by_qnum.values():
        for im in imgs:
            if im.filename not in attached and im.filename not in seen:
                seen.add(im.filename)
                leftovers.append(im)
    if leftovers:
        last_q = next((s.questions[-1] for s in reversed(doc.sections) if s.questions), None)
        if last_q:
            last_q.images = list(last_q.images) + leftovers
        else:
            extra = Section(type='khac', label='Hình ảnh trong đề')
            extra.questions.append(Question(number=1, text='(Hình minh hoạ)', images=leftovers))
            doc.sections.append(extra)

    if not doc.sections or not any(s.questions or s.intro for s in doc.sections):
        raise ValueError('JSON không có nội dung hợp lệ')

    _infer_doc_type(doc)
    return doc


def import_with_vision(filepath: str, gemini_key: str) -> Document:
    """
    Import dùng Gemini Vision → JSON structured output.
    Không dùng regex parser — Gemini trực tiếp trả cấu trúc JSON.
    Fallback sang import thông thường nếu JSON parse thất bại.
    """
    title = os.path.splitext(os.path.basename(filepath))[0]

    # Bước 1: Import thông thường để lấy ảnh (để gắn lại sau)
    img_by_qnum: dict = {}
    img_by_order: List = []
    fallback_doc: Optional[Document] = None
    try:
        fallback_doc = import_file(filepath)
        for sec in fallback_doc.sections:
            for qi, q in enumerate(sec.questions):
                if q.images:
                    img_by_qnum[q.number] = list(q.images)
                    img_by_order.append((sec.type, qi, list(q.images)))
    except Exception:
        pass

    # Bước 2: Render pages thành PIL Images
    work_path = filepath
    tmp_pdf: Optional[str] = None
    if filepath.lower().endswith(('.docx', '.doc')):
        try:
            from docx2pdf import convert
            tmp_pdf = os.path.join(IMG_DIR, f'tmp_{uuid.uuid4().hex[:8]}.pdf')
            convert(filepath, tmp_pdf)
            if os.path.exists(tmp_pdf) and os.path.getsize(tmp_pdf) > 0:
                work_path = tmp_pdf
        except Exception:
            pass

    page_images = _render_pdf_pages(work_path)
    if tmp_pdf and os.path.exists(tmp_pdf):
        try: os.remove(tmp_pdf)
        except Exception: pass

    if not page_images:
        if fallback_doc:
            return fallback_doc
        raise RuntimeError('Không thể render file thành ảnh.')

    # Bước 3: Gọi Gemini Vision → nhận JSON
    raw_response = _call_gemini_vision_json(page_images, gemini_key)

    # Bước 4: Parse JSON → Document
    data = _extract_and_parse_json(raw_response)
    if data is None:
        if fallback_doc:
            return fallback_doc
        raise RuntimeError('Không parse được JSON từ Gemini.')

    try:
        return _json_to_document(data, title, img_by_qnum, img_by_order)
    except Exception:
        if fallback_doc:
            return fallback_doc
        raise


# ── Import qua 9Router (Gemini 3 Flash) — chạy SONG SONG theo cụm trang ──
#
# Vì sao theo cụm + giới hạn luồng: Gemini CLI (free) qua 9Router bị rate-limit chặt.
# Chia PDF thành các cụm trang, gọi song song TỐI ĐA 2 luồng + retry/backoff khi 429.
# Gộp kết quả: nối các phần cùng loại liền nhau → phần bị cắt ngang trang vẫn liền mạch.

# ĐO THỰC TẾ: Gemini CLI (free) qua 9Router xử lý TUẦN TỰ request cùng tài khoản → chia trang
# chạy "song song" thực chất nối đuôi (2 trang = 72s thay vì 40s) + làm vỡ đánh số/ghép phần khi
# câu bị cắt ngang trang. Độ trễ ~40s là SÀN của model, không chia nhỏ được trên gói free.
# → Để BATCH lớn = gọi 1 lần cho cả đề: cùng tốc độ Gemini API trực tiếp nhưng model mới hơn,
#   đọc công thức/hình tốt hơn, KHÔNG vỡ cấu trúc. (Hạ BATCH xuống nếu sau này dùng gói trả phí
#   chạy song song thật.)
P2W_VISION_BATCH = 24     # ≥ max trang render (16) → thực tế luôn 1 call
P2W_VISION_WORKERS = 3    # chỉ dùng khi BATCH nhỏ (gói trả phí); free thì vô hiệu vì serialize


def _pil_to_jpeg_b64(img: PILImage.Image, max_side: int = 2000, quality: int = 85) -> str:
    """PIL → JPEG base64 (nhẹ hơn PNG nhiều → upload nhanh, ít chạm giới hạn 9Router)."""
    import base64
    im = img.convert('RGB')
    w, h = im.size
    if max(w, h) > max_side:
        scale = max_side / max(w, h)
        im = im.resize((int(w * scale), int(h * scale)), PILImage.LANCZOS)
    buf = io.BytesIO()
    im.save(buf, format='JPEG', quality=quality)
    return base64.b64encode(buf.getvalue()).decode()


def _call_9router_vision_json(page_images: List[PILImage.Image], niner_key: str,
                              niner_url: str = 'http://localhost:20128/v1',
                              model: str = 'ag/gemini-3.5-flash-low',
                              max_retries: int = 3) -> str:
    """Gọi Gemini 3 Flash qua 9Router (OpenAI-compatible) với ảnh base64 → trả raw text JSON.
    Retry/backoff khi gặp 429."""
    import time as _t
    from openai import OpenAI
    client = OpenAI(api_key=niner_key or '9router', base_url=niner_url,
                    timeout=180, max_retries=0)
    content = [{"type": "text", "text": VISION_JSON_PROMPT}]
    for img in page_images:
        content.append({"type": "image_url", "image_url": {
            "url": "data:image/jpeg;base64," + _pil_to_jpeg_b64(img)}})
    last_err = None
    for attempt in range(max_retries):
        try:
            resp = client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": content}],
                temperature=0.1, max_tokens=16000)
            return (resp.choices[0].message.content or '').strip()
        except Exception as e:
            last_err = e
            es = str(e).lower()
            if '429' in es or 'rate' in es or 'quota' in es or 'resource' in es:
                _t.sleep(2 ** attempt + 1)   # 2s, 3s, 5s
                continue
            raise
    raise RuntimeError(f'9Router Vision thất bại sau {max_retries} lần: {last_err}')


def _merge_vision_sections(section_lists: List[list]) -> list:
    """Gộp sections từ nhiều batch (theo thứ tự trang). Nối phần cùng type + label liền nhau."""
    merged: list = []
    for sections in section_lists:
        for sec in (sections or []):
            if not isinstance(sec, dict):
                continue
            if merged and sec.get('type') == merged[-1].get('type') and (
                    not sec.get('label') or sec.get('label') == merged[-1].get('label')):
                merged[-1].setdefault('questions', []).extend(sec.get('questions') or [])
                if sec.get('intro') and not merged[-1].get('intro'):
                    merged[-1]['intro'] = sec['intro']
            else:
                merged.append(sec)
    return merged


def import_with_vision_9router(filepath: str, niner_key: str,
                               niner_url: str = 'http://localhost:20128/v1',
                               model: str = 'ag/gemini-3.5-flash-low',
                               gemini_key: str = '') -> Document:
    """Import PDF/DOCX bằng Gemini 3 Flash qua 9Router, render trang → gọi SONG SONG theo cụm.
    Fallback: Gemini API trực tiếp (nếu có key) → import_file."""
    from concurrent.futures import ThreadPoolExecutor
    title = os.path.splitext(os.path.basename(filepath))[0]

    # Bước 1: import thường để lấy ảnh (gắn lại sau)
    img_by_qnum: dict = {}
    img_by_order: List = []
    fallback_doc: Optional[Document] = None
    try:
        fallback_doc = import_file(filepath)
        for sec in fallback_doc.sections:
            for qi, q in enumerate(sec.questions):
                if q.images:
                    img_by_qnum[q.number] = list(q.images)
                    img_by_order.append((sec.type, qi, list(q.images)))
    except Exception:
        pass

    # Bước 2: render pages → PIL
    work_path = filepath
    tmp_pdf: Optional[str] = None
    if filepath.lower().endswith(('.docx', '.doc')):
        try:
            from docx2pdf import convert
            tmp_pdf = os.path.join(IMG_DIR, f'tmp_{uuid.uuid4().hex[:8]}.pdf')
            convert(filepath, tmp_pdf)
            if os.path.exists(tmp_pdf) and os.path.getsize(tmp_pdf) > 0:
                work_path = tmp_pdf
        except Exception:
            pass
    page_images = _render_pdf_pages(work_path)
    if tmp_pdf and os.path.exists(tmp_pdf):
        try: os.remove(tmp_pdf)
        except Exception: pass
    if not page_images:
        if fallback_doc:
            return fallback_doc
        raise RuntimeError('Không render được file thành ảnh.')

    # Bước 3: chia cụm trang + gọi SONG SONG (giới hạn luồng)
    batches = [page_images[i:i + P2W_VISION_BATCH]
               for i in range(0, len(page_images), P2W_VISION_BATCH)]

    def _do_batch(batch):
        raw = _call_9router_vision_json(batch, niner_key, niner_url, model)
        data = _extract_and_parse_json(raw)
        return (data or {}).get('sections') or []

    section_lists: List[list] = []
    try:
        if len(batches) == 1:
            section_lists = [_do_batch(batches[0])]
        else:
            with ThreadPoolExecutor(max_workers=P2W_VISION_WORKERS) as ex:
                section_lists = list(ex.map(_do_batch, batches))
    except Exception as e:
        print(f'[9Router Vision fallback] {e}', flush=True)
        # Fallback: Gemini API trực tiếp (1 call) nếu có key
        if gemini_key:
            try:
                return import_with_vision(filepath, gemini_key)
            except Exception:
                pass
        if fallback_doc:
            return fallback_doc
        raise

    merged_sections = _merge_vision_sections(section_lists)
    if not merged_sections:
        if gemini_key:
            try:
                return import_with_vision(filepath, gemini_key)
            except Exception:
                pass
        if fallback_doc:
            return fallback_doc
        raise RuntimeError('9Router Vision không trả nội dung hợp lệ.')

    try:
        return _json_to_document({'title': title, 'sections': merged_sections},
                                 title, img_by_qnum, img_by_order)
    except Exception:
        if fallback_doc:
            return fallback_doc
        raise


# ── Import ảnh trực tiếp → Gemini Vision → JSON ──────────────────

def import_image_file(filepath: str, gemini_key: str = '') -> 'Document':
    """Import file ảnh (JPG/PNG/...) bằng Gemini Vision → JSON → Document.
    Ảnh được gửi thẳng tới Vision API, không cần convert.
    """
    title = os.path.splitext(os.path.basename(filepath))[0]
    try:
        with PILImage.open(filepath) as _im:
            img = _im.convert('RGB')   # convert tạo bản copy — đóng file gốc ngay
        raw = _call_gemini_vision_json([img], gemini_key)
        data = _extract_and_parse_json(raw)
        if data:
            # Lưu ảnh gốc vào IMG_DIR để hiển thị trong editor
            with open(filepath, 'rb') as f:
                raw_bytes = f.read()
            ext = os.path.splitext(filepath)[1].lstrip('.')
            fname = sharpen_image(raw_bytes, f'{uuid.uuid4().hex[:8]}.png')
            # Gắn ảnh vào câu đầu tiên nếu chưa có
            doc = _json_to_document(data, title, {}, [])
            if doc.sections and doc.sections[0].questions:
                q0 = doc.sections[0].questions[0]
                if not q0.images:
                    q0.images.append(Image(filename=fname))
            return doc
    except Exception as e:
        print(f'[import_image_file] {e}', flush=True)
    return import_image_as_document(filepath)


def import_image_as_document(filepath: str) -> 'Document':
    """Fallback: tạo document chứa ảnh (không dùng AI)."""
    title = os.path.splitext(os.path.basename(filepath))[0]
    doc = Document()
    doc.title = title
    sec = Section(type='khac', label='Nội dung từ ảnh')
    try:
        with open(filepath, 'rb') as f:
            fname = sharpen_image(f.read(), f'{uuid.uuid4().hex[:8]}.png')
        q = Question(number=1, text='(Nhấn [Giải toàn đề] để AI nhận diện nội dung ảnh)')
        q.images.append(Image(filename=fname))
        sec.questions.append(q)
    except Exception:
        sec.intro = f'[Ảnh: {title}]'
    doc.sections.append(sec)
    return doc


# ── Pandoc pipeline: DOCX → Markdown → Gemini text → JSON ────────
#
# Pandoc chuyển .docx → .md với công thức OMML/MathType → LaTeX $...$
# Gemini text API (không phải Vision) phân tích markdown → JSON
# Lợi ích: không mất công thức, không dùng ảnh, rẻ hơn Vision ~10x

PANDOC_CANDIDATES = ['pandoc']
if sys.platform == 'win32':
    _user = os.environ.get('USERNAME', '')
    PANDOC_CANDIDATES += [
        fr'C:\Users\{_user}\AppData\Local\Pandoc\pandoc.exe',
        r'C:\Program Files\Pandoc\pandoc.exe',
        r'C:\Program Files (x86)\Pandoc\pandoc.exe',
    ]


def find_pandoc() -> Optional[str]:
    """Tìm pandoc. Return đường dẫn executable hoặc None."""
    for cmd in PANDOC_CANDIDATES:
        try:
            r = subprocess.run([cmd, '--version'], capture_output=True, timeout=5,
                               creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            if r.returncode == 0:
                return cmd
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
            pass
    return None


def pandoc_docx_to_markdown(docx_path: str, pandoc_cmd: str) -> Tuple[str, List[str]]:
    """Dùng Pandoc convert DOCX → Markdown. Trích xuất ảnh ra IMG_DIR.
    Returns: (markdown_text, [image_filenames_in_order])
    """
    work_dir = os.path.join(IMG_DIR, f'pandoc_{uuid.uuid4().hex[:8]}')
    os.makedirs(work_dir, exist_ok=True)
    md_path = os.path.join(work_dir, 'doc.md')

    try:
        r = subprocess.run([
            pandoc_cmd, docx_path,
            '-f', 'docx',
            '-t', 'markdown+tex_math_dollars',
            '--wrap=none',
            '--extract-media', work_dir,
            '-o', md_path,
        ], capture_output=True, timeout=120, text=True, encoding='utf-8',
           creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))

        if r.returncode != 0:
            raise RuntimeError(f'Pandoc lỗi: {r.stderr[:400]}')

        with open(md_path, encoding='utf-8') as f:
            md_content = f.read()
    finally:
        if os.path.exists(md_path):
            try: os.remove(md_path)
            except Exception: pass

    # Copy ảnh được extract ra IMG_DIR
    img_names: List[str] = []
    media_sub = os.path.join(work_dir, 'media')
    if os.path.isdir(media_sub):
        for fname in sorted(os.listdir(media_sub)):
            src = os.path.join(media_sub, fname)
            if os.path.isfile(src):
                dst_name = f'{uuid.uuid4().hex[:8]}_{fname}'
                _shutil.copy2(src, os.path.join(IMG_DIR, dst_name))
                img_names.append(dst_name)

    try: _shutil.rmtree(work_dir)
    except Exception: pass

    return md_content, img_names


_KATEX_INJECT = """
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/katex@0.16.10/dist/katex.min.css">
<script defer src="https://cdn.jsdelivr.net/npm/katex@0.16.10/dist/katex.min.js"></script>
<script defer src="https://cdn.jsdelivr.net/npm/katex@0.16.10/dist/contrib/auto-render.min.js"
  onload="renderMathInElement(document.body,{delimiters:[
    {left:'$$',right:'$$',display:true},
    {left:'$',right:'$',display:false},
    {left:'\\\\(',right:'\\\\)',display:false},
    {left:'\\\\[',right:'\\\\]',display:true}
  ]})"></script>
<style>
  body{font-family:'Times New Roman',serif;font-size:14pt;line-height:1.6;
       max-width:860px;margin:0 auto;padding:16px 24px;color:#111;}
  h1,h2,h3{color:#0f3d8e;} table{border-collapse:collapse;width:100%;}
  td,th{border:1px solid #ccc;padding:4px 8px;}
  img{max-width:100%;height:auto;}
</style>
"""


def pandoc_docx_to_html(docx_path: str, pandoc_cmd: str, out_dir: str) -> Optional[str]:
    """Convert DOCX → HTML để hiển thị panel trái. Inject KaTeX CDN để render công thức."""
    out_name = f'{uuid.uuid4().hex}.html'
    out_path = os.path.join(out_dir, out_name)

    # Thử 3 bộ tham số theo thứ tự ưu tiên giảm dần
    arg_sets = [
        [pandoc_cmd, docx_path, '-f', 'docx', '-t', 'html5',
         '--standalone', '--embed-resources', '-o', out_path],
        [pandoc_cmd, docx_path, '-f', 'docx', '-t', 'html5',
         '--standalone', '--self-contained', '-o', out_path],
        [pandoc_cmd, docx_path, '-f', 'docx', '-t', 'html5',
         '--standalone', '-o', out_path],
    ]

    for args in arg_sets:
        try:
            r = subprocess.run(args, capture_output=True, timeout=120,
                               text=True, encoding='utf-8',
                               creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            if r.returncode == 0 and os.path.exists(out_path) and os.path.getsize(out_path) > 0:
                _inject_katex(out_path)
                return out_name
        except Exception:
            pass

    return None


def _inject_katex(html_path: str):
    """Chèn KaTeX CDN + charset UTF-8 vào <head> của file HTML nếu chưa có."""
    try:
        with open(html_path, encoding='utf-8') as f:
            html = f.read()
        # Thêm charset nếu thiếu
        if '<meta charset' not in html.lower():
            html = html.replace('<head>', '<head>\n<meta charset="utf-8">', 1)
        # Thêm lang vi nếu thiếu
        if 'lang=' not in html[:200].lower():
            html = html.replace('<html', '<html lang="vi"', 1)
        if 'katex' in html.lower():
            return  # Đã có KaTeX
        html = html.replace('</head>', _KATEX_INJECT + '</head>', 1)
        if '</head>' not in html:
            html = '<head><meta charset="utf-8">' + _KATEX_INJECT + '</head>' + html
        with open(html_path, 'w', encoding='utf-8') as f:
            f.write(html)
    except Exception:
        pass


def _map_images_to_questions_from_md(md: str, img_names: List[str]) -> dict:
    """Map image → câu hỏi dựa trên vị trí trong markdown.
    Returns: {question_number: [Image, ...]}
    """
    if not img_names:
        return {}

    # Tìm vị trí câu hỏi (Câu N / câu N)
    q_positions: List[Tuple[int, int]] = []
    for m in re.finditer(
        r'(?:^|\n)[^\S\n]*\*?\*?(?:Câu|câu|C)\s*(\d+)[^0-9]',
        md, re.IGNORECASE
    ):
        try:
            q_positions.append((int(m.group(1)), m.start()))
        except ValueError:
            pass

    # Tìm vị trí ảnh trong markdown: ![...](path)
    img_refs: List[Tuple[str, int]] = []
    for m in re.finditer(r'!\[[^\]]*\]\(([^)]+)\)', md):
        basename = os.path.basename(m.group(1).replace('\\', '/'))
        img_refs.append((basename, m.start()))

    if not img_refs or not q_positions:
        # Nếu không map được: gắn lần lượt vào câu 1, 2, 3...
        result = {}
        for i, img_name in enumerate(img_names):
            qnum = i + 1
            if qnum not in result:
                result[qnum] = []
            result[qnum].append(Image(filename=img_name))
        return result

    # Dùng thứ tự trong md để map ảnh → câu hỏi
    orig_to_saved: dict = {}
    for img_name in img_names:
        # img_name = "abcd1234_image1.png" → match với image1.png trong md
        parts = img_name.split('_', 1)
        orig_basename = parts[1] if len(parts) == 2 else img_name
        orig_to_saved[orig_basename] = img_name
        # Also match without extension prefix differences
        name_no_ext = os.path.splitext(orig_basename)[0]
        orig_to_saved[name_no_ext] = img_name

    result: dict = {}
    for orig_base, img_pos in img_refs:
        # Tìm câu hỏi cuối cùng trước vị trí ảnh này
        assoc = None
        for qnum, qpos in q_positions:
            if qpos <= img_pos:
                assoc = qnum
            else:
                break

        # Nếu không tìm được câu nào trước ảnh → gắn vào câu đầu tiên
        if assoc is None and q_positions:
            assoc = q_positions[0][0]

        if assoc is not None:
            saved = orig_to_saved.get(orig_base)
            if not saved:
                # Fuzzy match
                for orig_k, saved_v in orig_to_saved.items():
                    if orig_k in orig_base or orig_base in orig_k:
                        saved = saved_v
                        break
            if saved:
                if assoc not in result:
                    result[assoc] = []
                result[assoc].append(Image(filename=saved))

    return result


MARKDOWN_JSON_PROMPT = (
    "Bạn phân tích nội dung đề thi Vật lý THPT từ Markdown đã convert bằng Pandoc.\n"
    "Công thức LaTeX ĐÃ có dạng $...$ đúng chuẩn, KHÔNG cần thay đổi gì.\n\n"
    "Trả về JSON hợp lệ DUY NHẤT, KHÔNG có text hay ```json``` bên ngoài.\n\n"
    'Schema:\n'
    '{"sections":[{"type":"<loại>","label":"<tiêu đề phần>","intro":"<lý thuyết nếu có>","questions":['
    '{"number":<int>,"text":"<nội dung câu, GIỮ NGUYÊN LaTeX $...$>","options":["A. ...","B. ...","C. ...","D. ..."],'
    '"sub_items":["a) ...","b) ...","c) ...","d) ..."]}]}]}\n\n'
    "type: trac_nghiem_lua_chon | dung_sai | tra_loi_ngan | tu_luan | ly_thuyet | khac\n"
    "options: chỉ điền khi có A/B/C/D. sub_items: chỉ điền khi có a/b/c/d\n\n"
    "BẢNG SỐ LIỆU: câu hỏi có bảng Markdown (các dòng | ... |) → GIỮ NGUYÊN bảng\n"
    'trong "text" của câu, mỗi hàng bảng 1 dòng (dùng \\n) — KHÔNG bỏ bảng, KHÔNG\n'
    "gộp bảng thành 1 dòng chữ. Bảng có cột Đ/S (Đúng/Sai) là câu đúng-sai → đưa\n"
    "từng nhận định vào sub_items thay vì giữ bảng.\n\n"
    "QUY TẮC QUAN TRỌNG cho JSON:\n"
    "- LaTeX trong text: backslash PHẢI viết DOUBLE trong JSON string\n"
    "- Ví dụ: $\\\\frac{F}{m}$ (hai backslash trong JSON → một backslash trong giá trị)\n"
    "- Ví dụ: $\\\\sqrt{2aS}$  $\\\\omega$  $\\\\Delta$  $\\\\vec{v}$\n"
    "- ĐÚNG: \"text\": \"Gia toc $a = \\\\frac{F}{m}$, $v^2 = v_0^2 + 2aS$\"\n"
    "- SAI:  \"text\": \"Gia toc $a = \\frac{F}{m}$\"  ← một backslash → JSON lỗi\n\n"
    "Bỏ qua: header trường, tên đề, mã đề, ngày tháng, câu lệnh ảnh ![...]\n\n"
    "Nội dung Markdown:\n"
)


def _call_gemini_text_json(md_content: str, gemini_key: str) -> str:
    """Gọi Gemini TEXT API (không phải Vision) để parse markdown → JSON.
    Nhanh hơn, rẻ hơn, không bị giới hạn ảnh.
    """
    import google.generativeai as genai
    genai.configure(api_key=gemini_key)

    # Giới hạn độ dài markdown (Gemini flash hỗ trợ 1M tokens nhưng để an toàn)
    max_chars = 80000
    if len(md_content) > max_chars:
        md_content = md_content[:max_chars] + '\n\n[... nội dung còn lại bị cắt bớt ...]'

    prompt = MARKDOWN_JSON_PROMPT + md_content + '\n\nTrả về JSON:'
    last_err = None

    for model_name in ('gemini-2.0-flash', 'gemini-2.5-flash', 'gemini-2.0-flash-lite'):
        for use_mime in (True, False):
            try:
                model = genai.GenerativeModel(model_name)
                cfg = {'max_output_tokens': 32768, 'temperature': 0.1}
                if use_mime:
                    cfg['response_mime_type'] = 'application/json'
                resp = model.generate_content(prompt, generation_config=cfg)
                text = resp.text
                if text and len(text.strip()) > 10:
                    return text
            except Exception as e:
                last_err = e
                err_s = str(e)
                if '429' in err_s or 'quota' in err_s.lower():
                    import time; time.sleep(5)
                    break
                if use_mime and ('mime' in err_s.lower() or 'unsupported' in err_s.lower()
                                 or 'response_mime_type' in err_s.lower()):
                    continue
                break

    raise RuntimeError(f'Gemini text API thất bại: {last_err}')


def import_docx_pandoc(filepath: str, gemini_key: str = '') -> Tuple[Document, Optional[str]]:
    """Import DOCX dùng Pandoc pipeline:
    1. Pandoc: .docx → markdown (LaTeX giữ nguyên) + HTML (để hiển thị)
    2. Gemini text API: markdown → JSON → Document
    Returns: (Document, html_filename_for_display)
    """
    pandoc_cmd = find_pandoc()
    if not pandoc_cmd:
        raise RuntimeError('Pandoc chưa được cài đặt. Vào Cài đặt để xem hướng dẫn.')

    title = os.path.splitext(os.path.basename(filepath))[0]
    orig_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(filepath))),
                            'data', 'uploads', 'originals')
    os.makedirs(orig_dir, exist_ok=True)

    # Bước 1: Pandoc → markdown + images
    md_content, img_names = pandoc_docx_to_markdown(filepath, pandoc_cmd)

    if not md_content.strip():
        raise ValueError('Pandoc không trích xuất được nội dung từ file.')

    # Map ảnh → câu hỏi
    img_map = _map_images_to_questions_from_md(md_content, img_names)

    # Bước 2: Pandoc → HTML để hiển thị panel trái
    html_name = pandoc_docx_to_html(filepath, pandoc_cmd, orig_dir)

    # Bước 3: Gemini text → JSON → Document
    if gemini_key:
        try:
            raw_json = _call_gemini_text_json(md_content, gemini_key)
            data = _extract_and_parse_json(raw_json)
            if data:
                doc = _json_to_document(data, title, img_map, [])
                return doc, html_name
        except Exception as e:
            print(f'[Gemini text fallback] {e}', flush=True)

    # Fallback: parse markdown trực tiếp bằng regex
    doc = _parse_markdown_as_document(md_content, title, img_map)
    return doc, html_name


def _parse_markdown_as_document(md: str, title: str, img_map: dict) -> Document:
    """Parse pandoc markdown → Document model (không cần Gemini).
    Dùng khi Gemini không khả dụng.
    """
    doc = Document()
    doc.title = title

    # Xoá header trường/đề
    lines = md.splitlines()

    current_sec: Optional[Section] = None
    current_q: Optional[Question] = None
    q_counter = 0

    SECTION_HEADER_RE = re.compile(
        r'(?:PH[ÀẦầà]N|ph[àầ]n|Phan|phan|PHAN)\s*[IVXivx1-9]+|'
        r'TR[ÁẮắá]C\s*NGHI[EỆệe]M|tr[áắ]c\s*nghi[eệ]m|TRAC\s*NGHIEM|trac\s*nghiem|'
        r'[ĐD][ÚU]NG.*SAI|[đd][úu]ng.*sai|DUNG.*SAI|dung.*sai|'
        r'T[ỰU]\s*LU[ẬA]N|t[ựu]\s*lu[aậ]n|TU\s*LUAN|tu\s*luan|'
        r'TR[ẢA]\s*L[ỜO]I\s*NG[ẮA]N|tra\s*loi\s*ngan',
        re.IGNORECASE | re.UNICODE
    )
    QUESTION_RE = re.compile(
        r'^(?:\*{1,2})?(?:C[ÂÂâ]u|Cau|cau)\s*(\d+)[\s.):]*\*{0,2}\s*(.*)',
        re.IGNORECASE | re.UNICODE
    )
    OPTION_RE = re.compile(
        r'^(?:\s*[-*]\s*)?([A-D])\s*[.)]\s*(.*)',
        re.UNICODE
    )
    SUBITEM_RE = re.compile(
        r'^(?:\s*[-*]\s*)?([a-d])\s*\\?[.)]\s*(.*)',  # \\? handles Pandoc-escaped \)
        re.UNICODE
    )

    def flush_question():
        nonlocal current_q
        if current_q and current_sec:
            current_sec.questions.append(current_q)
        current_q = None

    def flush_section():
        nonlocal current_sec
        if current_sec and (current_sec.questions or current_sec.intro):
            doc.sections.append(current_sec)
        current_sec = None

    for raw_line in lines:
        line = raw_line.strip()
        # Bỏ qua dòng ảnh (![...](...)  )
        if re.match(r'!\[.*?\]\(.*?\)', line):
            continue
        # Bỏ qua dòng trống hoàn toàn
        if not line:
            continue
        # Bỏ đầu/cuối dấu ** markdown
        clean = re.sub(r'^\*{1,2}|\*{1,2}$', '', line).strip()
        # Bỏ dấu # header
        clean_no_hash = re.sub(r'^#+\s*', '', clean).strip()

        # Section header?
        if SECTION_HEADER_RE.search(clean_no_hash):
            flush_question()
            flush_section()
            stype = detect_section_type(clean_no_hash) or 'khac'
            current_sec = Section(type=stype, label=clean_no_hash)
            q_counter = 0
            continue

        # Câu hỏi?
        m = QUESTION_RE.match(clean)
        if m:
            flush_question()
            if current_sec is None:
                current_sec = Section(type='khac', label='Nội dung')
            q_counter += 1
            qnum = int(m.group(1))
            qtext = m.group(2).strip()
            current_q = Question(number=qnum, text=qtext)
            if qnum in img_map:
                current_q.images = img_map[qnum]
            continue

        # Đáp án A/B/C/D?
        m = OPTION_RE.match(line)
        if m and current_q is not None:
            letter = m.group(1).upper()
            opt_text = m.group(2).strip()
            current_q.options.append(f'{letter}. {opt_text}')
            continue

        # Sub-item a/b/c/d?
        m = SUBITEM_RE.match(line)
        if m and current_q is not None and not current_q.options:
            letter = m.group(1).lower()
            sub_text = m.group(2).strip()
            current_q.sub_items.append(f'{letter}) {sub_text}')
            continue

        # Tiếp nối nội dung câu hỏi
        if current_q is not None:
            if current_q.text:
                current_q.text += ' ' + clean
            else:
                current_q.text = clean
            continue

        # Intro section
        if current_sec is not None and clean:
            current_sec.intro = (current_sec.intro + ' ' + clean).strip() if current_sec.intro else clean

    flush_question()
    flush_section()

    if not doc.sections:
        sec = Section(type='khac', label='Nội dung')
        sec.intro = md[:2000]
        doc.sections.append(sec)

    _infer_doc_type(doc)
    return doc


# ── Entry point ───────────────────────────────────────────────────

def import_file(filepath: str) -> Document:
    ext = os.path.splitext(filepath)[1].lower()
    if ext == '.pdf':
        return import_pdf(filepath)
    elif ext in ('.docx', '.doc'):
        return import_docx(filepath)
    else:
        raise ValueError(f'Định dạng không hỗ trợ: {ext}')
