"""
AI Solver — 9Router (ưu tiên) + Claude + Gemini (fallback).
Gửi toàn bộ đề trong 1 request, stream kết quả về frontend qua SSE.
"""
import re, time, random
from typing import Generator
from core.document_model import Document, Question

THEORY_TYPES  = {'trac_nghiem_lua_chon', 'dung_sai', 'ly_thuyet'}

SYSTEM_PROMPT = """Bạn là giáo viên Vật lý THPT Việt Nam giỏi. Lời giải MẠCH LẠC, TỐI GIẢN.
KHÔNG thêm bất kỳ text nào ngoài định dạng.

Mỗi câu bắt đầu bằng dòng riêng: [CÂU <số>]
Sau đó tự nhận biết câu thuộc 1 trong 2 TRƯỜNG HỢP và trình bày đúng kiểu:

▶ TRƯỜNG HỢP 1 — BÀI TÍNH TOÁN / ĐỒ THỊ:
- KHÔNG viết phần lý giải cơ sở lý thuyết hay hiện tượng. Đi THẲNG vào phần giải.
- Ghi nhanh: thông số → công thức gốc → biểu thức thế số → kết quả cuối kèm ĐƠN VỊ.
- Viết thành các DÒNG BIỂU THỨC liên tục (mỗi biểu thức một dòng), KHÔNG dùng gạch đầu dòng,
  BỎ QUA các bước biến đổi đại số trung gian.
- Nếu là trắc nghiệm / trả lời ngắn: phần kết luận ("=> Chọn B" hoặc "=> Đáp số: 56 °C")
  phải viết NGAY CÙNG HÀNG với kết quả tính toán cuối cùng.

▶ TRƯỜNG HỢP 2 — LÝ THUYẾT / ĐỊNH TÍNH / ĐÚNG-SAI:
- Tập trung làm rõ bản chất vật lý / khái niệm cốt lõi, trả lời THẲNG vào câu hỏi.
- Trình bày MỘT ĐOẠN VĂN XUÔI liên tục, KHÔNG dùng gạch đầu dòng, nghiêm ngặt KHÔNG QUÁ 3 DÒNG.
- Dạng Đúng/Sai: phần kết luận từng ý ("a) Đúng, b) Sai, c) Đúng, d) Sai") ghi gọn ngay
  DÒNG CUỐI của đoạn giải thích.
- Trắc nghiệm lý thuyết: kết thúc bằng "=> Chọn X".

QUY TẮC CÔNG THỨC (BẮT BUỘC):
- Công thức toán bọc trong $...$ (inline). Ví dụ: $v = v_0 + at$, $\\frac{a}{b}$, $\\sqrt{x}$,
  $v^2$, $v_0$, $\\omega$, $\\lambda$, $\\pi$, $\\Delta$, $\\vec{v}$, $\\times$, $\\approx$, $\\pm$.
- ĐƠN VỊ trong công thức bọc \\text{...}, số mũ đơn vị ở NGOÀI \\text:
  $g = 10\\text{m/s}^2$, $1,0\\text{cm}^2$, $c = 0,460\\text{kJ/kg.K}$.
- NHIỆT ĐỘ: $25^\\circ\\text{C}$ (dùng ^\\circ, KHÔNG dùng ký tự °). Góc: $45^\\circ$.
- KÝ HIỆU KHOA HỌC: $2,0.10^5\\text{Pa}$ — dấu CHẤM nhân trước 10, KHÔNG dùng \\cdot hay \\times.
- Số thập phân dùng dấu PHẨY trần: $0,460$ (KHÔNG viết $0{,}460$).
- Số + đơn vị ĐƠN GIẢN (không mũ, không phân số) để text thường: 1,5J, 20N, 30 m — KHÔNG bọc $.
- TUYỆT ĐỐI KHÔNG dùng $$...$$ (chỉ $...$). Chữ thường KHÔNG bọc trong $."""


