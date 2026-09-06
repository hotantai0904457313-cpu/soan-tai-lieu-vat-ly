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
# $$...$$ display — cho phép nhiều dòng nhưng KHÔNG vắt qua dòng trống hay qua
# dòng phương án "A. …" (một $$ lẻ ở đề từng ghép cặp với $$ trong phương án
# rồi gộp cả khối đề + phương án thành 1 dòng)
MATH_DISPLAY_RE = re.compile(
    r'\$\$((?:(?!\n[ \t]*\n)(?!\n[ \t]*\**[A-Da-d][.)][ \t]).)+?)\$\$', re.DOTALL)


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
        if line.count('$$') == 1 and line.count('$') == 2:
            # $$ mồ côi — 2 dấu $ duy nhất của dòng (cặp $$ kia nằm ở dòng khác,
            # thường là dòng phương án). Đuôi trông như công thức → bọc $…$ inline;
            # không thì gỡ hẳn. ($a$$b$ — hai span dính nhau — có 4 dấu $, không dính.)
            k = line.find('$$')
            head, tail = line[:k], line[k + 2:]
            opt_like = re.match(r'\s*\**[A-Da-d][.)]\s', tail)
            if (tail.strip() and len(tail) <= 80 and not opt_like
                    and not _VN_DIACRITIC.search(tail) and _MATH_SIGNAL_RE.search(tail)):
                lines[i] = head + '$' + tail.strip() + '$'
            else:
                lines[i] = head + tail
            continue
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

# Đơn vị tiếng Việt → ký hiệu (chỉ áp dụng bên trong \text{...} và bên trong
# $...$). AIOMT không chuyển được chữ có dấu; bỏ dấu suông cho ra \text{lit}
# (chữ l·i·t nghiêng) nên đổi hẳn sang ký hiệu SI. Thầy chốt 06/09/2026: lít → l.
_LIT_SYM = 'l'
_VN_UNIT_MAP = {
    'lít': _LIT_SYM, 'lit': _LIT_SYM,
    'phút': 'min', 'phut': 'min',
    'giây': 's', 'giay': 's',
    'giờ': 'h', 'gio': 'h',
}
_VN_UNIT_WORDS = '|'.join(sorted(_VN_UNIT_MAP, key=len, reverse=True))
_VN_UNIT_WORD_RE = re.compile(
    r'(?<![A-Za-zÀ-Ỹà-ỹ])(' + _VN_UNIT_WORDS + r')(?![A-Za-zÀ-Ỹà-ỹ])')
_TEXT_CMD_RE = re.compile(r'\\text\{([^{}]*)\}')
# Đơn vị tiếng Việt trần trong math: "2 lít" → "2\text{l}"; "lít/phút" → \text{l}/\text{min}
_VN_BARE_AFTER_NUM_RE = re.compile(
    r'([0-9}])[ \t]*(' + _VN_UNIT_WORDS + r')(?![A-Za-zÀ-Ỹà-ỹ])')
_VN_BARE_RE = re.compile(
    r'(?<![A-Za-zÀ-Ỹà-ỹ\\{])(' + _VN_UNIT_WORDS + r')(?![A-Za-zÀ-Ỹà-ỹ}])')


def normalize_vn_units(text: str) -> str:
    r"""\text{lít} → \text{l}, \text{lít/phút} → \text{l/min}, \text{km/giờ} → \text{km/h}."""
    if not text or '\\text{' not in text:
        return text
    return _TEXT_CMD_RE.sub(
        lambda m: '\\text{%s}' % _VN_UNIT_WORD_RE.sub(
            lambda u: _VN_UNIT_MAP[u.group(1)], m.group(1)),
        text)


# Vùng math (không chứa $ bên trong, không xuống dòng với inline)
_MAU_SPLIT = re.compile(r'(\$\$[^$]+?\$\$|\$[^$\n]+?\$)')
# Display math tách riêng để xử lý theo DÒNG phần còn lại
_MAU_DISPLAY_SPLIT = re.compile(r'(\$\$[^$]*\$\$)')

# MỌI khoảng trắng trong các regex gộp dưới đây là [ \t] — KHÔNG được xuyên \n:
# phương án "A. 2 s" / "C. 6 s" ở dòng dưới từng bị nuốt thành đơn vị Ampe/Coulomb
# ($h = 20\text{A}$. 2 s) → nội dung phương án "nhảy lên đề".

