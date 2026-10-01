"""
core/pdf_to_word.py — Chuyển PDF sang Word (.docx) giữ công thức thành
Word Equation native (sửa được), kèm ảnh & bảng.

Pipeline:
  1. Tự nhận diện PDF có lớp text không (PyMuPDF/fitz).
       - Cả tài liệu text tốt  -> tắt OCR  (nhanh, ~15-30s/trang)
       - Scan / ít text         -> để Marker tự OCR thông minh từng trang (chậm)
  2. Marker (chạy trong venv_marker, Python 3.12) : PDF -> Markdown (LaTeX $...$)
  3. Pandoc --from=markdown+tex_math_dollars        : Markdown -> DOCX (OMML)

Module THUẦN (không phụ thuộc Flask). app.py gọi convert_pdf_to_word() rồi
tự copy sang Downloads/OneDrive bằng copy_to_destinations().
"""
import os
import sys
import time
import json
import socket
import shutil
import subprocess
import threading
import urllib.request
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
VENV_MARKER = BASE_DIR / "venv_marker"
MARKER_EXE = VENV_MARKER / "Scripts" / "marker_single.exe"
MARKER_PY = VENV_MARKER / "Scripts" / "python.exe"
MARKER_OUT = BASE_DIR / "data" / "marker_out"
EXPORT_DIR = BASE_DIR / "data" / "exports"

# Tỉ lệ trang có text để coi cả tài liệu là "digital" -> tắt OCR
# (ngưỡng ký tự/trang nay nằm trong core/pdf_scan.py — xem detect_text_layer)
_DIGITAL_COVERAGE = 0.80


# ── Kiểm tra sẵn sàng ─────────────────────────────────────────────

def marker_available() -> bool:
    """venv_marker đã cài Marker chưa."""
    return MARKER_EXE.exists() and MARKER_PY.exists()


def find_pandoc() -> str | None:
    p = shutil.which("pandoc")
    return p


ODL_SERVER_PORT = 5002  # Port mac dinh cua opendataloader-pdf-hybrid

# Duong dan tuyet doi toi ODL executables (tranh phu thuoc PATH)
_PY_SCRIPTS = Path(os.path.dirname(os.path.abspath(__file__))).parent / (
    r"..\AppData\Local\Python\pythoncore-3.14-64\Scripts"
    if os.name == "nt" else ""
)
# Tim Scripts directory tu sys.executable
import sys as _sys
_SCRIPTS_DIR = Path(_sys.executable).parent / "Scripts" if os.name == "nt" else Path(_sys.executable).parent
ODL_HYBRID_EXE = _SCRIPTS_DIR / "opendataloader-pdf-hybrid.exe"
ODL_EXE        = _SCRIPTS_DIR / "opendataloader-pdf.exe"

# Java paths (Adoptium Temurin)
_JAVA_DIRS = [
    r"C:\Program Files\Eclipse Adoptium\jdk-21.0.11.10-hotspot\bin",
    r"C:\Program Files\Eclipse Adoptium\jdk-21.0.7.6-hotspot\bin",
    r"C:\Program Files\Java\jdk-21\bin",
]


def _find_java_dir() -> str | None:
    """Tra ve thu muc bin chua java.exe, uu tien Adoptium."""
    for d in _JAVA_DIRS:
        if os.path.isfile(os.path.join(d, "java.exe")):
            return d
    java = shutil.which("java")
    return str(Path(java).parent) if java else None


def _java_env() -> dict:
    """Tra ve os.environ co them Java vao PATH."""
    env = dict(os.environ)
    jdir = _find_java_dir()
    if jdir and jdir not in env.get("PATH", ""):
        env["PATH"] = env.get("PATH", "") + os.pathsep + jdir
        env["JAVA_HOME"] = str(Path(jdir).parent)
    return env


def odl_available() -> bool:
    return ODL_HYBRID_EXE.exists() or shutil.which("opendataloader-pdf-hybrid") is not None


def java_available() -> bool:
    return _find_java_dir() is not None


def readiness() -> dict:
    """Trả về tình trạng sẵn sàng để UI báo lỗi rõ ràng."""
    return {
        "marker": marker_available(),
        "pandoc": find_pandoc() is not None,
        "odl": odl_available(),
        "java": java_available(),
    }


# ── Sidecar Marker (tiến trình thường trú giữ model nóng) ─────────
SIDECAR_HOST = "127.0.0.1"
SIDECAR_PORT = 17923
SIDECAR_SCRIPT = BASE_DIR / "scripts_marker" / "marker_server.py"
_sidecar_proc = None
_sidecar_lock = threading.Lock()


def _sidecar_url(path: str) -> str:
    return f"http://{SIDECAR_HOST}:{SIDECAR_PORT}{path}"


def _sidecar_ready(timeout: float = 2.0) -> bool:
    try:
        with urllib.request.urlopen(_sidecar_url("/health"), timeout=timeout) as r:
            return bool(json.loads(r.read().decode("utf-8")).get("ready"))
    except Exception:
        return False


def _port_open() -> bool:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(0.5)
    try:
        s.connect((SIDECAR_HOST, SIDECAR_PORT))
        return True
    except Exception:
        return False
    finally:
        s.close()


def ensure_sidecar(wait_ready: int = 300, progress_cb=None) -> bool:
    """Đảm bảo sidecar đang chạy + đã nạp model. Khởi động nếu cần."""
    global _sidecar_proc
    if _sidecar_ready():
        return True
    with _sidecar_lock:
        if _sidecar_ready():
            return True
        if not _port_open():
            # Khởi động sidecar — detached, ghi log ra file (KHÔNG pipe -> tránh deadlock)
            MARKER_OUT.mkdir(parents=True, exist_ok=True)
            logf = open(MARKER_OUT / "sidecar.log", "ab")
            flags = 0x08000000 if os.name == "nt" else 0  # CREATE_NO_WINDOW
            _sidecar_proc = subprocess.Popen(
                [str(MARKER_PY), str(SIDECAR_SCRIPT),
                 "--port", str(SIDECAR_PORT), "--idle-timeout", "900"],
                stdout=logf, stderr=logf, creationflags=flags,
            )
        # Đợi nạp model xong
        t0 = time.time()
        announced = False
        while time.time() - t0 < wait_ready:
            if _sidecar_ready():
                return True
            if progress_cb and not announced:
                progress_cb("sidecar", "Đang khởi động máy chủ Marker, nạp model 1 lần…")
                announced = True
            time.sleep(2)
    return _sidecar_ready()