# Gợi ý ngắn theo loại phần — AI vẫn tự chọn TRƯỜNG HỢP 1/2 theo bản chất câu hỏi
_SECTION_FORMAT_HINT = {
    'trac_nghiem_lua_chon': 'Trắc nghiệm → kết luận "=> Chọn X" cùng hàng kết quả cuối',
    'dung_sai':             'Đúng/Sai → TRƯỜNG HỢP 2: đoạn văn ≤3 dòng, kết luận "a) Đúng, b) Sai,..." ở dòng cuối',
    'tra_loi_ngan':         'Trả lời ngắn → TRƯỜNG HỢP 1: kết luận "=> Đáp số: <số + đơn vị>" cùng hàng kết quả cuối',
    'tu_luan':              'Tự luận → TRƯỜNG HỢP 1: các dòng biểu thức liên tục, đáp số ở cuối',
}


def _build_full_prompt(doc: Document) -> tuple[str, dict]:
    """Tạo prompt chứa toàn bộ đề. Trả về (prompt_text, {số_toàn_cục: q_id}).

    Đánh số [CÂU n] TOÀN CỤC liên tục qua mọi phần — số câu trong đề thi
    chuẩn reset về 1 ở đầu mỗi phần (Phần I câu 1-18, Phần II câu 1-4...)
    nên q.number trùng nhau giữa các phần, không dùng làm khóa map được."""
    from core.chuong_trinh import build_scope_prompt, REMINDER

    scope_text = build_scope_prompt(getattr(doc, 'ai_scope', None))
    lines = []
    if scope_text:
        lines.append(scope_text)
        lines.append('\n=== ĐỀ BÀI CẦN GIẢI ===')
    num_to_id = {}
    gnum = 0

    for section in doc.sections:
        if section.is_theory:
            continue
        if section.label:
            lines.append(f"\n== {section.label} ==")
        hint = _SECTION_FORMAT_HINT.get(section.type)
        if hint:
            lines.append(f"(Định dạng lời giải: {hint})")
        for q in section.questions:
            gnum += 1
            num_to_id[str(gnum)] = q.id
            lines.append(f"\n[CÂU {gnum}]")
            lines.append(q.text)
            if q.options:
                lines.append("  " + "  ".join(q.options))
            if q.sub_items:
                for sub in q.sub_items:
                    lines.append(f"  {sub}")

    if scope_text:
        lines.append(REMINDER)
    return "\n".join(lines), num_to_id


# Chấp nhận [CÂU 1] / [Câu 1] / [câu 1] / **[CÂU 1]** / [CÂU 1]: — model qua
# 9Router hay trả về hoa/thường lẫn lộn, khớp cứng chữ hoa sẽ mất sạch lời giải.
_QMARKER_RE = re.compile(r'\*{0,2}\[\s*c[âa]u\s*(\d+)\s*\]\*{0,2}:?', re.IGNORECASE)


def _parse_solutions(text: str, num_to_id: dict) -> dict:
    """Parse response [CÂU X] ... thành {q_id: solution}."""
    solutions = {}
    parts = _QMARKER_RE.split(text)
    for i in range(1, len(parts) - 1, 2):
        num = parts[i].strip()
        sol = parts[i + 1].strip() if i + 1 < len(parts) else ''
        qid = num_to_id.get(num)
        if qid and sol:
            solutions[qid] = sol
    return solutions


# ── 9Router (ưu tiên — OpenAI-compatible local proxy) ────────────────

