"""
core/latex_normalize.py — Chuẩn hoá LaTeX + chuyển ký hiệu Unicode Vật lý sang
LaTeX $...$ trong văn bản trước khi xuất Word.

- normalize_latex(): sửa lỗi cú pháp $...$ (delimiter, khoảng trắng, $ lẻ...).
- unicode_to_latex(): chuyển ký hiệu Unicode (vectơ F⃗, nhiệt độ °C, chỉ số v₀,
  số mũ x², chữ Hy Lạp α β, ký hiệu × ÷ ±...) thành LaTeX $...$ để công cụ
  LaTeX trên Word tự chuyển. KHÔNG tạo Equation OMML — chỉ ra text $...$.

Thuần regex/mapping, offline, không gọi AI.
"""
import re
import unicodedata

# $...$ inline (non-greedy, không chứa $ bên trong)
MATH_INLINE_RE = re.compile(r'(?<!\$)\$(?!\$)([^$\n]+?)(?<!\$)\$(?!\$)')
# $$...$$ display
MATH_DISPLAY_RE = re.compile(r'\$\$(.+?)\$\$', re.DOTALL)


def _strip_markdown_inside(expr: str) -> str:
    """Bỏ **đậm** / *nghiêng* lọt vào trong công thức."""
    expr = re.sub(r'\*\*([^*]+?)\*\*', r'\1', expr)
    expr = re.sub(r'(?<!\*)\*(?!\*)([^*]+?)(?<!\*)\*(?!\*)', r'\1', expr)
    return expr


def _clean_expr(expr: str) -> str:
    """Làm sạch nội dung 1 biểu thức (phần giữa các dấu $)."""
    # Gộp xuống dòng thành khoảng trắng (inline math không nên có \n)
    expr = re.sub(r'\s*\n\s*', ' ', expr)
    expr = _strip_markdown_inside(expr)
    # Bỏ khoảng trắng thừa đầu/cuối
    expr = expr.strip()
    # Gộp nhiều khoảng trắng
    expr = re.sub(r'[ \t]{2,}', ' ', expr)
    return expr


_VN_DIACRITIC = re.compile(
    r'[àáảãạăắằẳẵặâấầẩẫậèéẻẽẹêếềểễệìíỉĩịòóỏõọôốồổỗộơớờởỡợùúủũụưứừửữựỳýỷỹỵđ]',
    re.IGNORECASE)


def unwrap_pseudo_math(text: str) -> str:
    """
    Bỏ dấu $ / $$ bọc quanh đoạn KHÔNG phải công thức (đáp án 'A. ... B. ...',
    hay câu chữ tiếng Việt). Giữ nguyên $...$ là công thức thật.
    VD: '$$A. 15. B. 30. C. 32. D. 16.$$' → 'A. 15. B. 30. C. 32. D. 16.'
    """
    if not text or '$' not in text:
        return text

    def _is_real_math(inner: str) -> bool:
        # Có đáp án A./B. hoặc ý a)/b) → là text đáp án, KHÔNG phải công thức.
        # Chỉ khớp khi ký hiệu đứng ĐẦU chuỗi hoặc sau khoảng trắng — công thức
        # thật kiểu $f(a) = g(b)$ chứa "a) " sau dấu '(' không được tính.
        if re.search(r'(?:^|\s)(?:[A-D]\.|[a-d]\))\s', inner):
            return False
        # Có chữ tiếng Việt có dấu → là câu chữ, KHÔNG phải công thức
        if _VN_DIACRITIC.search(inner):
            return False
        return True

    def _u(m):
        inner = m.group(1)
        return m.group(0) if _is_real_math(inner) else inner

    text = re.sub(r'\$\$(.+?)\$\$', _u, text, flags=re.DOTALL)
    text = re.sub(r'\$([^$\n]+?)\$', _u, text)
    return text


