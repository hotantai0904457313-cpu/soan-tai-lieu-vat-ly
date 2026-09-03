"""
Ứng dụng Soạn Tài Liệu Vật Lý THPT
Flask backend — http://localhost:5000
"""
import os, sys, json, shutil, uuid, socket, time
if sys.stdout: sys.stdout.reconfigure(encoding='utf-8')
if sys.stderr: sys.stderr.reconfigure(encoding='utf-8')
from flask import Flask, request, jsonify, render_template, send_file, Response, stream_with_context
from flask_cors import CORS

# Đảm bảo import đúng path
sys.path.insert(0, os.path.dirname(__file__))
import config
from config import UPLOAD_DIR, EXPORT_DIR, ALLOWED_EXTENSIONS, IMAGE_EXTENSIONS
from core.db import init_db, save_document, get_document, list_documents, delete_document
from core.db import get_settings, update_settings, save_ai_history, get_ai_history, delete_ai_history
from core.document_model import Document

app = Flask(__name__)
CORS(app, expose_headers=['X-Saved-Dirs'])
app.config['MAX_CONTENT_LENGTH'] = config.MAX_CONTENT_LENGTH


@app.errorhandler(413)
def _too_large(e):
    """Trả JSON (không phải HTML) khi file vượt giới hạn — để frontend hiện thông báo rõ ràng."""
    limit_mb = config.MAX_CONTENT_LENGTH // (1024 * 1024)
    return jsonify({'error': f'File quá lớn (vượt {limit_mb} MB). '
                             f'Hãy tách PDF thành nhiều phần nhỏ hơn rồi thử lại.'}), 413


# Khởi tạo DB khi start
init_db()
os.makedirs(UPLOAD_DIR, exist_ok=True)
os.makedirs(EXPORT_DIR, exist_ok=True)


def _cleanup_old_files():
    """Dọn file tích tụ (chạy nền 1 lần lúc khởi động, tránh đầy đĩa lâu dài):
    - data/exports/: file cũ hơn 7 ngày (bản chính đã copy sang Downloads/OneDrive)
    - data/uploads/p2w/<job>/: thư mục job PDF→Word cũ hơn 7 ngày
    - file tạm ở gốc data/uploads/: cũ hơn 2 ngày
    KHÔNG đụng uploads/images/ và uploads/originals/ — tài liệu đã lưu còn tham chiếu."""
    cutoff7 = time.time() - 7 * 86400
    cutoff2 = time.time() - 2 * 86400
    try:
        for name in os.listdir(EXPORT_DIR):
            p = os.path.join(EXPORT_DIR, name)
            if os.path.isfile(p) and os.path.getmtime(p) < cutoff7:
                try:
                    os.remove(p)
                except OSError:
                    pass
        p2w_root = os.path.join(UPLOAD_DIR, 'p2w')
        if os.path.isdir(p2w_root):
            for name in os.listdir(p2w_root):
                d = os.path.join(p2w_root, name)
                if os.path.isdir(d) and os.path.getmtime(d) < cutoff7:
                    shutil.rmtree(d, ignore_errors=True)
        for name in os.listdir(UPLOAD_DIR):
            p = os.path.join(UPLOAD_DIR, name)
            if os.path.isfile(p) and os.path.getmtime(p) < cutoff2:
                try:
                    os.remove(p)
                except OSError:
                    pass
    except Exception as e:
        print(f'[Cleanup] {e}', flush=True)


import threading as _cleanup_threading
_cleanup_threading.Thread(target=_cleanup_old_files, daemon=True).start()


# ── Tiện ích ──────────────────────────────────────────────────────

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

_net_cache = {'ok': False, 'ts': 0.0}

def check_internet():
    """Kiểm tra mạng — cache 30s (api_import gọi nhiều lần liên tiếp).
    KHÔNG dùng socket.setdefaulttimeout: nó đổi timeout mặc định của MỌI
    socket trong tiến trình (từng cắt ngang OCR file lớn ở 3s)."""
    import time as _time
    now = _time.time()
    if now - _net_cache['ts'] < 30:
        return _net_cache['ok']
    # Thử 2 đích khác nhau: một lần chớp nháy mạng (hoặc 8.8.8.8 bị chặn tạm)
    # từng làm nút Giải toàn đề báo "không có internet" suốt 30s dù mạng vẫn tốt
    ok = False
    for host in ('8.8.8.8', '1.1.1.1'):
        try:
            with socket.create_connection((host, 53), timeout=3):
                ok = True
                break
        except Exception:
            continue
    _net_cache['ok'] = ok
    # Chỉ cache lâu khi CÓ mạng; lúc mất mạng cache ngắn (5s) để vừa có lại
    # là dùng được ngay, thầy không phải chờ
    _net_cache['ts'] = now if ok else now - 25
    return ok

def copy_to_destinations(filepath: str, settings: dict) -> list:
    """Copy file xuất sang Downloads, OneDrive và Google Drive nếu được cài đặt.
    Trả về list thư mục đã copy thành công."""
    filename = os.path.basename(filepath)
    saved_to = []
    for key in ('save_dir', 'onedrive_dir', 'gdrive_dir'):
        d = settings.get(key, '')
        if not d:
            if key == 'save_dir':
                d = os.path.join(os.path.expanduser('~'), 'Downloads')
            else:
                continue
        if os.path.isdir(d):
            try:
                shutil.copy2(filepath, os.path.join(d, filename))
                saved_to.append(d)
            except Exception:
                pass
    return saved_to


# ── Trang chính ───────────────────────────────────────────────────

@app.route('/')
def index():
    return render_template('index.html')


# ── Import file ───────────────────────────────────────────────────