def _call_9router(prompt: str, model: str, api_key: str,
                  base_url: str = 'http://localhost:20128/v1') -> str:
    import requests
    headers = {
        'Authorization': f'Bearer {api_key}',
        'Content-Type': 'application/json',
    }
    payload = {
        'model': model,
        'messages': [
            {'role': 'system', 'content': SYSTEM_PROMPT},
            {'role': 'user',   'content': prompt},
        ],
        'max_tokens': 8000,
        'stream': False,
    }
    for attempt in range(4):
        try:
            r = requests.post(f'{base_url}/chat/completions',
                              json=payload, headers=headers, timeout=120)
            if r.status_code == 200:
                import json as _j
                raw = r.text.strip()
                # Some providers return SSE format even with stream=False — collect all chunks
                if raw.startswith('data:'):
                    parts = []
                    for line in raw.splitlines():
                        line = line.strip()
                        if not line.startswith('data:'):
                            continue
                        data = line[5:].strip()
                        if data == '[DONE]':
                            break
                        try:
                            content = _j.loads(data)['choices'][0]['delta'].get('content', '')
                            if content:
                                parts.append(content)
                        except Exception:
                            pass
                    return ''.join(parts).strip()
                # Standard JSON response
                return _j.loads(raw)['choices'][0]['message']['content'].strip()
            elif r.status_code in (429, 503):
                time.sleep((2 ** attempt) + random.uniform(0, 2))
            else:
                raise ValueError(f'9Router lỗi {r.status_code}: {r.text[:300]}')
        except requests.exceptions.ConnectionError:
            raise RuntimeError(
                'Không kết nối được 9Router tại ' + base_url +
                '. Hãy chạy: cd 9router-master/9router-master && npm run dev')
        except requests.exceptions.Timeout:
            if attempt == 3: raise
            time.sleep(5)
    raise RuntimeError('9Router: quá nhiều yêu cầu, thử lại sau.')


def _stream_9router(prompt: str, model: str, api_key: str,
                    base_url: str = 'http://localhost:20128/v1') -> Generator[str, None, None]:
    """Stream từng text chunk từ 9Router qua SSE."""
    import requests, json as _json
    r = requests.post(
        f'{base_url}/chat/completions',
        headers={'Authorization': f'Bearer {api_key}', 'Content-Type': 'application/json'},
        json={
            'model': model, 'stream': True, 'max_tokens': 8000,
            'messages': [
                {'role': 'system', 'content': SYSTEM_PROMPT},
                {'role': 'user',   'content': prompt},
            ],
        },
        stream=True, timeout=120,
    )
    r.raise_for_status()
    for line in r.iter_lines():
        if line and line.startswith(b'data: '):
            data = line[6:]
            if data == b'[DONE]':
                break
            try:
                chunk = _json.loads(data)['choices'][0]['delta'].get('content', '')
                if chunk:
                    yield chunk
            except Exception:
                pass


# ── Claude ─────────────────────────────────────────────────────────

def _call_claude(prompt: str, model: str, api_key: str,
                 max_tokens: int = 8192) -> str:
    import anthropic
    client = anthropic.Anthropic(api_key=api_key)

    for attempt in range(4):
        try:
            msg = client.messages.create(
                model=model,
                max_tokens=max_tokens,
                system=SYSTEM_PROMPT,
                messages=[{'role': 'user', 'content': prompt}]
            )
            return msg.content[0].text.strip()

        except anthropic.RateLimitError as e:
            wait = float(getattr(e.response, 'headers', {}).get('retry-after', 30))
            wait += random.uniform(0, 5)
            time.sleep(wait)

        except anthropic.APIStatusError as e:
            if e.status_code == 529:
                time.sleep((2 ** attempt) + random.uniform(0, 2))
            else:
                raise

    raise RuntimeError('Claude API không phản hồi sau 4 lần thử.')


def _stream_claude(prompt: str, model: str, api_key: str,
                   max_tokens: int = 8192) -> Generator[str, None, None]:
    import anthropic
    client = anthropic.Anthropic(api_key=api_key)

    for attempt in range(4):
        try:
            with client.messages.stream(
                model=model,
                max_tokens=max_tokens,
                system=SYSTEM_PROMPT,
                messages=[{'role': 'user', 'content': prompt}]
            ) as stream:
                for chunk in stream.text_stream:
                    yield chunk
            return

        except anthropic.RateLimitError as e:
            wait = float(getattr(e.response, 'headers', {}).get('retry-after', 30))
            wait += random.uniform(0, 5)
            time.sleep(wait)

        except anthropic.APIStatusError as e:
            if e.status_code == 529:
                time.sleep((2 ** attempt) + random.uniform(0, 2))
            else:
                raise

    # Hết 4 lần thử mà vẫn 429/529 — phải raise để frontend nhận event lỗi,
    # nếu return êm thì mọi câu thành "[AI không giải được]" không rõ nguyên nhân
    raise RuntimeError('Claude API quá tải — không phản hồi sau 4 lần thử.')


# ── Gemini (fallback) ─────────────────────────────────────────────