def normalize_latex(text: str) -> str:
    r"""
    Chuẩn hoá toàn bộ LaTeX trong 1 đoạn văn bản.

    - \(...\) -> $...$   ;   \[...\] -> $$...$$
    - $$ ... $$ : trim khoảng trắng trong, xoá nếu rỗng
    - $ ... $   : trim khoảng trắng trong, xoá nếu rỗng, bỏ markdown lọt vào
    - $x$ dính chữ hai bên -> thêm khoảng trắng ngoài: 'nếu$x$thì' -> 'nếu $x$ thì'
    """
    if not text or ('$' not in text and '\\(' not in text and '\\[' not in text):
        return text

    # 0) Gỡ $ bọc nhầm quanh đáp án / câu chữ tiếng Việt
    text = unwrap_pseudo_math(text)
    if '$' not in text and '\\(' not in text and '\\[' not in text:
        return text

    # 1) Đổi delimiter \(...\) và \[...\] sang dạng $
    text = re.sub(r'\\\((.+?)\\\)', lambda m: f'${m.group(1)}$', text, flags=re.DOTALL)
    text = re.sub(r'\\\[(.+?)\\\]', lambda m: f'$${m.group(1)}$$', text, flags=re.DOTALL)

    # Helper: thêm space NGOÀI delimiter nếu dính ký tự chữ/số (biết chính xác biên
    # qua m.start()/m.end() nên không bị nhầm khoảng giữa 2 công thức).
    def _wrap(m, e, delim):
        if not e:
            return ''
        s = m.string
        pre = s[m.start() - 1] if m.start() > 0 else ''
        post = s[m.end()] if m.end() < len(s) else ''
        lead = ' ' if pre and pre.isalnum() else ''
        trail = ' ' if post and post.isalnum() else ''
        return f'{lead}{delim}{e}{delim}{trail}'

    # 2) Display math $$...$$ — chỉ GIỮ $$ khi đứng riêng 1 dòng (hệ pt, ma trận);
    #    nếu nằm GIỮA dòng văn bản → hạ xuống $...$ (tránh AIOMT đẩy công thức
    #    xuống dòng/căn giữa làm vỡ bố cục câu).
    def _disp(m):
        e = _clean_expr(m.group(1))
        s = m.string
        before = s[:m.start()]
        after = s[m.end():]
        # phần cùng dòng 2 phía (từ \n trước → m.start, m.end → \n sau)
        left = before[before.rfind('\n') + 1:]
        nl = after.find('\n')
        right = after if nl < 0 else after[:nl]
        standalone = (left.strip() == '' and right.strip() == '')
        return _wrap(m, e, '$$' if standalone else '$')
    text = MATH_DISPLAY_RE.sub(_disp, text)

    # 3) Inline math $...$
    text = MATH_INLINE_RE.sub(lambda m: _wrap(m, _clean_expr(m.group(1)), '$'), text)

    # 3.5) Vá dòng có dấu $ lẻ — 1 dấu $ mồ côi làm AIOMT ghép cặp lệch
    #      TOÀN BỘ tài liệu phía sau (chữ bị nuốt vào equation → mất dấu
    #      tiếng Việt + mất khoảng trắng, công thức rơi ra ngoài thành text)
    text = fix_unbalanced_dollars(text)

    # 4) Dọn khoảng trắng thừa
    text = re.sub(r'[ \t]{2,}', ' ', text)
    return text


def count_unbalanced_dollars(text: str) -> int:
    """
    Đếm số dấu $ không tạo thành cặp (lẻ) — dùng để log cảnh báo.
    Bỏ qua $$ (mỗi $$ tính là 1 delimiter).
    Trả về 0 nếu cân bằng, >0 nếu có $ lẻ.
    """
    # Đếm $$ rồi $ đơn
    tmp = text.replace('$$', '')          # loại display delimiter
    single = tmp.count('$')
    display = text.count('$$')
    # single phải chẵn, display phải chẵn
    return (single % 2) + (display % 2)


# ── Vá dấu $ lẻ (mất cân bằng) ─────────────────────────────────────
# AIOMT ghép cặp $...$ tuần tự trên CẢ tài liệu: chỉ cần 1 dấu $ mồ côi
# là mọi cặp phía sau lệch hết — chữ giữa 2 công thức bị nuốt vào equation
# (Cambria Math không có tiếng Việt → thành '?', math mode bỏ khoảng trắng),
# còn công thức thật rơi ra ngoài thành text. Đã gặp thật 05/07/2026:
# Gemini trả '$T_1 >$ T_0$' (3 dấu $) → hỏng từ đó đến cuối file.

# Đóng $ ngay sau toán tử rồi mở lại: '$T_1 >$ T_0$' → '$T_1 > T_0$'
_ODD_MERGE_OP_END = re.compile(
    r'\$([^$\n]*?[=<>+\-±·×/]\s*)\$(\s*)([^$\n]+?)\$')
# Mảnh sau bắt đầu bằng toán tử: '$v_0$ = 360 km/h$' → '$v_0 = 360 km/h$'
_ODD_MERGE_OP_START = re.compile(
    r'\$([^$\n]+?)\$(\s*)([=<>+\-±·×][^$\n]*?)\$')


def _remove_nth_dollar(pieces: list, n: int) -> str:
    """Nối lại các mảnh (đã split theo '$'), bỏ đi dấu $ thứ n (1-based)."""
    head = '$'.join(pieces[:n]) + pieces[n]
    tail = '$'.join(pieces[n + 1:])
    return head + ('$' + tail if n + 1 < len(pieces) else '')


def fix_unbalanced_dollars(text: str) -> str:
    """
    Vá từng DÒNG có số dấu $ lẻ. Thứ tự thử:
    1. Gộp 2 span bị AI cắt đôi quanh toán tử (lỗi hay gặp nhất).
    2. Gỡ dấu $ mở ra span "không phải công thức" (chứa tiếng Việt có
       dấu hoặc dài bất thường >80 ký tự).
    3. Cùng đường: gỡ dấu $ cuối cùng của dòng — hỏng cục bộ 1 chỗ
       còn hơn lây ra cả tài liệu.
    Dấu $$ (display) được bảo vệ, không tính vào cân bằng $ đơn.
    """
    if '$' not in text:
        return text
    lines = text.split('\n')
    for i, line in enumerate(lines):
        prot = line.replace('$$', '\x00\x00')   # che display delimiter
        if prot.count('$') % 2 == 0:
            continue

        def _merge(m):
            # Không gộp nếu sẽ nuốt chữ tiếng Việt vào công thức
            if _VN_DIACRITIC.search(m.group(1)) or _VN_DIACRITIC.search(m.group(3)):
                return m.group(0)
            return f'${m.group(1)}{m.group(2)}{m.group(3)}$'

        repaired = None
        for pat in (_ODD_MERGE_OP_END, _ODD_MERGE_OP_START):
            cand = pat.sub(_merge, prot, count=1)
            if cand != prot and cand.count('$') % 2 == 0:
                repaired = cand
                break
        if repaired is None:
            pieces = prot.split('$')
            bad = len(pieces) - 1               # mặc định: gỡ $ cuối dòng
            for k in range(1, len(pieces), 2):  # các span (vị trí lẻ)
                if _VN_DIACRITIC.search(pieces[k]) or len(pieces[k]) > 80:
                    bad = k
                    break
            repaired = _remove_nth_dollar(pieces, bad)
        lines[i] = repaired.replace('\x00\x00', '$$')
    return '\n'.join(lines)