# Số đứng trước cụm math "đơn vị^mũ" do unicode_to_latex sinh: 1,0$cm^{2}$ /
# 1,$0cm^{2}$ (_take_base gộp chữ số vào base) / 10m/$s^{2}$ ('m/' rớt ngoài)
# → gộp lại 1 span. Nhóm: (số ngoài)(khoảng trắng)(đuôi đơn vị ngoài)
#   $(số lọt vào trong)(đơn vị)(mũ)(phần còn lại của span — phải bắt đầu bằng
#   toán tử hoặc lệnh LaTeX: "0,5 $m^3 \rightarrow \rho$")$
_MAU_MERGE_EXP = re.compile(
    r'(\d+(?:[.,]\d+)?[.,]?)?([ \t]*)([A-Za-zµμΩ/.]{0,7}?)'
    r'\$(\d*)([A-Za-zµμΩ/.]+)(\^\{?-?\d+\}?)'
    r'((?:[ \t]*(?:[=<>≤≥≈]|\\[A-Za-z]+)[^$\n]*)?)\$')

# Sau đơn vị không được dính thêm chữ (kể cả tiếng Việt có dấu: "5 sẽ" ≠ 5 giây)
_MAU_UNIT_END = r'(?![A-Za-zµμΩÀ-Ỹà-ỹ0-9])'

# Khoa học bị tách: 2,0.$10^{5}$Pa → $2,0.10^{5}\text{Pa}$ ;
# 3,3.$10^{-3} m^{3}$ (unicode_to_latex gộp mũ+đơn vị vào span) → kéo cả đuôi
_MAU_MERGE_SCI = re.compile(
    r'(\d+(?:,\d+)?)[ \t]*[.·][ \t]*\$10(\^\{?-?\d+\}?)([^$\n]*)\$[ \t]*'
    r'(' + _MAU_UNIT_ALT + r')?' + _MAU_UNIT_END)

# Span kết thúc bằng SỐ ĐỨNG RIÊNG hoặc mũ, theo sau là đơn vị trần: $1,5.10^{6}$ Hz
# → kéo vào \text{}. Span kết thúc bằng biến có chỉ số ($p_1V_1 = p_2V_2$) thì
# chữ theo sau là văn xuôi ("V là thể tích"), KHÔNG phải đơn vị Volt.
_MAU_MERGE_TRAIL_UNIT = re.compile(
    r'\$([^$\n]*?(?:(?<![A-Za-z_\\])\d+(?:[.,]\d+)?|\^\{?-?\d+\}?))\$[ \t]*'
    r'(' + _MAU_UNIT_ALT + r')' + _MAU_UNIT_END)

# Biến 1 chữ cái đứng trước span: g = $10\text{m/s}^2$ → $g = 10\text{m/s}^2$
_MAU_MERGE_VAR = re.compile(
    r'(?<![A-Za-zÀ-Ỹà-ỹ0-9])([A-Za-z])[ \t]*=[ \t]*\$([^$\n]+)\$')
# Span đứng trước " = số + đơn vị": $\lambda$ = 500nm → $\lambda = 500\text{nm}$
_MAU_MERGE_SPAN_EQ = re.compile(
    r'\$([^$\n]+)\$[ \t]*=[ \t]*(\d+(?:[.,]\d+)?)[ \t]*(' + _MAU_UNIT_ALT + r')' + _MAU_UNIT_END)
# Biến 1 chữ cái + " = số + đơn vị" thuần text: c = 0,460kJ/kg.K → $c = 0,460\text{kJ/kg.K}$
_MAU_VAR_EQ_UNIT = re.compile(
    r'(?<![A-Za-zÀ-Ỹà-ỹ0-9])([A-Za-z])[ \t]*=[ \t]*(\d+(?:[.,]\d+)?)[ \t]*'
    r'(' + _MAU_UNIT_ALT + r')' + _MAU_UNIT_END)