GEMINI_MODELS = ['gemini-2.5-flash', 'gemini-2.0-flash']
# Model mac dinh qua 9Router Antigravity (uu tien, da ket noi)
DEFAULT_9ROUTER_MODEL = 'ag/gemini-3.5-flash-low'

def _call_gemini(prompt: str, model: str, api_key: str) -> str:
    import google.generativeai as genai
    genai.configure(api_key=api_key)

    models_to_try = [model] + [m for m in GEMINI_MODELS if m != model]
    last_err = None
    for m in models_to_try:
        try:
            response = genai.GenerativeModel(
                m, system_instruction=SYSTEM_PROMPT
            ).generate_content(prompt, request_options={'timeout': 120})
            return response.text.strip()
        except Exception as e:
            last_err = e
            err = str(e)
            if '429' in err or 'quota' in err.lower() or 'rate' in err.lower():
                time.sleep(3)
                continue
            raise
    raise last_err


# ── Routing helper ────────────────────────────────────────────────

_9ROUTER_PREFIXES = ('if/', 'kr/', 'gh/', 'ag/', 'cc/', 'cx/', 'gc/', 'oc/', 'cu/', 'cl/')

def _is_9router_model(model: str) -> bool:
    """Model có prefix alias/  → route qua 9Router proxy."""
    return any(model.startswith(p) for p in _9ROUTER_PREFIXES)


# ── Vision solve cho câu CÓ HÌNH/ĐỒ THỊ (3 bước tư duy của thầy) ──────

VISION_PHYSICS_PROMPT = """Bạn là giáo viên Vật lý THPT Việt Nam giỏi. Câu hỏi kèm HÌNH/ĐỒ THỊ.
KHÔNG quét ảnh thụ động — DÙNG LOGIC VẬT LÝ DẪN ĐƯỜNG CHO MẮT và tự sửa lỗi thị giác, theo 3 BƯỚC:

BƯỚC 1 — "Bắt bài" hệ lưới bằng logic toán:
Xác định GIÁ TRỊ MỖI Ô LƯỚI (trục thời gian và trục đại lượng) bằng cách đối chiếu các mốc đặc biệt
(biên độ, chu kỳ, điểm cắt trục, cực đại/cực tiểu) với số ô đếm được. Nếu đọc ra số lẻ vô lý →
suy luận lại scale sao cho khớp giá trị vật lý hợp lý (T, A, ω thường là số tròn hoặc bội của π).

BƯỚC 2 — Hiểu bản chất đồ thị:
Xác định đồ thị loại gì (x–t, v–t, a–t...) và dùng QUAN HỆ pha: v sớm pha π/2 so với x;
a ngược pha x (a = −ω²x). Đồ thị điều hòa là sin/cos. Đối chiếu điểm xuất phát, chiều đi đầu tiên,
điểm cắt trục để suy ra pha ban đầu φ.

BƯỚC 3 — Quy trình HAI bước (bóc tách dữ liệu TRƯỚC, giải toán SAU):
(a) BÓC TÁCH từ đồ thị: biên độ A, chu kỳ T, ω = 2π/T, pha ban đầu φ, giá trị tại các mốc.
(b) Có đủ dữ liệu RỒI mới GIẢI TOÁN để ra đáp án.

Trả về ĐÚNG định dạng (KHÔNG thêm text ngoài, KHÔNG ghi <thinking>):
[CÂU <số>]
<Đây là bài ĐỒ THỊ/TÍNH TOÁN → trình bày kiểu TỐI GIẢN:
 - KHÔNG lý giải lý thuyết. Đi thẳng: bóc tách (A, T, ω, φ...) → công thức gốc → thế số → kết quả.
 - Viết các DÒNG BIỂU THỨC liên tục, KHÔNG gạch đầu dòng, bỏ qua biến đổi đại số trung gian.
 - Trắc nghiệm/trả lời ngắn: kết luận "=> Chọn X" (hoặc "=> Đáp số: <số + đơn vị>") viết
   NGAY CÙNG HÀNG với kết quả tính cuối.
 - Công thức bọc $...$ (CHỈ $...$, KHÔNG $$).>"""


