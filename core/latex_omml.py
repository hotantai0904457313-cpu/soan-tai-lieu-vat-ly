"""
core/latex_omml.py — Chuyển LaTeX ($...$) thành Word Equation thật (OMML)
và nhúng vào tài liệu python-docx.

Chuỗi: LaTeX --latex2mathml--> MathML --MML2OMML.XSL--> OMML (<m:oMath>)
       --> chèn vào <w:p> của python-docx.

Nếu công thức không parse được -> fallback ghi $...$ literal (để công cụ
LaTeX trên Word của thầy xử lý nốt / sửa tay). KHÔNG bao giờ làm mất nội dung.
"""
import os
import re
import glob

# Namespace OMML chuẩn OOXML
_M_NS = 'http://schemas.openxmlformats.org/officeDocument/2006/math'

# Tách text theo $$...$$ (display) hoặc $...$ (inline). Group 1 = display, group 2 = inline.
_SPLIT_RE = re.compile(r'(\$\$[^$\n]+?\$\$|\$[^$\n]+?\$)')
# Markdown đậm/nghiêng cho phần text ngoài công thức
_MD_RE = re.compile(r'(\*\*[^*\n]+?\*\*|\*[^*\n]+?\*)')

_xslt = None          # cache đối tượng XSLT
_xslt_tried = False    # đã thử load chưa


def _find_xsl() -> str | None:
    """Tìm MML2OMML.XSL của Office (auto-detect)."""
    try:
        from config import MML2OMML_XSL
        if MML2OMML_XSL and os.path.isfile(MML2OMML_XSL):
            return MML2OMML_XSL
    except Exception:
        pass
    patterns = [
        r'C:\Program Files\Microsoft Office\root\Office*\MML2OMML.XSL',
        r'C:\Program Files (x86)\Microsoft Office\root\Office*\MML2OMML.XSL',
        r'C:\Program Files\Microsoft Office\Office*\MML2OMML.XSL',
        r'C:\Program Files (x86)\Microsoft Office\Office*\MML2OMML.XSL',
    ]
    for pat in patterns:
        hits = glob.glob(pat)
        if hits:
            return hits[0]
    return None


def _get_xslt():
    """Load + cache XSLT. Trả None nếu không có (môi trường không Office)."""
    global _xslt, _xslt_tried
    if _xslt_tried:
        return _xslt
    _xslt_tried = True
    try:
        from lxml import etree
        xsl = _find_xsl()
        if not xsl:
            return None
        _xslt = etree.XSLT(etree.parse(xsl))
    except Exception:
        _xslt = None
    return _xslt


def omml_available() -> bool:
    return _get_xslt() is not None


def latex_to_omml_xml(latex: str) -> str | None:
    """LaTeX -> chuỗi XML <m:oMath>. None nếu chuyển thất bại."""
    latex = (latex or '').strip()
    if not latex:
        return None
    xslt = _get_xslt()
    if xslt is None:
        return None
    try:
        from latex2mathml.converter import convert
        from lxml import etree
        mathml = convert(latex)
        dom = etree.fromstring(mathml)
        omml = xslt(dom)
        root = omml.getroot()
        if root is None:
            return None
        return etree.tostring(root).decode('utf-8')
    except Exception:
        return None


def _append_omml(paragraph, omml_xml: str, display: bool) -> bool:
    """Chèn 1 phần tử OMML vào cuối paragraph. Trả True nếu thành công."""
    try:
        from docx.oxml import parse_xml
        if display:
            omml_xml = f'<m:oMathPara xmlns:m="{_M_NS}">{omml_xml}</m:oMathPara>'
        el = parse_xml(omml_xml)
        paragraph._p.append(el)
        return True
    except Exception:
        return False


def _add_text_with_md(paragraph, text: str, set_font_fn, size: int, bold_base: bool = False):
    """Ghi text thường có **đậm** / *nghiêng* vào paragraph (không có $)."""
    if not text:
        return
    for seg in _MD_RE.split(text):
        if not seg:
            continue
        if seg.startswith('**') and seg.endswith('**') and len(seg) > 4:
            r = paragraph.add_run(seg[2:-2]); set_font_fn(r, bold=True, size=size)
        elif seg.startswith('*') and seg.endswith('*') and len(seg) > 2:
            r = paragraph.add_run(seg[1:-1]); set_font_fn(r, bold=bold_base, italic=True, size=size)
        else:
            r = paragraph.add_run(seg); set_font_fn(r, bold=bold_base, size=size)


def add_latex_runs(paragraph, text: str, set_font_fn, size: int = 12,
                   bold_base: bool = False) -> int:
    """
    Ghi `text` (có thể chứa $...$ / $$...$$) vào paragraph:
      - Phần ngoài công thức: run thường (xử lý **đậm**/*nghiêng*).
      - Mỗi $...$  -> chèn OMML <m:oMath> (Equation inline).
      - Mỗi $$...$$ -> chèn OMML <m:oMathPara> (Equation display).
      - Công thức không parse được -> ghi literal '$...$' (fallback).

    Trả về: số công thức phải fallback (để log).
    """
    if text is None:
        return 0
    if '$' not in text:
        _add_text_with_md(paragraph, text, set_font_fn, size, bold_base)
        return 0

    fallback = 0
    for part in _SPLIT_RE.split(text):
        if not part:
            continue
        is_display = part.startswith('$$') and part.endswith('$$') and len(part) > 4
        is_inline = (not is_display) and part.startswith('$') and part.endswith('$') and len(part) > 2
        if is_display or is_inline:
            expr = part[2:-2] if is_display else part[1:-1]
            omml = latex_to_omml_xml(expr)
            if omml and _append_omml(paragraph, omml, display=is_display):
                continue
            # Fallback: ghi literal (giữ nguyên dấu $ để công cụ Word xử lý)
            fallback += 1
            r = paragraph.add_run(part)
            set_font_fn(r, bold=bold_base, size=size)
        else:
            _add_text_with_md(paragraph, part, set_font_fn, size, bold_base)
    return fallback
