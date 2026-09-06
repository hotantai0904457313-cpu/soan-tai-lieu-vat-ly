"""
Xuất tài liệu ra PDF (ReportLab) hoặc Word (python-docx).
Header: Logo + GV: [tên giáo viên] + [tên trường] (cài trong trang Cài đặt)
Footer: - X -
"""
import os, re, tempfile, uuid
from typing import Optional
from core.document_model import Document, Section, Question
from config import LOGO_PATH

# Bắt CẢ $$...$$ (display) lẫn $...$ (inline) — display đặt trước để không bị
# cắt nhầm thành 2 mảnh, để sót dấu $ trơ trọi trong PDF.
MATH_RE = re.compile(r'\$\$(.+?)\$\$|\$([^$]+?)\$', re.DOTALL)

# Cache PNG công thức theo (expr, fontsize) — đề dài lặp lại nhiều công thức
# giống nhau, mỗi lần render matplotlib tốn kém (khởi tạo figure ~chục ms).
_latex_cache: dict = {}


def _xml(s: str) -> str:
    """Escape XML special chars cho ReportLab Paragraph."""
    return s.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')


def _render_latex(expr: str, fontsize: float, dpi: int = 180) -> Optional[tuple]:
    """
    Render biểu thức LaTeX thành PNG bằng matplotlib.
    Trả về (png_bytes, width_pt, height_pt) hoặc None nếu lỗi.
    """
    key = (expr, fontsize)
    cached = _latex_cache.get(key)
    if cached is not None:
        return cached
    try:
        # OO API (Figure trực tiếp), KHÔNG dùng pyplot: pyplot giữ state toàn cục
        # không thread-safe — 2 export đồng thời sẽ lẫn hình của nhau.
        from matplotlib.figure import Figure
        from io import BytesIO
        from PIL import Image as PILImage

        fig = Figure(figsize=(0.01, 0.01))
        fig.patch.set_alpha(0)
        fig.text(0.5, 0.5, f'${expr}$',
                 fontsize=fontsize * 1.1, ha='center', va='center',
                 color='black', math_fontfamily='dejavusans')
        buf = BytesIO()
        fig.savefig(buf, format='png', bbox_inches='tight',
                    transparent=True, dpi=dpi, pad_inches=0.04)
        buf.seek(0)
        png = buf.read()
        img = PILImage.open(BytesIO(png))
        w_pt = img.width  * 72 / dpi
        h_pt = img.height * 72 / dpi
        if len(_latex_cache) > 2000:
            _latex_cache.clear()
        _latex_cache[key] = (png, w_pt, h_pt)
        return png, w_pt, h_pt
    except Exception:
        return None


def _math_paragraph(text: str, style, tmp_dir: str, fontsize: float = 11, prefix: str = ''):
    """
    Chuyển text có $...$ thành Paragraph với ảnh inline cho mỗi công thức.
    prefix: HTML markup ghép vào đầu (ví dụ '<i>Lời giải: </i>').
    Trả về list flowable (thường là 1 Paragraph).

    BẤT BIẾN: file PDF là bản học sinh cầm trên tay — không được để lọt bất kỳ
    lệnh LaTeX thô nào. Ba lớp bảo vệ: (1) normalize_latex chuẩn hoá delimiter
    \\(..\\) \\[..\\] và vá dấu $ lẻ; (2) công thức trong $ → ảnh; (3) mọi thứ
    còn sót — kể cả LaTeX AI quên bọc $ — hạ xuống Unicode qua plain_if_latex.
    """
    from reportlab.platypus import Paragraph
    from core.latex_normalize import normalize_latex, latex_to_plain, plain_if_latex

    text = normalize_latex(text or '')

    if '$' not in text:
        # Không có công thức bọc $ — nhưng AI vẫn có thể viết LaTeX trần
        return [Paragraph(prefix + _xml(plain_if_latex(text)), style)]

    markup = prefix
    last = 0
    for m in MATH_RE.finditer(text):
        markup += _xml(plain_if_latex(text[last:m.start()]))
        expr = m.group(1) or m.group(2) or ''
        result = _render_latex(expr, fontsize)
        if result:
            png, w_pt, h_pt = result
            # uuid thay counter toàn cục — counter dùng chung từng làm 2 export
            # đồng thời ghi đè file ảnh của nhau
            fname = os.path.join(tmp_dir, f'math_{uuid.uuid4().hex[:12]}.png')
            with open(fname, 'wb') as f:
                f.write(png)
            # Giữ nguyên tỉ lệ, chiều cao khớp với dòng chữ
            scale = (fontsize * 1.4) / max(h_pt, 1)
            w_s = min(w_pt * scale, 200)   # không rộng quá 200pt
            h_s = h_pt * (w_s / max(w_pt * scale, 1)) * scale
            h_s = max(h_s, fontsize * 1.2)
            markup += f'<img src="{fname}" width="{w_s:.1f}" height="{h_s:.1f}" valign="middle"/>'
        else:
            # Render lỗi → hạ xuống Unicode đọc được, KHÔNG in LaTeX thô
            markup += f'<i>{_xml(latex_to_plain(expr))}</i>'
        last = m.end()
    markup += _xml(plain_if_latex(text[last:]))
    return [Paragraph(markup, style)]

