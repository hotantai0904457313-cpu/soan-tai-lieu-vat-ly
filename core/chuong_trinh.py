"""
Chương trình Vật lí THPT — CT GDPT 2018 (SGK Kết nối tri thức).

Dùng để RÀNG BUỘC PHẠM VI KIẾN THỨC khi AI giải bài: học sinh mới học tới đâu
thì lời giải chỉ được dùng kiến thức tới đó, tránh giải bài lớp 10 bằng bảo toàn
cơ năng (chưa học) hay giải dao động bằng tích phân/số phức (toán đại học).

Thầy cô có thể sửa trực tiếp danh mục bên dưới cho khớp sách đang dạy —
không cần đụng tới code xử lý.
"""
from typing import Optional

# Mỗi chương: id · ten · kien_thuc (liệt kê khái niệm/công thức cốt lõi,
# chính là phần được đưa vào prompt cho AI).
CHUONG_TRINH = {
    10: {
        'ten': 'Lớp 10',
        'cong_cu_toan': (
            'đại số, hình học phẳng, lượng giác cơ bản (sin, cos, tan), vectơ. '
            'CHƯA học đạo hàm, tích phân, số phức'),
        'chuong': [
            {'id': '10.1', 'ten': 'Mở đầu', 'kien_thuc':
                'đối tượng và phương pháp nghiên cứu Vật lí; an toàn trong phòng thực hành; '
                'sai số phép đo (sai số tuyệt đối, sai số tương đối, cách ghi kết quả đo)'},
            {'id': '10.2', 'ten': 'Động học', 'kien_thuc':
                'độ dịch chuyển và quãng đường; tốc độ, vận tốc; đồ thị độ dịch chuyển–thời gian; '
                'gia tốc, đồ thị vận tốc–thời gian; chuyển động thẳng biến đổi đều '
                '($v = v_0 + at$, $s = v_0t + \\frac{1}{2}at^2$, $v^2 - v_0^2 = 2as$); '
                'sự rơi tự do; chuyển động ném ngang và ném xiên; tính tương đối của chuyển động '
                '(tổng hợp vận tốc)'},
            {'id': '10.3', 'ten': 'Động lực học', 'kien_thuc':
                'tổng hợp và phân tích lực; ba định luật Newton; trọng lực; lực ma sát nghỉ và '
                'ma sát trượt; lực cản của chất lưu, lực nâng; lực căng dây; lực đàn hồi; '
                'moment lực và quy tắc moment; ngẫu lực; điều kiện cân bằng của vật rắn'},
            {'id': '10.4', 'ten': 'Công, năng lượng, công suất', 'kien_thuc':
                'công cơ học $A = Fs\\cos\\alpha$; công suất $P = A/t = Fv$; động năng '
                '$W_đ = \\frac{1}{2}mv^2$; thế năng trọng trường $W_t = mgh$; cơ năng và '
                'định luật bảo toàn cơ năng; hiệu suất'},
            {'id': '10.5', 'ten': 'Động lượng', 'kien_thuc':
                'động lượng $\\vec{p} = m\\vec{v}$; xung lượng của lực; định luật bảo toàn '
                'động lượng; va chạm đàn hồi và va chạm mềm'},
            {'id': '10.6', 'ten': 'Chuyển động tròn', 'kien_thuc':
                'radian, tốc độ góc $\\omega$, liên hệ $v = \\omega r$; gia tốc hướng tâm '
                '$a_{ht} = v^2/r = \\omega^2 r$; lực hướng tâm $F = mv^2/r$'},
            {'id': '10.7', 'ten': 'Biến dạng của vật rắn', 'kien_thuc':
                'biến dạng kéo, biến dạng nén; độ biến dạng tỉ đối; định luật Hooke '
                '$F = k|\\Delta l|$; giới hạn đàn hồi'},
        ],
    },
    11: {
        'ten': 'Lớp 11',
        'cong_cu_toan': (
            'đại số, lượng giác (kể cả phương trình lượng giác cơ bản), vectơ; '
            'đạo hàm ở mức cơ bản (Toán 11 học kì 2) — hạn chế dùng. '
            'CHƯA học tích phân, số phức'),
        'chuong': [
            {'id': '11.1', 'ten': 'Dao động', 'kien_thuc':
                'dao động điều hoà $x = A\\cos(\\omega t + \\varphi)$; li độ, biên độ, chu kì, '
                'tần số, tần số góc, pha ban đầu; vận tốc $v = -\\omega A\\sin(\\omega t + \\varphi)$ '
                'và gia tốc $a = -\\omega^2 x$; con lắc lò xo $\\omega = \\sqrt{k/m}$; con lắc đơn '
                '$\\omega = \\sqrt{g/l}$; năng lượng dao động điều hoà $W = \\frac{1}{2}kA^2$; '
                'dao động tắt dần, dao động cưỡng bức, hiện tượng cộng hưởng'},
            {'id': '11.2', 'ten': 'Sóng', 'kien_thuc':
                'sóng cơ, sóng dọc và sóng ngang; bước sóng, chu kì, tần số, tốc độ truyền sóng '
                '$v = \\lambda f$; phương trình sóng; sóng điện từ và thang sóng điện từ; '
                'giao thoa sóng (điều kiện cực đại, cực tiểu); nhiễu xạ; sóng dừng '
                '(nút, bụng sóng); đo tốc độ truyền âm'},
            {'id': '11.3', 'ten': 'Điện trường', 'kien_thuc':
                'điện tích, tương tác điện; định luật Coulomb $F = k\\frac{q_1q_2}{r^2}$; '
                'điện trường và cường độ điện trường $E = F/q$; đường sức điện, điện trường đều; '
                'công của lực điện, thế năng điện; điện thế, hiệu điện thế $U = Ed$; '
                'tụ điện, điện dung $C = Q/U$, ghép tụ; năng lượng tụ điện $W = \\frac{1}{2}CU^2$'},
            {'id': '11.4', 'ten': 'Dòng điện, mạch điện', 'kien_thuc':
                'cường độ dòng điện $I = q/t$; định luật Ohm cho đoạn mạch $U = IR$; điện trở, '
                'điện trở suất; ghép điện trở nối tiếp và song song; nguồn điện, suất điện động; '
                'năng lượng điện $A = UIt$, công suất điện $P = UI$; định luật Joule–Lenz'},
        ],
    },
    12: {
        'ten': 'Lớp 12',
        'cong_cu_toan': (
            'đại số, lượng giác, vectơ, đạo hàm và tích phân cơ bản (Toán 12). '
            'KHÔNG dùng số phức và giản đồ vectơ quay (Fresnel) cho dòng điện xoay chiều — '
            'chương trình 2018 không còn phần mạch RLC nối tiếp'),
        'chuong': [
            {'id': '12.1', 'ten': 'Vật lí nhiệt', 'kien_thuc':
                'mô hình động học phân tử về cấu tạo chất; sự chuyển thể; thang nhiệt độ Celsius '
                'và Kelvin; nội năng và các cách làm thay đổi nội năng; định luật I nhiệt động '
                'lực học $\\Delta U = A + Q$; nhiệt dung riêng $Q = mc\\Delta t$; nhiệt nóng chảy '
                'riêng $Q = \\lambda m$; nhiệt hoá hơi riêng $Q = Lm$'},
            {'id': '12.2', 'ten': 'Khí lí tưởng', 'kien_thuc':
                'mô hình khí lí tưởng; định luật Boyle $p_1V_1 = p_2V_2$; định luật Charles '
                '$V/T = $ hằng số; quá trình đẳng tích $p/T = $ hằng số; phương trình trạng thái '
                '$pV/T = $ hằng số; phương trình Clapeyron $pV = nRT$; áp suất chất khí theo mô '
                'hình động học phân tử; động năng phân tử và nhiệt độ'},
            {'id': '12.3', 'ten': 'Từ trường', 'kien_thuc':
                'từ trường, đường sức từ; cảm ứng từ $B$; lực từ $F = BIl\\sin\\theta$; '
                'lực Lorentz $f = qvB\\sin\\theta$; từ thông $\\Phi = BS\\cos\\alpha$; hiện tượng '
                'cảm ứng điện từ; định luật Faraday $e = -\\Delta\\Phi/\\Delta t$; định luật Lenz; '
                'dòng điện xoay chiều, giá trị hiệu dụng $U = U_0/\\sqrt{2}$; máy phát điện xoay chiều'},
            {'id': '12.4', 'ten': 'Vật lí hạt nhân và phóng xạ', 'kien_thuc':
                'cấu tạo hạt nhân, kí hiệu hạt nhân, đồng vị; đơn vị khối lượng nguyên tử u; '
                'độ hụt khối, năng lượng liên kết $W_{lk} = \\Delta mc^2$, năng lượng liên kết riêng; '
                'phản ứng hạt nhân; phóng xạ $\\alpha$, $\\beta$, $\\gamma$; định luật phóng xạ '
                '$N = N_0 2^{-t/T}$, chu kì bán rã, độ phóng xạ; phân hạch, nhiệt hạch; '
                'an toàn phóng xạ'},
        ],
    },
}