# Chữ đơn vừa là đơn vị (Ampe, Coulomb, Volt, Tesla…) vừa là chữ phương án A–D
# hoặc tên biến (V thể tích, T chu kỳ, A biên độ…).
_MAU_AMBIG = set('ACVFTNJWKHLSM')
_OPT_AFTER_RE = re.compile(r'[.)][ \t]')
_OPT_LATER_RE = re.compile(r'(?:^|\s)\**[B-D][.)][ \t]')
# Chữ đang được dùng làm BIẾN trong biểu thức: V_1, V', V = …, \Delta V
# (KHÔNG tính V^2 — "m^3" là đơn vị mét khối chứ không phải biến m mũ 3).
# Chỉ chặn với chữ HOA (V thể tích/Volt, T chu kỳ/Tesla, A biên độ/Ampe…);
# chữ thường m, s, g, h, l sau chữ số luôn là đơn vị (mét, giây, gam…).
_VAR_LETTER_RE = re.compile(
    r"(?<![A-Za-z\\])([A-Z])(?=[_']|[ \t]*=)|\\[Dd]elta[ \t]*([A-Z])")
# $A$ = $B$ → $A = B$ (hai vế cùng một phương trình bị tách 2 span)
_MAU_MERGE_SPAN_SPAN = re.compile(r'\$([^$\n]+)\$[ \t]*=[ \t]*\$([^$\n]+)\$')
# Hai span DÍNH nhau $10^5$$\text{N/m}^2$ (display đã tách riêng trước đó nên
# $$ giữa dòng chỉ có thể là 2 span inline kề nhau) → gộp 1 span
_MAU_MERGE_ADJ = re.compile(r'\$([^$\n]+)\$\$([^$\n]+)\$')


def _merge_adj(m):
    a, b = m.group(1), m.group(2)
    glue = '' if (b.startswith('\\text{') and re.search(r'[\d}]$', a)) else ' '
    return '$%s%s%s$' % (a, glue, b)


def _ambig_ok(unit: str, s: str, end: int) -> bool:
    """False khi 'đơn vị' 1 chữ A–D thực ra là chữ phương án của dòng chưa tách
    ("h = 20 A. 2 s B. 4 s")."""
    if unit not in _MAU_AMBIG:
        return True
    if unit in 'ABCD' and _OPT_AFTER_RE.match(s, end) and _OPT_LATER_RE.search(s, end):
        return False
    return True


def _norm_exp(exp: str) -> str:
    """Mũ theo mẫu: ^{2}→2 (dương 1 chữ số bỏ ngoặc); -3 / {-3} / 12 → {-3} / {12}."""
    e = exp.strip('{}')
    return e if re.fullmatch(r'\d', e) else '{%s}' % e


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
    # Đơn vị tiếng Việt: \text{lít}→\text{l}; "2 lít"→2\text{l}; "lít/phút"→\text{l/min}
    expr = normalize_vn_units(expr)
    expr = _VN_BARE_AFTER_NUM_RE.sub(
        lambda m: '%s\\text{%s}' % (m.group(1), _VN_UNIT_MAP[m.group(2)]), expr)
    expr = _VN_BARE_RE.sub(lambda m: '\\text{%s}' % _VN_UNIT_MAP[m.group(1)], expr)
    expr = re.sub(r'\\text\{([^{}]+)\}/\\text\{([^{}]+)\}', r'\\text{\1/\2}', expr)
    # Đơn vị trần sau chữ số (hoặc sau ^{n}) → \text{...}
    # Lookahead chặn } (tránh re-wrap chữ cuối trong \text{...} sẵn có)
    # và chặn _ (biến có chỉ số dưới V_{2}, p_{1}... KHÔNG phải đơn vị Volt;
    # vẫn cho ^ vì đơn vị mang mũ m^{2}, cm^{3} phải được bọc).
    # Chữ đơn đang là BIẾN trong cùng biểu thức (V' = 2V, \Delta V = 2V) thì
    # KHÔNG phải đơn vị.
    var_letters = {a or b for a, b in _VAR_LETTER_RE.findall(expr)}

    def _wrap_unit(m):
        unit = m.group(2)
        if len(unit) == 1 and unit in var_letters:
            return m.group(0)
        if not _ambig_ok(unit, m.string, m.end()):
            return m.group(0)
        return '%s\\text{%s}' % (m.group(1), unit)
    expr = re.sub(
        r'([0-9}])\s*(' + _MAU_UNIT_ALT + r')(?![A-Za-zµμΩÀ-Ỹà-ỹ}_])',
        _wrap_unit, expr)
    # Mũ 1 chữ số bỏ ngoặc cho khớp mẫu: ^{2} → ^2 (giữ ^{-3}, ^{10})
    expr = re.sub(r'\^\{([0-9])\}', r'^\1', expr)
    return expr