IMG_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'data', 'uploads', 'images')

# ── PDF ───────────────────────────────────────────────────────────

_RASTER_EXT = {'.png', '.jpg', '.jpeg', '.gif', '.bmp'}


def _raster_path(path: str, tmp_dir: str) -> Optional[str]:
    """Đường dẫn ảnh dùng được cho python-docx / reportlab. File .wmf/.emf (212 file
    cũ trong uploads, và cả file .png nhưng ruột là WMF do bản cũ ghi thô) → chuyển
    PNG tạm qua PIL; không mở được → None (bỏ ảnh, không ném — trước đây add_picture
    ném rồi bị nuốt lặng, hình "mất luôn")."""
    if not path or not os.path.exists(path):
        return None
    try:
        from PIL import Image as PILImage
    except Exception:
        return path
    try:
        with PILImage.open(path) as im:
            if im.format in ('PNG', 'JPEG', 'GIF', 'BMP') and \
                    os.path.splitext(path)[1].lower() in _RASTER_EXT:
                return path
    except Exception:
        return None
    try:
        im = PILImage.open(path)
        try:
            im.load(dpi=300)
        except TypeError:
            im.load()
        if im.mode in ('RGBA', 'LA') or (im.mode == 'P' and 'transparency' in im.info):
            im = im.convert('RGBA')
            bg = PILImage.new('RGB', im.size, (255, 255, 255))
            bg.paste(im, mask=im.split()[-1])
            im = bg
        elif im.mode != 'RGB':
            im = im.convert('RGB')
        out = os.path.join(tmp_dir, uuid.uuid4().hex[:8] + '.png')
        im.save(out, 'PNG')
        return out
    except Exception:
        return None


def _split_opts(q):
    """(đề, phương án) — cứu khối A./B./C./D. bị nuốt vào đề (tài liệu nhập trước khi
    vá), bản cục bộ, KHÔNG ghi đè tài liệu đã lưu."""
    from core.importer import split_options_from_text
    return split_options_from_text(q.text, q.options)


def _opt_images_for(q, n_opts):
    """Danh sách hình theo phương án, độ dài đúng n_opts (thiếu → [])."""
    oi = [list(x or []) for x in (getattr(q, 'option_images', None) or [])]
    return (oi + [[] for _ in range(n_opts)])[:n_opts]