# ── Unicode Vật lý → LaTeX ─────────────────────────────────────────

_SUP = {  # số mũ (superscript) → ^
    '⁰': '0', '¹': '1', '²': '2', '³': '3', '⁴': '4', '⁵': '5', '⁶': '6',
    '⁷': '7', '⁸': '8', '⁹': '9', '⁺': '+', '⁻': '-', '⁼': '=',
    '⁽': '(', '⁾': ')', 'ⁿ': 'n', 'ⁱ': 'i',
}
_SUB = {  # chỉ số dưới (subscript) → _
    '₀': '0', '₁': '1', '₂': '2', '₃': '3', '₄': '4', '₅': '5', '₆': '6',
    '₇': '7', '₈': '8', '₉': '9', '₊': '+', '₋': '-', '₌': '=',
    '₍': '(', '₎': ')', 'ₐ': 'a', 'ₑ': 'e', 'ₒ': 'o', 'ₓ': 'x', 'ₕ': 'h',
    'ₖ': 'k', 'ₗ': 'l', 'ₘ': 'm', 'ₙ': 'n', 'ₚ': 'p', 'ₛ': 's', 'ₜ': 't',
}
_GREEK = {
    'α': r'\alpha', 'β': r'\beta', 'γ': r'\gamma', 'δ': r'\delta',
    'ε': r'\varepsilon', 'ϵ': r'\epsilon', 'ζ': r'\zeta', 'η': r'\eta',
    'θ': r'\theta', 'ϑ': r'\vartheta', 'ι': r'\iota', 'κ': r'\kappa',
    'λ': r'\lambda', 'μ': r'\mu', 'ν': r'\nu', 'ξ': r'\xi', 'π': r'\pi',
    'ϖ': r'\varpi', 'ρ': r'\rho', 'σ': r'\sigma', 'ς': r'\varsigma',
    'τ': r'\tau', 'υ': r'\upsilon', 'φ': r'\varphi', 'ϕ': r'\phi',
    'χ': r'\chi', 'ψ': r'\psi', 'ω': r'\omega',
    'Γ': r'\Gamma', 'Δ': r'\Delta', 'Θ': r'\Theta', 'Λ': r'\Lambda',
    'Ξ': r'\Xi', 'Π': r'\Pi', 'Σ': r'\Sigma', 'Φ': r'\Phi',
    'Ψ': r'\Psi', 'Ω': r'\Omega',
}
_SYM = {
    '×': r'\times', '÷': r'\div', '±': r'\pm', '∓': r'\mp',
    '≥': r'\geq', '≤': r'\leq', '≠': r'\neq', '≈': r'\approx',
    '≡': r'\equiv', '∼': r'\sim', '∝': r'\propto',
    '→': r'\rightarrow', '←': r'\leftarrow', '↔': r'\leftrightarrow',
    '⇒': r'\Rightarrow', '⇐': r'\Leftarrow', '⇔': r'\Leftrightarrow',
    '∞': r'\infty', '∫': r'\int', '∮': r'\oint', '∑': r'\sum',
    '∏': r'\prod', '∂': r'\partial', '∇': r'\nabla', '√': r'\sqrt{}',
    '∈': r'\in', '∉': r'\notin', '⊂': r'\subset', '⊃': r'\supset',
    '∪': r'\cup', '∩': r'\cap', '∅': r'\emptyset',
    '⊥': r'\perp', '∥': r'\parallel', '∠': r'\angle', '△': r'\triangle',
    '·': r'\cdot', '∙': r'\cdot', '•': r'\cdot', '′': "'", '″': "''",
    '≅': r'\cong', '⩽': r'\leqslant', '⩾': r'\geqslant',
    'ℏ': r'\hbar', '∆': r'\Delta',
}
# Ký tự "kích hoạt" chế độ toán: chắc chắn là ký hiệu toán/lý
_TRIGGER = set(_GREEK) | set(_SYM) | set(_SUP) | set(_SUB) | {'°', '⃗', '⃑', '→'}
# Glue giữa 2 cụm toán (chỉ khoảng trắng + toán tử ASCII) → gộp chung $...$
_GLUE_RE = re.compile(r'^[\s=+\-*/<>]*$')