# Lưới an toàn cuối cho đoạn NGOÀI math: lệnh LaTeX trần (\text{m}^3, \frac{a}{b})
# còn sót → bọc $ (kèm số đứng ngay trước). Word không được nhận LaTeX trần.
_RAW_LATEX_RE = re.compile(
    r'(\d+(?:[.,]\d+)?)?([ \t]*)'
    r'(\\(?:text|frac|sqrt|vec|mathrm)\{[^{}\n]*\}(?:\{[^{}\n]*\})?(?:\^\{?-?\d+\}?)?)')
# Tiền tố "V = " tuỳ chọn cho các luật khoa học ở đoạn ngoài math
_MAU_VAR_PREFIX = r'(?:(?<![A-Za-zÀ-Ỹà-ỹ0-9])([A-Za-z])[ \t]*=[ \t]*)?'
_MAU_UNIT_EXP = r'(?:(' + _MAU_UNIT_ALT + r')(\^\{?-?\d+\}?)?)?' + _MAU_UNIT_END


def _sub_outside_math(pattern, repl, seg: str) -> str:
    """re.sub CHỈ trên phần ngoài $...$ — các luật ở _mau_fix_plain sinh span mới,
    luật sau không được đụng vào span luật trước vừa tạo (từng bọc $ chồng $)."""
    out = []
    for part in _MAU_SPLIT.split(seg):
        if not part:
            continue
        if part.startswith('$') and part.endswith('$') and len(part) > 2:
            out.append(part)
        else:
            out.append(re.sub(pattern, repl, part))
    return ''.join(out)


def _mau_fix_plain(seg: str) -> str:
    """Xử lý đoạn NGOÀI math: chỉ bọc $ khi có cấu trúc toán (độ, khoa học,
    phương trình var=số+đơn vị) — số+đơn vị đơn giản giữ text thường theo mẫu."""
    # 25°C → $25^\circ\text{C}$
    seg = _sub_outside_math(r'(\d+(?:[.,]\d+)?)\s*°\s*([CFK])(?![A-Za-zÀ-Ỹà-ỹ])',
                            r'$\1^\\circ\\text{\2}$', seg)
    # Góc 45° → $45^\circ$
    seg = _sub_outside_math(r'(\d+(?:[.,]\d+)?)\s*°(?!\s*[CFK])', r'$\1^\\circ$', seg)

    def _unit_tail(unit, uexp):
        if not unit:
            return ''
        out = r'\text{%s}' % unit
        if uexp:
            out += '^' + _norm_exp(uexp[1:])
        return out

    # Khoa học ASCII: V = 3,3.10^{-3} m^3 / 2,0.10^5 Pa / 3.10^-6
    # → $V = 3,3.10^{-3}\text{m}^3$ (mũ đơn vị đi CÙNG span, mũ âm giữ ngoặc)
    def _sci(m):
        var, num, exp, unit, uexp = m.groups()
        out = '$' + ('%s = ' % var if var else '')
        out += '%s.10^%s' % (num, _norm_exp(exp))
        return out + _unit_tail(unit, uexp) + '$'
    seg = _sub_outside_math(
        _MAU_VAR_PREFIX + r'(\d+(?:,\d+)?)[ \t]*[.·×][ \t]*10\^(\{?-?\d+\}?)[ \t]*'
        + _MAU_UNIT_EXP, _sci, seg)

    # Luỹ thừa 10 không có phần định trị: V = 10^{-3} m^3 → $V = 10^{-3}\text{m}^3$
    def _pow(m):
        var, exp, unit, uexp = m.groups()
        out = '$' + ('%s = ' % var if var else '') + '10^' + _norm_exp(exp)
        return out + _unit_tail(unit, uexp) + '$'
    seg = _sub_outside_math(
        _MAU_VAR_PREFIX + r'(?<![\d,.^{])10\^(\{?-?\d+\}?)[ \t]*' + _MAU_UNIT_EXP,
        _pow, seg)

    # c = 0,460kJ/kg.K (thuần text, không có gì kích hoạt math) → bọc chuẩn mẫu
    def _var_eq_unit(m):
        if not _ambig_ok(m.group(3), m.string, m.end()):
            return m.group(0)
        return '$%s = %s\\text{%s}$' % m.groups()
    seg = _sub_outside_math(_MAU_VAR_EQ_UNIT, _var_eq_unit, seg)

    # Lưới an toàn: LaTeX trần còn sót ngoài $ → bọc lại
    seg = _sub_outside_math(
        _RAW_LATEX_RE,
        lambda m: '%s$%s%s$' % ('' if m.group(1) else m.group(2),
                               m.group(1) or '', m.group(3)), seg)
    return seg