def export_pdf(doc: Document, output_path: str, show_solutions: bool = True, settings: dict = None):
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import cm, mm
    from reportlab.lib import colors
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer,
                                    Table, TableStyle, Image as RLImage,
                                    HRFlowable, KeepTogether)
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_JUSTIFY
    import shutil

    if settings is None:
        settings = {}
    teacher  = settings.get('teacher_name', 'Giáo viên')
    school   = settings.get('school_name',  'Trường THPT')

    # Đăng ký font Times New Roman (Windows)
    font_name, font_bold, font_italic = 'Helvetica', 'Helvetica-Bold', 'Helvetica-Oblique'
    _font_reg = [
        ('TimesVN',        r'C:\Windows\Fonts\times.ttf',   'name'),
        ('TimesVN-Bold',   r'C:\Windows\Fonts\timesbd.ttf', 'bold'),
        ('TimesVN-Italic', r'C:\Windows\Fonts\timesi.ttf',  'italic'),
    ]
    for alias, path, role in _font_reg:
        if os.path.exists(path):
            try:
                pdfmetrics.registerFont(TTFont(alias, path))
                if role == 'name':   font_name   = alias
                elif role == 'bold': font_bold   = alias
                elif role == 'italic': font_italic = alias
            except Exception:
                pass

    PAGE_W, PAGE_H = A4
    LEFT = RIGHT = 2.5 * cm
    TOP = BOTTOM = 2.0 * cm
    HEADER_H = 1.5 * cm
    CONTENT_W = PAGE_W - LEFT - RIGHT

    # ── Styles ──────────────────────────────────────────────────────
    NAVY = colors.HexColor('#0F3D8E')
    SOL_BG = colors.HexColor('#EBF4FF')
    SOL_BORDER = colors.HexColor('#93C5FD')

    style_normal  = ParagraphStyle('N',  fontName=font_name,   fontSize=12, leading=19,
                                   spaceAfter=2, alignment=TA_JUSTIFY)
    style_section = ParagraphStyle('S',  fontName=font_bold,   fontSize=13, leading=20,
                                   spaceBefore=10, spaceAfter=4, textColor=NAVY)
    style_q       = ParagraphStyle('Q',  fontName=font_bold,   fontSize=12, leading=19,
                                   spaceBefore=5, spaceAfter=2)
    style_q_math  = ParagraphStyle('QM', fontName=font_name,   fontSize=12, leading=19,
                                   spaceBefore=5, spaceAfter=2)
    style_opt     = ParagraphStyle('O',  fontName=font_name,   fontSize=12, leading=18,
                                   leftIndent=10)
    style_sub     = ParagraphStyle('SB', fontName=font_name,   fontSize=12, leading=18,
                                   leftIndent=14, spaceAfter=1)
    style_sol     = ParagraphStyle('SL', fontName=font_name,   fontSize=11, leading=17,
                                   leftIndent=12, rightIndent=6,
                                   spaceBefore=3, spaceAfter=5,
                                   backColor=SOL_BG,
                                   borderPadding=(4, 8, 4, 8))
    style_title   = ParagraphStyle('T',  fontName=font_bold,   fontSize=14, leading=22,
                                   alignment=TA_CENTER, spaceAfter=6)
    style_intro   = ParagraphStyle('I',  fontName=font_italic, fontSize=12, leading=18,
                                   spaceAfter=4, leftIndent=4)

    # ── Header / Footer ──────────────────────────────────────────────
    def on_page(canvas, pdf_doc):
        canvas.saveState()
        pn = pdf_doc.page
        y_top = PAGE_H - TOP

        # Logo (hình tròn ~1.2cm)
        logo_w = 1.2 * cm
        if os.path.exists(LOGO_PATH):
            try:
                canvas.drawImage(LOGO_PATH,
                                 LEFT, y_top - logo_w,
                                 width=logo_w, height=logo_w,
                                 preserveAspectRatio=True, mask='auto')
            except Exception:
                pass
        # GV name
        canvas.setFont(font_bold, 10.5)
        canvas.setFillColor(colors.HexColor('#111827'))
        canvas.drawString(LEFT + logo_w + 3*mm, y_top - logo_w/2 + 1*mm, f'GV: {teacher}')
        # School name (right-aligned)
        canvas.setFont(font_bold, 10.5)
        sw = canvas.stringWidth(school, font_bold, 10.5)
        canvas.drawString(PAGE_W - RIGHT - sw, y_top - logo_w/2 + 1*mm, school)
        # Blue underline
        canvas.setStrokeColor(NAVY)
        canvas.setLineWidth(0.8)
        y_line = y_top - HEADER_H + 2*mm
        canvas.line(LEFT, y_line, PAGE_W - RIGHT, y_line)
        # Footer page number
        canvas.setFont(font_name, 10)
        canvas.setFillColor(colors.HexColor('#374151'))
        ft = f'— {pn} —'
        fw = canvas.stringWidth(ft, font_name, 10)
        canvas.drawString((PAGE_W - fw) / 2, BOTTOM - 6*mm, ft)
        canvas.restoreState()

    pdf = SimpleDocTemplate(
        output_path, pagesize=A4,
        leftMargin=LEFT, rightMargin=RIGHT,
        topMargin=TOP + HEADER_H + 2*mm,
        bottomMargin=BOTTOM,
        title=doc.title or '',
    )

    tmp_dir = tempfile.mkdtemp(prefix='vatly_math_')
    try:
        story = []
        # Tiêu đề tài liệu
        if doc.title:
            story.append(Paragraph(_xml(doc.title), style_title))
            story.append(HRFlowable(width='100%', thickness=1,
                                    color=NAVY, spaceAfter=6))

        for section in doc.sections:
            sec_items = []
            if section.label:
                sec_items.append(Paragraph(_xml(section.label), style_section))
            if section.intro:
                sec_items.append(Paragraph(_xml(section.intro), style_intro))
            if sec_items:
                story.extend(sec_items)

            for q in section.questions:
                q_block = []

                # Câu hỏi — LUÔN qua _math_paragraph: LaTeX có thể xuất hiện cả
                # khi không có dấu $ (AI/file gốc viết \frac, \sqrt trần)
                q_prefix = f'<b>Câu {q.number}.</b> '
                q_text, q_options = _split_opts(q)
                q_block.extend(_math_paragraph(q_text, style_q_math, tmp_dir,
                                               fontsize=12, prefix=q_prefix))

                # Hình ảnh câu hỏi (ảnh .wmf cũ → PNG tạm; không đọc được → bỏ)
                for img in q.images:
                    img_path = _raster_path(os.path.join(IMG_DIR, img.filename), tmp_dir)
                    if img_path:
                        try:
                            ri = RLImage(img_path, kind='proportional',
                                         width=min(10*cm, CONTENT_W * 0.7),
                                         height=8*cm)
                            q_block.append(Spacer(1, 2*mm))
                            q_block.append(ri)
                            q_block.append(Spacer(1, 2*mm))
                        except Exception:
                            pass

                # Options A/B/C/D — bố cục 2 cột (đáp án cũng có thể chứa công thức);
                # hình theo phương án (4 đồ thị) đặt ngay dưới chữ A/B/C/D trong ô
                def _opt_para(s):
                    return _math_paragraph(s, style_opt, tmp_dir, fontsize=12)[0]

                opt_imgs = _opt_images_for(q, len(q_options))

                def _opt_cell(i, max_w):
                    parts = [_opt_para(q_options[i])]
                    for img in opt_imgs[i]:
                        p_img = _raster_path(os.path.join(IMG_DIR, img.filename), tmp_dir)
                        if not p_img:
                            continue
                        try:
                            parts.append(RLImage(p_img, kind='proportional',
                                                 width=max_w, height=6*cm))
                        except Exception:
                            pass
                    return parts if len(parts) > 1 else parts[0]

                if q_options:
                    opts = q_options
                    if len(opts) == 4:
                        col_w = CONTENT_W / 2
                        tdata = [
                            [_opt_cell(0, col_w * 0.92), _opt_cell(1, col_w * 0.92)],
                            [_opt_cell(2, col_w * 0.92), _opt_cell(3, col_w * 0.92)],
                        ]
                        t = Table(tdata, colWidths=[col_w, col_w])
                        t.setStyle(TableStyle([
                            ('VALIGN',  (0,0), (-1,-1), 'TOP'),
                            ('TOPPADDING',  (0,0), (-1,-1), 1),
                            ('BOTTOMPADDING', (0,0), (-1,-1), 1),
                        ]))
                        q_block.append(t)
                    else:
                        for i, opt in enumerate(opts):
                            cell = _opt_cell(i, min(7.5*cm, CONTENT_W * 0.6))
                            q_block.extend(cell if isinstance(cell, list) else [cell])

                # Sub-items (đúng-sai a/b/c/d)
                for sub in q.sub_items:
                    q_block.extend(_math_paragraph(sub, style_sub, tmp_dir, fontsize=12))

                # Lời giải
                if show_solutions and q.show_solution and q.ai_solution:
                    sol_items = _math_paragraph(
                        q.ai_solution, style_sol, tmp_dir,
                        fontsize=11, prefix='<i>Lời giải: </i>'
                    )
                    q_block.extend(sol_items)

                # Giữ câu hỏi + đáp án không bị tách trang nếu ngắn
                try:
                    story.append(KeepTogether(q_block))
                except Exception:
                    story.extend(q_block)

            story.append(Spacer(1, 3*mm))

        pdf.build(story, onFirstPage=on_page, onLaterPages=on_page)
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