# Đơn vị đo đi kèm số mũ bị mất định dạng khi trích PDF (vd m/s2 → m/s²).
# Token dài đặt trước token ngắn (alternation ăn token đầu khớp).
_SUP_DIGIT = {'1': '¹', '2': '²', '3': '³'}
_UNIT_EXP_RE = re.compile(
    r'(?<![A-Za-zÀ-Ỹà-ỹ])'                              # không đứng sau chữ cái
    r'(m/s|km/h|rad/s|N/m|kg/m|J/kg|W/m|N\.m|kg\.m/s'   # đơn vị ghép
    r'|cm|mm|dm|km|nm|µm|m|s|N|J|W|Pa|Hz|Ω)'             # đơn vị đơn
    r'([123])'                                            # số mũ 1/2/3
    r'(?![0-9A-Za-zÀ-Ỹà-ỹ])'                              # không dính số/chữ phía sau
)


def _fix_unit_exponents(text: str) -> str:
    """m/s2 → m/s² · cm3 → cm³ · m2 → m² (chèn superscript Unicode, để
    unicode_to_latex chuyển tiếp thống nhất như khi PDF có sẵn ²³)."""
    if not text:
        return text
    return _UNIT_EXP_RE.sub(lambda m: m.group(1) + _SUP_DIGIT[m.group(2)], text)


# Dấu độ ° trong nhiệt độ thường bị ODL trích thành chữ số 0 thường:
# "35°C" → "350C", "42°C" → "420C". Nhận <số ≥2 chữ số HOẶC số thập phân>0(C|F).
# Yêu cầu ≥2 chữ số để KHÔNG nhầm tên lớp "10C/11C/12C" (lớp 10C có 1 chữ số trước 0).
_DEG_ZERO_RE = re.compile(
    r'(?<![A-Za-zÀ-Ỹà-ỹ])(\d{2,}|\d+[.,]\d+)0([CF])(?![A-Za-zÀ-Ỹà-ỹ0-9])')

# Ngữ cảnh nhiệt độ — bắt buộc phải có gần match mới sửa, vì "200C" cũng có thể
# là điện tích 200 Coulomb (bài tĩnh điện) → đổi thành "20°C" là phá đề.
_TEMP_CTX_RE = re.compile(
    r'nhi[eệ]t|s[oô]i|n[oó]ng|l[aạ]nh|nung|đ[oộ]\s*C\b|°|℃|thermometer|temperature',
    re.IGNORECASE)


def _fix_degree_zero(text: str) -> str:
    """350C → 35°C · 420C → 42°C · 2,50C → 2,5°C (khôi phục dấu độ bị trích nhầm
    thành số 0). Bỏ qua tên lớp 10C/11C/12C (1 chữ số trước 0) và giá trị
    Coulomb (không có ngữ cảnh nhiệt độ trong vòng ±40 ký tự)."""
    if not text or ('C' not in text and 'F' not in text):
        return text

    def _sub(m):
        s = m.string
        lo = max(0, m.start() - 40)
        hi = min(len(s), m.end() + 40)
        if _TEMP_CTX_RE.search(s[lo:hi]):
            return m.group(1) + '°' + m.group(2)
        return m.group(0)

    return _DEG_ZERO_RE.sub(_sub, text)


# ── Số mũ / chỉ số bị "bẹp" khi trích PDF ────────────────────────────
# Text layer của PDF không giữ vị trí cao/thấp của ký tự: 10⁵ → "105",
# V₀ → "V 0", 3,3.10⁻³ → "3,3.10−3". Khôi phục về ký tự Unicode ⁵ ₀ ⁻³
# để scanner unicode_to_latex xử lý thống nhất như PDF giữ đúng định dạng.
_SUP_OF = {'0': '⁰', '1': '¹', '2': '²', '3': '³', '4': '⁴',
           '5': '⁵', '6': '⁶', '7': '⁷', '8': '⁸', '9': '⁹'}
_SUB_OF = {'0': '₀', '1': '₁', '2': '₂', '3': '₃', '4': '₄',
           '5': '₅', '6': '₆', '7': '₇', '8': '₈', '9': '₉'}

# a) Khoa học số mũ ÂM: "3,3.10−3" / "1,6.10-19" → 3,3.10⁻³ (mantissa + chấm
#    nhân + 10 + dấu trừ DÍNH LIỀN chỉ có thể là số mũ — số thường không viết vậy)
_SCI_NEG_RE = re.compile(r'(?<=\d)\.10[−–‐-](\d{1,2})(?!\d)')
# b) Khoa học số mũ DƯƠNG: "2.105Pa" → 2.10⁵Pa — CHỈ khi ngay sau là đơn vị đo
#    (tránh phá số hiệu mục lục/hình: "Hình 2.104 mô tả..." giữ nguyên)
_SCI_POS_RE = re.compile(
    r'(?<=\d)\.10(\d{1,2})'
    r'(?= ?(?:N/m|mmHg\b|atm\b|[kM]?Pa\b|[kMG]?Hz\b|mol\b|gam\b|kg\b|g\b'
    r'|m[²³]|[ck]m\b|dm\b|mm\b|m\b|lít\b|[kM]?J\b|[kMG]?W\b|K\b|s\b|N\b'
    r'|[kMG]?eV\b|Bq\b|Ω|µ))')