# Chế độ ràng buộc
MODE_NONE = 'khong_rang_buoc'
MODE_CT = 'chuong_trinh'
MODE_CUSTOM = 'tuy_chinh'

# Dòng nhắc lại đặt CUỐI prompt — đề dài, model dễ quên ràng buộc đặt ở đầu
REMINDER = ('\n\n(NHẮC LẠI: chỉ dùng kiến thức trong PHẠM VI đã nêu ở đầu. Nếu buộc phải '
            'vượt phạm vi, thêm dòng cuối câu đó: "⚠ Ngoài phạm vi: <kiến thức đã dùng>")')

_OUT_OF_SCOPE_RULE = (
    'Nếu một câu BẮT BUỘC phải dùng kiến thức ngoài phạm vi trên: VẪN GIẢI bình thường,\n'
    'nhưng thêm dòng cuối cùng của câu đó:\n'
    '⚠ Ngoài phạm vi: <nêu đúng kiến thức đã dùng>')


def get_curriculum_tree() -> dict:
    """Cây lớp → chương (không kèm phần kiến thức dài) cho dropdown giao diện."""
    return {
        str(grade): {
            'ten': info['ten'],
            'chuong': [{'id': c['id'], 'ten': c['ten']} for c in info['chuong']],
        }
        for grade, info in sorted(CHUONG_TRINH.items())
    }