def _vision_solve_one(q: Question, model: str,
                      niner_key: str = '', niner_url: str = 'http://localhost:20128/v1',
                      gemini_key: str = '', scope_text: str = '') -> str | None:
    """Giải 1 câu CÓ HÌNH bằng Vision (gửi kèm ảnh). Trả lời giải hoặc None."""
    import os, base64
    from config import UPLOAD_DIR
    img_dir = os.path.join(UPLOAD_DIR, 'images')
    blobs = []
    for im in q.images:
        p = os.path.join(img_dir, im.filename)
        if os.path.exists(p):
            try:
                with open(p, 'rb') as f:
                    blobs.append(f.read())
            except Exception:
                pass
    if not blobs:
        return None

    qtext = f"[CÂU {q.number}]\n{q.text}"
    if q.options:
        qtext += "\n" + "\n".join(q.options)
    if q.sub_items:
        qtext += "\n" + "\n".join(q.sub_items)
    # Ràng buộc phạm vi phải đi kèm cả đường Vision — câu có đồ thị được giải
    # lại bằng Vision và ĐÈ lên đáp án text, bỏ sót là vỡ ràng buộc
    if scope_text:
        qtext = scope_text + '\n\n=== CÂU HỎI ===\n' + qtext

    def _via_router(mdl):
        from openai import OpenAI
        client = OpenAI(api_key=niner_key or '9router', base_url=niner_url,
                        timeout=180, max_retries=0)
        content = [{"type": "text", "text": qtext}]
        for b in blobs:
            content.append({"type": "image_url", "image_url": {
                "url": "data:image/png;base64," + base64.b64encode(b).decode()}})
        resp = client.chat.completions.create(
            model=mdl,
            max_tokens=8000,
            messages=[{"role": "system", "content": VISION_PHYSICS_PROMPT},
                      {"role": "user", "content": content}])
        return (resp.choices[0].message.content or '').strip()

    # 1) Model đã chọn nếu là 9Router (kr/claude, gc/gemini... đều hỗ trợ vision)
    if _is_9router_model(model) and niner_key is not None:
        try:
            return _vision_clean(_via_router(model), q.number)
        except Exception:
            pass
    # 2) Fallback Gemini API trực tiếp (luôn có vision)
    if gemini_key:
        try:
            import google.generativeai as genai
            genai.configure(api_key=gemini_key)
            gm = genai.GenerativeModel('gemini-2.5-flash',
                                       system_instruction=VISION_PHYSICS_PROMPT)
            parts = [qtext] + [{"mime_type": "image/png", "data": b} for b in blobs]
            resp = gm.generate_content(parts, request_options={'timeout': 120})
            return _vision_clean((resp.text or '').strip(), q.number)
        except Exception:
            pass
    # 3) Fallback Gemini 3 Flash qua 9Router (Antigravity)
    try:
        return _vision_clean(_via_router('ag/gemini-3.5-flash-low'), q.number)
    except Exception:
        return None


def _vision_clean(text: str, number: int) -> str:
    """Bỏ <thinking>...</thinking>, lấy phần sau [CÂU number]."""
    if not text:
        return text
    text = re.sub(r'<think(?:ing)?>.*?</think(?:ing)?>', '', text,
                  flags=re.DOTALL | re.IGNORECASE).strip()
    m = re.search(r'\[\s*c[âa]u\s*' + str(number) + r'\s*\]\s*:?\s*(.+)', text,
                  flags=re.DOTALL | re.IGNORECASE)
    if m:
        return m.group(1).strip()
    return re.sub(r'^\*{0,2}\[\s*c[âa]u\s*\d+\s*\]\*{0,2}\s*:?\s*', '', text,
                  flags=re.IGNORECASE).strip()