@app.route('/api/import', methods=['POST'])
def api_import():
    if 'file' not in request.files:
        return jsonify({'error': 'Không có file'}), 400
    f = request.files['file']
    if not f.filename or not allowed_file(f.filename):
        return jsonify({'error': 'Định dạng không hỗ trợ. Chấp nhận: PDF, DOCX, JPG, PNG...'}), 400

    ext = f.filename.rsplit('.', 1)[1].lower()
    tmp_name = f'{uuid.uuid4().hex}.{ext}'
    tmp_path = os.path.join(UPLOAD_DIR, tmp_name)
    f.save(tmp_path)

    # Chọn trang (chỉ áp dụng cho PDF) — cắt PDF con để đọc + hiển thị đúng các trang đã chọn
    page_range = (request.form.get('page_range') or '').strip()
    if ext == 'pdf' and page_range:
        try:
            from core.importer import subset_pdf
            sub_path = os.path.join(UPLOAD_DIR, f'{uuid.uuid4().hex}.pdf')
            if subset_pdf(tmp_path, sub_path, page_range):
                os.remove(tmp_path)
                tmp_path = sub_path
        except Exception as pe:
            print(f'[Chọn trang import] {pe}', flush=True)

    # Lưu file gốc để hiển thị panel trái (iframe cho PDF) — đã là bản đã cắt trang nếu có
    orig_dir = os.path.join(UPLOAD_DIR, 'originals')
    os.makedirs(orig_dir, exist_ok=True)
    orig_name = f'{uuid.uuid4().hex}.{ext}'
    orig_path = os.path.join(orig_dir, orig_name)
    shutil.copy2(tmp_path, orig_path)

    try:
        settings   = get_settings()
        gemini_key = settings.get('gemini_api_key', '')

        import_method = 'standard'
        display_file  = orig_name
        display_ext   = ext

        niner_key  = settings.get('niner_router_key', '')
        niner_url  = settings.get('niner_router_url', 'http://localhost:20128/v1')

        from core.importer import (import_file, import_with_vision,
                                    import_with_vision_9router,
                                    import_docx_pandoc, find_pandoc,
                                    import_image_file, import_image_as_document)

        if ext in IMAGE_EXTENSIONS:
            # ── Ảnh: gửi thẳng Gemini Vision ──────────────────────
            if gemini_key and check_internet():
                try:
                    doc = import_image_file(tmp_path, gemini_key)
                    import_method = 'image_vision'
                except Exception as img_err:
                    print(f'[Image Vision fallback] {img_err}', flush=True)
                    doc = import_image_as_document(tmp_path)
            else:
                doc = import_image_as_document(tmp_path)
            # Hiển thị ảnh gốc trực tiếp trong panel trái
            display_file = orig_name
            display_ext  = ext

        elif ext in ('docx', 'doc'):
            pandoc = find_pandoc()
            if pandoc:
                # ── Pipeline Pandoc (ưu tiên) ──────────────────────
                try:
                    doc, html_name = import_docx_pandoc(
                        tmp_path, gemini_key if check_internet() else '')
                    import_method = 'pandoc_gemini' if gemini_key and check_internet() else 'pandoc'
                    # Hiển thị HTML pandoc tạo ra (có KaTeX)
                    if html_name:
                        display_file = html_name
                        display_ext  = 'html'
                    else:
                        # Fallback display: convert sang PDF bằng docx2pdf nếu có
                        pdf_name = _docx_to_pdf_for_display(orig_path, orig_dir)
                        if pdf_name:
                            display_file = pdf_name
                            display_ext  = 'pdf'
                except Exception as pandoc_err:
                    print(f'[Pandoc fallback] {pandoc_err}', flush=True)
                    doc = import_file(tmp_path)
            elif gemini_key and check_internet():
                # ── Fallback Vision (khi chưa cài Pandoc) ──────────
                try:
                    doc = import_with_vision(tmp_path, gemini_key)
                    import_method = 'vision'
                except Exception as vision_err:
                    print(f'[Vision fallback] {vision_err}', flush=True)
                    doc = import_file(tmp_path)
                pdf_name = _docx_to_pdf_for_display(orig_path, orig_dir)
                if pdf_name:
                    display_file = pdf_name
                    display_ext  = 'pdf'
            else:
                doc = import_file(tmp_path)
                pdf_name = _docx_to_pdf_for_display(orig_path, orig_dir)
                if pdf_name:
                    display_file = pdf_name
                    display_ext  = 'pdf'
        else:
            # ── PDF: ưu tiên 9Router (Gemini 3 Flash) chạy SONG SONG theo cụm trang ──
            if niner_key and check_internet():
                try:
                    doc = import_with_vision_9router(tmp_path, niner_key, niner_url,
                                                     gemini_key=gemini_key)
                    import_method = 'vision_9router_parallel'
                except Exception as niner_err:
                    print(f'[9Router import fallback] {niner_err}', flush=True)
                    # Fallback: Gemini API trực tiếp → import thường
                    if gemini_key:
                        try:
                            doc = import_with_vision(tmp_path, gemini_key)
                            import_method = 'vision'
                        except Exception as vision_err:
                            print(f'[Vision fallback] {vision_err}', flush=True)
                            doc = import_file(tmp_path)
                    else:
                        doc = import_file(tmp_path)
            elif gemini_key and check_internet():
                try:
                    doc = import_with_vision(tmp_path, gemini_key)
                    import_method = 'vision'
                except Exception as vision_err:
                    print(f'[Vision fallback] {vision_err}', flush=True)
                    doc = import_file(tmp_path)
            else:
                doc = import_file(tmp_path)

        return jsonify({'success': True, 'document': doc.to_dict(),
                        'original_file': display_file, 'original_ext': display_ext,
                        'import_method': import_method})
    except Exception as e:
        if os.path.exists(orig_path):
            os.remove(orig_path)
        return jsonify({'error': f'Lỗi khi đọc file: {str(e)}'}), 500
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)


# ── Debug Vision — xem raw Gemini response ───────────────────────