def _convert_via_sidecar(pdf_path: Path, work_dir: Path, disable_ocr: bool,
                         timeout: int = 3600) -> Path:
    body = json.dumps({
        "pdf_path": str(pdf_path), "work_dir": str(work_dir),
        "disable_ocr": bool(disable_ocr), "fname_base": "doc",
    }).encode("utf-8")
    req = urllib.request.Request(_sidecar_url("/convert"), data=body,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        d = json.loads(r.read().decode("utf-8"))
    if not d.get("ok"):
        raise RuntimeError("Sidecar: " + d.get("error", "lỗi không rõ"))
    return Path(d["md_path"])


def stop_sidecar():
    """Tắt sidecar (giải phóng RAM) — gọi khi thoát app nếu muốn."""
    try:
        req = urllib.request.Request(_sidecar_url("/shutdown"), data=b"{}",
                                     headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=3)
    except Exception:
        pass


# ── Nhận diện lớp text ────────────────────────────────────────────

_detect_cache: dict = {}        # (path, mtime, dải trang) → kết quả; 1 job gọi nhiều lần


def detect_text_layer(pdf_path: str, page_indices: list | None = None) -> dict:
    """
    Đếm tỉ lệ trang có lớp text thật, và liệt kê trang nào là BẢN SCAN.
    Trả về {pages, checked, pages_with_text, coverage, is_digital,
            scan_pages, is_scan_doc, boilerplate}.

    Uỷ quyền cho core/pdf_scan.analyze_pdf: cách đếm cũ (len(text) >= 60) bị
    watermark dán ở lớp text qua mặt — PDF scan có watermark 63 ký tự/trang bị
    coi là PDF chữ, OCR bị tắt và nội dung thật mất sạch.

    Có cache theo (đường dẫn, mtime, dải trang): một lần convert gọi hàm này ở
    vài chỗ, mà quét cả quyển sách vài trăm trang không rẻ.
    """
    from core.pdf_scan import analyze_pdf
    try:
        key = (str(pdf_path), os.path.getmtime(pdf_path),
               tuple(page_indices) if page_indices is not None else None)
    except Exception:
        return analyze_pdf(pdf_path, page_indices=page_indices)
    if key in _detect_cache:
        return _detect_cache[key]
    res = analyze_pdf(pdf_path, page_indices=page_indices)
    if len(_detect_cache) > 8:
        _detect_cache.clear()
    _detect_cache[key] = res
    return res


# ── Các bước convert ──────────────────────────────────────────────

def _run_marker(pdf_path: Path, work_dir: Path, disable_ocr: bool,
                page_range: str | None = None) -> Path:
    work_dir.mkdir(parents=True, exist_ok=True)
    cmd = [
        str(MARKER_EXE),
        str(pdf_path),
        "--output_format", "markdown",
        "--output_dir", str(work_dir),
        "--disable_multiprocessing",
    ]
    if disable_ocr:
        cmd += ["--disable_ocr"]
    if page_range:
        cmd += ["--page_range", page_range]

    env = dict(os.environ)
    env.setdefault("TORCH_DEVICE", "cpu")

    proc = subprocess.run(cmd, env=env, capture_output=True,
                          text=True, encoding="utf-8", errors="replace",
                          creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if proc.returncode != 0:
        tail = (proc.stderr or "")[-1500:]
        raise RuntimeError(f"Marker lỗi (exit {proc.returncode}): {tail}")

    md_files = list(work_dir.rglob("*.md"))
    if not md_files:
        raise RuntimeError("Marker không tạo ra file Markdown")
    # Lấy file .md mới nhất (phòng khi còn sót lần trước)
    return max(md_files, key=lambda p: p.stat().st_mtime)


_PAGE_MARKER_RE = r'(?m)^[ \t]*<!--\s*p2w:page=\d+\s*-->[ \t]*\n?'


def _strip_page_markers(md_path: Path) -> Path:
    """Xoá mốc trang <!-- p2w:page=N --> khỏi markdown trước khi dựng Word."""
    import re as _re
    raw = md_path.read_text(encoding="utf-8", errors="replace")
    md_path.write_text(_re.sub(_PAGE_MARKER_RE, '', raw), encoding="utf-8")
    return md_path


def _run_pandoc(md_path: Path, out_docx: Path) -> Path:
    out_docx = out_docx.resolve()
    out_docx.parent.mkdir(parents=True, exist_ok=True)
    pandoc = find_pandoc()
    if not pandoc:
        raise RuntimeError("Chưa cài Pandoc")

    # Xoá mốc trang <!-- p2w:page=N --> (dùng để gắn hình đúng trang) — Word
    # không được thấy chúng.
    try:
        _strip_page_markers(md_path)
    except Exception:
        pass

    # Định dạng đề (CHOKEPOINT cho MỌI method odl/gemini/hybrid/marker):
    # tách đáp án A/B/C/D mỗi cái 1 dòng, Câu/Bài/PHẦN xuống dòng, xoá bullet,
    # gỡ $$ bọc nhầm. Idempotent nên ODL đã chạy rồi vẫn an toàn; quan trọng cho
    # output Gemini (vốn không qua _apply_bold_formatting).
    try:
        _strip_answer_bullets(md_path)
        _apply_bold_formatting(md_path)
    except Exception:
        pass

    # Chuyển ký hiệu Unicode Vật lý (vectơ, nhiệt độ, chỉ số, Hy Lạp...) → LaTeX
    # $...$, chuẩn hoá, rồi đưa về chuẩn file mẫu AIOMT (\text{đơn vị}, ^\circ,
    # .10^n — xem to_mau_standard). Áp dụng cho MỌI method.
    try:
        from core.latex_normalize import (normalize_latex, unicode_to_latex,
                                          to_mau_standard)
        raw = md_path.read_text(encoding="utf-8", errors="replace")
        md_path.write_text(to_mau_standard(normalize_latex(unicode_to_latex(raw))),
                           encoding="utf-8")
    except Exception:
        pass

    # GIỮ $...$ dạng TEXT literal (không chuyển OMML) để công cụ LaTeX trên Word
    # của thầy tự chuyển. -tex_math_dollars: $ là text; -raw_tex: \vec, \frac... giữ nguyên.
    cmd = [
        pandoc,
        md_path.name,
        "-o", str(out_docx),
        "--from=markdown-tex_math_dollars-raw_tex",
        "--wrap=none",
    ]
    proc = subprocess.run(cmd, cwd=str(md_path.parent), capture_output=True,
                          text=True, encoding="utf-8", errors="replace",
                          creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if proc.returncode != 0:
        tail = (proc.stderr or "")[-1500:]
        raise RuntimeError(f"Pandoc lỗi (exit {proc.returncode}): {tail}")
    return out_docx


def _odl_server_ready() -> bool:
    try:
        urllib.request.urlopen(
            f"http://127.0.0.1:{ODL_SERVER_PORT}/health", timeout=1)
        return True
    except Exception:
        return False


def _ensure_odl_server(progress_cb=None) -> bool:
    """Khởi động opendataloader-pdf-hybrid server nếu chưa chạy."""
    if _odl_server_ready():
        return True
    # Dung duong dan tuyet doi, tranh phu thuoc PATH
    odl_cmd = (str(ODL_HYBRID_EXE) if ODL_HYBRID_EXE.exists()
               else shutil.which("opendataloader-pdf-hybrid"))
    if not odl_cmd:
        return False
    MARKER_OUT.mkdir(parents=True, exist_ok=True)
    logf = open(MARKER_OUT / "odl_server.log", "ab")
    flags = 0x08000000 if os.name == "nt" else 0
    subprocess.Popen(
        [odl_cmd, "--port", str(ODL_SERVER_PORT)],
        stdout=logf, stderr=logf, creationflags=flags, env=_java_env(),
    )
    if progress_cb:
        progress_cb("odl", "Đang khởi động OpenDataLoader server…")
    for _ in range(30):
        time.sleep(2)
        if _odl_server_ready():
            return True
    return False


def _strip_pdf_encryption(pdf_path: Path, progress_cb=None) -> Path:
    """PDF có OWNER password (mở đọc không cần mật khẩu, chỉ khoá quyền in/copy —
    rất phổ biến với đề tải trên mạng): PyMuPDF/pdfplumber mở bình thường nên app
    không thấy gì lạ, nhưng PDFBox trong OpenDataLoader từ chối
    ("Error: 'x.pdf' is password-protected") → Java thoát mã 1 → thầy thấy
    "lỗi Java". Gỡ bằng cách lưu bản sao KHÔNG mã hoá vào MARKER_OUT rồi đưa bản
    sao cho ODL. PDF đòi mật khẩu thật (user password) → báo lỗi rõ ràng."""
    try:
        import fitz
    except ImportError:
        return pdf_path
    try:
        doc = fitz.open(str(pdf_path))
    except Exception:
        return pdf_path  # để bước sau báo lỗi đúng ngữ cảnh
    try:
        if doc.needs_pass:
            raise RuntimeError(
                "PDF được đặt mật khẩu mở file. Hãy mở bằng mật khẩu, "
                "lưu lại (Save As) bản không mật khẩu rồi chuyển lại.")
        if not doc.metadata.get("encryption"):
            return pdf_path
        MARKER_OUT.mkdir(parents=True, exist_ok=True)
        dec = MARKER_OUT / (_safe_stem(pdf_path.name) + "_giai_ma.pdf")
        doc.save(str(dec), encryption=fitz.PDF_ENCRYPT_NONE)
        if progress_cb:
            try:
                progress_cb("detect", "PDF có mã hoá (khoá quyền) — đã gỡ để OpenDataLoader đọc được")
            except Exception:
                pass
        return dec
    finally:
        doc.close()


def _run_odl(kwargs: dict) -> None:
    """Gọi opendataloader_pdf.convert và đổi lỗi Java thành thông báo đọc được.
    Runner của ODL chỉ raise CalledProcessError ("Command ['java', ...] returned
    non-zero exit status 1") — thông báo thật ODL in ra STDOUT (vd
    "is password-protected"), dưới pythonw không ai thấy. Lấy lại đuôi output."""
    import opendataloader_pdf
    try:
        opendataloader_pdf.convert(**kwargs)
    except FileNotFoundError as e:
        raise RuntimeError(
            "Không tìm thấy lệnh 'java'. Cài Java 21 (Temurin) tại https://adoptium.net") from e
    except subprocess.CalledProcessError as e:
        out = ((e.stdout or "") + "\n" + (e.stderr or "")).strip()
        tail = out[-600:] if out else f"exit {e.returncode}"
        raise RuntimeError(f"OpenDataLoader (Java) lỗi: {tail}") from e


def _strip_rule_lines(md_path: Path) -> Path:
    """Bỏ dòng kẻ ngang header/footer (____) lọt vào khi bật include_header_footer."""
    import re as _re
    try:
        t = md_path.read_text(encoding="utf-8", errors="replace")
        t = _re.sub(r'(?m)^[ \t]*_{5,}[ \t]*$\n?', '', t)
        md_path.write_text(t, encoding="utf-8")
    except Exception:
        pass
    return md_path


def _merge_md_files(work_dir: Path) -> Path:
    """Gộp tất cả .md trong work_dir theo thứ tự tên file → merged.md."""
    md_files = sorted(
        [p for p in work_dir.rglob("*.md") if p.name not in ("merged.md", "fixed.md")],
        key=lambda p: p.name
    )
    if not md_files:
        raise RuntimeError("Không tìm thấy file Markdown từ ODL")
    if len(md_files) == 1:
        return md_files[0]
    parts = [p.read_text(encoding="utf-8", errors="replace") for p in md_files]
    out = work_dir / "merged.md"
    out.write_text("\n\n---\n\n".join(parts), encoding="utf-8")
    return out


# Ngắt mạch: khi 9Router hoặc Gemini bị rate-limit/lỗi trong 1 phiên convert,
# bỏ qua các lần gọi sau để KHÔNG treo lâu (reset mỗi lần bắt đầu fix tài liệu mới).
_ai_skip_router = False
_ai_skip_gemini = False


def _reset_ai_breakers():
    global _ai_skip_router, _ai_skip_gemini
    _ai_skip_router = False
    _ai_skip_gemini = False


def _call_ai_fix(prompt_text: str, gemini_api_key: str,
                  niner_url: str = "", niner_key: str = "") -> str:
    """
    Goi AI de sua cong thuc. Uu tien:
    1. 9Router Gemini CLI (ag/gemini-3.7-flash-low) neu 9Router dang chay
    2. Gemini API truc tiep neu co key
    Fail-fast: timeout ngan, KHONG retry; gap rate-limit thi ngat mach (skip).
    Tra ve text da sua, hoac '' neu khong sua duoc.
    """
    global _ai_skip_router, _ai_skip_gemini
    router_url = niner_url or "http://127.0.0.1:20128/v1"

    # 1) 9Router (bo qua neu da bi ngat mach phien nay)
    if not _ai_skip_router:
        try:
            import urllib.request as _ur
            health = _ur.urlopen("http://127.0.0.1:20128/api/version", timeout=1)
            if health.status == 200:
                from openai import OpenAI
                client = OpenAI(
                    api_key=niner_key or "9router",
                    base_url=router_url,
                    timeout=20.0,      # fail nhanh, khong treo
                    max_retries=0,     # KHONG tu retry (tranh backoff lau khi 429)
                )
                resp = client.chat.completions.create(
                    model="ag/gemini-3.7-flash-low",
                    messages=[{"role": "user", "content": prompt_text}],
                    temperature=0.1,
                )
                return resp.choices[0].message.content.strip()
        except Exception as e:
            msg = str(e)
            # Rate-limit / het quota -> ngat mach 9Router cho ca tai lieu
            if '429' in msg or 'RESOURCE_EXHAUSTED' in msg or 'RATE_LIMIT' in msg \
               or 'exhausted' in msg.lower():
                _ai_skip_router = True

    # 2) Fallback: Gemini API truc tiep (bo qua neu da ngat mach)
    if gemini_api_key and not _ai_skip_gemini:
        try:
            import google.generativeai as genai
            genai.configure(api_key=gemini_api_key)
            gm = genai.GenerativeModel("gemini-2.5-flash")
            resp = gm.generate_content(
                prompt_text,
                request_options={"timeout": 30},
            )
            return resp.text.strip()
        except Exception as e:
            msg = str(e)
            if '429' in msg or 'RESOURCE_EXHAUSTED' in msg or 'quota' in msg.lower() \
               or 'exhausted' in msg.lower():
                _ai_skip_gemini = True

    return ""  # Khong sua duoc, giu nguyen


def _fix_formulas_with_gemini(md_path: Path, api_key: str,
                               progress_cb=None,
                               niner_url: str = "", niner_key: str = "") -> Path:
    """
    Sua cong thuc trong Markdown sau khi ODL convert.
    Uu tien: 9Router ag/gemini-3.7-flash-low → Gemini API direct fallback.
    """
    PROMPT = (
        "Bạn là chuyên gia LaTeX và Vật lý THPT Việt Nam.\n"
        "Sửa lại Markdown đề thi Vật lý sau:\n"
        "1. Block $$...$$ chứa text không phải LaTeX (vd: đáp án 'A. ... B. ... C. ... D. ...', "
        "hay câu trả lời thông thường) → xóa dấu $$ đó, giữ nguyên text bên trong\n"
        "2. Ký hiệu Vật lý thuần (v₀, v0, F, m, a, t, Δt, λ, α, β, ω, μ, ε...) "
        "nằm rời trong văn bản → bọc trong $...$\n"
        "3. Công thức bị vỡ thành các ký tự rời rạc → ghép lại thành LaTeX inline $...$\n"
        "4. Giữ nguyên tiếng Việt, cấu trúc trắc nghiệm, số thứ tự câu\n"
        "5. KHÔNG thêm giải thích, chỉ trả về Markdown đã sửa\n\n"
        "Markdown cần sửa:\n```markdown\n{content}\n```"
    )

    _reset_ai_breakers()   # reset ngat mach cho tai lieu moi
    content = md_path.read_text(encoding="utf-8", errors="replace")
    sections = content.split("\n\n---\n\n")
    fixed_parts = []

    for i, section in enumerate(sections):
        if not section.strip():
            fixed_parts.append(section)
            continue
        # Neu ca 2 AI deu da ngat mach (rate-limit/loi) -> bo qua, giu nguyen text
        if _ai_skip_router and (_ai_skip_gemini or not api_key):
            if progress_cb and i == 0:
                progress_cb("fix", "AI sửa công thức không khả dụng (hết quota) — bỏ qua, giữ nguyên")
            fixed_parts.append(section)
            continue
        if progress_cb:
            progress_cb("fix", f"Đang sửa công thức trang {i+1}/{len(sections)}…")
        try:
            fixed = _call_ai_fix(
                PROMPT.format(content=section),
                gemini_api_key=api_key,
                niner_url=niner_url,
                niner_key=niner_key,
            )
            if not fixed:
                fixed_parts.append(section)
                continue
            # Boc markdown code block neu AI tra ve
            if fixed.startswith("```markdown"):
                fixed = fixed[len("```markdown"):].strip()
            if fixed.startswith("```"):
                fixed = fixed[3:].strip()
            if fixed.endswith("```"):
                fixed = fixed[:-3].strip()
            fixed_parts.append(fixed)
        except Exception:
            fixed_parts.append(section)

    out = md_path.parent / "fixed.md"
    out.write_text("\n\n---\n\n".join(fixed_parts), encoding="utf-8")
    return out


def _remove_page_images(md_path: Path,
                         max_pixels: int = 400_000) -> Path:
    """
    Xoa anh chup nguyen trang (dien tich > max_pixels) khoi Markdown.
    Giu lai anh nho: so do, do thi, hinh ve Vat Ly (thuong < 200x200).

    Nguong mac dinh: 400_000 pixels (~630x630) loc duoc anh nguyen trang A4
    nhung giu lai cac hinh ve nho trong de thi.
    """
    import re
    try:
        from PIL import Image as PILImage
    except ImportError:
        return md_path

    content = md_path.read_text(encoding="utf-8", errors="replace")
    base_dir = md_path.parent

    def _is_page_image(img_rel: str) -> bool:
        """Tra ve True neu day la anh nguyen trang can xoa."""
        # Strip angle brackets Markdown dung boc path co khoang trang
        # VD: <0_images/imageFile1.png> → 0_images/imageFile1.png
        img_rel = img_rel.strip().strip('<>').strip()
        # Thu cac duong dan co the
        for candidate in [
            base_dir / img_rel,
            base_dir / "images" / Path(img_rel).name,
            Path(img_rel),
        ]:
            if candidate.exists():
                try:
                    with PILImage.open(candidate) as im:
                        w, h = im.size
                        return (w * h) > max_pixels
                except Exception:
                    pass
                # Neu khong mo duoc PIL, dung kich thuoc file lam tieu chi
                try:
                    size_kb = candidate.stat().st_size / 1024
                    return size_kb > 300  # > 300KB = gan chac la anh nguyen trang
                except Exception:
                    pass
        return False

    def _replace_img(m):
        path = m.group(2).strip()
        if _is_page_image(path):
            return ""  # Xoa anh nguyen trang
        return m.group(0)  # Giu nguyen anh nho

    # Bắt cả 2 dạng: ![alt](path) và ![alt](<path>)
    content = re.sub(r'!\[([^\]]*)\]\(<([^>]*)>\)', _replace_img, content)
    content = re.sub(r'!\[([^\]]*)\]\(([^)<>]+)\)', _replace_img, content)
    # Don dep dong trong thua sau khi xoa
    content = re.sub(r'\n{3,}', '\n\n', content)

    md_path.write_text(content, encoding="utf-8")
    return md_path


def _strip_answer_bullets(md_path: Path) -> Path:
    """
    Xoa dau bullet/list truoc dap an A/B/C/D va y a/b/c/d.
    Bat tat ca cac dang: - * + • ▪ ◦ ▸ ▹ ► ➤ va Unicode bullet.
    """
    import re
    content = md_path.read_text(encoding="utf-8", errors="replace")

    # Go $ / $$ boc nham quanh dap an (vd "$$A. 15. B. 30...$$") truoc khi tach dong
    try:
        from core.latex_normalize import unwrap_pseudo_math
        content = unwrap_pseudo_math(content)
    except Exception:
        pass

    # Pattern rong: bat ki ky tu bullet nao (ASCII + Unicode) truoc A./B./C./D. hoac a)/b)/c)/d)
    BULLET_CHARS = r'[-*+•·▪▸▹►➤◦‣⁃]'
    content = re.sub(
        rf'^[ \t]*{BULLET_CHARS}{{1,3}}\s+(?=([A-D]\.|[a-d]\)|\*\*[A-D][\.\*]|\*\*[a-d]\)\*\*))',
        '',
        content,
        flags=re.MULTILINE
    )

    # Bat them truong hop Gemini dung so thu tu: "1. A." hoac "- **A.**"
    content = re.sub(
        r'^[ \t]*\d+\.\s+(?=([A-D]\.|\*\*[A-D]\.))',
        '',
        content,
        flags=re.MULTILINE
    )

    md_path.write_text(content, encoding="utf-8")
    return md_path


def _meaningful_text_len(content: str) -> int:
    """
    So ky tu CHU thuc su trong 1 doan Markdown (bo thẻ ảnh, bỏ ký hiệu markdown).
    Dung de phan dinh "trang nay rong" — trang scan ma ODL xu ly chi ra anh trang.
    """
    import re
    text = re.sub(r'!\[[^\]]*\]\(<[^>]*>\)', '', content or '')
    text = re.sub(r'!\[[^\]]*\]\([^)]*\)', '', text)
    text = re.sub(r'<!--[\s\S]*?-->', '', text)          # mốc trang p2w:page=N
    text = re.sub(r'^---+$', '', text, flags=re.MULTILINE)
    text = re.sub(r'[#*_`\-=|>]', '', text)
    return len(text.strip())


def _has_meaningful_text(md_path: Path) -> bool:
    """
    Kiem tra Markdown co chua text thuc su khong (khong chi toan anh).
    ODL convert scan PDF se chi ra anh trang, khong co text → can fallback.
    """
    return _meaningful_text_len(
        md_path.read_text(encoding="utf-8", errors="replace")) > 50


def _parse_page_range(page_range: str, total_pages: int) -> list:
    """
    Parse chuoi chon trang (1-indexed) thanh list index 0-indexed.
    Ho tro: "1-3", "1,3,5", "2", "1-3,5,7-9"
    """
    pages = set()
    for part in page_range.replace(' ', '').split(','):
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
    return sorted(p for p in pages if 0 <= p < total_pages)


def _apply_bold_formatting(md_path: Path) -> Path:
    """
    To dam va tach dong:
      - So thu tu cau hoi: "Câu 1." → **Câu 1.**
      - Dap an ABCD: A. → **A.**  (moi dap an 1 dong rieng)
      - Y dung sai: a) → **a)**   (moi y 1 dong rieng)
    """
    import re
    content = md_path.read_text(encoding="utf-8", errors="replace")

    # --- Buoc 0: Câu/Bài/PHẦN — xoa bullet/dau gach phia truoc + xuong 1 dong ---
    # Header de thi: "Câu 1." "Bài 2." "PHẦN I." (â/a, à/a; PHẦN/PHAN), co the co ** boc
    _HEADER = r'(?:C[aâ]u|B[àa]i)\s+\d+\s*[.:]|PH[ẦA]N\b'
    # 1) Xoa bullet/dau gach/vach dung ngay truoc header (KHONG dung '*' de khong an ** bold)
    content = re.sub(
        r'[│|\-–—•·▪▸▹►➤◦‣⁃]+[ \t]*(?=\*{0,2}(?:' + _HEADER + r'))',
        '',
        content,
    )
    # 2) Header dinh cuoi dong truoc (vd "D. 4 s. Câu 11.") → xuong 1 dong (giu ** neu co)
    content = re.sub(
        r'(?<=\S)[ \t]+(\*{0,2}(?:' + _HEADER + r'))',
        r'\n\1',
        content,
    )

    # 3) So thap phan bi tach: "4 , 5" → "4,5" (space ca 2 ben dau phay)
    content = re.sub(r'(\d) , (\d)', r'\1,\2', content)
    # 4) Xoa dong chi co dau cham/ky tu rac
    content = re.sub(r'(?m)^[ \t]*[.·•]+[ \t]*$\n?', '', content)

    # --- Buoc 1: Tach KHOI dap an inline "A. .. B. .. C. .. D. .." ---
    # Chi tach khi co DU 4 dap an A→B→C→D dung thu tu tren 1 dong → AN TOAN,
    # KHONG nham "vat B." / "diem A." le trong cau hoi (vi khong tao thanh khoi).
    # Bat ca dang thuong "A. " lan bold "**A.**".
    _opt = lambda L: r'\*{0,2}' + L + r'\.(?:\*\*)?[ \t]'
    _ANS_BLOCK = re.compile(
        r'[ \t]*(' + _opt('A') + r'.+?)\s+(' + _opt('B') + r'.+?)\s+('
        + _opt('C') + r'.+?)\s+(' + _opt('D') + r')')
    content = _ANS_BLOCK.sub(
        lambda m: '\n' + m.group(1) + '\n' + m.group(2) + '\n'
                  + m.group(3) + '\n' + m.group(4),
        content)

    # Tach khoi y dung-sai inline "a) .. b) .. c) .. d) .." (du 4 y)
    _sub = lambda L: r'\*{0,2}' + L + r'\)(?:\*\*)?[ \t]'
    _SUB_BLOCK = re.compile(
        r'[ \t]*(' + _sub('a') + r'.+?)\s+(' + _sub('b') + r'.+?)\s+('
        + _sub('c') + r'.+?)\s+(' + _sub('d') + r')')
    content = _SUB_BLOCK.sub(
        lambda m: '\n' + m.group(1) + '\n' + m.group(2) + '\n'
                  + m.group(3) + '\n' + m.group(4),
        content)

    # --- Buoc 2: To dam ---
    # So cau hoi: "Câu X." / "Cau X."
    content = re.sub(
        r'(?<!\*)(Câu\s+\d+|C[aâ]u\s+\d+)(\.|:)(?!\*)',
        r'**\1\2**',
        content
    )

    # Dap an A/B/C/D dau dong (chua bold)
    content = re.sub(
        r'^(?!\*\*)([A-D])\.\s',
        r'**\1.** ',
        content,
        flags=re.MULTILINE
    )

    # Y a/b/c/d dau dong (chua bold)
    content = re.sub(
        r'^(?!\*\*)([a-d])\)\s',
        r'**\1)** ',
        content,
        flags=re.MULTILINE
    )

    # --- Buoc 3: Dam bao moi dap an da bold cung tren dong rieng ---
    # Neu **A.**/**B.**/**C.**/**D.** van con giua dong → xuong hang
    # (dap an luon duoc bold dang **X.** nen an toan, khong nham text thuong)
    content = re.sub(
        r'(?<=[^\n])\s+(\*\*[A-D]\.\*\*)',
        r'\n\1',
        content
    )
    content = re.sub(
        r'(?<=[^\n])\s+(\*\*[b-d]\)\*\*)',
        r'\n\1',
        content
    )

    # --- Buoc 4: Dam bao blank line truoc moi dong dap an (tao paragraph rieng trong Word) ---
    # Blank line = paragraph break → moi dap an A/B/C/D la 1 doan rieng, co khoang cach dep
    # Neu da co blank line truoc → giu nguyen; neu chua → them vao
    content = re.sub(
        r'(?<!\n)\n(\*\*[A-D]\.\*\*)',
        r'\n\n\1',
        content
    )
    content = re.sub(
        r'(?<!\n)\n(\*\*[a-d]\)\*\*)',
        r'\n\n\1',
        content
    )

    md_path.write_text(content, encoding="utf-8")
    return md_path


def _is_ds_table_rows(rows) -> bool:
    """Bảng CÂU HỎI ĐÚNG-SAI (format 2025): có cột Đ/S (hoặc Đúng/Sai) ở hàng đầu,
    hoặc ≥2 hàng mà ô đầu bắt đầu bằng nhận định a)/b)/c)/d). Bảng này phải giữ
    TEXT (nhận định là nội dung câu hỏi), KHÔNG được cắt thành ảnh."""
    import re as _re
    if not rows:
        return False
    for r in rows[:2]:
        cells = [str(c or '').strip() for c in (r or [])]
        if (('Đ' in cells or 'Đúng' in cells) and ('S' in cells or 'Sai' in cells)):
            return True
    n = sum(1 for r in rows
            if r and _re.match(r'^\s*\**[a-d][.)]', str(r[0] or '')))
    return n >= 2


def _is_ds_md_block(block_lines) -> bool:
    """Nhận diện block bảng Markdown là bảng câu hỏi Đúng-Sai (xem _is_ds_table_rows)."""
    import re as _re
    rows = []
    for ln in block_lines:
        cells = [c.strip() for c in ln.strip().strip('|').split('|')]
        if cells and _re.match(r'^:?-{2,}:?$', cells[0].replace(' ', '')):
            continue   # dòng kẻ |---|---|
        rows.append(cells)
    return _is_ds_table_rows(rows)


def _ds_block_to_lines(block_lines):
    """Bảng Markdown Đúng-Sai → các dòng text: lấy ô ĐẦU mỗi hàng (nhận định /
    câu dẫn), bỏ hàng kẻ + hàng tiêu đề 'Nội dung|Đ|S'. Cột Đ/S trống bị bỏ."""
    import re as _re
    outl = ['']
    for ln in block_lines:
        cells = [c.strip() for c in ln.strip().strip('|').split('|')]
        first = cells[0] if cells else ''
        if not first or _re.match(r'^:?-{2,}:?$', first.replace(' ', '')):
            continue
        if _re.match(r'^(Nội dung|Phát biểu|Nhận định|Phương án)\s*$', first, _re.IGNORECASE):
            continue
        outl.append(first)
        outl.append('')
    return outl


def _replace_tables_with_images(pdf_path: Path, work_dir: Path, md_path: Path,
                                progress_cb=None) -> Path:
    """
    Phát hiện bảng trong PDF (PyMuPDF find_tables), cắt thành ảnh và thay block
    bảng Markdown (| ... |) tương ứng bằng ![Bảng](figures/...).

    Ghép theo THỨ TỰ đọc (bảng PDF thứ N ↔ block markdown thứ N). Nếu lệch số
    lượng, thay được bao nhiêu hay bấy nhiêu; block còn lại giữ nguyên (Pandoc
    sẽ tạo bảng Word — fallback an toàn).

    NGOẠI LỆ (04/07/2026): bảng CÂU HỎI ĐÚNG-SAI (cột Đ/S, nhận định a-d) giữ
    TEXT — chuyển block thành các dòng nhận định, KHÔNG thay ảnh. Entry None
    trong table_refs giữ đúng cặp thứ tự với các bảng còn lại.
    """
    import re as _re
    try:
        import fitz
    except ImportError:
        return md_path

    fig_dir = work_dir / "figures"
    fig_dir.mkdir(exist_ok=True)

    # 1) Cắt mọi bảng trong PDF theo thứ tự trang → trên xuống
    table_refs = []
    try:
        doc = fitz.open(str(pdf_path))
    except Exception:
        return md_path
    for pidx, page in enumerate(doc):
        try:
            found = page.find_tables()
            tlist = list(getattr(found, "tables", []) or [])
        except Exception:
            tlist = []
        # Sắp xếp theo y (trên xuống) rồi x (trái qua phải)
        tlist.sort(key=lambda t: (round(t.bbox[1]), round(t.bbox[0])))
        for t in tlist:
            try:
                try:
                    rows = t.extract() or []
                except Exception:
                    rows = []
                if _is_ds_table_rows(rows):
                    # Bảng câu hỏi Đúng-Sai → giữ TEXT; None giữ chỗ cho đúng cặp
                    table_refs.append(None)
                    continue
                try:
                    from core.pdf_word_figures import is_figure_like_table
                    if is_figure_like_table(t):
                        # Đồ thị có lưới ô vuông bị find_tables tưởng là bảng → KHÔNG
                        # phải bảng: bước gắn hình (pdf_word_figures) sẽ cắt và neo vào
                        # câu. Không đưa vào table_refs (ODL không xuất block cho nó;
                        # nếu có thì block toàn ô trống bị bỏ ở vòng dưới) để cặp thứ tự
                        # bảng↔block của các bảng THẬT không lệch.
                        continue
                except Exception:
                    pass
                clip = fitz.Rect(t.bbox)
                # Nới nhẹ biên để không cắt cụt viền/chữ
                clip = fitz.Rect(clip.x0 - 3, clip.y0 - 3, clip.x1 + 3, clip.y1 + 3)
                pix = page.get_pixmap(matrix=fitz.Matrix(2, 2), clip=clip)
                try:
                    pix.set_dpi(144, 144)   # zoom 2 → 144 DPI: Word hiện đúng cỡ gốc
                except Exception:
                    pass
                name = f"p{pidx+1}_table{len(table_refs)+1}.png"
                pix.save(str(fig_dir / name))
                table_refs.append(f"figures/{name}")
            except Exception:
                pass
    doc.close()

    if not table_refs:
        return md_path
    if progress_cb:
        n_img = sum(1 for r in table_refs if r)
        n_ds = sum(1 for r in table_refs if r is None)
        progress_cb("table", f"Đã phát hiện {len(table_refs)} bảng → {n_img} thành ảnh, "
                             f"{n_ds} bảng Đúng-Sai giữ text…")

    # 2) Thay block bảng Markdown theo thứ tự
    content = md_path.read_text(encoding="utf-8", errors="replace")
    lines = content.split("\n")
    is_tbl = lambda s: bool(_re.match(r'^\s*\|.*\|\s*$', s))

    def _is_empty_grid_block(block) -> bool:
        """Block bảng markdown ≥ 12 ô mà ≤ 25 % ô có chữ = ODL xuất lưới đồ thị
        thành bảng → bỏ (hình do bước gắn hình chèn), không tiêu thụ ref bảng thật."""
        cells = filled = 0
        for ln in block:
            s = ln.strip().strip('|')
            if _re.match(r'^[\s|:\-]*$', s):
                continue
            for c in s.split('|'):
                cells += 1
                if c.strip():
                    filled += 1
        return cells >= 12 and filled <= 0.25 * cells

    out, i, used = [], 0, 0
    while i < len(lines):
        if is_tbl(lines[i]):
            j = i
            while j < len(lines) and is_tbl(lines[j]):
                j += 1
            if (j - i) >= 2:   # >=2 dòng mới coi là bảng
                block = lines[i:j]
                if _is_empty_grid_block(block):
                    i = j
                    continue
                ref = table_refs[used] if used < len(table_refs) else False
                if (used < len(table_refs) and ref is None) or _is_ds_md_block(block):
                    # Bảng Đúng-Sai → các dòng nhận định (text), không ảnh
                    out.extend(_ds_block_to_lines(block))
                    if used < len(table_refs):
                        used += 1
                elif used < len(table_refs):
                    out.append("")
                    out.append(f"![Bảng]({ref})")
                    out.append("")
                    used += 1
                else:
                    out.extend(block)   # hết ref → giữ nguyên (Pandoc tạo bảng Word)
            else:
                out.extend(lines[i:j])   # giữ nguyên
            i = j
        else:
            out.append(lines[i])
            i += 1

    md_path.write_text("\n".join(out), encoding="utf-8")
    return md_path


def _convert_via_odl(pdf_path: Path, work_dir: Path,
                     gemini_api_key: str = "", page_range: str | None = None,
                     niner_url: str = "", niner_key: str = "",
                     progress_cb=None) -> Path:
    """OpenDataLoader-PDF hybrid mode: PDF → Markdown (KHÔNG gọi AI — nhanh)."""
    try:
        import opendataloader_pdf
    except ImportError:
        raise RuntimeError(
            "Chưa cài opendataloader-pdf. Chạy: pip install opendataloader-pdf[hybrid]")

    work_dir.mkdir(parents=True, exist_ok=True)
    if progress_cb:
        progress_cb("odl", "OpenDataLoader đang phân tích PDF…")

    # Them Java vao PATH cua process hien tai de ODL goi duoc java subprocess
    jdir = _find_java_dir()
    if jdir and jdir not in os.environ.get("PATH", ""):
        os.environ["PATH"] = os.environ.get("PATH", "") + os.pathsep + jdir
        os.environ["JAVA_HOME"] = str(Path(jdir).parent)

    odl_kwargs = dict(
        input_path=[str(pdf_path)],
        output_dir=str(work_dir),
        format="markdown",
        hybrid="docling-fast",
        hybrid_mode="auto",
        hybrid_fallback=True,
        quiet=True,
        # BẮT BUỘC (10/09/2026): mặc định ODL tự bỏ header/footer lặp và khi chạy
        # ≥ 2 trang nó NUỐT LUÔN đoạn đầu trang nằm sát header (mất cả câu dẫn
        # "Câu 14: …" của Đề 4). Giữ header/footer rồi tự lọc dòng kẻ ____ ở dưới.
        include_header_footer=True,
    )
    if page_range:
        odl_kwargs["pages"] = page_range  # ODL nhan "1-3" hoac "1,2,5"
    _run_odl(odl_kwargs)

    # Merge tất cả .md thành một file duy nhất
    if progress_cb:
        progress_cb("odl", "Gộp các trang Markdown…")
    md = _strip_rule_lines(_merge_md_files(work_dir))

    # Xoa anh nguyen trang, chuyen bang -> anh, xoa bullet, to dam.
    # KHONG goi AI sua cong thuc (cham + thuong vo ich voi de chu yeu la chu).
    # LaTeX $...$ neu co se duoc normalize_latex + Pandoc chuyen thanh OMML o _run_pandoc.
    md = _remove_page_images(md)
    # Bang -> anh: chen SAU _remove_page_images de anh bang khong bi xoa nham
    md = _replace_tables_with_images(pdf_path, work_dir, md, progress_cb=progress_cb)
    md = _strip_answer_bullets(md)
    md = _apply_bold_formatting(md)

    return md


# Gemini trả JSON: markdown (có {{FIGURE_N}} placeholder) + bbox từng hình
_GEMINI_PROMPT = (
    "Đây là một trang đề thi Vật Lý THPT Việt Nam.\n\n"
    "Trả về JSON THUẦN (không có ```json``` code block) theo đúng format:\n"
    "{\n"
    '  "markdown": "...",\n'
    '  "figures": [\n'
    '    {"id": 1, "bbox": [x1_pct, y1_pct, x2_pct, y2_pct], "caption": "..."}\n'
    "  ]\n"
    "}\n\n"
    "QUY TẮC cho markdown:\n"
    "1. Layout 2 cột (Phần I): đọc cột TRÁI hết từ trên xuống, sau đó cột PHẢI.\n"
    "   KHÔNG đọc ngang. Số câu phải liên tục 1, 2, 3, 4...\n"
    "2. Mỗi hình vẽ/đồ thị/sơ đồ VÀ MỖI BẢNG SỐ LIỆU → đặt {{FIGURE_1}}, "
    "{{FIGURE_2}}... đúng vị trí trong text (sau câu hỏi liên quan, trước đáp án). "
    "TRỪ bảng câu hỏi Đúng-Sai và ô trống điền đáp án — xem quy tắc 7a/7b.\n"
    "3. Công thức Vật lý/Toán → LaTeX: $v = v_0 + at$ hoặc $$F = ma$$\n"
    "3a. PHÂN SỐ / THƯƠNG: π/4 → $\\dfrac{\\pi}{4}$ (hoặc $\\pi/4$); 1/2 → $\\dfrac{1}{2}$. "
    "TUYỆT ĐỐI KHÔNG tách tử và mẫu thành 'π 4' — phải giữ dấu '/' hoặc \\dfrac.\n"
    "3b. Giữ ĐÚNG vị trí dấu '=': 'v = 2000 m/s' KHÔNG đổi thành 'v 2000 = m/s'. "
    "Đọc TRỌN VẸN mỗi đáp án (số + đơn vị), KHÔNG chèn ký tự lạ (□, ?, ).\n"
    "3c. KÝ HIỆU KHOA HỌC (số mũ của 10): đọc kỹ phần mũ nhỏ phía trên. "
    "1,5.10⁶ Hz → $1,5.10^6\\text{Hz}$; 3.10⁻⁶ → $3.10^{-6}$. Dấu CHẤM nhân trước 10 "
    "(KHÔNG \\cdot, KHÔNG \\times), thập phân dấu PHẨY trần (KHÔNG {,}). "
    "TUYỆT ĐỐI KHÔNG viết liền '106', '102' — số mũ phải ở dạng $10^6$, $10^{-6}$.\n"
    "3d. ĐƠN VỊ trong công thức bọc \\text{...}, số mũ đơn vị NGOÀI \\text: "
    "$g = 10\\text{m/s}^2$; $1,0\\text{cm}^2$; $c = 0,460\\text{kJ/kg.K}$. "
    "NHIỆT ĐỘ: $25^\\circ\\text{C}$ (dùng ^\\circ, KHÔNG ký tự °); góc: $45^\\circ$. "
    "Số + đơn vị ĐƠN GIẢN (không mũ/độ/khoa học) để text thường KHÔNG bọc $: 1,5J, 20N, 30 m. "
    "THỂ TÍCH luôn bọc $ và viết đơn vị bằng \\text{}: $V = 3,3.10^{-3}\\text{m}^3$, "
    "$20\\text{cm}^3$, $5\\text{dm}^3$, lít viết $2\\text{l}$ (KHÔNG viết 'lít' trong công thức).\n"
    "3e. Dấu $ phải LUÔN ĐỦ CẶP mở-đóng. Biểu thức so sánh nằm TRỌN trong 1 cặp $: "
    "viết $T_1 > T_0$, TUYỆT ĐỐI KHÔNG viết '$T_1 >$ T_0$' (dư 1 dấu $ lẻ).\n"
    "4. Đáp án A/B/C/D: **A.** text, **B.** text — mỗi cái 1 dòng, tô đậm, ĐÚNG THỨ TỰ A→B→C→D\n"
    "5. Ý a/b/c/d Đúng-Sai: **a)** text — mỗi ý 1 dòng, tô đậm\n"
    "6. Số câu: **Câu 1.** **Câu 2.** ... — tô đậm\n"
    "7. BẢNG SỐ LIỆU: KHÔNG viết Markdown table. Thay bằng {{FIGURE_N}} và "
    "khai báo trong figures (sẽ được cắt thành ảnh giữ nguyên định dạng)\n"
    "7a. NGOẠI LỆ — BẢNG CÂU HỎI ĐÚNG-SAI (bảng có cột 'Đ'/'S' hoặc 'Đúng'/'Sai', "
    "mỗi hàng là 1 nhận định a/b/c/d): KHÔNG PHẢI hình, TUYỆT ĐỐI KHÔNG tạo {{FIGURE}} "
    "cho bảng này — đọc TOÀN BỘ nội dung thành TEXT: câu dẫn trước, rồi mỗi nhận định "
    "1 dòng theo quy tắc 5 (**a)** ... **b)** ...). Bỏ qua 2 cột Đ/S trống (chỉ là ô tick).\n"
    "7b. Ô/KHUNG KẺ TRỐNG để học sinh điền đáp án (trả lời ngắn): KHÔNG phải hình, "
    "KHÔNG {{FIGURE}} — thay bằng 1 dòng '**Điền đáp án:**'.\n"
    "8. Giữ nguyên tiếng Việt có dấu, đúng chính tả\n\n"
    "QUY TẮC cho figures:\n"
    "- bbox: [left%, top%, right%, bottom%] tính theo % chiều rộng/cao trang (0.0–100.0)\n"
    "- Liệt kê: hình vẽ vật lý, đồ thị (v-t, x-t, I-U...), sơ đồ mạch điện, "
    "hình học, VÀ MỌI BẢNG SỐ LIỆU\n"
    "- KHÔNG liệt kê: văn bản thuần, công thức rời, logo, số trang, "
    "bảng câu hỏi Đúng-Sai (cột Đ/S — đọc thành text theo 7a), ô trống điền đáp án (7b)\n"
    "- caption: mô tả ngắn bằng tiếng Việt (vd: 'Đồ thị v-t', 'Bảng số liệu', 'Sơ đồ mạch RLC')\n"
    "- Nếu trang không có hình/bảng → figures: []"
)


# Prompt riêng cho TRANG SCAN (ảnh chụp). Khác _GEMINI_PROMPT ở mấy điểm sống còn:
#   - nói rõ đây là bản scan, nhiệm vụ số một là ĐỌC ĐỦ (prompt digital không hề
#     nhắc tới scan, cũng không có lệnh đọc đủ)
#   - BỎ "số câu phải liên tục 1,2,3,4": sách scan hay bắt đầu giữa câu, lệnh đó
#     khiến model tự đánh số lại và bỏ phần lẻ đầu/cuối trang
#   - BỎ ép đọc 2 cột: sách scan thường 1 cột, ép 2 cột làm model đọc ngang
#   - Bảng số liệu → BẢNG MARKDOWN (không phải {{FIGURE}}): trên trang scan bbox
#     hay lệch, mà placeholder không cắt được thì mất nguyên bảng
#   - nén vi-quy-tắc LaTeX lại cho gọn để model dồn sức vào việc đọc đủ
#   - bắt buộc anchor_text để neo hình: trang scan không có lớp text để so khớp
_SCAN_PROMPT = (
    "Đây là ẢNH CHỤP/BẢN SCAN một trang sách hoặc đề thi Vật lí THPT Việt Nam.\n"
    "Chữ có thể mờ, lệch, dính nhau.\n\n"
    "NHIỆM VỤ SỐ MỘT: ĐỌC TOÀN BỘ chữ trên trang, KHÔNG BỎ SÓT dòng nào.\n"
    "Thà đọc hơi sai một từ còn hơn bỏ qua cả một ý. Trước khi trả lời, hãy tự\n"
    "quét lại từ mép trên xuống mép dưới xem còn dòng nào chưa ghi không.\n\n"
    "Trả về JSON THUẦN (không có ```json``` code block) theo đúng format:\n"
    "{\n"
    '  "markdown": "...",\n'
    '  "figures": [\n'
    '    {"id": 1, "bbox": [x1_pct, y1_pct, x2_pct, y2_pct], '
    '"anchor_text": "...", "caption": "..."}\n'
    "  ]\n"
    "}\n\n"
    "QUY TẮC ĐỌC\n"
    "1. Đọc theo đúng thứ tự mắt đọc. Tự nhận ra trang 1 cột hay 2 cột: CHỈ KHI "
    "thấy rõ hai khối chữ song song mới đọc hết cột trái rồi sang cột phải. "
    "Sách thường là 1 cột — đừng tự chẻ thành 2 cột.\n"
    "2. GIỮ NGUYÊN số thứ tự IN TRÊN TRANG (Câu 37, Bài 12, Ví dụ 3, ý a), b)...). "
    "TUYỆT ĐỐI KHÔNG đánh số lại từ 1, KHÔNG sắp xếp lại.\n"
    "3. Trang bắt đầu hoặc kết thúc GIỮA một câu là bình thường: cứ ghi đúng phần "
    "nhìn thấy, không bịa thêm, không bỏ phần lẻ đó đi.\n"
    "4. BỎ: watermark, tên website, header/footer lặp, số trang.\n"
    "5. Giữ nguyên tiếng Việt có dấu. Tự sửa lỗi OCR hiển nhiên (chữ dính, thiếu "
    "dấu) nhưng KHÔNG đổi số liệu, KHÔNG đổi đơn vị.\n\n"
    "ĐỊNH DẠNG\n"
    "6. Số câu: **Câu 37.** — Phương án: **A.** mỗi phương án 1 dòng, đúng thứ tự "
    "A→B→C→D — Ý đúng/sai: **a)** mỗi ý 1 dòng.\n"
    "7. Tiêu đề bài/mục → heading Markdown (##, ###).\n"
    "8. BẢNG SỐ LIỆU → viết thành BẢNG MARKDOWN chuẩn (| ... | và |---|). "
    "KHÔNG biến bảng thành hình.\n\n"
    "CÔNG THỨC (ngắn gọn, bắt buộc)\n"
    "9. Công thức → $...$; phân số → $\\dfrac{a}{b}$ hoặc giữ dấu '/', TUYỆT ĐỐI "
    "KHÔNG tách tử-mẫu thành 'π 4'.\n"
    "10. Số mũ của 10: $1,5.10^{6}$, $3.10^{-6}$ — dấu CHẤM nhân, thập phân dấu "
    "PHẨY trần. KHÔNG viết liền '106', '10-6'.\n"
    "11. Đơn vị trong công thức bọc \\text{}, mũ NGOÀI: $10\\text{m/s}^2$, "
    "$25^\\circ\\text{C}$, $45^\\circ$, $V = 3,3.10^{-3}\\text{m}^3$, lít viết "
    "$2\\text{l}$. Số + đơn vị ĐƠN GIẢN (20N, 1,5J) để text thường, KHÔNG bọc $.\n"
    "12. Dấu $ LUÔN ĐỦ CẶP; cả biểu thức so sánh nằm trong MỘT cặp: $T_1 > T_0$.\n\n"
    "HÌNH VẼ\n"
    "13. Mỗi hình vẽ / đồ thị / sơ đồ mạch → đặt {{FIGURE_1}}, {{FIGURE_2}}... đúng "
    "chỗ trong markdown và khai trong \"figures\".\n"
    "14. bbox: [left%, top%, right%, bottom%] theo % chiều rộng/cao trang (0.0–100.0).\n"
    "15. anchor_text = NGUYÊN VĂN 6–12 từ đầu của dòng chữ ngay TRÊN (hoặc bên cạnh) "
    "hình đó — dùng để đặt hình vào đúng chỗ. BẮT BUỘC có.\n"
    "16. KHÔNG khai: bảng số liệu, công thức rời, logo, số trang, watermark, ô kẻ "
    "trống để điền đáp án (thay bằng dòng '**Điền đáp án:**').\n"
    "17. Trang không có hình → figures: []"
)

# Nhắc khi chỉ gửi MỘT PHẦN của trang (đọc lại theo nửa trang khi bị cắt giữa đường)
_SCAN_STRIP_HINT = (
    "LƯU Ý: ảnh này chỉ là MỘT PHẦN của trang. Câu/ý nào bị cắt cụt ở mép trên "
    "hoặc mép dưới ảnh thì BỎ QUA, phần kia của trang đã có. Chỉ ghi những gì "
    "nhìn thấy trọn vẹn.\n\n"
)


def _render_page_png(doc, idx: int, zoom: int = 2):
    """Render 1 trang PDF → (png_bytes, width, height). Gọi ở luồng chính
    (fitz KHÔNG an toàn đa luồng) rồi truyền bytes cho các luồng Gemini."""
    import fitz
    page = doc[idx]
    pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom))
    return pix.tobytes("png"), pix.width, pix.height


class VisionBlocked(RuntimeError):
    """Gemini trả RỖNG vì bộ lọc Google (finish_reason 'recitation': trang đề trùng nguyên
    văn tài liệu phổ biến trên mạng, hoặc safety). Không phải lỗi mạng — retry/đổi model
    Gemini đều bị chặn y hệt → caller phải lùi sang ODL (lớp chữ) cho trang đó."""


class VisionTruncated(RuntimeError):
    """Model đọc trang chưa xong đã hết hạn mức output (finish_reason 'length' /
    MAX_TOKENS). Mang theo phần text đã nhận ở .partial để caller đọc lại trang
    theo từng nửa. Trước đây trường hợp này bị coi là kết quả hợp lệ → mất nửa
    cuối trang mà không ai biết."""

    def __init__(self, partial: str = ""):
        super().__init__("Model bị cắt giữa đường (hết hạn mức output)")
        self.partial = partial or ""


class PageLost(RuntimeError):
    """ODL không lấy được chữ nào cho dải trang này (trang scan: thứ duy nhất ODL
    xuất ra là ảnh nguyên trang, mà ảnh đó bị _remove_page_images xoá). Phải raise
    để caller biết mà lùi sang Vision — trước đây hàm trả chuỗi rỗng im lặng."""


_BLOCK_REASONS = ('recitation', 'content_filter', 'safety', 'blocklist', 'prohibited_content')

# Hạn mức output cho Vision. Model ag/ là loại "thinking" (phần suy nghĩ cũng ăn
# hạn mức) nên phải để rộng; trang scan dày chữ trước đây bị cắt âm thầm.
_VISION_MAX_TOKENS = 16000              # nhánh 9Router (bằng core/importer.py)
_VISION_MAX_OUTPUT_GEMINI = 32768       # nhánh Gemini API trực tiếp

# Đường dự phòng bằng key Gemini trực tiếp của thầy, thử lần lượt.
# Đã kiểm 01/10/2026: key truy cập được cả 2 model này.
_GEMINI_DIRECT_MODELS = ("gemini-3.5-flash", "gemini-2.5-flash")


def _resp_text(resp) -> str:
    """Lấy text từ response Gemini. resp.text ném lỗi khi candidate không có part
    (bị chặn, hoặc cắt ngay từ token đầu) → phải tự gom part."""
    try:
        return resp.text or ""
    except Exception:
        pass
    try:
        out = []
        for cand in (getattr(resp, "candidates", None) or []):
            content = getattr(cand, "content", None)
            for part in (getattr(content, "parts", None) or []):
                t = getattr(part, "text", "")
                if t:
                    out.append(t)
        return "".join(out)
    except Exception:
        return ""


def _vision_call(model: str, img_bytes: bytes, prompt: str,
                 niner_url: str = "", niner_key: str = "",
                 gemini_api_key: str = "", max_retries: int = 2) -> str:
    """
    Gửi 1 ảnh trang + prompt cho model Vision, trả về text. Tự lùi (backoff) khi 429.
      - model có '/' (vd ag/gemini-3.7-flash-low) → gọi 9Router (OpenAI-compatible, ảnh base64).
      - model không '/' (vd gemini-2.5-flash) → gọi Gemini API trực tiếp.
    Fallback: 9Router lỗi + có Gemini key → thử gemini-2.5-flash trực tiếp.
    Trả rỗng / finish_reason recitation → VisionBlocked ngay (trước đây trả "" im lặng
    → trang mất trắng trong Word; và fallback Gemini trực tiếp cũng bị chặn y hệt).
    """
    import time as _t
    import re as _re
    import base64 as _b64

    is_router = "/" in model
    last = None
    for attempt in range(max_retries + 1):
        try:
            if is_router:
                from openai import OpenAI
                client = OpenAI(api_key=niner_key or "9router",
                                base_url=niner_url or "http://127.0.0.1:20128/v1",
                                timeout=120, max_retries=0)
                b64 = _b64.b64encode(img_bytes).decode()
                resp = client.chat.completions.create(
                    model=model,
                    temperature=0.1,
                    max_tokens=_VISION_MAX_TOKENS,
                    messages=[{"role": "user", "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image_url",
                         "image_url": {"url": f"data:image/png;base64,{b64}"}}]}])
                ch = resp.choices[0]
                text = (ch.message.content or "").strip()
                reason = (getattr(ch, "finish_reason", "") or "").lower()
                if reason in _BLOCK_REASONS or not text:
                    raise VisionBlocked(f"Gemini bị Google chặn ({reason or 'rỗng'})")
                if reason == "length":
                    # Bị cắt giữa đường: trước đây vẫn coi là hợp lệ → mất nửa
                    # cuối trang mà không ai biết. Mang theo phần đã đọc để
                    # người gọi đọc lại theo nửa trang.
                    raise VisionTruncated(text)
                return text
            else:
                import google.generativeai as genai
                genai.configure(api_key=gemini_api_key)
                gm = genai.GenerativeModel(model)
                resp = gm.generate_content(
                    [{"mime_type": "image/png", "data": img_bytes}, prompt],
                    generation_config={"response_mime_type": "application/json",
                                       "max_output_tokens": _VISION_MAX_OUTPUT_GEMINI,
                                       "temperature": 0.1})
                cand = resp.candidates[0] if getattr(resp, "candidates", None) else None
                fr = getattr(cand, "finish_reason", None)
                fr_name = str(getattr(fr, "name", fr) or "").upper()
                if fr_name in ("RECITATION", "SAFETY", "BLOCKLIST", "PROHIBITED_CONTENT") or fr == 4:
                    raise VisionBlocked(f"Gemini bị Google chặn ({fr_name})")
                text = (_resp_text(resp) or "").strip()
                if not text:
                    # Trước đây return "" im lặng ở đây → trang mất trắng
                    raise VisionBlocked(f"Gemini trả rỗng ({fr_name or 'không rõ'})")
                if fr_name == "MAX_TOKENS":
                    raise VisionTruncated(text)
                return text
        except VisionBlocked:
            raise
        except Exception as e:
            last = e
            msg = str(e)
            rate = ('429' in msg or 'RESOURCE_EXHAUSTED' in msg
                    or 'quota' in msg.lower() or 'exhausted' in msg.lower())
            if rate and attempt < max_retries:
                m = _re.search(r'ret[rR]y.{0,12}?([\d.]+)\s*s', msg)
                wait = float(m.group(1)) if m else (5.0 * (attempt + 1))
                _t.sleep(min(wait + 0.5, 30))
                continue
            break
    # Fallback: 9Router hỏng nhưng có Gemini key → dùng key trực tiếp của thầy.
    # Thử lần lượt vài model (key truy cập được cả gemini-3.5-flash, mạnh hơn
    # gemini-2.5-flash vốn hard-code ở đây), và kiểm finish_reason đàng hoàng
    # thay vì nhận "" âm thầm.
    if is_router and gemini_api_key:
        for fb_model in _GEMINI_DIRECT_MODELS:
            try:
                return _vision_call(fb_model, img_bytes, prompt,
                                    gemini_api_key=gemini_api_key,
                                    max_retries=1)
            except VisionTruncated:
                raise
            except Exception as e:
                last = e
                continue
    raise last if last else RuntimeError("Vision call lỗi không rõ")