def _solve_image_questions(doc: Document, model: str,
                           niner_key: str = '', niner_url: str = 'http://localhost:20128/v1',
                           gemini_key: str = '') -> dict:
    """Giải SONG SONG mọi câu có hình bằng Vision. Trả {q_id: solution}."""
    from concurrent.futures import ThreadPoolExecutor, as_completed
    from core.chuong_trinh import build_scope_prompt

    img_qs = [q for s in doc.sections if not s.is_theory
              for q in s.questions if q.images]
    if not img_qs:
        return {}
    scope_text = build_scope_prompt(getattr(doc, 'ai_scope', None))
    results = {}

    def _work(q):
        try:
            return q.id, _vision_solve_one(q, model, niner_key, niner_url,
                                           gemini_key, scope_text)
        except Exception:
            return q.id, None

    with ThreadPoolExecutor(max_workers=3) as ex:
        for fut in as_completed([ex.submit(_work, q) for q in img_qs]):
            qid, sol = fut.result()
            if sol:
                results[qid] = sol
    return results


# ── Public API ────────────────────────────────────────────────────

def solve_document(doc: Document,
                   gemini_key: str = '', claude_key: str = '',
                   niner_key: str = '', niner_url: str = 'http://localhost:20128/v1',
                   model_theory: str = 'ag/gemini-3.5-flash-low',
                   model_exercise: str = 'ag/gemini-3.5-flash-low') -> dict:
    """
    Giải toàn bộ đề trong 1 request. Trả về {q_id: solution_text}.
    Thứ tự ưu tiên theo model đã chọn: 9Router > Claude > Gemini.
    """
    prompt, num_to_id = _build_full_prompt(doc)
    if not num_to_id:
        return {}

    has_tu_luan = any(s.type in ('tu_luan', 'tra_loi_ngan')
                      for s in doc.sections if not s.is_theory)
    model = model_exercise if has_tu_luan else model_theory

    def _route(m: str) -> str:
        # 9Router: model dạng provider/model-name (if/kimi-k2, kr/claude-sonnet-4.5...)
        if _is_9router_model(m):
            if niner_key:
                return _call_9router(prompt, m, niner_key, niner_url)
            raise ValueError(f'Model "{m}" cần 9Router key. Vào Cài đặt → 9Router để nhập key.')
        # Claude direct
        if m.startswith('claude-'):
            if claude_key:
                return _call_claude(prompt, m, claude_key)
            # Thử qua 9Router nếu có key
            if niner_key:
                return _call_9router(prompt, m, niner_key, niner_url)
            raise ValueError(f'Model "{m}" cần Claude API key hoặc 9Router key.')
        # Gemini direct
        if m.startswith('gemini-'):
            if gemini_key:
                return _call_gemini(prompt, m, gemini_key)
            # Tu dong chuyen sang Gemini CLI qua 9Router
            if niner_key:
                print(f'[AI] Khong co Gemini key, tu chuyen sang {DEFAULT_9ROUTER_MODEL}', flush=True)
                return _call_9router(prompt, DEFAULT_9ROUTER_MODEL, niner_key, niner_url)
            raise ValueError('Chưa có Gemini API key. Vào Cài đặt → AI để nhập key Gemini hoặc dùng 9Router.')
        # Model không rõ — thử theo thứ tự
        if niner_key:
            return _call_9router(prompt, m, niner_key, niner_url)
        if gemini_key:
            return _call_gemini(prompt, m, gemini_key)
        raise ValueError('Chưa cài API key phù hợp. Vào Cài đặt → AI để thêm key.')

    try:
        text = _route(model)
    except Exception as e:
        return {'error': str(e)}

    solutions = _parse_solutions(text, num_to_id)

    # Câu CÓ HÌNH/ĐỒ THỊ → giải lại bằng Vision (đè lên đáp án text), 3 bước tư duy
    try:
        img_sols = _solve_image_questions(doc, model_exercise, niner_key, niner_url, gemini_key)
        solutions.update(img_sols)
    except Exception:
        pass

    for num, qid in num_to_id.items():
        if qid not in solutions:
            solutions[qid] = '[AI không giải được câu này — thử lại]'

    return solutions