# ── Word ──────────────────────────────────────────────────────────

_MD_RE = re.compile(r'(\*\*[^*\n]+?\*\*|\*[^*\n]+?\*)')
_TABLE_LINE_RE = re.compile(r'^\s*\|.*\|\s*$')
_TABLE_SEP_RE = re.compile(r'^\s*\|?[\s:|-]+\|?\s*$')  # dòng phân cách |---|---|


def _add_md_runs(para, text: str, size: int, set_font_fn, bold_base: bool = False):
    """Parse **bold** và *italic* trong text, thêm runs tương ứng vào paragraph."""
    for seg in _MD_RE.split(text):
        if not seg:
            continue
        if seg.startswith('**') and seg.endswith('**') and len(seg) > 4:
            r = para.add_run(seg[2:-2])
            set_font_fn(r, bold=True, size=size)
        elif seg.startswith('*') and seg.endswith('*') and len(seg) > 2:
            r = para.add_run(seg[1:-1])
            set_font_fn(r, bold=bold_base, italic=True, size=size)
        else:
            r = para.add_run(seg)
            set_font_fn(r, bold=bold_base, size=size)


def _split_md_tables(text: str):
    """
    Tách text thành các block: ('text', str) hoặc ('table', [rows]).
    Bảng Markdown = >= 2 dòng liên tiếp khớp |...|.
    rows là list các list ô (đã bỏ dòng phân cách |---|).
    """
    if not text or '|' not in text:
        return [('text', text)]
    lines = text.split('\n')
    blocks = []
    buf_text, buf_tbl = [], []

    def flush_text():
        if buf_text:
            blocks.append(('text', '\n'.join(buf_text)))
            buf_text.clear()

    def flush_tbl():
        if len(buf_tbl) >= 2:
            rows = []
            for ln in buf_tbl:
                if _TABLE_SEP_RE.match(ln) and set(ln.replace('|', '').replace(' ', '')) <= set(':-'):
                    continue  # bỏ dòng phân cách
                cells = [c.strip() for c in ln.strip().strip('|').split('|')]
                rows.append(cells)
            if rows:
                blocks.append(('table', rows))
        else:
            # Không đủ dòng để thành bảng → coi là text
            blocks.append(('text', '\n'.join(buf_tbl)))
        buf_tbl.clear()

    for ln in lines:
        if _TABLE_LINE_RE.match(ln):
            flush_text()
            buf_tbl.append(ln)
        else:
            flush_tbl() if buf_tbl else None
            buf_text.append(ln)
    flush_text()
    if buf_tbl:
        flush_tbl()
    return blocks


_CELL_SUP = {'^{2}': '²', '^2': '²', '^{3}': '³', '^3': '³', '^{-1}': '⁻¹',
             '^{o}': '°', '^\\circ': '°', '^{\\circ}': '°'}


def _clean_table_cell(s: str) -> str:
    """Làm sạch ô bảng trước khi vẽ matplotlib: mathtext dễ lỗi với tiếng Việt
    trong công thức → hỏng cả bảng. Hạ trọn về Unicode qua latex_to_plain
    (một nguồn sự thật, dùng chung với đường PDF)."""
    from core.latex_normalize import latex_to_plain
    return latex_to_plain(str(s)).strip()