# c) 10^x trần dính đơn vị áp suất: "105N/m2" → 10⁵N/m² (đề Vật lý luôn ghi
#    áp suất dạng 10^x N/m²; số đo thật hiếm khi dính liền không khoảng trắng)
_POW10_PRES_RE = re.compile(r'(?<![\d.,])10(\d)(?=N/m[²2³]|[kM]?Pa\b|mmHg\b|atm\b)')
# d) Chỉ số dưới trước dấu "=": "p 1 = 748" / "V0 =" → p₁ =, V₀ =
_SUB_EQ_RE = re.compile(r'(?<![A-Za-zÀ-Ỹà-ỹ0-9])([A-Za-z]) ?([0-9])(?=[ \t]*=)')
# e) Cặp biến trạng thái p/V/T: "p 1 V 1 = p 2 V 2" — chỉ nhận khi ngay sau là
#    dấu câu / hết dòng / một cặp p-V-T khác (KHÔNG ăn "V 2 lít khí")
_SUB_STATE_RE = re.compile(
    r'(?<![A-Za-zÀ-Ỹà-ỹ0-9])([pVT]) ([0-9])'
    r'(?=[ \t]*(?:[=.,;:)?!]|$|[pVT] ?[0-9]))', re.MULTILINE)


def _fix_flattened_scripts(text: str) -> str:
    """Khôi phục số mũ/chỉ số bị mất định dạng cao/thấp khi trích text PDF:
    105N/m2 → 10⁵N/m² · 3,3.10−3m³ → 3,3.10⁻³m³ · p 1 = → p₁ ="""
    if not text:
        return text
    _sup = lambda ds: ''.join(_SUP_OF[d] for d in ds)
    text = _SCI_NEG_RE.sub(lambda m: '.10⁻' + _sup(m.group(1)), text)
    text = _SCI_POS_RE.sub(lambda m: '.10' + _sup(m.group(1)), text)
    text = _POW10_PRES_RE.sub(lambda m: '10' + _sup(m.group(1)), text)
    # STATE chạy TRƯỚC EQ: "p 1 V 1 = ..." — nếu EQ đổi "V 1 =" → "V₁ =" trước
    # thì lookahead [pVT] [0-9] của STATE không còn khớp, sót "p 1" đầu chuỗi
    text = _SUB_STATE_RE.sub(lambda m: m.group(1) + _SUB_OF[m.group(2)], text)
    text = _SUB_EQ_RE.sub(lambda m: m.group(1) + _SUB_OF[m.group(2)], text)
    return text