def _merge_exp(m):
    num_out, ws, pre, num_in, base, exp, tail = m.groups()
    unit = (pre or '') + base
    num = (num_out or '') + (num_in or '')
    # số ngoài kết thúc bằng . , thì phần số trong phải nối tiếp (vd "1," + "0")
    if num and num[-1] in '.,' and not num_in:
        return m.group(0)
    if unit in _MAU_UNIT_SET:
        # Không gộp số/đuôi nào → giữ khoảng trắng đứng trước ("sang $m^3$")
        lead = ws if not (num_out or pre) else ''
        return '%s$%s\\text{%s}%s%s$' % (lead, num, unit, exp, tail or '')
    return m.group(0)


def _merge_sci(m):
    num, exp, tail, unit = m.groups()
    if unit and not _ambig_ok(unit, m.string, m.end()):
        return m.group(0)
    out = '$%s.10%s%s' % (num, exp, tail or '')
    if unit:
        out += r'\text{%s}' % unit
    return out + '$'


def _merge_trail(m):
    if not _ambig_ok(m.group(2), m.string, m.end()):
        return m.group(0)
    return '$%s\\text{%s}$' % (m.group(1), m.group(2))


def _merge_span_eq(m):
    if not _ambig_ok(m.group(3), m.string, m.end()):
        return m.group(0)
    return '$%s = %s\\text{%s}$' % (m.group(1).strip(), m.group(2), m.group(3))


def _merge_pass(text: str) -> str:
    """Các bước gộp span (1–3). Chạy trước VÀ sau bước chuẩn hoá từng vùng,
    vì bước 4 có thể sinh span mới cần kéo biến vào (V = $10^{-3}\\text{m}^3$)."""
    text = _MAU_MERGE_EXP.sub(_merge_exp, text)                     # 1
    text = _MAU_MERGE_SCI.sub(_merge_sci, text)                     # 2
    text = _MAU_MERGE_TRAIL_UNIT.sub(_merge_trail, text)            # 2b
    text = _MAU_MERGE_VAR.sub(lambda m: '$%s = %s$' % (m.group(1), m.group(2)), text)  # 3
    text = _MAU_MERGE_SPAN_EQ.sub(_merge_span_eq, text)
    text = _MAU_MERGE_SPAN_SPAN.sub(r'$\1 = \2$', text)
    text = _MAU_MERGE_ADJ.sub(_merge_adj, text)
    return text


def _to_mau_line(text: str) -> str:
    """to_mau_standard cho MỘT dòng (không chứa \\n, không chứa $$ display)."""
    if not text:
        return text
    text = _merge_pass(text)
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
    return _merge_pass(''.join(out))


def to_mau_standard(text: str) -> str:
    r"""
    Đưa văn bản (đã qua unicode_to_latex + normalize_latex) về chuẩn file mẫu:
      1,0$cm^{2}$        → $1,0\text{cm}^2$
      2,0.$10^{5}$Pa     → $2,0.10^5\text{Pa}$
      g = $10\text{m/s}^2$ → $g = 10\text{m/s}^2$   (kéo biến vào span)
      25°C (text thường)  → $25^\circ\text{C}$
      $v = 5 m/s$        → $v = 5\text{m/s}$
      $2\text{lít}$      → $2\text{l}$
    Xử lý THEO TỪNG DÒNG (display $$…$$ nhiều dòng giữ nguyên khối) — regex gộp
    không được phép nuốt chữ phương án ở dòng dưới. Idempotent.
    CHỈ dùng cho đường xuất Word (AIOMT).
    """
    if not text:
        return text
    text = normalize_vn_units(text)
    out = []
    for chunk in _MAU_DISPLAY_SPLIT.split(text):
        if not chunk:
            continue
        if chunk.startswith('$$') and chunk.endswith('$$') and len(chunk) > 4:
            out.append('$$' + _mau_fix_math_expr(chunk[2:-2]) + '$$')
        else:
            out.append('\n'.join(_to_mau_line(line) for line in chunk.split('\n')))
    return ''.join(out)