def _render_table_png(rows, tmp_dir: str, dpi: int = 160):
    """Vẽ bảng (list các list ô) thành PNG bằng matplotlib. Trả path hoặc None."""
    try:
        # OO API — không dùng pyplot (state toàn cục, không thread-safe)
        from matplotlib.figure import Figure

        rows = [[_clean_table_cell(c) for c in r] for r in rows]
        ncol = max(len(r) for r in rows)
        rows = [r + [''] * (ncol - len(r)) for r in rows]  # pad cho đều cột
        nrow = len(rows)

        fig = Figure(figsize=(min(0.1 + 1.6 * ncol, 9), 0.45 * nrow + 0.2))
        ax = fig.add_subplot(111)
        ax.axis('off')
        tbl = ax.table(cellText=rows, cellLoc='center', loc='center')
        tbl.auto_set_font_size(False)
        tbl.set_fontsize(11)
        tbl.scale(1, 1.4)
        # Tô đậm hàng đầu (header)
        for j in range(ncol):
            try:
                c = tbl[0, j]
                c.set_text_props(weight='bold')
                c.set_facecolor('#EAF0FB')
            except Exception:
                pass
        path = os.path.join(tmp_dir, f'table_{uuid.uuid4().hex[:12]}.png')
        fig.savefig(path, bbox_inches='tight', dpi=dpi, pad_inches=0.05)
        return path
    except Exception:
        return None


# Tách đáp án A/B/C/D + Câu/Bài/PHẦN xuống dòng (string-based, dùng cho export Word/PDF).
# Cùng quy tắc với pdf_to_word._apply_bold_formatting nhưng làm trên chuỗi.
_EX_HEADER = r'(?:C[aâ]u|B[àa]i)\s+\d+\s*[.:]|PH[ẦA]N\b'


def _format_exam_text(text: str) -> str:
    """Đáp án A/B/C/D / ý a)b)c)d) inline → mỗi cái 1 dòng; Câu/Bài/PHẦN xuống dòng;
    tô đậm đầu dòng. Trả về chuỗi có '\\n' để emit_field tách thành paragraph."""
    if not text:
        return text
    # 1) Câu/Bài/PHẦN dính cuối dòng trước → xuống dòng (giữ ** nếu có)
    text = re.sub(r'(?<=\S)[ \t]+(\*{0,2}(?:' + _EX_HEADER + r'))', r'\n\1', text)
    # 2) Tách KHỐI đáp án A→B→C→D (đủ 4, đúng thứ tự) — an toàn, không nhầm "vật B."
    _opt = lambda L: r'\*{0,2}\(?' + L + r'\*{0,2}(?:\)|\s*\\?[.):])\*{0,2}[ \t]'
    # [ \t]*\n?[ \t]* : nuốt xuống dòng sẵn có trước A. để không sinh dòng trống
    text = re.sub(
        r'[ \t]*\n?[ \t]*(' + _opt('A') + r'.+?)\s+(' + _opt('B') + r'.+?)\s+('
        + _opt('C') + r'.+?)\s+(' + _opt('D') + r')',
        lambda m: '\n' + m.group(1) + '\n' + m.group(2) + '\n' + m.group(3) + '\n' + m.group(4),
        text)
    _sub = lambda L: r'\*{0,2}' + L + r'\)(?:\*\*)?[ \t]'
    text = re.sub(
        r'[ \t]*\n?[ \t]*(' + _sub('a') + r'.+?)\s+(' + _sub('b') + r'.+?)\s+('
        + _sub('c') + r'.+?)\s+(' + _sub('d') + r')',
        lambda m: '\n' + m.group(1) + '\n' + m.group(2) + '\n' + m.group(3) + '\n' + m.group(4),
        text)
    # 3) Tô đậm đầu dòng: Câu/Bài, A./B./C./D., a)/b)/c)/d)
    text = re.sub(r'(?<!\*)(C[aâ]u\s+\d+|B[àa]i\s+\d+)(\.|:)(?!\*)', r'**\1\2**', text)
    text = re.sub(r'(?m)^(?!\*\*)\(?([A-D])\*{0,2}(?:\)|\s*\\?[.):])\*{0,2}\s', r'**\1.** ', text)
    text = re.sub(r'(?m)^(?!\*\*)([a-d])\)\s', r'**\1)** ', text)
    return text


def _correct_mc_letter(q) -> str | None:
    """Xác định đáp án đúng A/B/C/D: ưu tiên correct_answer, rồi parse từ lời giải."""
    if getattr(q, 'correct_answer', ''):
        m = re.search(r'[A-D]', str(q.correct_answer).upper())
        if m:
            return m.group(0)
    sol = getattr(q, 'ai_solution', '') or ''
    m = re.search(r'(?:Ch[oọ]n|[Đđ]áp\s*án|→)[^A-Da-d\n]{0,10}\b([A-D])\b', sol)
    return m.group(1) if m else None


def _correct_subs(q) -> set:
    """Tập ý đúng (a/b/c/d) cho câu Đúng-Sai — parse 'a) Đúng' từ lời giải."""
    res = set()
    sol = getattr(q, 'ai_solution', '') or ''
    for m in re.finditer(r'\b([a-d])\)\s*\**\s*(Đúng|đúng|Sai|sai)', sol):
        if m.group(2).lower().startswith('đ'):
            res.add(m.group(1))
    return res


def _opt_letter(opt: str) -> str | None:
    """Lấy chữ cái đầu của 1 phương án: '**A.** ...'/'A. ...' → 'A'; 'a) ...' → 'a'."""
    m = re.match(r'\s*\*{0,2}([A-Da-d])[.)]', opt)
    return m.group(1) if m else None