def unicode_to_latex(text: str) -> str:
    r"""
    Chuyển ký hiệu Vật lý dạng Unicode trong văn bản thành LaTeX $...$ (text).
      F⃗ → $\vec{F}$ · v₀ → $v_0$ · x² → $x^{2}$ · 30°C → $30^{\circ}C$
      α → $\alpha$ · ω×r → $\omega \times r$ · 10⁻³ → $10^{-3}$
    Không tạo Equation; chỉ ra $...$ để công cụ LaTeX trên Word chuyển tiếp.
    """
    if not text:
        return text

    # Khôi phục số mũ đơn vị (m/s2 → m/s²), dấu độ (350C → 35°C) và số mũ/chỉ số
    # bị bẹp (105N/m2 → 10⁵N/m², p 1 = → p₁ =) trước khi xử lý
    text = _fix_unit_exponents(text)
    text = _fix_degree_zero(text)
    text = _fix_flattened_scripts(text)

    units = []  # mỗi phần tử: {'s': str, 'math': bool}

    def _emit_text(s):
        if units and not units[-1]['math']:
            units[-1]['s'] += s
        else:
            units.append({'s': s, 'math': False})

    def _emit_math(s):
        units.append({'s': s, 'math': True})

    def _take_base():
        """Lấy định danh đứng ngay trước (chữ/số) để làm cơ sở cho ^ _ vec.
        Có thể lấy từ unit toán liền trước (vd chữ in nghiêng) hoặc đuôi unit text."""
        if not units:
            return ''
        last = units[-1]
        if last['math']:
            # vd chữ in nghiêng vừa emit: dùng làm base, gỡ ra
            if re.fullmatch(r'[A-Za-z0-9]+', last['s']):
                units.pop()
                return last['s']
            return ''
        m = re.search(r'[A-Za-z0-9]+$', last['s'])
        if m:
            base = m.group(0)
            last['s'] = last['s'][:m.start()]
            if not last['s']:
                units.pop()
            return base
        return ''

    i, n = 0, len(text)
    while i < n:
        ch = text[i]

        # Vectơ: combining arrow (U+20D7/20D1). Gộp nhiều mũi tên liên tiếp thành 1.
        if ch in ('⃗', '⃑'):
            j = i
            while j < n and text[j] in ('⃗', '⃑'):
                j += 1
            base = _take_base()
            if base:
                _emit_math(r'\vec{%s}' % base)
            # base rỗng → bỏ mũi tên thừa (tránh \vec{} rỗng)
            i = j
            continue

        # Nhiệt độ ký tự đơn ℃ ℉ → ký tự ° thật + C/F (text, không LaTeX)
        if ch in ('℃', '℉'):
            _emit_text('°C' if ch == '℃' else '°F')
            i += 1
            continue

        # Superscript-zero ⁰ và ký tự ordinal º hầu như luôn là dấu ĐỘ trong đề
        # Vật lý (35⁰C, góc 45⁰). Dùng ký tự ° THẬT (Unicode) — KHÔNG dùng \circ
        # vì nhiều công cụ LaTeX trên Word không hiểu \circ. ° hiển thị đúng sẵn.
        if ch in ('⁰', 'º'):
            _emit_text('°')
            i += 1
            continue

        # Số mũ (gộp chuỗi liên tiếp)
        if ch in _SUP:
            j, buf = i, ''
            while j < n and text[j] in _SUP:
                buf += _SUP[text[j]]
                j += 1
            base = _take_base()
            _emit_math('%s^{%s}' % (base, buf))
            i = j
            continue

        # Chỉ số dưới
        if ch in _SUB:
            j, buf = i, ''
            while j < n and text[j] in _SUB:
                buf += _SUB[text[j]]
                j += 1
            base = _take_base()
            _emit_math('%s_{%s}' % (base, buf))
            i = j
            continue

        # Độ / nhiệt độ: giữ ký tự ° THẬT (text), KHÔNG bọc LaTeX \circ
        # (công cụ LaTeX trên Word của thầy không hiểu \circ → để nguyên ° là chuẩn nhất)
        if ch == '°':
            _emit_text('°')
            i += 1
            continue

        if ch in _GREEK:
            _emit_math(_GREEK[ch])
            i += 1
            continue
        if ch in _SYM:
            _emit_math(_SYM[ch])
            i += 1
            continue

        # Chữ Toán in nghiêng/đậm Unicode (Latin hoặc Hy Lạp) → fold về chuẩn
        if ord(ch) >= 0x2100:
            nf = unicodedata.normalize('NFKC', ch)
            if len(nf) == 1 and nf in _GREEK:
                _emit_math(_GREEK[nf])
                i += 1
                continue
            if len(nf) == 1 and (('A' <= nf <= 'Z') or ('a' <= nf <= 'z')):
                _emit_math(nf)
                i += 1
                continue

        _emit_text(ch)
        i += 1

    # Gộp các cụm toán liền nhau (cho phép glue chỉ gồm khoảng trắng + toán tử)
    out = []
    k = 0
    while k < len(units):
        if units[k]['math']:
            buf = units[k]['s']
            k += 1
            while k < len(units):
                if units[k]['math']:
                    # nối trực tiếp 2 cụm toán
                    if buf and buf[-1] not in ' ' and not units[k]['s'].startswith('\\'):
                        buf += units[k]['s']
                    else:
                        buf += units[k]['s']
                    k += 1
                elif (k + 1 < len(units) and units[k + 1]['math']
                      and _GLUE_RE.match(units[k]['s']) and units[k]['s'] != ''):
                    buf += units[k]['s']   # glue (khoảng trắng/toán tử) giữa 2 cụm toán
                    k += 1
                else:
                    break
            out.append('$' + buf + '$')
        else:
            out.append(units[k]['s'])
            k += 1
    return ''.join(out)


# ── Chuẩn "file mẫu" (AIOMT) — to_mau_standard ─────────────────────
# Chuẩn đích rút từ `mẫu soạn latex.pdf` (chốt 04/07/2026, AIOMT V6.7 đã test PASS
# cả \circ lẫn \text{} — đảo ngược ghi chú 16/06 "AIOMT không hiểu \circ"):
#   - Đơn vị TRONG $...$ bọc \text{...}, số mũ đơn vị NGOÀI \text: $1,0\text{cm}^2$
#   - Nhiệt độ: $25^\circ\text{C}$  ·  Góc: $45^\circ$
#   - Ký hiệu khoa học: $2,0.10^5\text{Pa}$ (dấu chấm nhân, KHÔNG \cdot)
#   - Thập phân phẩy trần trong math: 0,460 (không {,})
#   - Số + đơn vị ĐƠN GIẢN (không mũ/độ/khoa học) giữ text thường, KHÔNG bọc $
# CHỈ dùng cho đường xuất WORD (AIOMT chuyển $...$); đường xuất PDF (matplotlib
# mathtext) KHÔNG dùng — mathtext không đảm bảo hỗ trợ \text{}.
# Gọi SAU unicode_to_latex + normalize_latex.

_MAU_UNITS = [
    # đơn vị ghép (dài trước — alternation ăn token khớp đầu tiên)
    'kJ/kg.K', 'J/kg.K', 'J/g.K', 'kg.m/s', 'g/cm', 'g/ml', 'kg/m',
    'rad/s', 'km/h', 'm/s', 'N.m', 'N/m', 'N/C', 'V/m', 'W/m', 'J/kg',
    'mmHg', 'kWh', 'Wh',
    # đơn vị đơn
    'mm', 'cm', 'dm', 'km', 'nm', 'pm', 'µm', 'μm', 'm',
    'mg', 'kg', 'g',
    'ms', 'µs', 'μs', 'ns', 's', 'min', 'h',
    'kN', 'N', 'kJ', 'MJ', 'J', 'kW', 'MW', 'W',
    'kPa', 'MPa', 'Pa', 'atm', 'bar',
    'kHz', 'MHz', 'GHz', 'Hz',
    'kΩ', 'MΩ', 'Ω', 'kV', 'mV', 'V', 'mA', 'µA', 'μA', 'A',
    'mC', 'µC', 'μC', 'nC', 'pC', 'C',
    'mF', 'µF', 'μF', 'nF', 'pF', 'F',
    'mH', 'H', 'mT', 'T', 'Wb', 'K', 'mol', 'ml', 'mL', 'l', 'L',
    'keV', 'MeV', 'eV', 'dB', 'rad',
]
_MAU_UNIT_ALT = '|'.join(re.escape(u) for u in
                         sorted(set(_MAU_UNITS), key=len, reverse=True))