def stream_solve_document(doc: Document,
                          claude_key: str = '', gemini_key: str = '',
                          niner_key: str = '', niner_url: str = 'http://localhost:20128/v1',
                          model_exercise: str = 'ag/gemini-3.5-flash-low') -> Generator:
    """
    Generator yield SSE events dạng dict.
    Mỗi event: {'type': 'chunk'|'done'|'error', 'text': ..., 'solutions': ...}
    """
    prompt, num_to_id = _build_full_prompt(doc)
    if not num_to_id:
        yield {'type': 'done', 'solutions': {}}
        return

    full_text = ''
    try:
        if niner_key and _is_9router_model(model_exercise):
            # 9Router streaming
            for chunk in _stream_9router(prompt, model_exercise, niner_key, niner_url):
                full_text += chunk
                yield {'type': 'chunk', 'text': chunk}
        elif claude_key:
            has_tu_luan = any(s.type in ('tu_luan', 'tra_loi_ngan')
                              for s in doc.sections if not s.is_theory)
            model = 'claude-sonnet-4-6' if has_tu_luan else 'claude-haiku-4-5-20251001'
            for chunk in _stream_claude(prompt, model, claude_key):
                full_text += chunk
                yield {'type': 'chunk', 'text': chunk}
        elif gemini_key:
            text = _call_gemini(prompt, model_exercise, gemini_key)
            full_text = text
            yield {'type': 'chunk', 'text': text}
        elif niner_key:
            # 9Router non-streaming fallback (model không phải 9router format)
            text = _call_9router(prompt, model_exercise, niner_key, niner_url)
            full_text = text
            yield {'type': 'chunk', 'text': text}
        else:
            yield {'type': 'error', 'text': 'Chưa cấu hình API key.'}
            return
    except Exception as e:
        yield {'type': 'error', 'text': str(e)}
        return

    solutions = _parse_solutions(full_text, num_to_id)

    # Câu CÓ HÌNH/ĐỒ THỊ → giải lại bằng Vision (3 bước tư duy), đè lên đáp án text
    try:
        img_qs = [q for s in doc.sections if not s.is_theory
                  for q in s.questions if q.images]
        if img_qs:
            yield {'type': 'chunk',
                   'text': f"\n\n⏳ Đang giải {len(img_qs)} câu có hình/đồ thị bằng Vision…\n"}
            img_sols = _solve_image_questions(doc, model_exercise, niner_key, niner_url, gemini_key)
            if img_sols:
                solutions.update(img_sols)
                yield {'type': 'chunk',
                       'text': f"✓ Đã giải {len(img_sols)} câu có hình bằng Vision.\n"}
    except Exception:
        pass

    for num, qid in num_to_id.items():
        if qid not in solutions:
            solutions[qid] = '[AI không giải được câu này — thử lại]'

    yield {'type': 'done', 'solutions': solutions}


def review_document(doc: Document,
                    gemini_key: str = '', claude_key: str = '',
                    niner_key: str = '', niner_url: str = 'http://localhost:20128/v1',
                    model_review: str = '') -> str:
    """Nhận xét đề: tìm lỗi, thiếu dữ kiện."""
    questions = [f'Câu {q.number}: {q.text}'
                 for sec in doc.sections if not sec.is_theory
                 for q in sec.questions]
    if not questions:
        return 'Không có câu hỏi nào để nhận xét.'

    prompt = (
        'Bạn là giáo viên Vật lý THPT có nhiều kinh nghiệm ra đề.\n'
        'Xem xét đề thi sau và CHỈ chỉ ra vấn đề nếu có:\n'
        '- Câu nào thiếu dữ kiện?\n- Câu nào sai Vật lý?\n- Câu nào không rõ ràng?\n\n'
        + '\n'.join(questions)
        + '\n\nNhận xét (nếu đề ổn ghi: "Đề thi không có lỗi rõ ràng."):'
    )
    try:
        # Dùng model review nếu có, mặc định model nhẹ
        m = model_review or DEFAULT_9ROUTER_MODEL
        if niner_key and _is_9router_model(m):
            return _call_9router(prompt, m, niner_key, niner_url)
        if claude_key:
            return _call_claude(prompt, 'claude-haiku-4-5-20251001', claude_key, max_tokens=2048)
        if gemini_key:
            return _call_gemini(prompt, 'gemini-2.5-flash', gemini_key)
        if niner_key:
            return _call_9router(prompt, DEFAULT_9ROUTER_MODEL, niner_key, niner_url)
    except Exception as e:
        return f'Lỗi: {str(e)}'
    return 'Chưa cấu hình API key.'