# ── LaTeX → chữ Unicode đọc được (cho đường xuất PDF) ────────────────
# BẤT BIẾN: LaTeX $...$ CHỈ dành cho đường xuất WORD (add-in AIOMT chuyển
# thành công thức thật). Mọi đường khác — nhất là PDF — phải render thành
# hình hoặc hạ xuống ký tự đọc được; TUYỆT ĐỐI không để lọt \frac, \sqrt,
# \text vào file học sinh cầm trên tay.

_LATEX_TO_SYM = {}          # '\alpha' -> 'α'  (đảo _GREEK và _SYM)
for _u, _l in list(_GREEK.items()) + list(_SYM.items()):
    _LATEX_TO_SYM.setdefault(_l, _u)
_LATEX_TO_SYM[r'\circ'] = '°'
_LATEX_TO_SYM[r'\sqrt{}'] = '√'
_LATEX_TO_SYM[r'\ '] = ' '
_LATEX_TO_SYM[r'\,'] = ' '
_LATEX_TO_SYM[r'\;'] = ' '
_LATEX_TO_SYM[r'\!'] = ''
# Khớp lệnh DÀI trước (\varphi trước \phi, \Rightarrow trước \rightarrow)
_LATEX_SYM_RE = re.compile(
    '|'.join(re.escape(k) for k in sorted(_LATEX_TO_SYM, key=len, reverse=True)))

# CHỈ dùng chữ số và dấu — ký tự mũ/chỉ số dạng CHỮ (ₘ ₐ ₓ ⁿ) thiếu glyph
# trong Times New Roman → PDF hiện ô vuông. Chữ giữ dạng ^n / _max cho chắc.
_SUP_CHARS = {'0': '⁰', '1': '¹', '2': '²', '3': '³', '4': '⁴', '5': '⁵',
              '6': '⁶', '7': '⁷', '8': '⁸', '9': '⁹', '+': '⁺', '-': '⁻',
              '°': '°'}
_SUB_CHARS = {'0': '₀', '1': '₁', '2': '₂', '3': '₃', '4': '₄', '5': '₅',
              '6': '₆', '7': '₇', '8': '₈', '9': '₉', '+': '₊', '-': '₋'}

_GREEK_CHARS = set(_GREEK)      # {'α','β','Δ',...} — chữ cái, khác toán tử

# Ký tự đã là mũ/chỉ số — tính là "một hạng tử đơn" khi cân nhắc thêm ngoặc.
# ¹²³ nằm ở Latin-1 (U+00B9/B2/B3), TÁCH RỜI khối ⁰⁴-⁹ (U+2070+) → phải liệt kê.
# Gồm cả ^ _ vì bước phân số chạy TRƯỚC bước đổi mũ/chỉ số: lúc đó "v_0^2"
# vẫn còn dạng thô, thiếu chúng thì bị bọc ngoặc thừa → "(v₀²)/2g".
_SIMPLE_TERM = r'[0-9A-Za-zÀ-Ỹà-ỹ₀-₉⁰-⁹¹²³°.,⃗^_]+'

# Có dấu hiệu LaTeX thật (lệnh \abc, ^{..}, _{..}) — dùng để biết đoạn text
# thường có lẫn LaTeX trần (AI đôi khi quên bọc $)
_HAS_LATEX_RE = re.compile(r'\\[A-Za-z]{2,}|\\[\[\]()]|[\^_]\{')


def has_latex(text: str) -> bool:
    """True nếu đoạn text còn lệnh LaTeX (kể cả khi không có dấu $)."""
    return bool(text) and bool(_HAS_LATEX_RE.search(text))


def _script_chars(s: str, table: dict) -> str | None:
    """Đổi chuỗi sang ký tự mũ/chỉ số Unicode; None nếu có ký tự không đổi được."""
    out = []
    for ch in s:
        if ch in table:
            out.append(table[ch])
        elif ch == ' ':
            continue
        else:
            return None
    return ''.join(out)