def _gemini_one_page(img_bytes: bytes, page_w: int, page_h: int, idx: int,
                     fig_dir: Path, model: str = "ag/gemini-3.7-flash-low",
                     niner_url: str = "", niner_key: str = "",
                     gemini_api_key: str = "", scan: bool = False,
                     prompt_extra: str = "") -> str:
    """Xử lý 1 trang qua Vision (9Router hoặc Gemini trực tiếp) → markdown
    (đã cắt figures/bảng theo bbox). THUẦN (không fitz) → an toàn đa luồng.

    scan=True → dùng _SCAN_PROMPT (bản scan: đọc đủ, giữ số in trên trang, bảng
    thành Markdown) thay vì _GEMINI_PROMPT (trang digital phức tạp).
    prompt_extra → thêm vào đầu prompt (dùng khi chỉ gửi một nửa trang).
    """
    import json as _json
    import re as _re
    import io as _io
    try:
        from PIL import Image as PILImage
        _pil_ok = True
    except ImportError:
        _pil_ok = False

    prompt = (prompt_extra or "") + (_SCAN_PROMPT if scan else _GEMINI_PROMPT)
    raw = _vision_call(model, img_bytes, prompt,
                       niner_url=niner_url, niner_key=niner_key,
                       gemini_api_key=gemini_api_key)

    m = _re.search(r'```(?:json)?\s*([\s\S]+?)```', raw)
    if m:
        raw = m.group(1).strip()

    md_text, figures = "", []
    # LaTeX trong response là escape hỏng với JSON theo 2 kiểu:
    #   (a) \circ \delta \geq... — escape KHÔNG hợp lệ → json.loads FAIL
    #       → trước đây đổ NGUYÊN JSON thô vào tài liệu (bug 04/07).
    #   (b) \text \times \frac \nu \rho \beta... — trùng escape HỢP LỆ (\t \f \n
    #       \r \b) → parse "thành công" nhưng công thức bị thay bằng tab/formfeed.
    # Sửa: nhân đôi backslash của (a) + whitelist lệnh LaTeX của (b) rồi parse.
    _LATEX_B = (r'(?:text|times|tanh|tan|tau|theta|triangle|to|frac|forall|'
                r'nu|nabla|neq|ne|notin|not|rho|rightarrow|right|'
                r'beta|bar|boxed|binom|upsilon|underbrace|underline)')

    def _repair(s):
        s = _re.sub(r'\\(?!["\\/bfnrtu])', r'\\\\', s)          # (a)
        s = _re.sub(r'\\(?=' + _LATEX_B + r'\b)', r'\\\\', s)   # (b)
        return s

    data = None
    for candidate in (raw, _repair(raw)):
        try:
            data = _json.loads(candidate)
            break
        except Exception:
            data = None
    if data is not None:
        md_text = data.get("markdown", "")
        figures = data.get("figures", [])
    elif raw.lstrip().startswith('{') and '"markdown"' in raw:
        # Vẫn không parse được nhưng RÕ RÀNG là JSON → móc trường markdown thủ công,
        # tuyệt đối không để cấu trúc JSON thô lọt vào tài liệu.
        m2 = _re.search(r'"markdown"\s*:\s*"([\s\S]*?)"\s*,\s*"figures"', raw)
        if m2:
            md_text = (m2.group(1)
                       .replace('\\n', '\n').replace('\\"', '"').replace('\\\\', '\\'))
        else:
            md_text = f"> [Trang {idx+1}: lỗi đọc kết quả AI — thử lại]"
    else:
        md_text = raw   # model trả thẳng markdown (không bọc JSON)

    # Lưới an toàn: nếu raw parse "thành công" NGAY lần đầu mà chứa \text \frac...
    # thì công thức đã bị nuốt thành ký tự điều khiển (tab/formfeed/CR/backspace)
    # → khôi phục lại lệnh LaTeX. (\nu, \ne bỏ qua — newline+chữ quá dễ trùng text thật.)
    _CTRL2ESC = {'\t': 't', '\x0c': 'f', '\r': 'r', '\x08': 'b', '\n': 'n'}
    _CTRL_SUF = {'\t': ('ext', 'imes', 'anh', 'an', 'au', 'heta', 'riangle'),
                 '\x0c': ('rac', 'orall'),
                 '\r': ('ho', 'ightarrow', 'ight'),
                 '\x08': ('eta', 'ar', 'oxed', 'inom'),
                 '\n': ('abla', 'otin')}
    for _ctrl, _sufs in _CTRL_SUF.items():
        for _suf in _sufs:
            md_text = md_text.replace(_ctrl + _suf, '\\' + _CTRL2ESC[_ctrl] + _suf)

    placed_boxes = []   # bbox (%) các hình ĐÃ chèn — để pdf_word_figures không cắt trùng
    for fig in figures:
        local_id = fig.get("id")
        try:
            bbox = fig.get("bbox", [])
            if len(bbox) == 4 and _pil_ok:
                x1 = max(0, int(bbox[0] / 100 * page_w))
                y1 = max(0, int(bbox[1] / 100 * page_h))
                x2 = min(page_w, int(bbox[2] / 100 * page_w))
                y2 = min(page_h, int(bbox[3] / 100 * page_h))
                placeholder = f"{{{{FIGURE_{local_id}}}}}"
                if (x2 - x1) > 30 and (y2 - y1) > 30 and placeholder in md_text:
                    img = PILImage.open(_io.BytesIO(img_bytes))
                    crop = img.crop((x1, y1, x2, y2))
                    fig_name = f"p{idx+1}_fig{local_id}.png"
                    # Trang render zoom 2 → 144 DPI: ghi vào PNG để Pandoc dựng đúng
                    # cỡ gốc (không ghi → Pandoc coi 96 DPI → hình to gấp 1,5 lần)
                    crop.save(fig_dir / fig_name, "PNG", dpi=(144, 144))
                    caption = fig.get("caption", f"Hình {local_id}")
                    md_text = md_text.replace(
                        placeholder,
                        f"\n\n![{caption}](figures/{fig_name})\n\n")
                    placed_boxes.append({"id": local_id, "bbox": [float(v) for v in bbox]})
        except Exception:
            pass
    try:
        (fig_dir / f"p{idx+1}_gemini_boxes.json").write_text(
            _json.dumps(placed_boxes), encoding="utf-8")
    except Exception:
        pass

    # Placeholder còn sót (bbox lệch / quá nhỏ / thiếu PIL): KHÔNG xoá trắng như
    # trước — để lại chú thích để thầy biết chỗ đó vốn có hình, nhất là khi
    # Gemini đã khai cả một BẢNG SỐ LIỆU thành hình.
    _caps = {}
    for fig in (figures or []):
        try:
            _caps[int(fig.get("id", 0))] = (fig.get("caption") or "").strip()
        except Exception:
            continue

    def _leftover(m):
        cap = _caps.get(int(m.group(1)), "")
        return f"\n\n*[Hình: {cap}]*\n\n" if cap else "\n\n*[Hình trong trang gốc]*\n\n"

    md_text = _re.sub(r'\{\{FIGURE_(\d+)\}\}', _leftover, md_text)
    return md_text.strip()