_MAU_UNIT_SET = set(_MAU_UNITS)

# Vùng math (không chứa $ bên trong, không xuống dòng với inline)
_MAU_SPLIT = re.compile(r'(\$\$[^$]+?\$\$|\$[^$\n]+?\$)')

# Số đứng trước cụm math "đơn vị^mũ" do unicode_to_latex sinh: 1,0$cm^{2}$ /
# 1,$0cm^{2}$ (_take_base gộp chữ số vào base) / 10m/$s^{2}$ ('m/' rớt ngoài)
# → gộp lại 1 span. Nhóm: (số ngoài, có thể dở dang "1,")(đuôi đơn vị ngoài)
#                          $(số lọt vào trong)(đơn vị)(mũ)$
_MAU_MERGE_EXP = re.compile(
    r'(\d+(?:[.,]\d+)?[.,]?)?\s*([A-Za-zµμΩ/.]{0,7}?)'
    r'\$(\d*)([A-Za-zµμΩ/.]+)(\^\{?-?\d+\}?)\$')

# Sau đơn vị không được dính thêm chữ (kể cả tiếng Việt có dấu: "5 sẽ" ≠ 5 giây)
_MAU_UNIT_END = r'(?![A-Za-zµμΩÀ-Ỹà-ỹ0-9])'

# Khoa học bị tách: 2,0.$10^{5}$Pa → $2,0.10^{5}\text{Pa}$
_MAU_MERGE_SCI = re.compile(
    r'(\d+(?:,\d+)?)\s*[.·]\s*\$10(\^\{?-?\d+\}?)\$\s*'
    r'(' + _MAU_UNIT_ALT + r')?' + _MAU_UNIT_END)

# Span kết thúc bằng số/mũ + đơn vị trần theo sau: $1,5.10^{6}$ Hz → kéo vào \text{}
_MAU_MERGE_TRAIL_UNIT = re.compile(
    r'\$([^$\n]*[0-9}])\$\s*(' + _MAU_UNIT_ALT + r')' + _MAU_UNIT_END)

# Biến 1 chữ cái đứng trước span: g = $10\text{m/s}^2$ → $g = 10\text{m/s}^2$
_MAU_MERGE_VAR = re.compile(
    r'(?<![A-Za-zÀ-Ỹà-ỹ0-9])([A-Za-z])\s*=\s*\$([^$\n]+)\$')
# Span đứng trước " = số + đơn vị": $\lambda$ = 500nm → $\lambda = 500\text{nm}$
_MAU_MERGE_SPAN_EQ = re.compile(
    r'\$([^$\n]+)\$\s*=\s*(\d+(?:[.,]\d+)?)\s*(' + _MAU_UNIT_ALT + r')' + _MAU_UNIT_END)
# Biến 1 chữ cái + " = số + đơn vị" thuần text: c = 0,460kJ/kg.K → $c = 0,460\text{kJ/kg.K}$
_MAU_VAR_EQ_UNIT = re.compile(
    r'(?<![A-Za-zÀ-Ỹà-ỹ0-9])([A-Za-z])\s*=\s*(\d+(?:[.,]\d+)?)\s*'
    r'(' + _MAU_UNIT_ALT + r')' + _MAU_UNIT_END)


def _mau_fix_math_expr(expr: str) -> str:
    """Chuẩn hoá nội dung 1 biểu thức $...$ theo chuẩn file mẫu."""
    # {,} → , (thập phân phẩy trần)
    expr = expr.replace('{,}', ',')
    # \cdot / \times / × / · đứng trước 10^ → dấu chấm nhân
    expr = re.sub(r'\s*(?:\\cdot|\\times|[×·])\s*(?=10\s*\^)', '.', expr)
    # Nhiệt độ mọi biến thể → ^\circ\text{X}
    expr = re.sub(
        r'\^\s*\{?\s*\\circ\s*\}?\s*(?:\\text\{([CFK])\}|([CFK]))(?![A-Za-z])',
        lambda m: r'^\circ\text{%s}' % (m.group(1) or m.group(2)), expr)
    expr = re.sub(r'°\s*([CFK])(?![A-Za-z])', r'^\\circ\\text{\1}', expr)
    expr = expr.replace('°', r'^\circ')
    # Đơn vị trần sau chữ số (hoặc sau ^{n}) → \text{...}
    # Lookahead chặn } (tránh re-wrap chữ cuối trong \text{...} sẵn có)
    # và chặn _ (biến có chỉ số dưới V_{2}, p_{1}... KHÔNG phải đơn vị Volt;
    # vẫn cho ^ vì đơn vị mang mũ m^{2}, cm^{3} phải được bọc)
    expr = re.sub(
        r'([0-9}])\s*(' + _MAU_UNIT_ALT + r')(?![A-Za-zµμΩÀ-Ỹà-ỹ}_])',
        r'\1\\text{\2}', expr)
    # Mũ 1 chữ số bỏ ngoặc cho khớp mẫu: ^{2} → ^2 (giữ ^{-3}, ^{10})
    expr = re.sub(r'\^\{([0-9])\}', r'^\1', expr)
    return expr