def latex_to_plain(expr: str) -> str:
    r"""Hạ biểu thức LaTeX xuống chữ Unicode đọc được — lưới an toàn cho PDF.

    \frac{v_0^2}{2g} → v₀²/2g · \sqrt{2gh} → √(2gh) · \text{m/s} → m/s
    25^\circ\text{C} → 25°C · \vec{F} → F⃗ · \alpha → α · \times → ×
    """
    if not expr:
        return ''
    s = expr

    # 1) Bỏ delimiter còn sót
    s = s.replace('$$', '').replace('$', '')
    s = re.sub(r'\\[\[\]()]', '', s)
    s = re.sub(r'\\(?:left|right|big|Big|bigg|Bigg)\s*', '', s)

    # 2) Gỡ lớp bọc chữ thường: \text{...} \mathrm{...} \mathbf{...}
    for _ in range(4):
        s2 = re.sub(r'\\(?:text|textbf|textit|mathrm|mathbf|mathit|mathsf|operatorname)'
                    r'\s*\{([^{}]*)\}', r'\1', s)
        if s2 == s:
            break
        s = s2

    # 3) Vectơ: \vec{F} → F⃗ (dấu mũi tên tổ hợp, hiển thị đúng trên PDF)
    for _ in range(3):
        s2 = re.sub(r'\\(?:vec|overrightarrow)\s*\{([^{}]*)\}', '\\1\u20d7', s)
        if s2 == s:
            break
        s = s2

    # 4) Căn: \sqrt[3]{x} → ∛(x) · \sqrt{x} → √x (bỏ ngoặc khi chỉ 1 hạng tử)
    def _sqrt(m):
        idx, body = m.group(1), m.group(2)
        sym = {'3': '∛', '4': '∜'}.get((idx or '').strip(), '√')
        simple = re.fullmatch(_SIMPLE_TERM, body or '')
        return sym + (body if simple else '(' + body + ')')
    for _ in range(4):
        s2 = re.sub(r'\\sqrt\s*(?:\[([^\]]*)\])?\s*\{([^{}]*)\}', _sqrt, s)
        if s2 == s:
            break
        s = s2

    # 5) Phân số: \frac{a}{b} → a/b, thêm ngoặc khi tử/mẫu có nhiều hạng tử
    def _frac(m):
        a, b = m.group(1).strip(), m.group(2).strip()
        wrap = lambda x: x if re.fullmatch(_SIMPLE_TERM, x) else '(' + x + ')'
        return wrap(a) + '/' + wrap(b)
    for _ in range(5):
        s2 = re.sub(r'\\(?:d|t)?frac\s*\{([^{}]*)\}\s*\{([^{}]*)\}', _frac, s)
        if s2 == s:
            break
        s = s2

    # 6) Ký hiệu & chữ Hy Lạp (khớp lệnh dài trước).
    #    Khoảng trắng sau lệnh là dấu KẾT THÚC TÊN LỆNH, không phải space thật:
    #    "\Delta U" nghĩa là ΔU chứ không phải "Δ U". Nhưng "\alpha + \beta"
    #    thì space quanh toán tử là thật → chỉ nuốt khi ký tự kế là chữ/số.
    def _sym(m):
        rep = _LATEX_TO_SYM[m.group(0)]
        nxt = m.string[m.end():]
        # CHỈ nuốt sau chữ cái Hy Lạp (\Delta U → ΔU). Toán tử thì space là
        # thật và phải giữ: "\times 10" → "× 10", "\geq 5" → "≥ 5"
        if rep in _GREEK_CHARS and nxt[:1] == ' ' and nxt[1:2].isalnum():
            return rep + '\x00'          # đánh dấu space cần nuốt
        return rep
    s = _LATEX_SYM_RE.sub(_sym, s)
    s = re.sub(r'\x00 ?', '', s)

    # 7) Mũ / chỉ số → ký tự Unicode; không đổi được thì giữ ^ _ cho dễ đọc
    def _sup(m):
        body = m.group(1) or m.group(2)
        return _script_chars(body, _SUP_CHARS) or ('^' + body)
    def _sub(m):
        body = m.group(1) or m.group(2)
        return _script_chars(body, _SUB_CHARS) or ('_' + body)
    for _ in range(3):
        s2 = re.sub(r'\^\s*(?:\{([^{}]*)\}|([0-9A-Za-z+\-°]))', _sup, s)
        s2 = re.sub(r'_\s*(?:\{([^{}]*)\}|([0-9A-Za-z+\-]))', _sub, s2)
        if s2 == s:
            break
        s = s2

    # 8) Lệnh còn lại: bỏ dấu \ giữ tên (\sin → sin, \log → log)
    s = re.sub(r'\\([A-Za-z]+)', r'\1', s)
    s = s.replace('\\', '')

    # 9) Dọn ngoặc nhọn thừa + khoảng trắng
    s = s.replace('{', '').replace('}', '')
    s = re.sub(r'[ \t]{2,}', ' ', s)
    return s.strip()