def export_word(doc: Document, output_path: str, show_solutions: bool = True, settings: dict = None):
    from docx import Document as DocxDoc
    from docx.shared import Pt, Cm, RGBColor
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.oxml.ns import qn
    from docx.oxml import OxmlElement
    import shutil
    from core.latex_normalize import (normalize_latex, unicode_to_latex,
                                  to_mau_standard, strip_accents_in_math,
                                  unwrap_pseudo_math)
    from core.importer import split_options_from_text

    if settings is None:
        settings = {}
    teacher = settings.get('teacher_name', 'Giáo viên')
    school  = settings.get('school_name',  'Trường THPT')

    docx = DocxDoc()
    tmp_dir = tempfile.mkdtemp(prefix='vatly_word_')

    # Style Normal: python-docx mặc định là Calibri 11, giãn đoạn 8pt, dòng 1.08
    # → tài liệu bị thưa. Đặt lại: Times New Roman 12, dòng đơn, giãn đoạn 2pt.
    _normal = docx.styles['Normal']
    _normal.font.name = 'Times New Roman'
    _normal.font.size = Pt(12)
    _npf = _normal.paragraph_format
    _npf.space_before = Pt(0)
    _npf.space_after  = Pt(2)
    _npf.line_spacing = 1.0

    # Margins — lề gọn kiểu đề thi (trên/dưới 1.5, trái 2.0, phải 1.5)
    section_w = docx.sections[0]
    section_w.top_margin    = Cm(1.5)
    section_w.bottom_margin = Cm(1.5)
    section_w.left_margin   = Cm(2.0)
    section_w.right_margin  = Cm(1.5)

    def set_font(run, bold=False, italic=False, size=12, color=None, underline=False):
        run.font.name = 'Times New Roman'
        run.font.size = Pt(size)
        run.font.bold = bold
        run.font.italic = italic
        if underline:
            run.font.underline = True
        if color:
            run.font.color.rgb = RGBColor(*color)
        # Đảm bảo font tiếng Việt
        r = run._r
        rPr = r.get_or_add_rPr()
        rFonts = OxmlElement('w:rFonts')
        rFonts.set(qn('w:ascii'),    'Times New Roman')
        rFonts.set(qn('w:hAnsi'),    'Times New Roman')
        rFonts.set(qn('w:cs'),       'Times New Roman')
        rPr.insert(0, rFonts)

    # Tách $$...$$ / $...$ để ghi nguyên văn (không parse markdown bên trong)
    _math_split = re.compile(r'(\$\$[^$]+?\$\$|\$[^$\n]+?\$)')

    def write_math_text(para, content, size, bold_base=False, underline=False):
        """Ghi text có $...$: giữ $...$ NGUYÊN VĂN (literal), phần ngoài xử lý **đậm**/*nghiêng*.
        underline=True → gạch chân toàn bộ (dùng cho phương án ĐÚNG ở bản đáp án)."""
        sf = (lambda r, **kw: set_font(r, underline=underline, **kw)) if underline else set_font
        if '$' not in content:
            _add_md_runs(para, content, size, sf, bold_base)
            return
        for part in _math_split.split(content):
            if not part:
                continue
            if part.startswith('$') and part.endswith('$') and len(part) > 2:
                sf(para.add_run(part), bold=bold_base, size=size)  # $...$ literal
            else:
                _add_md_runs(para, part, size, sf, bold_base)

    def write_letter_underline(para, content, size, bold_base=False):
        """Bản đáp án: GẠCH CHÂN CHỈ chữ cái đáp án đầu dòng (A/B/C/D hoặc a/b/c/d) + dấu .),
        phần nội dung còn lại ghi BÌNH THƯỜNG (không gạch). Bỏ ** bọc quanh nhãn nếu có."""
        m = re.match(r'^(\s*)(?:\*\*)?\s*([A-Da-d])([.)])\s*(?:\*\*)?\s*(.*)$', content, re.DOTALL)
        if not m:
            write_math_text(para, content, size, bold_base, underline=True)
            return
        lead, letter, sep, rest = m.group(1), m.group(2), m.group(3), m.group(4)
        if lead:
            set_font(para.add_run(lead), size=size)
        set_font(para.add_run(letter + sep), bold=bold_base, size=size, underline=True)
        if rest:
            write_math_text(para, ' ' + rest, size, bold_base)

    def _shade_para(para, fill):
        """Tô màu nền cho 1 paragraph (dùng cho lời giải)."""
        if not fill:
            return
        from docx.oxml import OxmlElement as OE
        pPr = para._p.get_or_add_pPr()
        shd = OE('w:shd')
        shd.set(qn('w:val'), 'clear')
        shd.set(qn('w:color'), 'auto')
        shd.set(qn('w:fill'), fill)
        pPr.append(shd)

    def emit_field(text, size=12, indent_cm=None, lead_para=None, bold_base=False,
                   split_answers=True, correct_letter=None, correct_subs=None,
                   shade_fill=None):
        """
        Ghi 1 trường text vào docx: Unicode Vật lý → LaTeX $...$ (text), bảng → ảnh.
        lead_para: paragraph có sẵn để ghi block text ĐẦU TIÊN (vd đã có 'Câu N.').
        split_answers=False → KHÔNG tách đáp án/dòng (dùng cho LỜI GIẢI: giữ cùng hàng).
        correct_letter/correct_subs → gạch chân dòng đáp án ĐÚNG (bản đáp án).
        shade_fill → mã màu nền (hex) áp cho MỌI paragraph của trường này (lời giải).
        """
        correct_subs = correct_subs or set()
        # Thứ tự BẮT BUỘC: (1) Unicode→LaTeX, gỡ $ bọc nhầm quanh đáp án;
        # (2) tách đáp án A/B/C/D mỗi cái 1 dòng; (3) bỏ dấu tiếng Việt TRONG công
        # thức (AIOMT không chuyển được chữ có dấu, unwrap còn gỡ luôn $) rồi chuẩn
        # hoá theo mẫu — chuẩn hoá chạy theo từng dòng nên phải tách dòng trước,
        # nếu không "h = 20\nA. 2 s" thành "$h = 20\\text{A}$. 2 s" (A = Ampe).
        text = unwrap_pseudo_math(unicode_to_latex(text or ''))
        if split_answers:
            text = _format_exam_text(text)   # tách đáp án A/B/C/D, Câu/Bài xuống dòng
        text = to_mau_standard(normalize_latex(strip_accents_in_math(text)))
        if not text.strip() and lead_para is None:
            return
        _ans_re = re.compile(r'^\s*\*{0,2}[A-Da-d][.)]')
        first = True
        for kind, content in _split_md_tables(text):
            if kind == 'table':
                png = _render_table_png(content, tmp_dir)
                pi = docx.add_paragraph()
                if indent_cm:
                    pi.paragraph_format.left_indent = Cm(indent_cm)
                if png:
                    try:
                        pi.add_run().add_picture(png, width=Cm(min(16, 3.2 * max(len(r) for r in content))))
                    except Exception:
                        png = None
                if not png:
                    # Fallback: ghi bảng dạng text nếu render ảnh lỗi
                    for row in content:
                        rp = docx.add_paragraph()
                        if indent_cm:
                            rp.paragraph_format.left_indent = Cm(indent_cm)
                        set_font(rp.add_run(' | '.join(row)), size=size)
                first = False
            else:
                if not content.strip():
                    continue
                if not split_answers:
                    # Lời giải: GIỮ xuống dòng giữa các phần (Cơ sở/Tính toán, a/b/c/d,
                    # Tóm tắt/Công thức/Kết quả, từng bước...) — mỗi dòng AI trả về là 1 paragraph.
                    # Dòng ĐẦU nằm cùng hàng với nhãn "Lời giải:" (lead_para).
                    for ln in content.split('\n'):
                        ln = ln.strip()
                        if not ln:
                            continue
                        if first and lead_para is not None:
                            para = lead_para
                        else:
                            para = docx.add_paragraph()
                            if indent_cm:
                                para.paragraph_format.left_indent = Cm(indent_cm)
                        write_math_text(para, ln, size, bold_base)
                        _shade_para(para, shade_fill)
                        first = False
                    continue
                # Mỗi DÒNG (câu hỏi / từng đáp án) là 1 paragraph riêng
                for ln in content.split('\n'):
                    if not ln.strip():
                        continue
                    is_ans = bool(_ans_re.match(ln))   # dòng đáp án → thụt lề
                    # Gạch chân dòng đáp án ĐÚNG (bản đáp án)
                    ul = False
                    if is_ans and (correct_letter or correct_subs):
                        L = _opt_letter(ln)
                        if L and (L == correct_letter or L in correct_subs):
                            ul = True
                    if first and lead_para is not None:
                        para = lead_para
                    else:
                        para = docx.add_paragraph()
                        ind = 0.8 if is_ans else indent_cm
                        if ind:
                            para.paragraph_format.left_indent = Cm(ind)
                    if ul:
                        write_letter_underline(para, ln.strip(), size, bold_base)
                    else:
                        write_math_text(para, ln.strip(), size, bold_base)
                    first = False

    # Header
    header_sect = docx.sections[0]
    header = header_sect.header
    hdr_para = header.paragraphs[0]
    hdr_para.alignment = WD_ALIGN_PARAGRAPH.LEFT
    # Logo
    if os.path.exists(LOGO_PATH):
        try:
            run_logo = hdr_para.add_run()
            run_logo.add_picture(LOGO_PATH, width=Cm(1))
            hdr_para.add_run('  ')
        except Exception:
            pass
    r_teacher = hdr_para.add_run(f'GV: {teacher}')
    set_font(r_teacher, bold=True, size=11)
    hdr_para.add_run('\t\t')
    r_school = hdr_para.add_run(school)
    set_font(r_school, bold=True, size=11)

    # Footer
    footer = header_sect.footer
    ftr_para = footer.paragraphs[0]
    ftr_para.alignment = WD_ALIGN_PARAGRAPH.CENTER
    fld = OxmlElement('w:fldChar')
    fld.set(qn('w:fldCharType'), 'begin')
    instr = OxmlElement('w:instrText')
    instr.text = ' PAGE '
    fld_end = OxmlElement('w:fldChar')
    fld_end.set(qn('w:fldCharType'), 'end')
    r_page = ftr_para.add_run()
    r_page._r.append(fld)
    r_page._r.append(instr)
    r_page._r.append(fld_end)
    set_font(r_page, size=10)

    # Tiêu đề
    title_para = docx.add_paragraph()
    title_para.alignment = WD_ALIGN_PARAGRAPH.CENTER
    title_para.paragraph_format.space_after = Pt(8)
    r_title = title_para.add_run(doc.title)
    set_font(r_title, bold=True, size=14)

    for section in doc.sections:
        if section.label:
            p = docx.add_paragraph()
            p.paragraph_format.space_before = Pt(10)
            p.paragraph_format.space_after  = Pt(3)
            r = p.add_run(section.label)
            set_font(r, bold=True, size=13, color=(0x0F, 0x3D, 0x8E))

        if section.intro:
            emit_field(section.intro, size=12)

        for q in section.questions:
            # Bản đáp án: xác định phương án ĐÚNG để gạch chân
            show_ans = show_solutions and q.show_solution and (q.ai_solution or q.correct_answer)
            correct_letter = _correct_mc_letter(q) if show_ans else None
            correct_subs = _correct_subs(q) if show_ans else set()

            # Phương án bị nuốt vào đề (tài liệu nhập trước khi vá) → tách ra
            # khi xuất, bản cục bộ, không đụng tài liệu đã lưu
            q_text, q_options = split_options_from_text(q.text, q.options)
            opt_imgs = _opt_images_for(q, len(q_options))

            p = docx.add_paragraph()
            p.paragraph_format.space_before = Pt(6)   # tách câu, nội dung trong câu sát nhau
            r_num = p.add_run(f'Câu {q.number}. ')
            set_font(r_num, bold=True, size=12)
            # Đáp án có thể nằm inline trong q.text → truyền correct để gạch chân
            emit_field(q_text, size=12, lead_para=p,
                       correct_letter=correct_letter, correct_subs=correct_subs)

            # Hình ảnh của câu (.wmf cũ → PNG tạm; không đọc được → bỏ, không ném)
            def add_picture_para(container, img, width_cm, indent_cm=None):
                p_img = _raster_path(os.path.join(IMG_DIR, img.filename), tmp_dir)
                if not p_img:
                    return
                try:
                    para = container.add_paragraph()
                    if indent_cm:
                        para.paragraph_format.left_indent = Cm(indent_cm)
                    para.add_run().add_picture(p_img, width=Cm(width_cm))
                except Exception:
                    pass

            for img in q.images:
                add_picture_para(docx, img, 8)

            # Options — chỉ gạch chân CHỮ CÁI ĐẦU của phương án ĐÚNG (bản đáp án).
            # Đủ 4 phương án đều có hình (4 đồ thị) → LƯỚI 2×2: mỗi ô chữ A/B/C/D + hình;
            # còn lại → hình đặt ngay sau dòng phương án
            def _emit_opt(para, opt):
                opt_txt = to_mau_standard(normalize_latex(
                    strip_accents_in_math(unicode_to_latex(opt))))
                if correct_letter and _opt_letter(opt) == correct_letter:
                    write_letter_underline(para, opt_txt, 12)
                else:
                    write_math_text(para, opt_txt, 12)

            if len(q_options) == 4 and all(opt_imgs[i] for i in range(4)):
                tbl = docx.add_table(rows=2, cols=2)
                for i, opt in enumerate(q_options):
                    cell = tbl.cell(i // 2, i % 2)
                    _emit_opt(cell.paragraphs[0], opt)
                    for img in opt_imgs[i]:
                        add_picture_para(cell, img, 7)
            else:
                for i, opt in enumerate(q_options):
                    po = docx.add_paragraph()
                    po.paragraph_format.left_indent = Cm(0.8)
                    _emit_opt(po, opt)
                    for img in opt_imgs[i]:
                        add_picture_para(docx, img, 6, indent_cm=0.8)

            # Sub-items (Đúng-Sai) — gạch chân ý ĐÚNG ở bản đáp án
            for sub in q.sub_items:
                emit_field(sub, size=12, indent_cm=0.8, correct_subs=correct_subs)

            # Lời giải — dòng đầu cùng hàng "Lời giải:", các phần sau xuống dòng riêng;
            # nền xanh nhạt áp cho mọi dòng lời giải
            if show_solutions and q.show_solution and q.ai_solution:
                psol = docx.add_paragraph()
                psol.paragraph_format.left_indent = Cm(0.8)
                r_lbl = psol.add_run('Lời giải: ')
                set_font(r_lbl, bold=True, size=11)
                emit_field(q.ai_solution, size=11, indent_cm=0.8, lead_para=psol,
                           split_answers=False, shade_fill='EBF4FF')

    try:
        docx.save(output_path)
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


# ── Entry point ───────────────────────────────────────────────────

def export_file(doc: Document, output_path: str, fmt: str,
                show_solutions: bool = True, settings: dict = None):
    if fmt == 'pdf':
        export_pdf(doc, output_path, show_solutions, settings)
    elif fmt == 'word':
        export_word(doc, output_path, show_solutions, settings)
    else:
        raise ValueError(f'Định dạng không hỗ trợ: {fmt}')