def _mau_fix_plain(seg: str) -> str:
    """Xử lý đoạn NGOÀI math: chỉ bọc $ khi có cấu trúc toán (độ, khoa học,
    phương trình var=số+đơn vị) — số+đơn vị đơn giản giữ text thường theo mẫu."""
    # 25°C → $25^\circ\text{C}$
    seg = re.sub(r'(\d+(?:[.,]\d+)?)\s*°\s*([CFK])(?![A-Za-zÀ-Ỹà-ỹ])',
                 r'$\1^\\circ\\text{\2}$', seg)
    # Góc 45° → $45^\circ$
    seg = re.sub(r'(\d+(?:[.,]\d+)?)\s*°(?!\s*[CFK])', r'$\1^\\circ$', seg)

    # Khoa học ASCII: 2,0.10^5 Pa / 3.10^-6 → $2,0.10^5\text{Pa}$
    def _sci(m):
        num, exp, unit = m.group(1), m.group(2), m.group(3)
        exp = re.sub(r'^\{(-?\d)\}$', r'\1', exp)  # bỏ ngoặc mũ 1 ký tự
        out = '$%s.10^%s' % (num, exp)
        if unit:
            out += r'\text{%s}' % unit
        return out + '$'
    seg = re.sub(
        r'(\d+(?:,\d+)?)\s*[.·×]\s*10\^(\{?-?\d+\}?)\s*'
        r'(' + _MAU_UNIT_ALT + r')?' + _MAU_UNIT_END, _sci, seg)

    # c = 0,460kJ/kg.K (thuần text, không có gì kích hoạt math) → bọc chuẩn mẫu
    seg = _MAU_VAR_EQ_UNIT.sub(r'$\1 = \2\\text{\3}$', seg)
    return seg


def to_mau_standard(text: str) -> str:
    r"""
    Đưa văn bản (đã qua unicode_to_latex + normalize_latex) về chuẩn file mẫu:
      1,0$cm^{2}$        → $1,0\text{cm}^2$
      2,0.$10^{5}$Pa     → $2,0.10^5\text{Pa}$
      g = $10\text{m/s}^2$ → $g = 10\text{m/s}^2$   (kéo biến vào span)
      25°C (text thường)  → $25^\circ\text{C}$
      $v = 5 m/s$        → $v = 5\text{m/s}$
    CHỈ dùng cho đường xuất Word (AIOMT).
    """
    if not text:
        return text

    # 1) Gộp số + cụm math đơn-vị-mũ thành 1 span
    def _merge_exp(m):
        num_out, pre, num_in, base, exp = m.groups()
        unit = (pre or '') + base
        num = (num_out or '') + (num_in or '')
        # số ngoài kết thúc bằng . , thì phần số trong phải nối tiếp (vd "1," + "0")
        if num and num[-1] in '.,' and not num_in:
            return m.group(0)
        if unit in _MAU_UNIT_SET:
            return '$%s\\text{%s}%s$' % (num, unit, exp)
        return m.group(0)
    text = _MAU_MERGE_EXP.sub(_merge_exp, text)

    # 2) Gộp ký hiệu khoa học bị tách + đơn vị theo sau
    def _merge_sci(m):
        num, exp, unit = m.groups()
        out = '$%s.10%s' % (num, exp)
        if unit:
            out += r'\text{%s}' % unit
        return out + '$'
    text = _MAU_MERGE_SCI.sub(_merge_sci, text)

    # 2b) Span kết thúc bằng số/mũ + đơn vị trần theo sau → kéo đơn vị vào span
    text = _MAU_MERGE_TRAIL_UNIT.sub(
        lambda m: '$%s\\text{%s}$' % (m.group(1), m.group(2)), text)

    # 3) Kéo biến/span vào phương trình
    text = _MAU_MERGE_VAR.sub(lambda m: '$%s = %s$' % (m.group(1), m.group(2)), text)
    text = _MAU_MERGE_SPAN_EQ.sub(
        lambda m: '$%s = %s\\text{%s}$' % (m.group(1).strip(), m.group(2), m.group(3)),
        text)

    # 4) Chuẩn hoá từng vùng
    out = []
    for part in _MAU_SPLIT.split(text):
        if not part:
            continue
        if part.startswith('$$') and part.endswith('$$') and len(part) > 4:
            out.append('$$' + _mau_fix_math_expr(part[2:-2]) + '$$')
        elif part.startswith('$') and part.endswith('$') and len(part) > 2:
            out.append('$' + _mau_fix_math_expr(part[1:-1]) + '$')
        else:
            out.append(_mau_fix_plain(part))
    return ''.join(out)