def plain_if_latex(text: str) -> str:
    """Chỉ hạ xuống Unicode khi đoạn text thực sự còn LaTeX — text thường
    (kể cả có ký hiệu ° ² sẵn) giữ nguyên không đụng tới."""
    return latex_to_plain(text) if has_latex(text) else (text or '')


# ── Bỏ dấu tiếng Việt TRONG công thức (chỉ cho đường xuất Word/AIOMT) ─
# AIOMT không chuyển được chữ có dấu nằm trong $...$ ("v khí", "m_{đá}").
# Nặng hơn: quy tắc _is_real_math coi span có dấu tiếng Việt là "câu chữ" nên
# GỠ LUÔN dấu $ → AIOMT không hề thấy đó là công thức. Bỏ dấu trước khi chuẩn
# hoá vừa cứu được dấu $, vừa cho AIOMT chuyển trọn công thức.
# CHỈ đụng phần trong $...$ — văn xuôi ngoài công thức giữ nguyên dấu tiếng Việt.

# Dấu hiệu "đây là công thức thật": có lệnh LaTeX, chỉ số/số mũ, hoặc phép tính
# (lệnh LaTeX là r'\\[A-Za-z]+' — từng thiếu 1 dấu \ nên \text{lít} không được coi
#  là công thức → không bỏ dấu → unwrap_pseudo_math gỡ luôn $ → LaTeX thô trong Word)
_MATH_SIGNAL_RE = re.compile(r'\\[A-Za-z]+|[_^]|=\s*[-\d.,]|\d\s*[+\-*/]\s*\d')


def _deaccent_vn(s: str) -> str:
    """Bỏ dấu thanh/dấu phụ tiếng Việt: 'khí'→'khi', 'đá'→'da', 'nước'→'nuoc'.
    CHỈ bỏ dấu phụ Latin (U+0300–U+036F) — giữ nguyên mũi tên vectơ U+20D7
    và mọi ký hiệu toán khác."""
    out = unicodedata.normalize('NFD', s)
    out = ''.join(c for c in out if not ('\u0300' <= c <= '\u036f'))
    out = unicodedata.normalize('NFC', out)
    return out.replace('đ', 'd').replace('Đ', 'D')


def strip_accents_in_math(text: str, max_vn_words: int = 3) -> str:
    """Bỏ dấu tiếng Việt trong các span $...$ TRÔNG NHƯ CÔNG THỨC.

    Span là văn xuôi bị bọc nhầm $ (nhiều chữ có dấu, không có ký hiệu toán)
    thì GIỮ NGUYÊN dấu — để unwrap_pseudo_math gỡ $ như cũ, tránh đẩy cả câu
    tiếng Việt vào AIOMT và làm mất dấu văn bản.
    """
    if not text or '$' not in text:
        return text

    def _fix(m):
        whole = m.group(0)
        inner = m.group(1)
        if not _VN_DIACRITIC.search(inner):
            return whole
        # Đáp án 'A. ... B. ...' / ý 'a) ...' → là text, không đụng
        if re.search(r'(?:^|\s)(?:[A-D]\.|[a-d]\))\s', inner):
            return whole
        if not _MATH_SIGNAL_RE.search(inner):
            return whole
        vn_words = sum(1 for w in inner.split() if _VN_DIACRITIC.search(w))
        if vn_words > max_vn_words:
            return whole            # cả câu tiếng Việt → để nguyên cho unwrap
        # Đơn vị tiếng Việt trong \text{} đổi sang ký hiệu (lít→l, phút→min)
        # TRƯỚC khi bỏ dấu — tránh ra \text{lit} (chữ l·i·t nghiêng vô nghĩa)
        return whole.replace(inner, _deaccent_vn(normalize_vn_units(inner)))

    text = re.sub(r'\$\$(.+?)\$\$', _fix, text, flags=re.DOTALL)
    text = re.sub(r'\$([^$\n]+?)\$', _fix, text)
    return text