def _png_halves(img_bytes: bytes, overlap: float = 0.15) -> list:
    """Chia ảnh trang thành nửa trên / nửa dưới CÓ CHỒNG LẤN (mặc định 15%).

    Dùng khi model đọc chưa xong trang đã hết hạn mức output: gửi lại từng nửa
    thì mỗi lần ít chữ hơn nên đọc trọn. Chồng lấn để câu nằm đúng chỗ cắt không
    bị mất ở cả hai nửa (cùng ý với _page_strips của core/importer.py, nhưng để
    riêng ở đây — import chéo sẽ kéo cả state của luồng nhập đề).
    """
    try:
        import io as _io
        from PIL import Image as PILImage
    except ImportError:
        return []
    try:
        with PILImage.open(_io.BytesIO(img_bytes)) as im:
            im.load()
            w, h = im.size
            cut_lo = int(h * (0.5 + overlap))
            cut_hi = int(h * (0.5 - overlap))
            out = []
            for box in ((0, 0, w, cut_lo), (0, cut_hi, w, h)):
                buf = _io.BytesIO()
                im.crop(box).save(buf, "PNG")
                out.append(buf.getvalue())
            return out
    except Exception:
        return []


def _looks_truncated(md: str) -> bool:
    """Phỏng đoán markdown của 1 trang bị cắt giữa đường (model hết hạn mức mà
    API không báo). Chỉ dùng để QUYẾT ĐỊNH ĐỌC LẠI — đọc lại thừa thì chỉ tốn
    thêm thời gian, còn bỏ sót thì mất chữ."""
    import re as _re
    s = (md or "").strip()
    if not s:
        return False
    if s.count("$") % 2 == 1:            # công thức hở một dấu $ → cắt giữa công thức
        return True
    last = s.splitlines()[-1].strip()
    if not last:
        return False
    # Dòng cuối là một phương án / ý / tiêu đề hoàn chỉnh → coi như bình thường
    if _re.match(r'^(\*\*)?([A-Da-d][.)]|Câu|Bài|#|\||\*|-|\d+[.)])', last):
        return False
    if last[-1] in '.?!:)]"”…|':
        return False
    return len(last) > 40                # câu dài mà không có dấu kết thúc