def _find_chapter_index(grade: int, chapter_id: str) -> Optional[int]:
    for i, c in enumerate(CHUONG_TRINH[grade]['chuong']):
        if c['id'] == chapter_id:
            return i
    return None


def build_scope_prompt(scope: dict) -> str:
    """Dựng khối văn bản ràng buộc phạm vi để chèn vào đầu prompt.

    scope = {
      'mode': 'khong_rang_buoc' | 'chuong_trinh' | 'tuy_chinh',
      'grade': 11, 'chapter_id': '11.1', 'cumulative': True,   # mode chuong_trinh
      'allow': '...', 'deny': '...',                            # mode tuy_chinh
    }
    Trả '' nếu không ràng buộc / dữ liệu không hợp lệ (giữ nguyên hành vi cũ).
    """
    if not scope or not isinstance(scope, dict):
        return ''
    mode = scope.get('mode') or MODE_NONE

    # ── Chế độ tự nhập ───────────────────────────────────────────
    if mode == MODE_CUSTOM:
        allow = (scope.get('allow') or '').strip()
        deny = (scope.get('deny') or '').strip()
        if not allow and not deny:
            return ''
        parts = ['=== PHẠM VI KIẾN THỨC HỌC SINH ĐÃ HỌC (BẮT BUỘC TUÂN THỦ) ===']
        if allow:
            parts.append('ĐƯỢC DÙNG:\n' + allow)
        if deny:
            parts.append('KHÔNG DÙNG:\n' + deny)
        parts.append(_OUT_OF_SCOPE_RULE)
        return '\n\n'.join(parts)

    if mode != MODE_CT:
        return ''

    # ── Chế độ theo chương trình ─────────────────────────────────
    try:
        grade = int(scope.get('grade'))
    except (TypeError, ValueError):
        return ''
    if grade not in CHUONG_TRINH:
        return ''

    chapter_id = scope.get('chapter_id') or ''
    idx = _find_chapter_index(grade, chapter_id)
    if idx is None:                       # không chọn chương → lấy trọn lớp
        idx = len(CHUONG_TRINH[grade]['chuong']) - 1
    cumulative = scope.get('cumulative', True)

    chuong_hien = CHUONG_TRINH[grade]['chuong'][idx]
    allow_lines = []

    if cumulative:
        for g in sorted(k for k in CHUONG_TRINH if k < grade):
            for c in CHUONG_TRINH[g]['chuong']:
                allow_lines.append(f"• [{CHUONG_TRINH[g]['ten']}] {c['ten']}: {c['kien_thuc']}")
        for c in CHUONG_TRINH[grade]['chuong'][:idx + 1]:
            allow_lines.append(f"• [{CHUONG_TRINH[grade]['ten']}] {c['ten']}: {c['kien_thuc']}")
        header = (f"Học sinh: {CHUONG_TRINH[grade]['ten']}, đã học đến hết chương "
                  f"\"{chuong_hien['ten']}\".")
    else:
        allow_lines.append(
            f"• [{CHUONG_TRINH[grade]['ten']}] {chuong_hien['ten']}: {chuong_hien['kien_thuc']}")
        header = (f"Học sinh: {CHUONG_TRINH[grade]['ten']} — CHỈ dùng kiến thức trong chương "
                  f"\"{chuong_hien['ten']}\" (không dùng kiến thức chương khác, kể cả lớp dưới).")

    # Phần bị cấm: các chương sau trong cùng lớp + toàn bộ lớp trên
    deny_bits = []
    sau = [c['ten'] for c in CHUONG_TRINH[grade]['chuong'][idx + 1:]]
    if sau:
        deny_bits.append('các chương sau chưa học (' + ', '.join(sau) + ')')
    tren = [CHUONG_TRINH[g]['ten'] for g in sorted(CHUONG_TRINH) if g > grade]
    if tren:
        deny_bits.append('toàn bộ ' + ', '.join(tren))
    if not cumulative:
        truoc = [c['ten'] for c in CHUONG_TRINH[grade]['chuong'][:idx]]
        duoi = [CHUONG_TRINH[g]['ten'] for g in sorted(CHUONG_TRINH) if g < grade]
        khac = truoc + duoi
        if khac:
            deny_bits.insert(0, 'kiến thức ngoài chương đã chọn (' + ', '.join(khac) + ')')
    deny_bits.append('phương pháp bậc đại học (không có trong SGK phổ thông)')

    return (
        '=== PHẠM VI KIẾN THỨC HỌC SINH ĐÃ HỌC (BẮT BUỘC TUÂN THỦ) ===\n'
        + header + '\n\n'
        + 'ĐƯỢC DÙNG:\n' + '\n'.join(allow_lines) + '\n\n'
        + 'CÔNG CỤ TOÁN được dùng: ' + CHUONG_TRINH[grade]['cong_cu_toan'] + '.\n\n'
        + 'KHÔNG DÙNG: ' + '; '.join(deny_bits) + '.\n\n'
        + _OUT_OF_SCOPE_RULE
    )


def describe_scope(scope: dict) -> str:
    """Nhãn ngắn cho giao diện, vd 'Lớp 11 · Dao động'."""
    if not scope or not isinstance(scope, dict):
        return 'Không ràng buộc'
    mode = scope.get('mode') or MODE_NONE
    if mode == MODE_CUSTOM:
        return 'Tùy chỉnh'
    if mode != MODE_CT:
        return 'Không ràng buộc'
    try:
        grade = int(scope.get('grade'))
    except (TypeError, ValueError):
        return 'Không ràng buộc'
    if grade not in CHUONG_TRINH:
        return 'Không ràng buộc'
    idx = _find_chapter_index(grade, scope.get('chapter_id') or '')
    if idx is None:
        return CHUONG_TRINH[grade]['ten']
    ten = CHUONG_TRINH[grade]['chuong'][idx]['ten']
    prefix = '' if scope.get('cumulative', True) else 'chỉ '
    return f"{CHUONG_TRINH[grade]['ten']} · {prefix}{ten}"