@app.route('/api/debug/vision', methods=['POST'])
def api_debug_vision():
    """Upload file → thấy raw JSON từ Gemini, không parse thành Document."""
    if 'file' not in request.files:
        return jsonify({'error': 'Không có file'}), 400
    f = request.files['file']
    if not f.filename or not allowed_file(f.filename):
        return jsonify({'error': 'Chỉ chấp nhận PDF, DOCX'}), 400

    settings   = get_settings()
    gemini_key = settings.get('gemini_api_key', '')
    if not gemini_key:
        return jsonify({'error': 'Chưa cài API key Gemini'}), 400

    ext = f.filename.rsplit('.', 1)[1].lower()
    tmp_path = os.path.join(UPLOAD_DIR, f'{uuid.uuid4().hex}.{ext}')
    f.save(tmp_path)

    try:
        from core.importer import _render_pdf_pages, _call_gemini_vision_json, _extract_and_parse_json
        import tempfile, shutil as sh

        work_path = tmp_path
        tmp_pdf = None
        if ext in ('docx', 'doc'):
            try:
                from docx2pdf import convert as d2p
                tmp_pdf = tmp_path + '_preview.pdf'
                d2p(tmp_path, tmp_pdf)
                if os.path.exists(tmp_pdf) and os.path.getsize(tmp_pdf) > 0:
                    work_path = tmp_pdf
            except Exception:
                pass

        pages = _render_pdf_pages(work_path)
        if not pages:
            return jsonify({'error': 'Không render được trang'}), 500

        raw = _call_gemini_vision_json(pages, gemini_key)
        parsed = _extract_and_parse_json(raw)
        parse_ok = parsed is not None

        return jsonify({
            'raw_response': raw,
            'char_count': len(raw),
            'pages_sent': len(pages),
            'parse_ok': parse_ok,
            'sections_count': len(parsed.get('sections', [])) if parsed else 0,
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        if 'tmp_pdf' in dir() and tmp_pdf and os.path.exists(tmp_pdf):
            try: os.remove(tmp_pdf)
            except: pass


# ── Convert DOCX → PDF để hiển thị panel trái ────────────────────

def _docx_to_pdf_for_display(docx_path: str, output_dir: str) -> str | None:
    """Dùng Word COM (Windows) convert DOCX → PDF. Trả về tên file PDF hoặc None nếu lỗi."""
    try:
        from docx2pdf import convert
        pdf_name = f'{uuid.uuid4().hex}.pdf'
        pdf_path = os.path.join(output_dir, pdf_name)
        convert(docx_path, pdf_path)
        if os.path.exists(pdf_path) and os.path.getsize(pdf_path) > 0:
            return pdf_name
    except Exception:
        pass
    return None


# ── Phục vụ file gốc (PDF/DOCX/HTML) cho panel trái ─────────────

@app.route('/api/original/<filename>')
def api_serve_original(filename):
    orig_dir = os.path.join(UPLOAD_DIR, 'originals')
    filepath = os.path.join(orig_dir, filename)
    if not os.path.exists(filepath):
        return jsonify({'error': 'File không tồn tại'}), 404
    ext = filename.rsplit('.', 1)[-1].lower()
    if ext == 'pdf':
        mime = 'application/pdf'
    elif ext == 'html':
        mime = 'text/html; charset=utf-8'
    elif ext in ('jpg', 'jpeg'):
        mime = 'image/jpeg'
    elif ext == 'png':
        mime = 'image/png'
    elif ext == 'gif':
        mime = 'image/gif'
    elif ext in ('webp',):
        mime = 'image/webp'
    elif ext in ('tiff', 'bmp'):
        mime = f'image/{ext}'
    else:
        mime = 'application/octet-stream'
    return send_file(filepath, mimetype=mime)


# ── Kiểm tra Pandoc ───────────────────────────────────────────────

@app.route('/api/check-pandoc')
def api_check_pandoc():
    from core.importer import find_pandoc
    import subprocess
    pandoc = find_pandoc()
    if pandoc:
        try:
            r = subprocess.run([pandoc, '--version'], capture_output=True, timeout=5, text=True,
                               creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            version = r.stdout.split('\n')[0] if r.returncode == 0 else 'unknown'
        except Exception:
            version = 'installed'
        return jsonify({'available': True, 'version': version, 'path': pandoc})
    return jsonify({'available': False,
                    'install_url': 'https://github.com/jgm/pandoc/releases/latest',
                    'message': 'Tải installer .msi cho Windows, cài xong khởi động lại app'})


# ── Kiểm tra 9Router ──────────────────────────────────────────────

@app.route('/api/check-9router')
def api_check_9router():
    settings = get_settings()
    url = settings.get('niner_router_url', 'http://localhost:20128/v1')
    key = settings.get('niner_router_key', '')
    try:
        import requests as req
        headers = {'Authorization': f'Bearer {key}'} if key else {}
        # /models gộp nhiều provider nên có thể mất vài giây — để timeout rộng tránh báo nhầm "chưa chạy"
        r = req.get(f'{url}/models', headers=headers, timeout=15)
        if r.status_code == 200:
            models = [m['id'] for m in r.json().get('data', [])[:10]]
            return jsonify({'running': True, 'models': models})
    except Exception as e:
        return jsonify({'running': False, 'error': str(e)})
    return jsonify({'running': False})


# ── Documents CRUD ────────────────────────────────────────────────

@app.route('/api/documents', methods=['GET'])
def api_list_documents():
    grade    = request.args.get('grade', type=int)
    doc_type = request.args.get('doc_type')
    search   = request.args.get('search')
    docs = list_documents(grade=grade, doc_type=doc_type, search=search)
    return jsonify(docs)


@app.route('/api/documents', methods=['POST'])
def api_create_document():
    data = request.get_json()
    if not data:
        return jsonify({'error': 'Không có dữ liệu'}), 400
    doc_id = save_document(data)
    return jsonify({'success': True, 'id': doc_id})


@app.route('/api/documents/<int:doc_id>', methods=['GET'])
def api_get_document(doc_id):
    doc = get_document(doc_id)
    if not doc:
        return jsonify({'error': 'Không tìm thấy tài liệu'}), 404
    return jsonify(doc)


@app.route('/api/documents/<int:doc_id>', methods=['PUT'])
def api_update_document(doc_id):
    data = request.get_json()
    if not data:
        return jsonify({'error': 'Không có dữ liệu'}), 400
    # Chặn ghi đè tài liệu bằng nội dung rỗng/vỡ cấu trúc (UPDATE thay toàn bộ
    # content_json, không có undo) — frontend lỗi một lần là mất sạch đề.
    if not str(data.get('title', '')).strip():
        return jsonify({'error': 'Thiếu tiêu đề tài liệu — không lưu.'}), 400
    sections = data.get('sections')
    if not isinstance(sections, list) or not sections:
        return jsonify({'error': 'Nội dung tài liệu rỗng — không lưu để tránh mất dữ liệu.'}), 400
    # Backup bản cũ ra file .bak trước khi ghi đè (giữ 1 bản gần nhất/tài liệu)
    try:
        old = get_document(doc_id)
        if old:
            bak_dir = os.path.join(os.path.dirname(EXPORT_DIR), 'backups')
            os.makedirs(bak_dir, exist_ok=True)
            with open(os.path.join(bak_dir, f'doc_{doc_id}.bak.json'), 'w',
                      encoding='utf-8') as bf:
                json.dump(old, bf, ensure_ascii=False)
    except Exception as be:
        print(f'[Backup] Không backup được tài liệu {doc_id}: {be}', flush=True)
    data['id'] = doc_id
    save_document(data)
    return jsonify({'success': True})


@app.route('/api/documents/<int:doc_id>', methods=['DELETE'])
def api_delete_document(doc_id):
    delete_document(doc_id)
    return jsonify({'success': True})


# ── AI ────────────────────────────────────────────────────────────

@app.route('/api/ai/solve/stream', methods=['POST'])
def api_ai_solve_stream():
    data = request.get_json(force=True, silent=True)
    if not data or 'document' not in data:
        return jsonify({'error': 'Thiếu dữ liệu'}), 400
    if not check_internet():
        return jsonify({'error': 'Không có kết nối internet — tính năng AI tạm dừng.'}), 503

    settings   = get_settings()
    gemini_key = settings.get('gemini_api_key',   '')
    claude_key = settings.get('claude_api_key',   '')
    niner_key  = settings.get('niner_router_key', '')
    niner_url  = settings.get('niner_router_url', 'http://localhost:20128/v1')
    model_ex   = settings.get('model_exercise',   'ag/gemini-3.7-flash-low')

    if not gemini_key and not claude_key and not niner_key:
        return jsonify({'error': 'Chưa cấu hình API key. Vào Cài đặt → AI để nhập.'}), 400

    doc    = Document.from_dict(data['document'])
    doc_id = data.get('doc_id')

    from core.ai_solver import stream_solve_document
    import json as _json

    def _generate():
        try:
            for event in stream_solve_document(doc, claude_key, gemini_key,
                                               niner_key, niner_url, model_ex):
                if event.get('type') == 'done' and doc_id:
                    for q_id, sol in (event.get('solutions') or {}).items():
                        save_ai_history(doc_id, q_id, '', sol, 'stream')
                yield f"data: {_json.dumps(event, ensure_ascii=False)}\n\n"
        except Exception as e:
            yield f"data: {_json.dumps({'type': 'error', 'text': str(e)}, ensure_ascii=False)}\n\n"

    return Response(
        stream_with_context(_generate()),
        content_type='text/event-stream',
        headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'},
    )


@app.route('/api/ai/solve', methods=['POST'])
def api_ai_solve():
    data = request.get_json(force=True, silent=True)
    if not data or 'document' not in data:
        return jsonify({'error': 'Thiếu dữ liệu'}), 400
    if not check_internet():
        return jsonify({'error': 'Không có kết nối internet — tính năng AI tạm dừng.'}), 503

    settings      = get_settings()
    gemini_key    = settings.get('gemini_api_key',   '')
    claude_key    = settings.get('claude_api_key',   '')
    niner_key     = settings.get('niner_router_key', '')
    niner_url     = settings.get('niner_router_url', 'http://localhost:20128/v1')
    model_th      = settings.get('model_theory',     'ag/gemini-3.7-flash-low')
    model_ex      = settings.get('model_exercise',   'ag/gemini-3.7-flash-low')

    if not gemini_key and not claude_key and not niner_key:
        return jsonify({'error': 'Chưa cấu hình API key. Vào Cài đặt → AI để nhập.'}), 400

    doc    = Document.from_dict(data['document'])
    doc_id = data.get('doc_id')

    from core.ai_solver import solve_document
    solutions = solve_document(doc, gemini_key, claude_key,
                               niner_key, niner_url, model_th, model_ex)

    if 'error' in solutions:
        return jsonify({'error': solutions['error']}), 400

    for q_id, sol_text in solutions.items():
        save_ai_history(doc_id, q_id, '', sol_text, 'claude' if claude_key else model_ex)

    return jsonify({'success': True, 'solutions': solutions})


@app.route('/api/ai/review', methods=['POST'])
def api_ai_review():
    data = request.get_json()
    if not data or 'document' not in data:
        return jsonify({'error': 'Thiếu dữ liệu'}), 400
    if not check_internet():
        return jsonify({'error': 'Không có kết nối internet.'}), 503

    settings   = get_settings()
    gemini_key = settings.get('gemini_api_key', '')
    claude_key = settings.get('claude_api_key', '')
    niner_key  = settings.get('niner_router_key', '')
    niner_url  = settings.get('niner_router_url', 'http://localhost:20128/v1')

    doc = Document.from_dict(data['document'])
    from core.ai_solver import review_document
    review = review_document(doc, gemini_key, claude_key, niner_key, niner_url)
    return jsonify({'review': review})


@app.route('/api/curriculum', methods=['GET'])
def api_curriculum():
    """Cây chương trình Vật lí (lớp → chương) cho dropdown chọn phạm vi kiến thức."""
    from core.chuong_trinh import get_curriculum_tree
    return jsonify(get_curriculum_tree())


@app.route('/api/curriculum/preview', methods=['POST'])
def api_curriculum_preview():
    """Xem trước đúng đoạn ràng buộc sẽ gửi cho AI (hiện trong modal Phạm vi)."""
    data = request.get_json(force=True, silent=True) or {}
    from core.chuong_trinh import build_scope_prompt, describe_scope
    scope = data.get('scope') or {}
    return jsonify({'text': build_scope_prompt(scope), 'label': describe_scope(scope)})


@app.route('/api/ai/history', methods=['GET'])
def api_ai_history():
    doc_id = request.args.get('document_id', type=int)
    history = get_ai_history(doc_id)
    return jsonify(history)


@app.route('/api/ai/history/<int:history_id>', methods=['DELETE'])
def api_delete_history(history_id):
    delete_ai_history(history_id)
    return jsonify({'success': True})


@app.route('/api/ai/history', methods=['DELETE'])
def api_delete_all_history():
    delete_ai_history()
    return jsonify({'success': True})


# ── Tạo HTML in ──────────────────────────────────────────────────

def _esc(s):
    """HTML escape ngoài các block $...$  (giữ nguyên LaTeX)."""
    import re as _re
    s = str(s)
    # Tách tại ranh giới $...$ để không escape bên trong toán
    parts = _re.split(r'(\$[^$\n]+?\$)', s)
    out = []
    for i, p in enumerate(parts):
        if i % 2 == 1:  # bên trong $...$
            out.append(p.replace('&', '&amp;'))
        else:
            out.append(p.replace('&', '&amp;').replace('<', '&lt;')
                        .replace('>', '&gt;').replace('"', '&quot;'))
    return ''.join(out)


def _build_print_html(doc, show_solutions: bool, settings: dict) -> str:
    """Trả về HTML đầy đủ để mở tab mới + window.print() → PDF."""
    teacher = settings.get('teacher_name', 'Giáo viên')
    school  = settings.get('school_name',  'Trường THPT')
    base    = 'http://localhost:5000'

    body_parts = []
    for sec in (doc.sections or []):
        if sec.label:
            body_parts.append(f'<div class="sec-label">{_esc(sec.label)}</div>')
        if sec.intro:
            body_parts.append(f'<p class="intro">{_esc(sec.intro)}</p>')
        for q in (sec.questions or []):
            body_parts.append('<div class="q-block">')
            body_parts.append(
                f'<p class="q-text"><strong>Câu {q.number}.</strong> '
                f'{_esc(q.text)}</p>')
            # Ảnh
            for img in (q.images or []):
                if img.filename:
                    body_parts.append(
                        f'<img src="{base}/api/images/{img.filename}" class="q-img"/>')
            # Options A/B/C/D
            if q.options:
                body_parts.append('<div class="opts">')
                for o in q.options:
                    body_parts.append(f'<span class="opt">{_esc(o)}</span>')
                body_parts.append('</div>')
            # Sub-items (đúng-sai)
            for sub in (q.sub_items or []):
                body_parts.append(f'<div class="sub">{_esc(sub)}</div>')
            # Lời giải
            if show_solutions and q.show_solution and q.ai_solution:
                body_parts.append(
                    f'<div class="solution"><em>Lời giải: </em>'
                    f'{_esc(q.ai_solution)}</div>')
            body_parts.append('</div>')

    body_html = '\n'.join(body_parts)

    return f'''<!DOCTYPE html>
<html lang="vi">
<head>
<meta charset="UTF-8">
<title>{_esc(doc.title or "Tài liệu")}</title>
<base href="{base}/">
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/katex@0.16.11/dist/katex.min.css">
<script defer src="https://cdn.jsdelivr.net/npm/katex@0.16.11/dist/katex.min.js"></script>
<script defer src="https://cdn.jsdelivr.net/npm/katex@0.16.11/dist/contrib/auto-render.min.js"
  onload="renderMathInElement(document.body,{{
    delimiters:[{{left:'$',right:'$',display:false}}],
    throwOnError:false,strict:false
  }});setTimeout(()=>window.print(),800);">
</script>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{
    font-family: 'Times New Roman', Times, serif;
    font-size: 12pt;
    line-height: 1.7;
    color: #000;
    padding: 2cm 2.5cm;
  }}
  .doc-header {{
    display: flex;
    justify-content: space-between;
    font-size: 11pt;
    font-weight: bold;
    padding-bottom: 6px;
    border-bottom: 1.5px solid #0f3d8e;
    margin-bottom: 16px;
  }}
  .doc-title {{
    text-align: center;
    font-size: 14pt;
    font-weight: bold;
    margin-bottom: 16px;
  }}
  .sec-label {{
    font-size: 13pt;
    font-weight: bold;
    color: #0f3d8e;
    margin: 16px 0 6px;
  }}
  .intro {{ margin-bottom: 8px; }}
  .q-block {{ margin-bottom: 12px; }}
  .q-text {{ font-weight: bold; margin-bottom: 4px; }}
  .q-img {{ max-width: 60%; display: block; margin: 6px 0 6px 20px; }}
  .opts {{
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 2px 12px;
    margin: 4px 0 4px 20px;
    font-weight: normal;
  }}
  .sub {{ margin-left: 20px; }}
  .solution {{
    background: #ebf4ff;
    border-left: 3px solid #93c5fd;
    padding: 5px 10px;
    margin: 4px 0 8px 20px;
    font-size: 11pt;
    font-weight: normal;
  }}
  @media print {{
    @page {{ margin: 2cm 2.5cm; }}
    body {{ padding: 0; }}
    .doc-header {{ position: running(header); }}
    .no-print {{ display: none; }}
    .q-block {{ page-break-inside: avoid; }}
  }}
</style>
</head>
<body>
<div class="doc-header">
  <span>GV: {_esc(teacher)}</span>
  <span>{_esc(school)}</span>
</div>
<div class="doc-title">{_esc(doc.title or "")}</div>
{body_html}
</body>
</html>'''


# ── Xuất file ─────────────────────────────────────────────────────

@app.route('/api/export/pdf', methods=['POST'])
def api_export_pdf():
    data = request.get_json()
    if not data or 'document' not in data:
        return jsonify({'error': 'Thiếu dữ liệu'}), 400
    show_sol = data.get('show_solutions', True)
    settings = get_settings()
    doc = Document.from_dict(data['document'])
    suffix = '_DapAn' if show_sol else '_HocSinh'
    safe = ''.join(c for c in (doc.title or 'TaiLieu') if c.isalnum() or c in ' _-')[:50].strip() or 'TaiLieu'
    out_name = f'{safe}{suffix}.pdf'
    out_path = os.path.join(EXPORT_DIR, out_name)
    try:
        from core.exporter import export_pdf
        export_pdf(doc, out_path, show_sol, settings)
        saved_to = copy_to_destinations(out_path, settings)
        resp = send_file(out_path, as_attachment=True,
                         download_name=out_name,
                         mimetype='application/pdf')
        # Thông báo thư mục đã lưu qua custom header (CORS expose bên dưới)
        resp.headers['X-Saved-Dirs'] = '|'.join(saved_to)
        return resp
    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({'error': f'Lỗi xuất PDF: {str(e)}'}), 500


@app.route('/api/export/print', methods=['POST'])
def api_export_print():
    """Xuất HTML để in qua trình duyệt (backup khi cần in trực tiếp)."""
    data = request.get_json()
    if not data or 'document' not in data:
        return jsonify({'error': 'Thiếu dữ liệu'}), 400
    show_sol = data.get('show_solutions', True)
    settings = get_settings()
    doc = Document.from_dict(data['document'])
    html = _build_print_html(doc, show_sol, settings)
    return html, 200, {'Content-Type': 'text/html; charset=utf-8'}


@app.route('/api/export/word', methods=['POST'])
def api_export_word():
    data = request.get_json()
    if not data or 'document' not in data:
        return jsonify({'error': 'Thiếu dữ liệu'}), 400
    show_sol = data.get('show_solutions', True)
    settings = get_settings()
    doc = Document.from_dict(data['document'])
    safe_title = ''.join(c for c in doc.title if c.isalnum() or c in ' _-').strip()[:50] or 'TaiLieu'
    out_path = os.path.join(EXPORT_DIR, f'{safe_title}.docx')
    try:
        from core.exporter import export_word
        export_word(doc, out_path, show_sol, settings)
        copy_to_destinations(out_path, settings)
        return send_file(out_path, as_attachment=True,
                         download_name=os.path.basename(out_path),
                         mimetype='application/vnd.openxmlformats-officedocument.wordprocessingml.document')
    except Exception as e:
        return jsonify({'error': f'Lỗi xuất Word: {str(e)}'}), 500


@app.route('/api/docx-images', methods=['POST'])
def api_docx_images():
    """Word bài tập → Word chỉ gồm 'Câu N.' + hình của câu đó (đã làm nét).
    Chạy đồng bộ: không gọi AI nặng, chỉ 1 lần hỏi AI gán số câu."""
    if 'file' not in request.files:
        return jsonify({'error': 'Chưa chọn file'}), 400
    f = request.files['file']
    if not f.filename:
        return jsonify({'error': 'Chưa chọn file'}), 400
    if not f.filename.lower().endswith('.docx'):
        return jsonify({'error': 'Chỉ nhận file Word (.docx). '
                                 'File .doc cũ hãy mở Word và Lưu thành .docx.'}), 400

    level = (request.form.get('level') or 'manh').strip()
    base = os.path.splitext(os.path.basename(f.filename))[0]
    safe = ''.join(c for c in base if c.isalnum() or c in ' _-').strip()[:50] or 'BaiTap'

    tmp_path = os.path.join(UPLOAD_DIR, f'{uuid.uuid4().hex[:8]}.docx')
    out_name = f'{safe}_HinhTheoCau.docx'
    out_path = os.path.join(EXPORT_DIR, out_name)
    f.save(tmp_path)
    try:
        s = get_settings()
        from core.docx_images import extract_question_images
        stats = extract_question_images(
            tmp_path, out_path, title=f'HÌNH BÀI TẬP — {base}',
            level=level,
            gemini_key=s.get('gemini_api_key', '') if check_internet() else '',
            niner_url=s.get('niner_router_url', ''),
            niner_key=s.get('niner_router_key', ''))
        stats['saved_to'] = copy_to_destinations(out_path, s)
        stats['filename'] = out_name
        return jsonify(stats)
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({'error': f'Lỗi xử lý file: {e}'}), 500
    finally:
        try:
            os.remove(tmp_path)
        except OSError:
            pass


@app.route('/api/docx-images/result/<path:name>', methods=['GET'])
def api_docx_images_result(name):
    """Tải file kết quả (tên do endpoint trên trả về)."""
    safe = os.path.basename(name)
    path = os.path.join(EXPORT_DIR, safe)
    if not os.path.exists(path):
        return jsonify({'error': 'File không còn trên máy chủ'}), 404
    return send_file(path, as_attachment=True, download_name=safe,
                     mimetype='application/vnd.openxmlformats-officedocument'
                              '.wordprocessingml.document')


@app.route('/api/export/answer_table', methods=['POST'])
def api_export_answer_table():
    data = request.get_json()
    if not data or 'document' not in data:
        return jsonify({'error': 'Thiếu dữ liệu'}), 400
    import openpyxl
    from openpyxl.styles import Font, Alignment, PatternFill, Border, Side

    doc = Document.from_dict(data['document'])
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = 'Bảng đáp án'

    hdr_font  = Font(name='Times New Roman', bold=True, size=12)
    body_font = Font(name='Times New Roman', size=11)
    hdr_fill  = PatternFill('solid', fgColor='0F3D8E')
    hdr_color = Font(name='Times New Roman', bold=True, size=12, color='FFFFFF')
    thin = Side(style='thin')
    border = Border(left=thin, right=thin, top=thin, bottom=thin)

    ws.append(['STT', 'Câu', 'Nội dung câu hỏi (tóm tắt)', 'Đáp án'])
    for cell in ws[1]:
        cell.font = hdr_color
        cell.fill = hdr_fill
        cell.alignment = Alignment(horizontal='center', vertical='center')
        cell.border = border

    ws.column_dimensions['A'].width = 6
    ws.column_dimensions['B'].width = 8
    ws.column_dimensions['C'].width = 60
    ws.column_dimensions['D'].width = 15

    stt = 1
    for section in doc.sections:
        if section.questions:
            ws.append([section.label or section.type])
            ws[ws.max_row][0].font = Font(name='Times New Roman', bold=True, size=11,
                                           color='0F3D8E')
        for q in section.questions:
            short_text = q.text[:80] + ('...' if len(q.text) > 80 else '')
            answer = q.correct_answer or (q.ai_solution[:60] if q.ai_solution else '')
            row = [stt, f'Câu {q.number}', short_text, answer]
            ws.append(row)
            for cell in ws[ws.max_row]:
                cell.font = body_font
                cell.border = border
                cell.alignment = Alignment(wrap_text=True, vertical='top')
            stt += 1

    settings = get_settings()
    safe_title = ''.join(c for c in doc.title if c.isalnum() or c in ' _-').strip()[:50] or 'TaiLieu'
    out_path = os.path.join(EXPORT_DIR, f'BangDapAn_{safe_title}.xlsx')
    wb.save(out_path)
    copy_to_destinations(out_path, settings)
    return send_file(out_path, as_attachment=True,
                     download_name=os.path.basename(out_path),
                     mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')


# ── PDF → Word (Marker + Pandoc) ─────────────────────────────────
# Convert PDF (kể cả scan) sang DOCX, giữ công thức thành Word Equation.
# An toàn RAM: 1 hàng đợi toàn cục + 1 worker tuần tự (mỗi Marker ~11GB RAM)
# nên KHÔNG bao giờ chạy 2 file song song.

import threading as _threading
import queue as _queue

_p2w_jobs = {}                 # job_id -> {files: [...], created}
_p2w_queue = _queue.Queue()    # (job_id, file_index)
_p2w_lock = _threading.Lock()
_p2w_worker_started = False


def _p2w_worker_loop():
    from core.pdf_to_word import convert_pdf_to_word, convert_pdf_to_markdown
    while True:
        job_id, idx = _p2w_queue.get()
        try:
            job = _p2w_jobs.get(job_id)
            if not job:
                continue
            # Tra theo field 'index' (KHÔNG theo vị trí list): file không phải
            # PDF bị bỏ qua lúc build items nên index có thể vượt len(files) —
            # truy cập theo vị trí từng gây IndexError giết chết worker.
            item = next((it for it in job['files'] if it['index'] == idx), None)
            if item is None:
                continue
            item['status'] = 'processing'

            def _cb(stage, msg, _item=item):
                _item['stage'] = stage
                _item['message'] = msg

            try:
                settings = get_settings()
                out_path = os.path.join(EXPORT_DIR, item['out_name'])
                if job.get('output') == 'markdown':
                    # OCR Gemini → Markdown sạch cho NotebookLM
                    result = convert_pdf_to_markdown(
                        item['src_path'], out_md=out_path,
                        page_range=job.get('page_range'),
                        gemini_api_key=settings.get('gemini_api_key', ''),
                        niner_url=settings.get('niner_router_url', 'http://127.0.0.1:20128/v1'),
                        niner_key=settings.get('niner_router_key', ''),
                        progress_cb=_cb)
                    saved = copy_to_destinations(result['md_path'], settings)
                    fp = result.get('failed_pages') or []
                    item['status'] = 'done'
                    item['seconds'] = result['seconds']
                    item['ocr_used'] = True
                    item['detect'] = None
                    item['saved_to'] = saved
                    item['message'] = (f"Xong sau {result['seconds']}s"
                                       if not fp
                                       else f"Xong sau {result['seconds']}s — {len(fp)} trang lỗi: {fp}")
                else:
                    result = convert_pdf_to_word(
                        item['src_path'], out_docx=out_path,
                        ocr_mode=job.get('ocr_mode', 'auto'),
                        method=job.get('method', 'auto'),
                        gemini_api_key=settings.get('gemini_api_key', ''),
                        page_range=job.get('page_range'),
                        niner_url=settings.get('niner_router_url', 'http://127.0.0.1:20128/v1'),
                        niner_key=settings.get('niner_router_key', ''),
                        progress_cb=_cb)
                    # Copy sang Downloads/OneDrive/GDrive
                    saved = copy_to_destinations(result['docx_path'], settings)
                    item['status'] = 'done'
                    item['seconds'] = result['seconds']
                    item['ocr_used'] = result['ocr_used']
                    item['detect'] = result['detect']
                    item['saved_to'] = saved
                    item['message'] = f"Xong sau {result['seconds']}s"
            except Exception as e:
                item['status'] = 'error'
                item['error'] = str(e)
                item['message'] = f"Lỗi: {str(e)[:200]}"
        except Exception as e:
            # Worker là thread duy nhất — tuyệt đối không để chết: nếu chết,
            # mọi job PDF→Word sau đó kẹt "Đang chờ" cho tới khi restart app.
            print(f'[P2W worker] Lỗi ngoài dự kiến (job {job_id}, idx {idx}): {e}', flush=True)
        finally:
            _p2w_queue.task_done()


def _ensure_p2w_worker():
    global _p2w_worker_started
    with _p2w_lock:
        if not _p2w_worker_started:
            t = _threading.Thread(target=_p2w_worker_loop, daemon=True)
            t.start()
            _p2w_worker_started = True


@app.route('/api/pdf-to-word/ready')
def api_p2w_ready():
    """UI gọi để biết Marker/Pandoc đã cài chưa."""
    from core.pdf_to_word import readiness
    return jsonify(readiness())


@app.route('/api/pdf-to-word', methods=['POST'])
def api_pdf_to_word():
    """Nhận 1 hoặc nhiều file PDF → xếp hàng convert nền. Trả job_id."""
    files = request.files.getlist('files') or (
        [request.files['file']] if 'file' in request.files else [])
    if not files:
        return jsonify({'error': 'Không có file'}), 400

    output = request.form.get('output', 'word')   # 'word' (mặc định) | 'markdown'
    from core.pdf_to_word import readiness, find_pandoc, odl_available, marker_available

    if output == 'markdown':
        # OCR Gemini → Markdown cho NotebookLM: không cần Pandoc/Marker, cần internet + key
        if not check_internet():
            return jsonify({'error': 'Không có kết nối internet — OCR Gemini cần mạng.'}), 503
        s = get_settings()
        if not s.get('gemini_api_key') and not s.get('niner_router_key'):
            return jsonify({'error': 'Cần API key Gemini hoặc 9Router để OCR. Vào Cài đặt → AI.'}), 400
        method = 'gemini'
    else:
        if not find_pandoc():
            return jsonify({'error': 'Chưa cài Pandoc.'}), 503
        method = request.form.get('method', 'auto')
        if method in ('odl', 'auto') and not odl_available():
            method = 'marker'
        if method == 'marker' and not marker_available():
            return jsonify({'error': 'Chưa cài Marker và OpenDataLoader. Cần cài ít nhất một trong hai.'}), 503

    ocr_mode = request.form.get('ocr_mode', 'auto')
    page_range = request.form.get('page_range', '').strip() or None
    ext = 'md' if output == 'markdown' else 'docx'
    job_id = uuid.uuid4().hex
    job_dir = os.path.join(UPLOAD_DIR, 'p2w', job_id)
    os.makedirs(job_dir, exist_ok=True)

    items = []
    for i, f in enumerate(files):
        if not f.filename or not f.filename.lower().endswith('.pdf'):
            continue
        src_name = os.path.basename(f.filename)
        src_path = os.path.join(job_dir, f'{i}.pdf')
        f.save(src_path)
        safe = ''.join(c for c in os.path.splitext(src_name)[0]
                       if c.isalnum() or c in ' _-').strip()[:80] or 'TaiLieu'
        items.append({
            'index': i, 'name': src_name, 'src_path': src_path,
            'out_name': f'{safe}.{ext}', 'status': 'pending',
            'stage': '', 'message': 'Đang chờ…',
        })

    if not items:
        return jsonify({'error': 'Không có file PDF hợp lệ'}), 400

    # Dọn entry job cũ hơn 1 ngày (dict này sống suốt vòng đời app)
    cutoff = time.time() - 86400
    for jid in [j for j, jb in _p2w_jobs.items() if jb.get('created', 0) < cutoff]:
        _p2w_jobs.pop(jid, None)

    _p2w_jobs[job_id] = {'files': items, 'ocr_mode': ocr_mode, 'method': method,
                         'page_range': page_range, 'output': output,
                         'created': time.time()}
    _ensure_p2w_worker()
    for it in items:
        _p2w_queue.put((job_id, it['index']))

    return jsonify({'success': True, 'job_id': job_id,
                    'files': [{'index': it['index'], 'name': it['name'],
                               'status': it['status']} for it in items]})


@app.route('/api/pdf-to-word/status/<job_id>')
def api_pdf_to_word_status(job_id):
    job = _p2w_jobs.get(job_id)
    if not job:
        return jsonify({'error': 'Không tìm thấy job'}), 404
    files = [{
        'index': it['index'], 'name': it['name'], 'status': it['status'],
        'stage': it.get('stage', ''), 'message': it.get('message', ''),
        'seconds': it.get('seconds'), 'ocr_used': it.get('ocr_used'),
        'out_name': it['out_name'], 'error': it.get('error'),
    } for it in job['files']]
    all_done = all(it['status'] in ('done', 'error') for it in job['files'])
    return jsonify({'job_id': job_id, 'files': files, 'all_done': all_done})


@app.route('/api/pdf-to-word/result/<job_id>/<int:index>')
def api_pdf_to_word_result(job_id, index):
    job = _p2w_jobs.get(job_id)
    if not job:
        return jsonify({'error': 'Không tìm thấy job'}), 404
    item = next((it for it in job['files'] if it['index'] == index), None)
    if not item or item['status'] != 'done':
        return jsonify({'error': 'File chưa sẵn sàng'}), 404
    out_path = os.path.join(EXPORT_DIR, item['out_name'])
    if not os.path.exists(out_path):
        return jsonify({'error': 'File không tồn tại'}), 404
    if item['out_name'].lower().endswith('.md'):
        mime = 'text/markdown'
    else:
        mime = 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'
    return send_file(out_path, as_attachment=True, download_name=item['out_name'],
                     mimetype=mime)


# ── Ảnh đã xử lý ──────────────────────────────────────────────────

@app.route('/api/images/<filename>')
def api_get_image(filename):
    img_dir = os.path.join(UPLOAD_DIR, 'images')
    img_path = os.path.join(img_dir, filename)
    if os.path.exists(img_path):
        return send_file(img_path)
    return jsonify({'error': 'Không tìm thấy ảnh'}), 404


@app.route('/api/images', methods=['POST'])
def api_upload_image():
    """Upload 1 ảnh (dán/chọn từ máy) → lưu vào uploads/images → trả về metadata
    để gắn vào q.images. Nhận multipart 'file' HOẶC JSON {data: 'data:image/png;base64,...'}."""
    import uuid as _uuid, base64 as _b64, io as _io
    from core.importer import sharpen_image
    img_bytes = None
    try:
        if 'file' in request.files:
            img_bytes = request.files['file'].read()
        else:
            data = request.get_json(silent=True) or {}
            raw = data.get('data', '')
            if ',' in raw:
                raw = raw.split(',', 1)[1]
            if raw:
                img_bytes = _b64.b64decode(raw)
    except Exception as e:
        return jsonify({'error': f'Không đọc được ảnh: {e}'}), 400
    if not img_bytes:
        return jsonify({'error': 'Không có dữ liệu ảnh'}), 400

    fname = f'paste_{_uuid.uuid4().hex[:8]}.png'
    sharpen_image(img_bytes, fname)
    w = h = 0
    try:
        from PIL import Image as _PILImage
        with _PILImage.open(_io.BytesIO(img_bytes)) as _im:
            w, h = _im.size
    except Exception:
        pass
    return jsonify({'filename': fname, 'width': w, 'height': h})


# ── Cài đặt ───────────────────────────────────────────────────────

@app.route('/api/settings', methods=['GET'])
def api_get_settings():
    s = get_settings()
    # Ẩn API key khi trả về (chỉ hiện nếu rỗng)
    masked = {k: ('***' if 'key' in k and v else v) for k, v in s.items()}
    return jsonify(masked)


@app.route('/api/settings', methods=['PUT'])
def api_update_settings():
    data = request.get_json()
    if not data:
        return jsonify({'error': 'Không có dữ liệu'}), 400
    current = get_settings()
    filtered = {}
    for k, v in data.items():
        is_key_field = 'key' in k
        existing = current.get(k, '')
        # Bỏ qua nếu: gửi '***' hoặc '' về cho trường key đang có giá trị
        if is_key_field and existing and v in ('***', ''):
            continue
        filtered[k] = v
    update_settings(filtered)
    return jsonify({'success': True})


@app.route('/api/settings/test-ai', methods=['POST'])
def api_test_ai():
    data = request.get_json() or {}
    api_type = data.get('type', 'gemini')
    api_key  = data.get('key', '')
    # Nếu ô key đang bị che (***) hoặc trống → dùng key đã lưu trong cài đặt
    if not api_key or api_key == '***':
        _s = get_settings()
        _key_field = {'gemini': 'gemini_api_key', 'claude': 'claude_api_key',
                      'niner': 'niner_router_key'}.get(api_type)
        if _key_field:
            api_key = _s.get(_key_field, '') or ''
    if not api_key:
        return jsonify({'ok': False, 'message': 'API key trống'})
    if not check_internet():
        return jsonify({'ok': False, 'message': 'Không có kết nối internet'})
    try:
        if api_type == 'gemini':
            import google.generativeai as genai
            genai.configure(api_key=api_key)
            # Thử lần lượt các model từ nhẹ đến nặng để kiểm tra key
            for model_name in ('gemini-2.5-flash', 'gemini-2.0-flash'):
                try:
                    m = genai.GenerativeModel(model_name)
                    r = m.generate_content('Trả lời đúng 1 từ: Vật lý')
                    return jsonify({'ok': True, 'message': f'Kết nối thành công ({model_name}): {r.text.strip()[:50]}'})
                except Exception as inner:
                    err = str(inner)
                    if '429' not in err and 'quota' not in err.lower() and 'not found' not in err.lower():
                        raise
            return jsonify({'ok': False, 'message': 'API key hợp lệ nhưng đã hết quota free tier. Thử lại sau vài phút.'})
        elif api_type == 'claude':
            import anthropic
            c = anthropic.Anthropic(api_key=api_key)
            msg = c.messages.create(model='claude-haiku-4-5-20251001', max_tokens=10,
                                    messages=[{'role': 'user', 'content': 'Trả lời 1 từ: Vật lý'}])
            return jsonify({'ok': True, 'message': f'Kết nối thành công: {msg.content[0].text}'})
        elif api_type == 'niner':
            import requests as req, re as _re
            base_url = data.get('url', 'http://localhost:20128/v1')
            model    = data.get('model', 'ag/gemini-3.7-flash-low')
            r = req.post(f'{base_url}/chat/completions',
                headers={'Authorization': f'Bearer {api_key}', 'Content-Type': 'application/json'},
                json={'model': model,
                      'messages': [{'role': 'user', 'content': 'Trả lời 1 từ: Vật lý'}],
                      'max_tokens': 2000, 'stream': False},
                timeout=60)
            if r.status_code == 200:
                raw = r.text.strip()
                if raw.startswith('data:'):
                    # SSE format — collect chunks
                    parts = []
                    for line in raw.splitlines():
                        line = line.strip()
                        if not line.startswith('data:'):
                            continue
                        d = line[5:].strip()
                        if d == '[DONE]':
                            break
                        try:
                            parts.append(json.loads(d)['choices'][0]['delta'].get('content', ''))
                        except Exception:
                            pass
                    msg = ''.join(parts).strip()
                else:
                    msg = json.loads(raw)['choices'][0]['message']['content']
                return jsonify({'ok': True, 'message': f'9Router OK ({model}): {msg.strip()[:50]}'})
            return jsonify({'ok': False, 'message': f'Lỗi {r.status_code}: {r.text[:100]}'})
    except Exception as e:
        return jsonify({'ok': False, 'message': str(e)})


@app.route('/api/status', methods=['GET'])
def api_status():
    return jsonify({'online': check_internet(), 'version': '1.0'})


# ── Upload logo ───────────────────────────────────────────────────

@app.route('/api/settings/logo', methods=['POST'])
def api_upload_logo():
    if 'logo' not in request.files:
        return jsonify({'error': 'Không có file'}), 400
    f = request.files['logo']
    ext = f.filename.rsplit('.', 1)[-1].lower() if '.' in f.filename else 'png'
    if ext not in ('png', 'jpg', 'jpeg'):
        return jsonify({'error': 'Chỉ chấp nhận PNG hoặc JPG'}), 400
    # Lưu đè vào static/logo.png
    logo_path = os.path.join(os.path.dirname(__file__), 'static', 'logo.png')
    f.save(logo_path)
    return jsonify({'success': True})


if __name__ == '__main__':
    import webbrowser, threading
    def open_browser():
        import time; time.sleep(1.2)
        webbrowser.open('http://localhost:5000')
    threading.Thread(target=open_browser, daemon=True).start()
    print('=' * 55)
    print('  Ứng dụng Soạn Tài Liệu Vật Lý đang chạy...')
    print('  Truy cập: http://localhost:5000')
    print('  Nhấn Ctrl+C để tắt.')
    print('=' * 55)
    app.run(host='127.0.0.1', port=5000, debug=False)