def _convert_via_gemini(pdf_path: Path, work_dir: Path, api_key: str,
                        model: str = "ag/gemini-3.7-flash-low",
                        page_range: str | None = None,
                        niner_url: str = "", niner_key: str = "",
                        progress_cb=None) -> tuple:
    """
    Dùng Vision đọc PDF → Markdown+LaTeX, CHẠY SONG SONG các trang
    (ThreadPoolExecutor) + cắt hình/bảng theo bbox. Model mặc định Gemini 3 Flash
    qua 9Router (mạnh hơn 2.5); fallback Gemini API trực tiếp.
    Trả về (md_path, stats) — stats có failed_pages/truncated_pages/scan_pages.
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    def _emit(stage, msg):
        if progress_cb:
            try:
                progress_cb(stage, msg)
            except Exception:
                pass

    try:
        import fitz
    except ImportError:
        raise RuntimeError("Cần PyMuPDF (fitz) để dùng Vision.")

    work_dir.mkdir(parents=True, exist_ok=True)
    fig_dir = work_dir / "figures"
    fig_dir.mkdir(exist_ok=True)

    doc = fitz.open(str(pdf_path))
    n = doc.page_count
    page_indices = _parse_page_range(page_range, n) if page_range else list(range(n))
    detect = detect_text_layer(str(pdf_path), page_indices=page_indices)
    scan_set = set(detect.get("scan_pages") or ())
    # Render PNG ở luồng chính (fitz không an toàn đa luồng).
    # Trang scan zoom 3 (216 DPI) — đo thực tế +38% chữ đọc được so với zoom 2.
    png_map = {idx: _render_page_png(doc, idx, zoom=(3 if idx in scan_set else 2))
               for idx in page_indices}
    doc.close()

    results, done = {}, [0]
    failed_pages, truncated_pages = [], []

    def _work(idx):
        b, w, h = png_map[idx]
        is_scan = idx in scan_set
        md, why = "", ""
        try:
            md = _gemini_one_page(b, w, h, idx, fig_dir, model=model,
                                  niner_url=niner_url, niner_key=niner_key,
                                  gemini_api_key=api_key, scan=is_scan)
            if _looks_truncated(md):
                better = _read_halves_gemini(idx, b, w, h, is_scan)
                if _meaningful_text_len(better) > _meaningful_text_len(md):
                    md = better
        except VisionTruncated as e:
            truncated_pages.append(idx + 1)
            md = _read_halves_gemini(idx, b, w, h, is_scan)
            if _meaningful_text_len(md) < _meaningful_text_len(e.partial):
                md = e.partial
            why = "hết hạn mức output"
        except Exception as e:
            why = str(e)[:120]
            if api_key:
                for fb in _GEMINI_DIRECT_MODELS:
                    try:
                        md = _gemini_one_page(b, w, h, idx, fig_dir, model=fb,
                                              gemini_api_key=api_key, scan=is_scan)
                        why = ""
                        break
                    except Exception as e2:
                        why = str(e2)[:120]
                        continue
        if _meaningful_text_len(md) <= 40:
            md = _page_failure_marker(idx, why or "không đọc được chữ",
                                      fig_dir, pdf_path, md)
            failed_pages.append(idx + 1)
        done[0] += 1
        _emit("gemini", f"Vision đọc trang {done[0]}/{len(page_indices)}…")
        return idx, md

    def _read_halves_gemini(idx, b, w, h, is_scan):
        halves = _png_halves(b)
        parts = []
        for hb in halves:
            try:
                parts.append(_gemini_one_page(
                    hb, w, h // 2, idx, fig_dir, model=model,
                    niner_url=niner_url, niner_key=niner_key,
                    gemini_api_key=api_key, scan=is_scan,
                    prompt_extra=_SCAN_STRIP_HINT))
            except Exception:
                continue
        return "\n\n".join(p for p in parts if p).strip()

    with ThreadPoolExecutor(max_workers=4) as ex:
        for fut in as_completed([ex.submit(_work, i) for i in page_indices]):
            idx, md = fut.result()
            results[idx] = md

    pages_md = [f"<!-- p2w:page={i+1} -->\n{results.get(i, '')}" for i in page_indices]
    md_path = work_dir / (pdf_path.stem + ".md")
    md_path.write_text("\n\n---\n\n".join(pages_md), encoding="utf-8")
    stats = {
        "failed_pages": sorted(set(failed_pages)),
        "truncated_pages": sorted(set(truncated_pages)),
        "scan_pages": sorted(p + 1 for p in scan_set),
        "ocr_used": True,
    }
    return md_path, stats


# ── PDF → Markdown SẠCH cho NotebookLM (OCR Gemini, không cắt hình) ──

_NOTEBOOKLM_PROMPT = (
    "Đây là MỘT TRANG tài liệu Vật lý THPT Việt Nam (có thể là bản scan/chụp).\n"
    "Hãy ĐỌC TOÀN BỘ nội dung trang và xuất ra MARKDOWN SẠCH để nạp vào NotebookLM.\n\n"
    "QUY TẮC:\n"
    "1. Đọc đúng thứ tự đọc. Nếu trang chia 2 cột: đọc HẾT cột TRÁI từ trên xuống rồi mới sang cột PHẢI. KHÔNG đọc ngang.\n"
    "2. Công thức Toán/Vật lý → LaTeX: dùng `$...$` cho công thức trong dòng, `$$...$$` cho công thức tách dòng. "
    "Phân số dùng `\\dfrac{a}{b}`; số mũ của 10 viết `$10^{6}$` (KHÔNG viết liền '106'); vectơ `$\\vec{F}$`.\n"
    "3. HÌNH VẼ / ĐỒ THỊ / SƠ ĐỒ MẠCH: KHÔNG chèn ảnh, KHÔNG để placeholder. Thay vào đó MÔ TẢ bằng chữ "
    "trong một blockquote, ví dụ:\n"
    "   > **[Hình: đồ thị li độ x theo thời gian t]** Đường hình sin, biên độ 4 cm, chu kỳ 0,2 s, xuất phát từ x = 0 đi lên.\n"
    "   Mô tả đủ chi tiết để người đọc hiểu được nội dung hình mà không cần nhìn ảnh (trục, đường, giá trị đặc biệt, chiều...).\n"
    "4. BẢNG SỐ LIỆU → viết thành BẢNG MARKDOWN chuẩn (`| cột | cột |` và dòng `|---|---|`).\n"
    "5. Tiêu đề lớn / tên phần → heading Markdown (`#`, `##`, `###`). Số câu giữ dạng '**Câu 1.**', đáp án A/B/C/D mỗi đáp án một dòng.\n"
    "6. Giữ NGUYÊN tiếng Việt có dấu, đúng chính tả. Tự sửa các lỗi OCR hiển nhiên (chữ dính, thiếu dấu).\n"
    "7. BỎ phần rác: số trang, header/footer lặp, watermark, dòng kẻ trang trí.\n"
    "8. CHỈ trả về nội dung Markdown của trang. KHÔNG bọc trong ```markdown, KHÔNG thêm lời dẫn/giải thích nào khác."
)


def _strip_md_fence(text: str) -> str:
    """Gỡ ```markdown ... ``` nếu Gemini lỡ bọc cả output."""
    import re as _re
    if not text:
        return ""
    t = text.strip()
    m = _re.match(r'^```(?:markdown|md)?\s*\n([\s\S]*?)\n?```$', t)
    return m.group(1).strip() if m else t


def convert_pdf_to_markdown(pdf_path: str, out_md: str | None = None,
                            page_range: str | None = None, progress_cb=None,
                            gemini_api_key: str = "", niner_url: str = "",
                            niner_key: str = "",
                            model: str = "ag/gemini-3.7-flash-low") -> dict:
    """
    OCR PDF (kể cả bản scan) bằng Gemini Vision → 1 file Markdown SẠCH cho NotebookLM.
    Mỗi trang 1 request Vision (song song), công thức $...$, hình mô tả bằng chữ,
    bảng → bảng Markdown. KHÔNG cắt ảnh, KHÔNG Pandoc.

    Trả về {md_path, md_name, seconds, pages, failed_pages}.
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    def _emit(stage, msg):
        if progress_cb:
            try:
                progress_cb(stage, msg)
            except Exception:
                pass

    try:
        import fitz
    except ImportError:
        raise RuntimeError("Cần PyMuPDF (fitz) để OCR PDF.")

    pdf_path = Path(pdf_path)
    if not pdf_path.exists():
        raise FileNotFoundError(str(pdf_path))

    out_path = Path(out_md) if out_md else (EXPORT_DIR / (_safe_stem(pdf_path.name) + ".md"))
    out_path.parent.mkdir(parents=True, exist_ok=True)

    t0 = time.time()
    doc = fitz.open(str(pdf_path))
    n = doc.page_count
    page_indices = _parse_page_range(page_range, n) if page_range else list(range(n))
    total = len(page_indices)
    _emit("ocr", f"Bắt đầu OCR {total} trang bằng Gemini Vision…")
    # Render PNG ở luồng chính (fitz KHÔNG an toàn đa luồng)
    png_map = {idx: _render_page_png(doc, idx) for idx in page_indices}
    doc.close()

    results, failed, done = {}, [], [0]

    def _work(idx):
        b, _w, _h = png_map[idx]
        try:
            # max_retries cao hơn cho file lớn — chịu 429 tốt hơn
            raw = _vision_call(model, b, _NOTEBOOKLM_PROMPT,
                               niner_url=niner_url, niner_key=niner_key,
                               gemini_api_key=gemini_api_key, max_retries=4)
            md = _strip_md_fence(raw)
            if not md:
                md = f"> [Trang {idx+1}: không đọc được nội dung — thử lại]"
                failed.append(idx + 1)
        except Exception as e:
            md = f"> [Trang {idx+1}: lỗi OCR ({str(e)[:80]}) — thử lại]"
            failed.append(idx + 1)
        done[0] += 1
        _emit("ocr", f"Đã OCR {done[0]}/{total} trang…")
        return idx, md

    # 3 luồng (êm rate limit hơn 4 cho file lớn)
    with ThreadPoolExecutor(max_workers=3) as ex:
        for fut in as_completed([ex.submit(_work, i) for i in page_indices]):
            idx, md = fut.result()
            results[idx] = md

    pages_md = [results.get(i, "") for i in page_indices]
    full = "\n\n".join(p for p in pages_md if p.strip())

    # Chuẩn hoá $...$ (không đụng _apply_bold_formatting — đó là định dạng đề thi)
    try:
        from core.latex_normalize import normalize_latex
        full = normalize_latex(full)
    except Exception:
        pass

    out_path.write_text(full, encoding="utf-8")
    seconds = round(time.time() - t0, 1)
    if failed:
        _emit("done", f"Xong sau {seconds}s — {len(failed)} trang lỗi: {failed}")
    else:
        _emit("done", f"Xong sau {seconds}s")
    return {
        "md_path": str(out_path),
        "md_name": out_path.name,
        "seconds": seconds,
        "pages": total,
        "failed_pages": failed,
    }


# Ký hiệu toán/lý báo hiệu trang nhiều công thức
_MATH_DENSITY_CHARS = ('√∫∑∂∇±×÷≥≤≠≈→⃗'
                       'αβγδεζηθλμνξπρστφχψωΓΔΘΛΞΠΣΦΨΩ'
                       '₀₁₂₃₄₅₆₇₈₉⁰¹²³⁴⁵⁶⁷⁸⁹^_')


_DRAW_THRESHOLD = 40     # số nét vẽ vector → sơ đồ/đồ thị (ODL không trích được)
_MATH_THRESHOLD = 8      # số ký hiệu toán/trang → công thức (ODL hay rớt "/" phân số)
_FRAC_THRESHOLD = 2      # số phân số (π/4, a/b, số/số) → ODL mất dấu "/" → Gemini


def _classify_pages(pdf_path: Path, page_indices: list,
                    scan_set: set | None = None) -> set:
    """
    Trả về set các trang PHỨC TẠP (cần Gemini): là bản scan, có bảng, ảnh/sơ đồ
    đáng kể, nhiều đồ hoạ vector, hoặc mật độ công thức cao. Còn lại = trang chữ (ODL).

    scan_set: các trang đã được core/pdf_scan xác định là bản scan — luôn được
    THÊM vào kết quả. Việc nhận diện scan nằm hẳn ở đó (chống watermark), không
    còn làm bằng ngưỡng "ít chữ + có ảnh" ngay trong hàm này nữa.
    """
    try:
        import fitz
    except Exception:
        return set(scan_set or ())
    DRAW_THRESHOLD = _DRAW_THRESHOLD
    MATH_THRESHOLD = _MATH_THRESHOLD
    FRAC_THRESHOLD = _FRAC_THRESHOLD
    complex_set = set(scan_set or ())
    try:
        doc = fitz.open(str(pdf_path))
    except Exception:
        return complex_set
    for idx in page_indices:
        if idx in complex_set:      # đã là trang scan → khỏi xét tiếp
            continue
        try:
            page = doc[idx]
        except Exception:
            continue
        cx = False
        txt = page.get_text("text") or ""
        # 1. Bảng → ODL hay làm vỡ → Gemini
        try:
            tabs = page.find_tables()
            if len(getattr(tabs, "tables", []) or []) > 0:
                cx = True
        except Exception:
            pass
        # 2. Nhiều đồ hoạ vector (sơ đồ mạch, đồ thị vẽ tay) → ODL không trích được
        if not cx:
            try:
                if len(page.get_drawings()) > DRAW_THRESHOLD:
                    cx = True
            except Exception:
                pass
        # 3. Mật độ công thức cao → Gemini. ĐẾM CẢ ký tự Toán in nghiêng Unicode
        #    (𝑥 𝜔 𝜑 𝜋 — block U+1D400+) vì nhiều PDF dùng block này cho công thức
        #    (fitz đọc Greek thường = 0 nhưng thực ra đầy 𝜔𝜑). + nhận diện hàm cos/sin.
        if not cx:
            import re as _rm
            n_mathit = sum(1 for c in txt if 0x1D400 <= ord(c) <= 0x1D7FF)
            n_sym = sum(txt.count(c) for c in _MATH_DENSITY_CHARS)
            has_func = bool(_rm.search(r'\b(cos|sin|tan|cot|log|ln|sqrt)\b', txt)) or '√' in txt
            if (n_sym + n_mathit) >= MATH_THRESHOLD or (has_func and n_mathit >= 2):
                cx = True
        # 4. Có phân số THẬT (π/4, 3/2, λ/2) → ODL hay mất dấu "/" → Gemini đọc lại.
        #    Chỉ bắt số/Hy-Lạp ở CẢ 2 vế → KHÔNG nhầm đơn vị "m/s", "km/h" (chữ/chữ).
        if not cx:
            import re as _re2
            n_frac = len(_re2.findall(r'[\dπλμαβγθωφ]\s*/\s*[\dπλμαβγθωφ]', txt))
            if n_frac >= FRAC_THRESHOLD:
                cx = True
        # 5. Ký hiệu khoa học 10^n bị làm phẳng (1,5.106, X.102, 3.10-6) → Gemini
        #    đọc lại số mũ từ ảnh (PDF text layer hay mất superscript)
        if not cx:
            import re as _re3
            if _re3.search(r'[.,xX×·]\s*10\s*-?\d', txt):
                cx = True
        if cx:
            complex_set.add(idx)
    doc.close()
    return complex_set


def _odl_text_only(pdf_path: Path, work_dir: Path, page_range: str,
                   progress_cb=None) -> str:
    """Chạy ODL chỉ lấy text (bỏ trích bảng/hình) cho dải trang đơn giản.
    Trả về markdown string. KHÔNG dùng fitz → an toàn gọi song song với Gemini."""
    import opendataloader_pdf
    work_dir.mkdir(parents=True, exist_ok=True)
    jdir = _find_java_dir()
    if jdir and jdir not in os.environ.get("PATH", ""):
        os.environ["PATH"] = os.environ.get("PATH", "") + os.pathsep + jdir
        os.environ["JAVA_HOME"] = str(Path(jdir).parent)
    kw = dict(input_path=[str(pdf_path)], output_dir=str(work_dir),
              format="markdown", hybrid="docling-fast", hybrid_mode="auto",
              hybrid_fallback=True, quiet=True,
              include_header_footer=True)   # xem ghi chú ở _convert_via_odl
    if page_range:
        kw["pages"] = page_range
    _run_odl(kw)
    md = _strip_rule_lines(_merge_md_files(work_dir))
    md = _remove_page_images(md)        # chỉ PIL, an toàn
    md = _strip_answer_bullets(md)
    md = _apply_bold_formatting(md)
    txt = md.read_text(encoding="utf-8", errors="replace")
    # Với trang scan, thứ duy nhất ODL xuất ra là ảnh nguyên trang — mà
    # _remove_page_images vừa xoá nó. Trước đây hàm trả "" và caller coi như
    # thành công → trang mất trắng, không marker, không log, không báo UI.
    if _meaningful_text_len(txt) <= 50:
        raise PageLost(f"ODL không lấy được chữ nào ở trang {page_range or 'tất cả'}")
    return txt


def _page_failure_marker(idx: int, reason: str, fig_dir: Path,
                         pdf_path: Path, partial: str = "") -> str:
    """
    Lưới an toàn cuối cùng: trang không đọc được chữ thì NHÚNG ẢNH TRANG GỐC vào
    Word kèm cảnh báo, thay vì để trang trắng. Thầy vẫn đọc được nội dung và biết
    đích danh trang nào cần chuyển lại.
    """
    note = (f"\n\n> ⚠️ **[Trang {idx+1}]** Không đọc được chữ ({reason}). "
            f"Ảnh gốc của trang ở dưới — hãy chuyển lại riêng trang này.\n\n")
    try:
        import fitz
        name = f"p{idx+1}_fullpage.png"
        doc = fitz.open(str(pdf_path))
        try:
            pix = doc[idx].get_pixmap(matrix=fitz.Matrix(3, 3))
            pix.set_dpi(216, 216)
            pix.save(str(fig_dir / name))
        finally:
            doc.close()
        note += f"![Trang {idx+1}](figures/{name})\n\n"
    except Exception:
        note += f"*(Không render được ảnh trang {idx+1}.)*\n\n"
    # Giữ phần chữ đã đọc được (nếu có) — thà ít còn hơn không
    if (partial or "").strip():
        note += partial.strip() + "\n"
    return note


def _convert_via_hybrid(pdf_path: Path, work_dir: Path,
                        gemini_api_key: str = "", page_range: str | None = None,
                        niner_url: str = "", niner_key: str = "",
                        gemini_model: str = "ag/gemini-3.7-flash-low",
                        progress_cb=None) -> tuple:
    """
    LAI theo trang: ODL cho trang chữ (gom cụm liên tiếp → ít lần gọi), Gemini
    Vision cho trang phức tạp (chạy SONG SONG 4 luồng). Ghép theo đúng thứ tự.
    Model Vision mặc định Gemini 3 Flash qua 9Router (mạnh hơn 2.5).
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed
    import threading

    def _emit(s, m):
        if progress_cb:
            try:
                progress_cb(s, m)
            except Exception:
                pass

    try:
        import fitz
    except ImportError:
        raise RuntimeError("Cần PyMuPDF (fitz).")

    work_dir.mkdir(parents=True, exist_ok=True)
    fig_dir = work_dir / "figures"
    fig_dir.mkdir(exist_ok=True)

    doc = fitz.open(str(pdf_path))
    n = doc.page_count
    page_indices = _parse_page_range(page_range, n) if page_range else list(range(n))

    # Nhận diện trang scan TRƯỚC (chống watermark) rồi mới phân loại phức tạp.
    # Trang scan BẮT BUỘC đi Gemini: ODL không có gì để lấy ở trang không có chữ.
    detect = detect_text_layer(str(pdf_path), page_indices=page_indices)
    scan_set = set(detect.get("scan_pages") or ())

    # Vision chạy được qua cả 9Router (_vision_call route theo prefix model) —
    # chỉ gate theo gemini_api_key sẽ tắt nhầm Vision khi thầy dùng 9Router-only.
    complex_set = (_classify_pages(pdf_path, page_indices, scan_set=scan_set)
                   if (gemini_api_key or niner_key) else set())
    n_cx = len(complex_set)
    n_sm = len(page_indices) - n_cx
    n_scan = len(scan_set)
    _emit("detect", f"Phân loại: {n_sm} trang chữ (ODL) · {n_cx} trang phức tạp (Gemini song song)"
                    + (f" — trong đó {n_scan} trang là bản scan" if n_scan else ""))

    # Không có trang phức tạp (hoặc không có key) → pure ODL nhanh như cũ
    if not complex_set:
        doc.close()
        if scan_set:
            # Có trang scan mà không có key nào → ODL sẽ trả trang trắng. Báo rõ
            # thay vì xuất ra file Word rỗng và nói "Hoàn tất".
            raise RuntimeError(
                f"PDF này có {len(scan_set)} trang là bản scan (ảnh chụp) nên cần AI "
                f"đọc chữ, nhưng chưa có API key. Vào Cài đặt nhập Gemini API key "
                f"hoặc bật 9Router rồi thử lại.")
        md = _convert_via_odl(pdf_path, work_dir, gemini_api_key=gemini_api_key,
                              page_range=page_range, niner_url=niner_url,
                              niner_key=niner_key, progress_cb=progress_cb)
        return md, {"failed_pages": [], "truncated_pages": [],
                    "scan_pages": [], "ocr_used": False}

    # Render PNG trang phức tạp ở luồng chính (fitz không an toàn đa luồng).
    # Trang SCAN render ở zoom 3 (216 DPI) thay vì zoom 2 (144 DPI): đo thực tế
    # trên đề scan của thầy cho +38% chữ đọc được, chi phí chỉ 0,2s/trang.
    # Trang digital giữ zoom 2 → không đụng đường đang chạy tốt.
    png_map = {idx: _render_page_png(doc, idx, zoom=(3 if idx in scan_set else 2))
               for idx in complex_set}
    doc.close()

    # Gom segment liên tiếp cùng loại (giữ thứ tự)
    segments = []  # (kind, [idxs])
    for idx in page_indices:
        kind = "C" if idx in complex_set else "S"
        if segments and segments[-1][0] == kind:
            segments[-1][1].append(idx)
        else:
            segments.append((kind, [idx]))

    results = {}
    done = [0]
    odl_lock = threading.Lock()
    failed_pages = []       # trang không đọc được chữ (đã chèn ảnh trang gốc)
    truncated_pages = []    # trang phải đọc lại theo nửa vì hết hạn mức

    def _do_simple(start, idxs):
        pr = f"{idxs[0]+1}-{idxs[-1]+1}" if len(idxs) > 1 else f"{idxs[0]+1}"
        with odl_lock:   # 1 ODL server → tuần tự
            try:
                md = _odl_text_only(pdf_path, work_dir / f"seg_{start}_odl", pr, progress_cb)
                # Ảnh ODL nằm trong seg_X_odl/<stem>_images/ nhưng md gộp ghi ở work_dir
                # → đường dẫn tương đối gãy, Pandoc lặng lẽ bỏ ảnh. Thêm tiền tố thư mục đoạn.
                import re as _re_seg
                md = _re_seg.sub(
                    r'\]\((<?)([^)<>]*_images[/\\][^)<>]*)(>?)\)',
                    lambda m: f"]({m.group(1)}seg_{start}_odl/{m.group(2)}{m.group(3)})",
                    md)
            except Exception as e:
                md = f"[Lỗi ODL trang {idxs[0]+1}-{idxs[-1]+1}: {str(e)[:100]}]"
        results[start] = md
        _emit("odl", f"ODL xong dải trang {idxs[0]+1}-{idxs[-1]+1}")

    def _read_halves(idx, b, w, h, is_scan):
        """Trang bị cắt giữa đường → đọc lại theo 2 nửa chồng lấn rồi nối."""
        halves = _png_halves(b)
        if not halves:
            return ""
        parts = []
        for hb in halves:
            try:
                parts.append(_gemini_one_page(
                    hb, w, h // 2, idx, fig_dir, model=gemini_model,
                    niner_url=niner_url, niner_key=niner_key,
                    gemini_api_key=gemini_api_key, scan=is_scan,
                    prompt_extra=_SCAN_STRIP_HINT))
            except Exception:
                continue
        return "\n\n".join(p for p in parts if p).strip()

    def _do_complex(idx):
        b, w, h = png_map[idx]
        is_scan = idx in scan_set
        md, why = "", ""

        # Bậc 1: Vision qua 9Router, prompt + zoom theo loại trang
        try:
            md = _gemini_one_page(b, w, h, idx, fig_dir, model=gemini_model,
                                  niner_url=niner_url, niner_key=niner_key,
                                  gemini_api_key=gemini_api_key, scan=is_scan)
            # Bậc 2: API không báo nhưng trông như bị cắt → đọc lại theo nửa trang
            if _looks_truncated(md):
                _emit("gemini", f"Trang {idx+1} trông như bị cắt — đọc lại theo nửa trang…")
                better = _read_halves(idx, b, w, h, is_scan)
                if _meaningful_text_len(better) > _meaningful_text_len(md):
                    md = better
        except VisionTruncated as e:
            # Bậc 2: hết hạn mức output → chia nửa trang
            _emit("gemini", f"Trang {idx+1} dày chữ, hết hạn mức — đọc lại theo nửa trang…")
            truncated_pages.append(idx + 1)
            md = _read_halves(idx, b, w, h, is_scan)
            if _meaningful_text_len(md) < _meaningful_text_len(e.partial):
                md = e.partial     # thà giữ phần đọc được còn hơn mất trắng
            why = "hết hạn mức output"
        except Exception as e:
            why = str(e)[:120]
            # Bậc 3: Gemini trực tiếp bằng key của thầy (9Router sập / bị chặn)
            if gemini_api_key:
                for fb in _GEMINI_DIRECT_MODELS:
                    try:
                        _emit("gemini", f"Trang {idx+1}: thử lại bằng {fb}…")
                        md = _gemini_one_page(
                            b, w, h, idx, fig_dir, model=fb,
                            gemini_api_key=gemini_api_key, scan=is_scan)
                        why = ""
                        break
                    except Exception as e2:
                        why = str(e2)[:120]
                        continue

        # Bậc 4: vẫn trống → ODL, nhưng CHỈ cho trang digital. Trang scan không
        # có chữ cho ODL lấy, gọi chỉ tốn thời gian rồi vẫn rỗng.
        if _meaningful_text_len(md) <= 40 and not is_scan:
            with odl_lock:
                try:
                    md = _odl_text_only(pdf_path, work_dir / f"seg_{idx}_fb", str(idx + 1))
                    why = ""
                except Exception as e:
                    why = str(e)[:120]

        # Bậc 5: hết cách → chèn ảnh nguyên trang + ghi nhận trang lỗi.
        # Bảo đảm cứng: KHÔNG BAO GIỜ để trang mất trắng không dấu vết.
        if _meaningful_text_len(md) <= 40:
            md = _page_failure_marker(idx, why or "không đọc được chữ",
                                      fig_dir, pdf_path, md)
            failed_pages.append(idx + 1)
            _emit("gemini", f"⚠️ Trang {idx+1} không đọc được — đã chèn ảnh trang gốc")

        results[idx] = md
        done[0] += 1
        _emit("gemini", f"Gemini xong {done[0]}/{n_cx} trang phức tạp…")

    # Mỗi trang phức tạp 1 task (song song); mỗi dải đơn giản 1 task
    tasks = []  # (kind, start_key, idxs)
    for kind, idxs in segments:
        if kind == "S":
            tasks.append(("S", idxs[0], idxs))
        else:
            for i in idxs:
                tasks.append(("C", i, [i]))

    with ThreadPoolExecutor(max_workers=4) as ex:
        futs = [ex.submit(_do_simple, st, ix) if k == "S"
                else ex.submit(_do_complex, st)
                for (k, st, ix) in tasks]
        for f in as_completed(futs):
            f.result()

    # Kiểm toán cuối: lưới an toàn độc lập với mọi nhánh ở trên. Đây là chỗ DUY
    # NHẤT phán quyết "trang này rỗng" nên không đường nào lọt được.
    for (k, st, ix) in tasks:
        if k != "C":
            continue
        if _meaningful_text_len(results.get(st, "")) <= 40 and (st + 1) not in failed_pages:
            results[st] = _page_failure_marker(st, "kết quả rỗng sau mọi bước thử",
                                               fig_dir, pdf_path, results.get(st, ""))
            failed_pages.append(st + 1)

    # Ghép theo thứ tự task (đã theo thứ tự tài liệu), kèm mốc trang để bước gắn
    # hình biết hình của trang nào phải nằm trong khoảng dòng nào (trang scan
    # không có lớp text nên không so khớp chuỗi được). Mốc bị xoá trước Pandoc.
    chunks = []
    for (k, st, ix) in tasks:
        body = results.get(st, "")
        chunks.append(f"<!-- p2w:page={ix[0]+1} -->\n{body}")
    md_all = "\n\n---\n\n".join(chunks)
    md_path = work_dir / (_safe_stem(pdf_path.name) + "_hybrid.md")
    md_path.write_text(md_all, encoding="utf-8")
    stats = {
        "failed_pages": sorted(set(failed_pages)),
        "truncated_pages": sorted(set(truncated_pages)),
        "scan_pages": sorted(p + 1 for p in scan_set),
        "ocr_used": bool(scan_set),
    }
    return md_path, stats


def _extract_digital_pdf_images(pdf_path: Path, work_dir: Path,
                                 md_path: Path) -> Path:
    """
    Trích xuất ảnh nhúng từ PDF digital (có text layer) dùng fitz.
    Ảnh nhỏ (hình vẽ, đồ thị < 400k px) được giữ lại và chèn vào đúng
    vị trí trong Markdown dựa theo tọa độ Y trên trang.
    """
    import re as _re
    try:
        import fitz
        from PIL import Image as PILImage
        import io as _io
    except ImportError:
        return md_path   # Thiếu thư viện → bỏ qua, không lỗi

    MAX_PX = 400_000   # Lớn hơn → ảnh trang scan → bỏ
    MIN_PX = 900       # Nhỏ hơn → icon/bullet → bỏ

    fig_dir = work_dir / "figures"
    fig_dir.mkdir(exist_ok=True)

    doc = fitz.open(str(pdf_path))
    # Thu thập tất cả ảnh nhúng cùng vị trí Y trên từng trang
    page_images: list[tuple[int, float, Path]] = []  # (page_idx, y_pct, img_path)
    global_img_id = 0

    for page_idx, page in enumerate(doc):
        img_list = page.get_images(full=True)
        for xref, *_ in img_list:
            try:
                base = doc.extract_image(xref)
                img_bytes = base["image"]
                img = PILImage.open(_io.BytesIO(img_bytes))
                w, h = img.size
                px = w * h
                if px < MIN_PX or px > MAX_PX:
                    continue   # Quá nhỏ (icon) hoặc quá lớn (trang scan)

                global_img_id += 1
                img_name = f"p{page_idx+1}_emb{global_img_id}.png"
                img.save(fig_dir / img_name, "PNG")

                # Tìm vị trí Y của ảnh trên trang (dùng get_image_rects)
                rects = page.get_image_rects(xref)
                y_pct = 50.0  # mặc định giữa trang
                if rects:
                    r = rects[0]
                    page_h = page.rect.height
                    y_pct = ((r.y0 + r.y1) / 2 / page_h * 100) if page_h else 50.0

                page_images.append((page_idx, y_pct, fig_dir / img_name))
            except Exception:
                pass

    doc.close()

    if not page_images:
        return md_path

    # Chèn ảnh vào Markdown — sau đoạn text gần nhất cùng trang
    content = md_path.read_text(encoding="utf-8", errors="replace")
    pages = content.split("\n\n---\n\n")

    for (page_idx, y_pct, img_path) in page_images:
        if page_idx >= len(pages):
            continue
        pg = pages[page_idx]
        lines = pg.splitlines()
        # Ước lượng dòng tương ứng với y_pct trong trang
        target_line = int(y_pct / 100 * len(lines)) if lines else 0
        target_line = max(0, min(target_line, len(lines)))
        # Chèn sau target_line
        rel = f"figures/{img_path.name}"
        lines.insert(target_line, f"\n![Hình]({rel})\n")
        pages[page_idx] = "\n".join(lines)

    md_path.write_text("\n\n---\n\n".join(pages), encoding="utf-8")
    return md_path


def _attach_det_figures(pdf_path: Path, work_dir: Path, md_path: Path,
                        page_range: str | None, _emit) -> Path:
    """Bước gắn hình XÁC ĐỊNH cho mọi method odl/hybrid/gemini (core.pdf_word_figures):
    ảnh nhúng + hình vector (cùng bộ pdf_figures của luồng nhập đề) → cắt từ trang →
    neo vào câu trong md. Gemini/ODL chỉ là nguồn bổ sung; bước này không bao giờ
    làm hỏng tài liệu (lỗi → giữ nguyên md)."""
    try:
        from core.pdf_word_figures import attach_figures
        _emit("img", "Đang trích hình vẽ, đồ thị (ảnh nhúng + vector) và gắn vào câu…")
        n = attach_figures(pdf_path, work_dir, md_path, page_range=page_range)
        _emit("img", f"Đã gắn {n} hình vào tài liệu")
    except Exception as e:
        _emit("img", f"Bỏ qua trích hình: {str(e)[:80]}")
    return md_path


def _safe_stem(name: str) -> str:
    stem = Path(name).stem
    safe = "".join(c for c in stem if c.isalnum() or c in " _-").strip()
    return safe[:80] or "TaiLieu"


# ── API chính ─────────────────────────────────────────────────────

def convert_pdf_to_word(pdf_path: str, out_docx: str | None = None,
                        ocr_mode: str = "auto", page_range: str | None = None,
                        progress_cb=None, method: str = "auto",
                        gemini_api_key: str = "",
                        niner_url: str = "", niner_key: str = "") -> dict:
    """
    Chuyển 1 file PDF -> DOCX.

    method: 'auto' | 'hybrid' | 'odl' | 'marker' | 'gemini'
      - 'hybrid': ODL cho trang chữ + Gemini Vision cho trang phức tạp (song song)
      - 'odl'   : OpenDataLoader hybrid (nhanh + chính xác, cần Java)
      - 'marker': Marker CPU (offline, chậm hơn)
      - 'gemini': Gemini Vision toàn bộ (cần API key + internet)
      - 'auto'  : hybrid nếu có Gemini key + ODL; ngược lại odl; ngược nữa marker
    ocr_mode: 'auto' | 'force' | 'off'  (chỉ áp dụng cho Marker)
    """
    def _emit(stage, msg):
        if progress_cb:
            try:
                progress_cb(stage, msg)
            except Exception:
                pass

    if not find_pandoc():
        raise RuntimeError("Chưa cài Pandoc.")

    pdf_path = Path(pdf_path)
    if not pdf_path.exists():
        raise FileNotFoundError(str(pdf_path))

    out_path = Path(out_docx) if out_docx else (
        EXPORT_DIR / (_safe_stem(pdf_path.name) + ".docx"))
    work_dir = MARKER_OUT / _safe_stem(pdf_path.name)
    if work_dir.exists():
        shutil.rmtree(work_dir, ignore_errors=True)

    # PDF khoá quyền (owner password) → ODL/Java từ chối; gỡ trước khi chuyển.
    # Đặt SAU khi tính work_dir/out_path để tên thư mục/file giữ theo tên gốc.
    pdf_path = _strip_pdf_encryption(pdf_path, progress_cb=_emit)

    # Quyết định method
    if method == "auto":
        if odl_available() and gemini_api_key:
            method = "hybrid"
        elif odl_available():
            method = "odl"
        else:
            method = "marker"

    t0 = time.time()
    # Mặc định cho các nhánh không đi qua Vision (odl/marker)
    stats = {"failed_pages": [], "truncated_pages": [], "scan_pages": [],
             "ocr_used": False}

    if method == "hybrid":
        if not odl_available() or not java_available():
            _emit("detect", "Thiếu ODL/Java — chuyển sang Gemini Vision toàn bộ…")
            md, stats = _convert_via_gemini(pdf_path, work_dir, gemini_api_key,
                                            page_range=page_range, niner_url=niner_url,
                                            niner_key=niner_key, progress_cb=progress_cb)
            method = "gemini"
        else:
            _emit("detect", "Chế độ lai: ODL (trang chữ) + Gemini Vision (trang phức tạp)…")
            # Server warm giúp các lần convert sau nhanh; nếu không khởi động được
            # vẫn tiếp tục — opendataloader_pdf.convert có hybrid_fallback=True
            if not _ensure_odl_server(progress_cb=progress_cb):
                _emit("odl", "ODL server không khởi động được — tiếp tục với fallback (chậm hơn)")
            md, stats = _convert_via_hybrid(pdf_path, work_dir,
                                            gemini_api_key=gemini_api_key,
                                            page_range=page_range,
                                            niner_url=niner_url, niner_key=niner_key,
                                            progress_cb=progress_cb)
        detect = detect_text_layer(str(pdf_path))
        ocr_used = bool(stats.get("ocr_used"))
        md = _attach_det_figures(pdf_path, work_dir, md, page_range, _emit)

    elif method == "odl":
        if not odl_available():
            raise RuntimeError(
                "OpenDataLoader chưa cài. Chạy: pip install opendataloader-pdf[hybrid]")
        if not java_available():
            raise RuntimeError(
                "Cần Java 11+. Tải tại: https://adoptium.net")
        _emit("detect", "Dùng OpenDataLoader hybrid (nhanh + chính xác)…")
        if not _ensure_odl_server(progress_cb=progress_cb):
            _emit("odl", "ODL server không khởi động được — tiếp tục với fallback (chậm hơn)")
        md = _convert_via_odl(pdf_path, work_dir,
                              gemini_api_key=gemini_api_key,
                              page_range=page_range,
                              niner_url=niner_url, niner_key=niner_key,
                              progress_cb=progress_cb)
        detect = detect_text_layer(str(pdf_path))
        ocr_used = False

        # Kiểm tra ODL có trích xuất text không — nếu chỉ ra ảnh (scan PDF)
        # thì tự fallback sang Gemini Vision để OCR + phát hiện hình
        if not _has_meaningful_text(md):
            if gemini_api_key:
                _emit("detect",
                      "PDF không có lớp text (bản scan) — tự động dùng Gemini Vision để đọc và phát hiện hình…")
                work_dir2 = MARKER_OUT / (_safe_stem(pdf_path.name) + "_gemini")
                if work_dir2.exists():
                    shutil.rmtree(work_dir2, ignore_errors=True)
                md, stats = _convert_via_gemini(pdf_path, work_dir2, gemini_api_key,
                                                page_range=page_range, niner_url=niner_url,
                                                niner_key=niner_key, progress_cb=progress_cb)
                work_dir = work_dir2
                method = "gemini"
                ocr_used = bool(stats.get("ocr_used"))
            elif marker_available():
                _emit("detect",
                      "PDF không có lớp text (bản scan) — tự động dùng Marker OCR…")
                work_dir2 = MARKER_OUT / (_safe_stem(pdf_path.name) + "_marker")
                if work_dir2.exists():
                    shutil.rmtree(work_dir2, ignore_errors=True)
                md = None
                try:
                    if ensure_sidecar(progress_cb=progress_cb):
                        md = _convert_via_sidecar(pdf_path, work_dir2, disable_ocr=False)
                except Exception:
                    md = None
                if md is None:
                    md = _run_marker(pdf_path, work_dir2, disable_ocr=False, page_range=page_range)
                work_dir = work_dir2
                method = "marker"
                ocr_used = True
            else:
                raise RuntimeError(
                    "PDF là bản scan nhưng không có Gemini API key và Marker chưa cài.\n"
                    "Vui lòng nhập Gemini API key trong Cài đặt để tự động OCR."
                )
        else:
            # PDF digital có text → trích hình xác định (ảnh nhúng + vector) và neo vào câu.
            # (Thay _extract_digital_pdf_images cũ: chỉ ảnh nhúng, đặt theo Y ước lượng,
            # trùng với ảnh ODL tự xuất, không có hình vector.)
            md = _attach_det_figures(pdf_path, work_dir, md, page_range, _emit)

    elif method == "gemini":
        if not gemini_api_key:
            raise RuntimeError("Cần nhập Gemini API key trong Cài đặt.")
        _emit("detect", "Dùng Gemini Vision để nhận diện nội dung và hình vẽ…")
        md, stats = _convert_via_gemini(pdf_path, work_dir, gemini_api_key,
                                        page_range=page_range, niner_url=niner_url,
                                        niner_key=niner_key, progress_cb=progress_cb)
        detect = detect_text_layer(str(pdf_path))
        ocr_used = bool(stats.get("ocr_used"))
        md = _attach_det_figures(pdf_path, work_dir, md, page_range, _emit)

    else:  # marker
        if not marker_available():
            raise RuntimeError("Chưa cài Marker (venv_marker).")
        detect = detect_text_layer(str(pdf_path))
        if ocr_mode == "off":
            disable_ocr = True
        elif ocr_mode == "force":
            disable_ocr = False
        else:
            # Có BẤT KỲ trang scan nào thì vẫn phải OCR — PDF scan có watermark
            # ở lớp text từng bị is_digital=True và tắt OCR, mất sạch nội dung.
            disable_ocr = (bool(detect.get("is_digital"))
                           and not detect.get("scan_pages"))
        mode_label = "bỏ OCR" if disable_ocr else "bật OCR (file scan)"
        _emit("detect", f"Nhận diện: {detect.get('pages_with_text')}/"
              f"{detect.get('pages')} trang có text → {mode_label}")
        scan_note = "" if disable_ocr else " — file scan nên sẽ lâu hơn"
        md = None
        if page_range is None:
            try:
                if ensure_sidecar(progress_cb=progress_cb):
                    _emit("marker", "Đang nhận diện nội dung (Marker)…" + scan_note)
                    md = _convert_via_sidecar(pdf_path, work_dir, disable_ocr)
            except Exception as e:
                _emit("marker", f"Sidecar lỗi ({e}), chuyển sang chế độ dự phòng…")
                md = None
        if md is None:
            _emit("marker", "Đang nhận diện nội dung (Marker)…" + scan_note)
            md = _run_marker(pdf_path, work_dir, disable_ocr, page_range)
        ocr_used = (not disable_ocr)

    # Sau Gemini Vision: áp dụng post-processing (Marker/ODL đã có, Gemini chưa)
    if method == "gemini":
        _emit("fmt", "Chuẩn hoá định dạng câu hỏi và đáp án…")
        md = _strip_answer_bullets(md)
        md = _apply_bold_formatting(md)

    _emit("pandoc", "Đang dựng file Word, giữ công thức và hình vẽ…")
    docx = _run_pandoc(md, out_path)

    seconds = round(time.time() - t0, 1)
    fp = stats.get("failed_pages") or []
    if fp:
        _emit("done", f"Hoàn tất sau {seconds}s — {len(fp)} trang không đọc được: {fp}")
    else:
        _emit("done", f"Hoàn tất sau {seconds}s")
    return {
        "docx_path": str(docx),
        "docx_name": docx.name,
        "seconds": seconds,
        "ocr_used": ocr_used,
        "method": method,
        "detect": detect,
        "failed_pages": fp,
        "truncated_pages": stats.get("truncated_pages") or [],
        "scan_pages": stats.get("scan_pages") or [],
    }
