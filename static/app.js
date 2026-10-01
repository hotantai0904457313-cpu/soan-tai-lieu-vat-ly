/**
 * app.js — Logic frontend ứng dụng Soạn Tài Liệu Vật Lý
 */

// ── State ─────────────────────────────────────────────────────────
const state = {
  currentDoc: null,      // Document dict hiện tại
  savedDocId: null,      // ID đã lưu trong DB
  isOnline: true,
  isSidebarOpen: false,
  showAllSolutions: true,
  isAIPanelOpen: true,
  hasUnsavedChanges: false,
  sidebarGradeFilter: null,  // null = tất cả
};

// ── KaTeX render helper ───────────────────────────────────────────
function rerenderMath(el) {
  if (!el || typeof renderMathInElement === 'undefined') return;
  renderMathInElement(el, {
    delimiters: [{ left: '$', right: '$', display: false }],
    throwOnError: false,
    strict: false,
  });
}

// ── DOM ───────────────────────────────────────────────────────────
const $ = id => document.getElementById(id);
const el = (tag, cls, html) => {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (html !== undefined) e.innerHTML = html;
  return e;
};

// ── Init ──────────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
  checkStatus();
  setInterval(checkStatus, 30000);
  loadDocumentList();
  buildMathPanel?.();
  // Disable "Thông tin" khi chưa có tài liệu
  const btnInfo = $('btn-doc-info');
  if (btnInfo) btnInfo.disabled = true;
  // Đóng export menu khi click ra ngoài
  document.addEventListener('click', closeAllExportMenus);
});

// Chặn đóng tab / F5 khi còn thay đổi chưa lưu
window.addEventListener('beforeunload', (e) => {
  if (state.hasUnsavedChanges) {
    e.preventDefault();
    e.returnValue = '';
  }
});

// Hỏi xác nhận trước khi bỏ các thay đổi chưa lưu (mở/nhập tài liệu khác)
function confirmDiscardChanges() {
  if (!state.hasUnsavedChanges) return true;
  return confirm('Tài liệu đang có thay đổi CHƯA LƯU.\nTiếp tục sẽ mất các thay đổi này — vẫn tiếp tục?');
}

// ── Kiểm tra kết nối ─────────────────────────────────────────────
async function checkStatus() {
  try {
    const r = await fetch('/api/status');
    const data = await r.json();
    state.isOnline = data.online;
  } catch {
    state.isOnline = false;
  }
  const dot  = $('status-dot');
  const text = $('status-text');
  if (dot) dot.className = 'status-dot' + (state.isOnline ? '' : ' offline');
  if (text) text.textContent = state.isOnline ? 'Trực tuyến' : 'Ngoại tuyến';

  // Disable AI buttons khi offline
  document.querySelectorAll('.ai-btn').forEach(btn => {
    btn.disabled = !state.isOnline;
    btn.title = state.isOnline ? '' : 'Không có kết nối internet';
  });
}

// ── Toast ─────────────────────────────────────────────────────────
function showToast(msg, type = 'info', duration = 3000) {
  const container = $('toast-container');
  const toast = el('div', `toast ${type}`, msg);
  container.appendChild(toast);
  setTimeout(() => toast.remove(), duration);
}

// ── Loading overlay ───────────────────────────────────────────────
function showLoading(text = 'Đang xử lý...') {
  $('loading-overlay').classList.add('show');
  $('loading-text').textContent = text;
}
function hideLoading() {
  $('loading-overlay').classList.remove('show');
}

// ── Import file — 3 loại riêng biệt ─────────────────────────────
function triggerImportImg()  { $('file-img').click(); }
function triggerImportPdf()  { $('file-pdf').click(); }
function triggerImportWord() { $('file-word').click(); }

let pendingPdfFile = null;   // file PDF đang chờ chọn trang

async function onFileSelectedAny(input, fileType) {
  const file = input.files[0];
  if (!file) return;
  input.value = '';
  // PDF → mở modal chọn trang trước khi import
  if (fileType === 'pdf') {
    pendingPdfFile = file;
    $('import-page-fname').textContent = file.name;
    $('import-page-enable').checked = false;
    importTogglePageRange(false);
    if ($('import-pages')) $('import-pages').value = '';
    $('import-page-modal').classList.add('show');
    return;
  }
  await handleFileImport(file, fileType);
}

function importTogglePageRange(checked) {
  const box = $('import-page-input');
  if (box) box.style.display = checked ? 'flex' : 'none';
  if (!checked && $('import-pages')) $('import-pages').value = '';
}

async function confirmImportPdf() {
  const file = pendingPdfFile;
  if (!file) { closeModal('import-page-modal'); return; }
  let pageRange = '';
  if ($('import-page-enable') && $('import-page-enable').checked) {
    pageRange = ($('import-pages').value || '').trim();
  }
  closeModal('import-page-modal');
  pendingPdfFile = null;
  await handleFileImport(file, 'pdf', pageRange);
}

async function handleFileImport(file, fileType, pageRange = '') {
  if (!confirmDiscardChanges()) return;
  const loadingMessages = {
    image: 'AI đang nhận diện công thức từ ảnh...',
    pdf:   'AI đang đọc PDF và nhận diện công thức...',
    word:  'Pandoc + AI đang xử lý Word, giữ nguyên công thức...',
  };
  showLoading(loadingMessages[fileType] || 'Đang xử lý...');

  // Client-side JSZip extraction trước khi upload
  state.clientImages = null;
  if (fileType === 'word' && window.JSZip) {
    state.clientImages = await extractDocxImagesClientSide(file);
  }

  const formData = new FormData();
  formData.append('file', file);
  if (pageRange) formData.append('page_range', pageRange);

  try {
    const r = await fetch('/api/import', { method: 'POST', body: formData });
    const data = await r.json();
    if (data.error) { showToast(data.error, 'error'); return; }

    state.currentDoc   = data.document;
    state.savedDocId   = null;
    state.originalFile = data.original_file || null;
    state.originalExt  = data.original_ext  || null;
    state.fileType     = fileType;

    renderOriginalPanel(file, data.document, data.original_file, data.original_ext);
    renderEditorPanel(data.document);

    const methodLabels = {
      pandoc_gemini: ' — Pandoc + AI (công thức giữ nguyên)',
      pandoc:        ' — Pandoc (công thức giữ nguyên)',
      vision:        ' — AI đọc ảnh',
      image_vision:  ' — AI nhận diện từ ảnh',
      standard:      '',
    };
    const label = methodLabels[data.import_method] || '';
    const qc = countQuestions(data.document);
    showToast(`Đã nhập: ${file.name}${qc ? ' (' + qc + ' câu)' : ''}${label}`, 'success', 4000);
    if (data.import_warning) showToast(data.import_warning, 'warning', 15000);

    const btnInfo = $('btn-doc-info');
    if (btnInfo) btnInfo.disabled = false;
  } catch (e) {
    showToast('Lỗi kết nối server: ' + e.message, 'error');
  } finally {
    hideLoading();
  }
}

// ── Client-side image extraction từ DOCX (JSZip) ─────────────────
async function extractDocxImagesClientSide(file) {
  if (!window.JSZip) return null;
  try {
    const zip = await JSZip.loadAsync(file);
    const images = {};
    for (const [path, entry] of Object.entries(zip.files)) {
      if (path.startsWith('word/media/') && !entry.dir) {
        const blob = await entry.async('blob');
        images[path.replace('word/media/', '')] = URL.createObjectURL(blob);
      }
    }
    return Object.keys(images).length > 0 ? images : null;
  } catch { return null; }
}

function countQuestions(doc) {
  return (doc.sections || []).reduce((acc, s) => acc + (s.questions || []).length, 0);
}

// ── Render panel trái (file gốc) ──────────────────────────────────
function renderOriginalPanel(file, doc, originalFile, originalExt) {
  const body = $('original-body');
  body.innerHTML = '';

  // Ảnh trực tiếp (JPG/PNG/...) → hiển thị img
  const IMG_EXTS = ['jpg','jpeg','png','gif','webp','tiff','bmp'];
  if (originalFile && IMG_EXTS.includes(originalExt)) {
    body.style.cssText = 'overflow:auto;display:flex;align-items:center;justify-content:center;padding:12px;';
    const imgEl = document.createElement('img');
    imgEl.src = `/api/original/${encodeURIComponent(originalFile)}`;
    imgEl.style.cssText = 'max-width:100%;max-height:calc(100vh - 140px);object-fit:contain;border-radius:6px;box-shadow:0 2px 12px rgba(0,0,0,.15);cursor:zoom-in;';
    imgEl.onclick = () => zoomImage(imgEl.src);
    imgEl.title = 'Click để phóng to';
    body.appendChild(imgEl);
    return;
  }

  // PDF hoặc HTML (Pandoc+KaTeX) → iframe
  if (originalFile && (originalExt === 'pdf' || originalExt === 'html')) {
    body.style.cssText = 'padding:0;overflow:hidden;display:flex;flex-direction:column;';
    const iframe = document.createElement('iframe');
    iframe.src = `/api/original/${encodeURIComponent(originalFile)}`;
    iframe.style.cssText = 'width:100%;flex:1;min-height:500px;border:none;display:block;';
    iframe.title = doc.title || (file && file.name) || '';
    body.appendChild(iframe);
    // Hiển thị ảnh JSZip trích xuất (DOCX→HTML)
    if (state.clientImages && originalExt === 'html') {
      const imgBar = el('div', 'client-images-bar');
      imgBar.appendChild(el('span', '', '📎 Ảnh trong file:'));
      Object.entries(state.clientImages).forEach(([name, url]) => {
        const thumb = document.createElement('img');
        thumb.src = url;
        thumb.title = name;
        thumb.onclick = () => zoomImage(url);
        imgBar.appendChild(thumb);
      });
      body.appendChild(imgBar);
    }
    // Hiển thị ảnh đã trích xuất từ PDF (server-side via PyMuPDF)
    if (originalExt === 'pdf') {
      const pdfImages = [];
      (doc.sections || []).forEach(sec =>
        (sec.questions || []).forEach(q =>
          (q.images || []).forEach(img => {
            if (img.filename && !pdfImages.includes(img.filename))
              pdfImages.push(img.filename);
          })
        )
      );
      if (pdfImages.length > 0) {
        const imgBar = el('div', 'client-images-bar');
        imgBar.appendChild(el('span', '', `📎 Ảnh trong PDF (${pdfImages.length}):`));
        pdfImages.forEach(fname => {
          const thumb = document.createElement('img');
          thumb.src = `/api/images/${encodeURIComponent(fname)}`;
          thumb.title = 'Click để phóng to · ' + fname;
          thumb.onclick = () => zoomImage(thumb.src);
          imgBar.appendChild(thumb);
        });
        body.appendChild(imgBar);
      }
    }
    return;
  }

  // DOCX hoặc không có file gốc → render text như cũ
  body.style.cssText = '';
  const titleEl = el('h2', 'doc-title-display', doc.title || (file && file.name) || '');
  titleEl.style.cssText = 'font-family:var(--font-doc);font-size:15pt;font-weight:bold;color:var(--navy);margin-bottom:12px;padding-bottom:8px;border-bottom:2px solid var(--border)';
  body.appendChild(titleEl);

  (doc.sections || []).forEach(sec => {
    if (sec.label) {
      const hdr = el('div', 'section-header', sec.label);
      hdr.style.pointerEvents = 'none';
      body.appendChild(hdr);
    }
    if (sec.intro) {
      const intro = el('p', '', sec.intro);
      intro.style.cssText = 'margin-bottom:8px;font-family:var(--font-doc);font-size:14pt;';
      body.appendChild(intro);
    }
    (sec.questions || []).forEach(q => {
      const card = el('div', 'question-card');
      card.style.pointerEvents = 'none';
      const head = el('div', 'question-head');
      head.innerHTML = `<div class="q-number">${q.number}</div><div class="q-text">${escHtml(q.text)}</div>`;
      card.appendChild(head);
      if (q.options && q.options.length) {
        const opts = el('div', 'q-options');
        q.options.forEach(o => opts.appendChild(el('div', 'q-option', escHtml(o))));
        card.appendChild(opts);
      }
      if (q.sub_items && q.sub_items.length) {
        const subs = el('div', 'q-subitems');
        q.sub_items.forEach(s => subs.appendChild(el('div', 'q-subitem', escHtml(s))));
        card.appendChild(subs);
      }
      body.appendChild(card);
    });
  });

  // Render LaTeX trong panel trái
  requestAnimationFrame(() => rerenderMath(body));

  // Scroll sync chỉ cho chế độ text — syncScroll là cùng 1 tham chiếu hàm nên
  // addEventListener lặp lại được trình duyệt tự khử trùng (không rò listener)
  $('editor-content').addEventListener('scroll', syncScroll, { passive: true });
}

function syncScroll() {
  const src = $('editor-content');
  const dst = $('original-body');
  if (!dst) return;
  const ratio = src.scrollTop / (src.scrollHeight - src.clientHeight || 1);
  dst.scrollTop = ratio * (dst.scrollHeight - dst.clientHeight);
}

// ── Render panel phải (editor) ────────────────────────────────────
// opts.preserveUnsaved: giữ nguyên cờ "chưa lưu" (khi vẽ lại giữa phiên chỉnh
// sửa, vd sau khi thêm ảnh) — chỉ reset cờ khi thực sự mở/nhập tài liệu mới.
// opts.preserveScroll: giữ vị trí cuộn hiện tại.
function renderEditorPanel(doc, opts = {}) {
  const content = $('editor-content');
  const prevScroll = opts.preserveScroll ? content.scrollTop : 0;
  content.innerHTML = '';

  // Cập nhật badge số câu
  const badge = $('q-count-badge');
  if (badge) badge.textContent = countQuestions(doc) + ' câu';

  if (!opts.preserveUnsaved) {
    state.hasUnsavedChanges = false;
    updateUnsavedIndicator();
  }
  updateDocInfoBadge();
  loadCurriculum().then(updateScopeChip);   // chip phạm vi theo tài liệu đang mở

  // Bật nút "Thông tin" khi có tài liệu
  const btnInfo = $('btn-doc-info');
  if (btnInfo) btnInfo.disabled = false;

  // Tiêu đề
  const titleInput = el('input', 'doc-title-input');
  titleInput.type = 'text';
  titleInput.value = doc.title || 'Tài liệu mới';
  titleInput.placeholder = 'Tiêu đề tài liệu...';
  titleInput.addEventListener('input', () => {
    if (state.currentDoc) state.currentDoc.title = titleInput.value;
    markUnsaved();
  });
  content.appendChild(titleInput);

  (doc.sections || []).forEach((sec, secIdx) => {
    // Section header
    if (sec.label) {
      const hdr = el('div', 'section-header', escHtml(sec.label));
      hdr.contentEditable = 'true';
      hdr.addEventListener('input', () => {
        state.currentDoc.sections[secIdx].label = hdr.textContent;
      });
      content.appendChild(hdr);
    }

    // Theory block — giữ nguyên
    if (sec.is_theory && sec.intro) {
      const theory = el('div', 'theory-block');
      theory.innerHTML = `<div class="theory-badge">📖 Lý thuyết (giữ nguyên)</div>${escHtml(sec.intro)}`;
      theory.querySelector('.theory-badge').style.pointerEvents = 'none';
      const textDiv = el('div');
      textDiv.contentEditable = 'false';
      textDiv.textContent = sec.intro;
      theory.addEventListener('dblclick', () => {
        showToast('Phần lý thuyết chỉ thay đổi khi bạn yêu cầu rõ ràng.', 'warning');
      });
      content.appendChild(theory);
    } else if (sec.intro) {
      const intro = el('div');
      intro.contentEditable = 'true';
      intro.style.cssText = 'margin-bottom:10px;font-family:var(--font-doc);font-size:14pt;outline:none;padding:4px;border-radius:4px;';
      intro.textContent = sec.intro;
      intro.addEventListener('input', () => {
        state.currentDoc.sections[secIdx].intro = intro.textContent;
      });
      content.appendChild(intro);
    }

    // Câu hỏi
    (sec.questions || []).forEach((q, qIdx) => {
      content.appendChild(buildQuestionCard(q, secIdx, qIdx));
    });
  });

  // Nếu chưa có tài liệu, hiện empty state
  if (!doc.sections || doc.sections.length === 0) {
    content.appendChild(buildEmptyState());
  }

  // Render LaTeX sau khi build xong toàn bộ editor
  requestAnimationFrame(() => rerenderMath(content));

  // Khôi phục vị trí cuộn (tránh nhảy về đầu trang khi vẽ lại giữa phiên sửa)
  if (opts.preserveScroll) content.scrollTop = prevScroll;
}

// Upload 1 ảnh (blob/file) lên server → gắn vào q.images → vẽ lại editor
// option_images song song với options (tài liệu cũ không có trường này)
function ensureOptionImages(q) {
  const n = (q.options || []).length;
  q.option_images = (q.option_images || []).slice(0, n).map(x => Array.isArray(x) ? x : []);
  while (q.option_images.length < n) q.option_images.push([]);
  return q.option_images;
}

async function addImageToOption(q, idx, blob) {
  if (!blob) return;
  try {
    showToast('Đang tải ảnh lên…', 'info', 1500);
    const fd = new FormData();
    fd.append('file', blob, 'paste.png');
    const r = await fetch('/api/images', { method: 'POST', body: fd });
    const data = await r.json();
    if (!r.ok || !data.filename) throw new Error(data.error || 'Lỗi tải ảnh');
    ensureOptionImages(q);
    q.option_images[idx].push({ id: (data.filename || '').slice(0, 8), filename: data.filename,
                                width: data.width || 0, height: data.height || 0, caption: '' });
    markUnsaved();
    renderEditorPanel(state.currentDoc, { preserveUnsaved: true, preserveScroll: true });
    showToast('Đã thêm hình cho phương án ' + getLetter(idx), 'success');
  } catch (e) {
    showToast('Không thêm được ảnh: ' + e.message, 'error');
  }
}

async function addImageToQuestion(q, blob) {
  if (!blob) return;
  try {
    showToast('Đang tải ảnh lên…', 'info', 1500);
    const fd = new FormData();
    fd.append('file', blob, 'paste.png');
    const r = await fetch('/api/images', { method: 'POST', body: fd });
    const data = await r.json();
    if (!r.ok || !data.filename) throw new Error(data.error || 'Lỗi tải ảnh');
    q.images = q.images || [];
    q.images.push({ id: (data.filename || '').slice(0, 8), filename: data.filename,
                    width: data.width || 0, height: data.height || 0, caption: '' });
    markUnsaved();
    // preserveUnsaved: KHÔNG để render reset cờ "chưa lưu" — thầy tưởng đã lưu
    // rồi đóng app là mất ảnh + mọi chỉnh sửa trước đó
    renderEditorPanel(state.currentDoc, { preserveUnsaved: true, preserveScroll: true });
    showToast('Đã thêm ảnh vào câu hỏi — AI sẽ đọc được ảnh này khi giải', 'success');
  } catch (e) {
    showToast('Không thêm được ảnh: ' + e.message, 'error');
  }
}

function buildQuestionCard(q, secIdx, qIdx) {
  const card = el('div', 'question-card');
  card.id = `card-${q.id}`;
  card.dataset.qid = q.id;

  const head = el('div', 'question-head');

  const num = el('div', 'q-number', q.number);

  const textDiv = el('div', 'q-text');
  textDiv.contentEditable = 'true';
  textDiv.textContent = q.text;
  // Focus: restore raw LaTeX để chỉnh sửa; Blur: render lại KaTeX
  textDiv.addEventListener('focus', () => { textDiv.textContent = q.text; saveCaret?.(textDiv); });
  // innerText giữ xuống dòng giữa đề và các phương án (textContent nối dính)
  textDiv.addEventListener('input', () => { q.text = textDiv.innerText.replace(/\u00a0/g, ' ').replace(/\n+$/, ''); markUnsaved(); });
  textDiv.addEventListener('blur', () => rerenderMath(textDiv));
  // Dán ảnh (Ctrl+V) trực tiếp vào câu hỏi → đăng ký vào q.images để AI đọc được
  textDiv.addEventListener('paste', (e) => {
    const items = e.clipboardData && e.clipboardData.items;
    if (!items) return;
    for (const it of items) {
      if (it.type && it.type.startsWith('image/')) {
        e.preventDefault();
        addImageToQuestion(q, it.getAsFile());
        return;
      }
    }
  });

  const actions = el('div', 'q-actions');

  // Nút giải lại câu này
  const btnSolve = el('button', 'q-act-btn q-solve-btn', '🤖');
  btnSolve.title = 'Giải lại câu này bằng AI';
  btnSolve.addEventListener('click', () => solveOne(q, card, btnSolve));

  // Nút thêm ảnh (chọn file) — ảnh sẽ được AI đọc khi giải
  const btnImg = el('button', 'q-act-btn', '🖼');
  btnImg.title = 'Thêm ảnh/đồ thị vào câu (AI sẽ đọc ảnh khi giải)';
  btnImg.addEventListener('click', () => {
    const inp = document.createElement('input');
    inp.type = 'file';
    inp.accept = 'image/*';
    inp.addEventListener('change', () => {
      if (inp.files && inp.files[0]) addImageToQuestion(q, inp.files[0]);
    });
    inp.click();
  });

  const btnToggle = el('button', 'q-act-btn', q.show_solution ? '👁' : '🙈');
  btnToggle.title = 'Ẩn/hiện lời giải';
  btnToggle.addEventListener('click', () => toggleSolution(q, btnToggle, card));

  // Nút tách phương án A/B/C/D đang dính trong đề ra thành danh sách riêng
  const btnSplit = el('button', 'q-act-btn', '⤴');
  btnSplit.title = 'Tách phương án A/B/C/D đang nằm trong đề';
  btnSplit.addEventListener('click', () => {
    const r = splitOptionsFromText(q.text, q.options);
    if (!r) { showToast('Không thấy khối A./B./C./D. trong đề (hoặc câu đã có phương án)'); return; }
    q.text = r.text; q.options = r.options;
    markUnsaved();
    renderEditorPanel(state.currentDoc, { preserveUnsaved: true, preserveScroll: true });
  });

  actions.appendChild(btnSolve);
  actions.appendChild(btnImg);
  actions.appendChild(btnSplit);
  actions.appendChild(btnToggle);
  head.appendChild(num);
  head.appendChild(textDiv);
  head.appendChild(actions);
  card.appendChild(head);

  // Options (trắc nghiệm A/B/C/D) — editable + đánh dấu đáp án đúng
  if (q.options && q.options.length) {
    const optsDiv = el('div', 'q-options-edit');
    q.options.forEach((o, idx) => {
      const row = el('div', 'q-option-row' + (q.correct_answer === getLetter(idx) ? ' correct' : ''));
      row.dataset.idx = idx;

      // Nút đánh dấu đáp án đúng
      const marker = el('button', 'q-opt-marker', getLetter(idx));
      marker.title = 'Đánh dấu là đáp án đúng';
      marker.addEventListener('click', () => {
        const letter = getLetter(idx);
        if (q.correct_answer === letter) {
          q.correct_answer = '';
        } else {
          q.correct_answer = letter;
        }
        optsDiv.querySelectorAll('.q-option-row').forEach((r, i) => {
          r.classList.toggle('correct', q.correct_answer === getLetter(i));
          r.querySelector('.q-opt-marker').textContent = getLetter(i);
        });
        markUnsaved();
      });

      // Text option — editable, bỏ prefix "A. " nếu có để sửa phần nội dung
      const textDiv = el('div', 'q-option-text');
      textDiv.contentEditable = 'true';
      let rawOpt = o.replace(/^\**\s*\(?([A-Da-d])\)?\**\s*\\?[.):]?\**\s*/, '');
      textDiv.textContent = rawOpt;
      textDiv.addEventListener('focus', () => { textDiv.textContent = rawOpt; });
      textDiv.addEventListener('input', () => {
        rawOpt = textDiv.textContent;
        q.options[idx] = getLetter(idx) + '. ' + rawOpt;
        markUnsaved();
      });
      textDiv.addEventListener('blur', () => rerenderMath(textDiv));

      // Hình của phương án (câu "4 phương án là 4 đồ thị"): thumbnail + ✕ + nút thêm
      ensureOptionImages(q);
      const optImgs = el('div', 'q-opt-images');
      (q.option_images[idx] || []).forEach((img, j) => {
        const w = el('span', 'q-opt-img');
        const im = document.createElement('img');
        im.src = `/api/images/${img.filename}`;
        im.title = 'Hình của phương án ' + getLetter(idx) + ' — bấm để phóng to';
        im.addEventListener('click', () => zoomImage(im.src));
        const x = el('button', 'q-opt-img-x', '✕');
        x.title = 'Bỏ hình này';
        x.addEventListener('click', (e) => {
          e.stopPropagation();
          q.option_images[idx].splice(j, 1);
          markUnsaved();
          renderEditorPanel(state.currentDoc, { preserveUnsaved: true, preserveScroll: true });
        });
        w.appendChild(im); w.appendChild(x); optImgs.appendChild(w);
      });
      const addImg = el('button', 'q-opt-img-add', '🖼');
      addImg.title = 'Thêm hình cho phương án ' + getLetter(idx);
      addImg.addEventListener('click', () => {
        const inp = document.createElement('input');
        inp.type = 'file'; inp.accept = 'image/*';
        inp.addEventListener('change', () => {
          if (inp.files && inp.files[0]) addImageToOption(q, idx, inp.files[0]);
        });
        inp.click();
      });
      optImgs.appendChild(addImg);

      row.appendChild(marker);
      row.appendChild(textDiv);
      row.appendChild(optImgs);
      optsDiv.appendChild(row);
    });
    card.appendChild(optsDiv);
  }

  // Sub-items (đúng-sai a/b/c/d) — editable
  if (q.sub_items && q.sub_items.length) {
    const subsDiv = el('div', 'q-subitems-edit');
    q.sub_items.forEach((s, idx) => {
      const row = el('div', 'q-subitem-row');
      const label = el('span', 'q-subitem-label', String.fromCharCode(97 + idx) + ')');
      const textDiv = el('div', 'q-subitem-text');
      textDiv.contentEditable = 'true';
      let rawSub = s.replace(/^[a-d]\s*[.)]\s*/, '');
      textDiv.textContent = rawSub;
      textDiv.addEventListener('focus', () => { textDiv.textContent = rawSub; });
      textDiv.addEventListener('input', () => {
        rawSub = textDiv.textContent;
        q.sub_items[idx] = String.fromCharCode(97 + idx) + '. ' + rawSub;
        markUnsaved();
      });
      textDiv.addEventListener('blur', () => rerenderMath(textDiv));
      row.appendChild(label);
      row.appendChild(textDiv);
      subsDiv.appendChild(row);
    });
    card.appendChild(subsDiv);
  }

  // Hình ảnh
  if (q.images && q.images.length) {
    q.images.forEach((img, idx) => {
      const wrapper = document.createElement('div');
      wrapper.style.cssText = 'position:relative;display:inline-block;margin:6px 14px;max-width:calc(100% - 28px);';

      const imgEl = document.createElement('img');
      imgEl.src = `/api/images/${img.filename}`;
      imgEl.style.cssText = 'max-width:100%;border-radius:4px;cursor:zoom-in;display:block;';
      imgEl.addEventListener('click', () => zoomImage(imgEl.src));

      const delBtn = document.createElement('button');
      delBtn.textContent = '✕';
      delBtn.title = 'Xóa hình này';
      delBtn.style.cssText = 'position:absolute;top:4px;right:4px;background:rgba(0,0,0,0.6);color:#fff;border:none;border-radius:50%;width:22px;height:22px;cursor:pointer;font-size:12px;display:flex;align-items:center;justify-content:center;padding:0;';
      delBtn.addEventListener('click', (e) => {
        e.stopPropagation();
        q.images.splice(idx, 1);
        markUnsaved();
        wrapper.remove();
      });

      wrapper.appendChild(imgEl);
      wrapper.appendChild(delBtn);
      card.appendChild(wrapper);
    });
  }

  // Solution box
  const solBox = buildSolutionBox(q);
  card.appendChild(solBox);

  return card;
}

function buildSolutionBox(q) {
  const box = el('div', `solution-box${q.show_solution ? '' : ' hidden'}`);
  box.id = `sol-${q.id}`;
  const label = el('div', 'sol-label', '🤖 Lời giải AI');
  const text = el('div', 'sol-text');
  text.contentEditable = 'true';
  text.textContent = q.ai_solution || '(Chưa có lời giải — bấm Giải toàn đề)';
  text.style.color = q.ai_solution ? '#1e3a6e' : '#9ca3af';
  if (q.ai_solution) rerenderMath(text);
  text.addEventListener('input', () => { q.ai_solution = text.textContent; markUnsaved(); });
  box.appendChild(label);
  box.appendChild(text);
  return box;
}

function toggleSolution(q, btn, card) {
  q.show_solution = !q.show_solution;
  btn.textContent = q.show_solution ? '👁' : '🙈';
  const box = card.querySelector('.solution-box');
  if (box) box.classList.toggle('hidden', !q.show_solution);
}

// ── Ẩn/Hiện tất cả ───────────────────────────────────────────────
function toggleAllSolutions(show) {
  state.showAllSolutions = show;
  if (!state.currentDoc) return;
  state.currentDoc.sections?.forEach(sec => {
    sec.questions?.forEach(q => {
      q.show_solution = show;
      const card = document.getElementById(`card-${q.id}`);
      if (!card) return;
      // :not(.q-solve-btn) — nút đầu tiên là nút Giải 🤖, không được đổi icon nó
      const btn = card.querySelector('.q-act-btn:not(.q-solve-btn)');
      if (btn) btn.textContent = show ? '👁' : '🙈';
      const box = card.querySelector('.solution-box');
      if (box) box.classList.toggle('hidden', !show);
    });
  });
}

// ── Unsaved changes indicator ─────────────────────────────────────
function markUnsaved() {
  state.hasUnsavedChanges = true;
  updateUnsavedIndicator();
}

function updateUnsavedIndicator() {
  const btn = document.querySelector('.tb-btn[onclick="saveDocument()"]');
  if (!btn) return;
  if (state.hasUnsavedChanges) {
    btn.style.background = 'rgba(251,191,36,.25)';
    btn.style.borderColor = '#fbbf24';
    btn.title = 'Có thay đổi chưa lưu — bấm để lưu (Ctrl+S)';
  } else {
    btn.style.background = '';
    btn.style.borderColor = '';
    btn.title = '';
  }
}

// ── Lưu tài liệu ─────────────────────────────────────────────────
function saveDocument() {
  if (!state.currentDoc) { showToast('Chưa có tài liệu để lưu', 'warning'); return; }
  if (!state.savedDocId) {
    // Tài liệu mới — hiện modal điền metadata trước
    openSaveModal();
  } else {
    // Đã lưu rồi — cập nhật thẳng
    doSave();
  }
}

function openSaveModal(forEdit = false) {
  if (!state.currentDoc) return;
  $('smeta-title').value    = state.currentDoc.title    || '';
  $('smeta-doctype').value  = state.currentDoc.doc_type || 'khac';
  $('smeta-grade').value    = state.currentDoc.grade    || '';
  $('smeta-chapter').value  = state.currentDoc.chapter  || '';
  $('smeta-semester').value = state.currentDoc.semester || '';
  // Nút confirm: nếu đang edit thông tin (doc đã có ID) chỉ cập nhật metadata
  $('smeta-confirm-btn').textContent = forEdit ? '💾 Cập nhật thông tin' : '💾 Lưu tài liệu';
  $('smeta-confirm-btn').dataset.mode = forEdit ? 'edit' : 'save';
  $('save-meta-modal').classList.add('show');
}

async function confirmSaveMeta() {
  if (!state.currentDoc) return;
  const title = $('smeta-title').value.trim();
  if (!title) { $('smeta-title').focus(); showToast('Vui lòng nhập tiêu đề', 'warning'); return; }

  // Cập nhật metadata vào state
  state.currentDoc.title    = title;
  state.currentDoc.doc_type = $('smeta-doctype').value;
  state.currentDoc.grade    = $('smeta-grade').value ? parseInt($('smeta-grade').value) : null;
  state.currentDoc.chapter  = $('smeta-chapter').value.trim();
  state.currentDoc.semester = $('smeta-semester').value;

  // Đồng bộ tiêu đề vào editor
  const titleInput = document.querySelector('.doc-title-input');
  if (titleInput) titleInput.value = state.currentDoc.title;

  closeModal('save-meta-modal');

  const mode = $('smeta-confirm-btn').dataset.mode;
  if (mode === 'edit' && state.savedDocId) {
    // Chỉ cập nhật metadata, không cần báo unsaved
    await doSave(true);
  } else {
    await doSave();
  }
}

async function doSave(silent = false) {
  showLoading('Đang lưu...');
  try {
    const payload = { ...state.currentDoc, id: state.savedDocId };
    const url    = state.savedDocId
      ? `/api/documents/${state.savedDocId}`
      : '/api/documents';
    const method = state.savedDocId ? 'PUT' : 'POST';
    const r = await fetch(url, {
      method, headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    const data = await r.json();
    if (data.error) { showToast(data.error, 'error'); return; }
    if (!state.savedDocId) {
      state.savedDocId = data.id;
      state.currentDoc.id = data.id;
    }
    state.hasUnsavedChanges = false;
    updateUnsavedIndicator();
    updateDocInfoBadge();
    if (!silent) showToast('Đã lưu tài liệu!', 'success');
    loadDocumentList();
  } catch (e) {
    showToast('Lỗi lưu: ' + e.message, 'error');
  } finally {
    hideLoading();
  }
}

function updateDocInfoBadge() {
  const badge = $('doc-info-badge');
  if (!badge || !state.currentDoc) return;
  const grade   = state.currentDoc.grade ? `Lớp ${state.currentDoc.grade}` : '';
  const chapter = state.currentDoc.chapter || '';
  const parts   = [grade, chapter].filter(Boolean);
  badge.textContent = parts.length ? parts.join(' · ') : '';
  badge.style.display = parts.length ? '' : 'none';
}

// ── Tải danh sách tài liệu ────────────────────────────────────────
async function loadDocumentList() {
  try {
    const params = new URLSearchParams();
    if (state.sidebarGradeFilter) params.set('grade', state.sidebarGradeFilter);
    const r = await fetch(`/api/documents?${params}`);
    const docs = await r.json();
    renderDocList(docs);
  } catch { /* ignore */ }
}

const DOC_TYPE_LABEL = {
  'de_thi': 'Đề thi', 'phieu_bai_tap': 'Phiếu BT', 'bai_giang': 'Bài giảng', 'khac': 'Khác'
};

function renderDocList(docs) {
  const list = $('doc-list');
  if (!list) return;
  list.innerHTML = '';
  if (!docs.length) {
    list.innerHTML = '<div style="text-align:center;color:var(--text-muted);font-size:12px;padding:20px;">Chưa có tài liệu nào</div>';
    return;
  }
  docs.forEach(doc => {
    const item = el('div', 'doc-item' + (doc.id === state.savedDocId ? ' active' : ''));
    const typeLabel = DOC_TYPE_LABEL[doc.doc_type] || doc.doc_type || '';

    const row = el('div', 'doc-item-row');

    // Tên tài liệu — click đúp để đổi tên
    const nameEl = el('div', 'doc-name', escHtml(doc.title));
    nameEl.title = 'Click đúp để đổi tên';
    nameEl.addEventListener('dblclick', (e) => {
      e.stopPropagation();
      startRename(nameEl, doc);
    });

    const delBtn = el('button', 'doc-del-btn', '✕');
    delBtn.title = 'Xóa tài liệu này';
    delBtn.addEventListener('click', (e) => confirmDeleteDoc(e, doc.id));

    row.appendChild(nameEl);
    row.appendChild(delBtn);

    const meta = el('div', 'doc-meta');
    if (typeLabel) {
      const tb = el('span', 'doc-badge', typeLabel); meta.appendChild(tb);
    }
    if (doc.grade) {
      const gb = el('span', 'doc-badge grade', 'Lớp ' + doc.grade); meta.appendChild(gb);
    }
    meta.appendChild(el('span', '', formatDate(doc.updated_at)));

    item.appendChild(row);
    item.appendChild(meta);
    item.addEventListener('click', () => openDocument(doc.id));
    list.appendChild(item);
  });
}

function startRename(nameEl, doc) {
  const input = document.createElement('input');
  input.type = 'text';
  input.value = doc.title;
  input.className = 'doc-rename-input';
  input.style.cssText = 'width:100%;border:1px solid var(--accent);border-radius:3px;padding:2px 5px;font-size:13px;font-family:inherit;outline:none;';

  nameEl.replaceWith(input);
  input.focus();
  input.select();

  const commit = async () => {
    const newTitle = input.value.trim();
    if (!newTitle || newTitle === doc.title) {
      input.replaceWith(nameEl); return;
    }
    try {
      await fetch(`/api/documents/${doc.id}`, {
        method: 'PUT', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ...doc, title: newTitle }),
      });
      doc.title = newTitle;
      nameEl.textContent = newTitle;
      // Cập nhật tiêu đề nếu đang mở chính tài liệu đó
      if (state.savedDocId === doc.id && state.currentDoc) {
        state.currentDoc.title = newTitle;
        const titleInput = document.querySelector('.doc-title-input');
        if (titleInput) titleInput.value = newTitle;
      }
      showToast('Đã đổi tên', 'success', 2000);
    } catch (e) {
      showToast('Lỗi đổi tên', 'error');
    }
    input.replaceWith(nameEl);
  };

  input.addEventListener('blur', commit);
  input.addEventListener('keydown', (e) => {
    if (e.key === 'Enter')  { e.preventDefault(); input.blur(); }
    if (e.key === 'Escape') { input.value = doc.title; input.blur(); }
  });
}

async function confirmDeleteDoc(e, docId) {
  e.stopPropagation();
  if (!confirm('Xóa tài liệu này khỏi thư viện?')) return;
  try {
    await fetch(`/api/documents/${docId}`, { method: 'DELETE' });
    if (state.savedDocId === docId) {
      state.currentDoc = null;
      state.savedDocId = null;
      $('editor-content').innerHTML = '';
      const badge = $('q-count-badge');
      if (badge) badge.textContent = '0 câu';
    }
    showToast('Đã xóa tài liệu', 'success');
    loadDocumentList();
  } catch (e) {
    showToast('Lỗi xóa: ' + e.message, 'error');
  }
}

async function openDocument(docId) {
  if (!confirmDiscardChanges()) return;
  showLoading('Đang mở tài liệu...');
  try {
    const r = await fetch(`/api/documents/${docId}`);
    const data = await r.json();
    if (data.error) { showToast(data.error, 'error'); return; }

    // Ghép metadata (grade, chapter, semester, doc_type) vào content
    const doc = data.content || data;
    doc.grade    = doc.grade    ?? data.grade;
    doc.chapter  = doc.chapter  ?? data.chapter;
    doc.semester = doc.semester ?? data.semester;
    doc.doc_type = doc.doc_type ?? data.doc_type;
    doc.title    = doc.title    || data.title;

    state.currentDoc = doc;
    state.savedDocId = docId;
    renderOriginalPanel({ name: doc.title }, doc);
    renderEditorPanel(doc);
    showToast('Đã mở: ' + doc.title, 'success');
  } catch (e) {
    showToast('Lỗi: ' + e.message, 'error');
  } finally {
    hideLoading();
  }
}

// ── Phạm vi kiến thức ràng buộc AI giải bài ──────────────────────
let curriculumTree = null;   // {"10": {ten, chuong:[{id,ten}]}, ...}
let scopeDraft = { mode: 'khong_rang_buoc' };

function currentScope() {
  return (state.currentDoc && state.currentDoc.ai_scope) || { mode: 'khong_rang_buoc' };
}

function updateScopeChip() {
  const chip = $('scope-chip');
  if (!chip) return;
  const s = currentScope();
  let label = 'Không ràng buộc';
  if (s.mode === 'tuy_chinh') {
    label = 'Tùy chỉnh';
  } else if (s.mode === 'chuong_trinh' && s.grade) {
    const g = curriculumTree && curriculumTree[String(s.grade)];
    const ch = g && g.chuong.find(c => c.id === s.chapter_id);
    label = 'Lớp ' + s.grade + (ch ? ' · ' + (s.cumulative === false ? 'chỉ ' : '') + ch.ten : '');
  }
  chip.textContent = '⚙ Phạm vi: ' + label;
  const on = s.mode === 'chuong_trinh' || s.mode === 'tuy_chinh';
  chip.style.background = on ? '#0f766e' : '';
  chip.style.color = on ? '#fff' : '';
  chip.style.borderColor = on ? '#0f766e' : '';
}

async function loadCurriculum() {
  if (curriculumTree) return curriculumTree;
  try {
    const r = await fetch('/api/curriculum');
    curriculumTree = await r.json();
  } catch (e) {
    curriculumTree = {};
  }
  return curriculumTree;
}

async function openScopeModal() {
  if (!state.currentDoc) { showToast('Chưa có tài liệu', 'warning'); return; }
  await loadCurriculum();

  const s = currentScope();
  scopeDraft = JSON.parse(JSON.stringify(s));

  // Nạp dropdown lớp
  const gSel = $('scope-grade');
  gSel.innerHTML = '';
  Object.keys(curriculumTree).forEach(g => {
    const o = document.createElement('option');
    o.value = g; o.textContent = curriculumTree[g].ten;
    gSel.appendChild(o);
  });
  // Mặc định theo lớp của tài liệu nếu chưa từng đặt phạm vi
  const gradeInit = s.grade || state.currentDoc.grade || Object.keys(curriculumTree)[0];
  gSel.value = String(gradeInit);
  fillScopeChapters(String(gradeInit), s.chapter_id);

  $('scope-only-chapter').checked = s.cumulative === false;
  $('scope-allow').value = s.allow || '';
  $('scope-deny').value = s.deny || '';

  scopeSetMode(s.mode || 'khong_rang_buoc');
  $('scope-modal').classList.add('show');
}

function fillScopeChapters(grade, selectedId) {
  const cSel = $('scope-chapter');
  cSel.innerHTML = '';
  const g = curriculumTree[grade];
  if (!g) return;
  g.chuong.forEach(c => {
    const o = document.createElement('option');
    o.value = c.id; o.textContent = c.ten;
    cSel.appendChild(o);
  });
  // Chưa chọn → mặc định chương cuối (đã học hết lớp)
  cSel.value = (selectedId && g.chuong.some(c => c.id === selectedId))
    ? selectedId : g.chuong[g.chuong.length - 1].id;
}

function scopeOnGradeChange() {
  fillScopeChapters($('scope-grade').value, null);
  scopePreview();
}

function scopeSetMode(mode) {
  scopeDraft.mode = mode;
  ['khong_rang_buoc', 'chuong_trinh', 'tuy_chinh'].forEach(m => {
    const el = $('scope-opt-' + m);
    if (el) el.classList.toggle('active', m === mode);
  });
  $('scope-ct-box').style.display     = mode === 'chuong_trinh' ? 'block' : 'none';
  $('scope-custom-box').style.display = mode === 'tuy_chinh'    ? 'block' : 'none';
  scopePreview();
}

function scopeCollect() {
  const mode = scopeDraft.mode || 'khong_rang_buoc';
  if (mode === 'chuong_trinh') {
    return { mode, grade: parseInt($('scope-grade').value),
             chapter_id: $('scope-chapter').value,
             cumulative: !$('scope-only-chapter').checked };
  }
  if (mode === 'tuy_chinh') {
    return { mode, allow: $('scope-allow').value.trim(), deny: $('scope-deny').value.trim() };
  }
  return { mode: 'khong_rang_buoc' };
}

let scopePreviewTimer = null;
function scopePreview() {
  const wrap = $('scope-preview-wrap');
  const scope = scopeCollect();
  if (scope.mode === 'khong_rang_buoc') { wrap.style.display = 'none'; return; }
  clearTimeout(scopePreviewTimer);
  scopePreviewTimer = setTimeout(async () => {
    try {
      const r = await fetch('/api/curriculum/preview', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ scope }),
      });
      const d = await r.json();
      if (d.text) {
        $('scope-preview').textContent = d.text;
        wrap.style.display = 'block';
      } else {
        wrap.style.display = 'none';
      }
    } catch (e) { wrap.style.display = 'none'; }
  }, 250);
}

function scopeSave() {
  if (!state.currentDoc) return;
  state.currentDoc.ai_scope = scopeCollect();
  markUnsaved();
  updateScopeChip();
  closeModal('scope-modal');
  showToast('Đã áp dụng phạm vi: ' + $('scope-chip').textContent.replace('⚙ Phạm vi: ', ''),
            'success');
}

// ── AI Giải bài ───────────────────────────────────────────────────
async function solveAll() {
  if (!state.currentDoc) { showToast('Chưa có tài liệu', 'warning'); return; }
  if (!state.isOnline) { showToast('Không có kết nối internet', 'error'); return; }
  if (state.isSolving) { showToast('AI đang giải — vui lòng chờ...', 'warning'); return; }
  state.isSolving = true;

  showAIStatus('AI đang giải bài...');
  showLoading('AI đang giải bài — vui lòng chờ...');

  try {
    const r = await fetch('/api/ai/solve/stream', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ document: state.currentDoc, doc_id: state.savedDocId }),
    });

    if (!r.ok) {
      const err = await r.json().catch(() => ({}));
      showToast(err.error || `Lỗi server ${r.status}`, 'error');
      return;
    }

    const reader  = r.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';

    const processLine = (line) => {
        if (!line.startsWith('data: ')) return;
        let event;
        try { event = JSON.parse(line.slice(6)); }
        catch (pe) { console.warn('SSE JSON lỗi:', pe, line.slice(0, 120)); return; }

        if (event.type === 'chunk') {
          const preview = event.text?.replace(/\n/g, ' ').slice(0, 50);
          showAIStatus(`AI đang giải... ${preview}`);

        } else if (event.type === 'done') {
          const solutions = event.solutions || {};
          let cnt = 0;
          state.currentDoc.sections?.forEach(sec => {
            sec.questions?.forEach(q => {
              if (solutions[q.id]) {
                q.ai_solution = solutions[q.id];
                q.show_solution = true;
                const box = document.getElementById(`sol-${q.id}`);
                if (box) {
                  box.classList.remove('hidden');
                  const textEl = box.querySelector('.sol-text');
                  if (textEl) {
                    textEl.textContent = q.ai_solution;
                    textEl.style.color = '#1e3a6e';
                    rerenderMath(textEl);
                  }
                }
                cnt++;
              }
            });
          });
          showToast(`AI đã giải ${cnt} câu!`, 'success');
          showAIStatus('');

        } else if (event.type === 'error') {
          showToast(event.text, 'error');
          showAIReview(event.text, 'error');
          showAIStatus('');
        }
    };

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });

      const lines = buffer.split('\n');
      buffer = lines.pop();
      for (const line of lines) processLine(line);
    }
    // Flush phần còn lại — nếu event 'done' cuối không kèm '\n' thì toàn bộ
    // lời giải nằm trong buffer, bỏ qua là mất sạch dù server đã giải xong
    buffer += decoder.decode();
    if (buffer.trim()) processLine(buffer.trim());
  } catch (e) {
    showToast('Lỗi AI: ' + e.message, 'error');
  } finally {
    state.isSolving = false;
    hideLoading();
  }
}

async function reviewDoc() {
  if (!state.currentDoc) { showToast('Chưa có tài liệu', 'warning'); return; }
  if (!state.isOnline) { showToast('Không có kết nối internet', 'error'); return; }
  showLoading('AI đang nhận xét đề...');
  try {
    const r = await fetch('/api/ai/review', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ document: state.currentDoc }),
    });
    const data = await r.json();
    if (data.error) { showToast(data.error, 'error'); return; }
    showAIReview(data.review);
  } catch (e) {
    showToast('Lỗi: ' + e.message, 'error');
  } finally {
    hideLoading();
  }
}

async function solveOne(q, card, btn) {
  if (!state.isOnline) { showToast('Không có kết nối internet', 'error'); return; }

  // Spinner trên nút
  const origText = btn.textContent;
  btn.textContent = '⏳';
  btn.disabled = true;

  // Tạo doc tạm chỉ có 1 section với 1 câu
  const miniDoc = {
    title: state.currentDoc?.title || '',
    // Giữ phạm vi kiến thức — thiếu dòng này thì giải từng câu mất ràng buộc
    ai_scope: state.currentDoc?.ai_scope || {},
    sections: [{ id: 'tmp', type: 'khac', label: '', intro: '',
                  questions: [q], is_theory: false }]
  };

  try {
    const r = await fetch('/api/ai/solve', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ document: miniDoc, doc_id: state.savedDocId }),
    });
    const data = await r.json();
    if (data.error) { showToast(data.error, 'error'); return; }

    const sol = data.solutions?.[q.id];
    if (sol) {
      q.ai_solution = sol;
      q.show_solution = true;
      const box = card.querySelector('.solution-box');
      if (box) {
        box.classList.remove('hidden');
        const textEl = box.querySelector('.sol-text');
        if (textEl) { textEl.textContent = sol; textEl.style.color = '#1e3a6e'; rerenderMath(textEl); }
      }
      // Cập nhật nút ẩn/hiện
      const btnToggle = card.querySelector('.q-act-btn:not(.q-solve-btn)');
      if (btnToggle) btnToggle.textContent = '👁';
      markUnsaved();
      showToast('Đã giải xong câu ' + q.number, 'success', 2000);
    }
  } catch (e) {
    showToast('Lỗi AI: ' + e.message, 'error');
  } finally {
    btn.textContent = origText;
    btn.disabled = false;
  }
}

function showAIStatus(text) {
  const el = $('ai-status-text');
  if (el) el.textContent = text;
}

function showAIReview(text, type = '') {
  const box = $('ai-review-box');
  if (!box) return;
  box.textContent = text;
  box.className = 'show' + (type === 'error' ? ' error' : '');
}

// ── Xuất file — 2 chế độ ─────────────────────────────────────────
async function exportAs(mode, fmt) {
  if (!state.currentDoc) { showToast('Chưa có tài liệu để xuất', 'warning'); return; }
  closeAllExportMenus();
  await doExportWith(fmt, mode === 'answer');
}

async function doExportWith(fmt, showSol) {
  showLoading(`Đang xuất ${fmt.toUpperCase()}...`);
  try {
    const r = await fetch(`/api/export/${fmt}`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ document: state.currentDoc, show_solutions: showSol }),
    });
    if (!r.ok) {
      const err = await r.json().catch(() => ({}));
      showToast(err.error || 'Lỗi xuất file', 'error');
      return;
    }
    const modeLabel = showSol ? '(bản đáp án)' : '(bản học sinh)';
    const blob = await r.blob();
    const url  = URL.createObjectURL(blob);
    const a    = document.createElement('a');
    a.href = url;
    const ext  = fmt === 'pdf' ? '.pdf' : '.docx';
    a.download = (state.currentDoc.title || 'TaiLieu') + (showSol ? '_DapAn' : '_HocSinh') + ext;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);

    // Hiển thị thư mục đã lưu (từ custom header X-Saved-Dirs)
    const savedDirs = r.headers.get('X-Saved-Dirs') || '';
    let toastMsg = `Xuất ${fmt.toUpperCase()} thành công ${modeLabel}!`;
    if (savedDirs) {
      const dirs = savedDirs.split('|').filter(Boolean);
      const labels = dirs.map(d => {
        if (d.toLowerCase().includes('google')) return 'Google Drive';
        if (d.toLowerCase().includes('onedrive')) return 'OneDrive';
        if (d.toLowerCase().includes('downloads')) return 'Downloads';
        return d.split(/[/\\]/).pop() || d;
      });
      if (labels.length) toastMsg += `\nĐã lưu vào: ${labels.join(', ')}`;
    }
    showToast(toastMsg, 'success', 4000);
  } catch (e) {
    showToast('Lỗi: ' + e.message, 'error');
  } finally {
    hideLoading();
  }
}

function toggleExportMenu(mode) {
  const menu = $(`export-menu-${mode}`);
  const wasShown = menu?.classList.contains('show');
  closeAllExportMenus();
  if (menu && !wasShown) menu.classList.add('show');
}

function closeAllExportMenus() {
  document.querySelectorAll('.export-menu').forEach(m => m.classList.remove('show'));
}

// Backward-compat: export modal vẫn còn trong HTML
function showExportDialog() {
  if (!state.currentDoc) { showToast('Chưa có tài liệu để xuất', 'warning'); return; }
  $('export-modal').classList.add('show');
}

async function doExport() {
  const fmt     = $('export-format').value;
  const showSol = $('export-show-sol').checked;
  if (!state.currentDoc) return;
  $('export-modal').classList.remove('show');
  await doExportWith(fmt, showSol);
}

async function exportAnswerTable() {
  if (!state.currentDoc) { showToast('Chưa có tài liệu', 'warning'); return; }
  showLoading('Đang tạo bảng đáp án...');
  try {
    const r = await fetch('/api/export/answer_table', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ document: state.currentDoc }),
    });
    if (!r.ok) { showToast('Lỗi xuất bảng đáp án', 'error'); return; }
    const blob = await r.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = 'BangDapAn_' + (state.currentDoc.title || 'TaiLieu') + '.xlsx';
    a.click();
    URL.revokeObjectURL(url);
    showToast('Đã xuất bảng đáp án!', 'success');
  } catch (e) {
    showToast('Lỗi: ' + e.message, 'error');
  } finally {
    hideLoading();
  }
}

// ── Print preview ─────────────────────────────────────────────────
function showPrintPreview() {
  if (!state.currentDoc) { showToast('Chưa có tài liệu', 'warning'); return; }
  const preview = $('print-preview-body');
  preview.innerHTML = buildPreviewHTML(state.currentDoc, state.showAllSolutions);
  $('print-preview').classList.add('show');
  // Render LaTeX sau khi DOM đã cập nhật
  requestAnimationFrame(() => rerenderMath(preview));
}

function buildPreviewHTML(doc, showSol) {
  let html = `<h2 style="text-align:center;font-family:var(--font-doc);font-size:16pt;margin-bottom:16px;">${escHtml(doc.title)}</h2>`;
  (doc.sections || []).forEach(sec => {
    if (sec.label) html += `<h3 style="color:var(--navy);font-size:13pt;margin:14px 0 8px;">${escHtml(sec.label)}</h3>`;
    if (sec.intro) html += `<p style="margin-bottom:8px;">${escHtml(sec.intro)}</p>`;
    (sec.questions || []).forEach(q => {
      html += `<p style="margin-bottom:6px;"><strong>Câu ${q.number}.</strong> ${escHtml(q.text)}</p>`;
      if (q.options?.length) {
        html += '<div style="display:grid;grid-template-columns:1fr 1fr;gap:2px 16px;margin-left:20px;margin-bottom:6px;">';
        q.options.forEach(o => html += `<span>${escHtml(o)}</span>`);
        html += '</div>';
      }
      if (showSol && q.show_solution && q.ai_solution) {
        html += `<div style="background:#EBF4FF;border-left:3px solid #93C5FD;padding:6px 10px;margin:4px 0 10px 20px;font-size:12pt;color:#1e3a6e;"><em>Lời giải:</em> ${escHtml(q.ai_solution)}</div>`;
      }
    });
  });
  return html;
}

// ── Cài đặt ───────────────────────────────────────────────────────
async function openSettings() {
  const r = await fetch('/api/settings');
  const s = await r.json();
  $('set-teacher').value   = s.teacher_name  || '';
  $('set-school').value    = s.school_name   || '';
  $('set-gemini').value    = s.gemini_api_key    ? '***' : '';
  $('set-claude').value    = s.claude_api_key    ? '***' : '';
  $('set-niner-key').value = s.niner_router_key  ? '***' : '';
  $('set-niner-url').value = s.niner_router_url || 'http://localhost:20128/v1';
  $('set-model-th').value  = s.model_theory  || 'ag/gemini-3-flash';
  $('set-model-ex').value  = s.model_exercise|| 'ag/gemini-3-flash';
  $('set-save-dir').value  = s.save_dir      || '';
  $('set-onedrive').value  = s.onedrive_dir  || '';
  $('set-gdrive').value    = s.gdrive_dir    || '';
  $('settings-modal').classList.add('show');
  checkPandocStatus();
}

async function checkPandocStatus() {
  const dot = $('pandoc-dot');
  const msg = $('pandoc-msg');
  const btn = $('pandoc-install-btn');
  if (!dot) return;
  dot.style.background = '#facc15';
  msg.textContent = 'Đang kiểm tra...';
  try {
    const r = await fetch('/api/check-pandoc');
    const d = await r.json();
    if (d.available) {
      dot.style.background = '#22c55e';
      msg.textContent = d.version + ' — Sẵn sàng import Word giữ nguyên công thức';
      btn.style.display = 'none';
    } else {
      dot.style.background = '#ef4444';
      msg.textContent = 'Chưa cài Pandoc — Import Word sẽ dùng Gemini Vision (công thức có thể bị lỗi)';
      btn.style.display = 'inline-block';
    }
  } catch {
    dot.style.background = '#aaa';
    msg.textContent = 'Không kiểm tra được';
  }
}

async function saveSettings() {
  const data = {
    teacher_name:     $('set-teacher').value,
    school_name:      $('set-school').value,
    gemini_api_key:   $('set-gemini').value,
    claude_api_key:   $('set-claude').value,
    niner_router_key: $('set-niner-key').value,
    niner_router_url: $('set-niner-url').value || 'http://localhost:20128/v1',
    model_theory:     $('set-model-th').value,
    model_exercise:   $('set-model-ex').value,
    save_dir:         $('set-save-dir').value,
    onedrive_dir:     $('set-onedrive').value,
    gdrive_dir:       $('set-gdrive').value,
  };
  await fetch('/api/settings', {
    method: 'PUT', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  });
  $('settings-modal').classList.remove('show');
  showToast('Đã lưu cài đặt!', 'success');
}

async function testAPIKey(type) {
  let key, payload;
  if (type === 'gemini') {
    key = $('set-gemini').value;
    payload = { type, key };
  } else if (type === 'claude') {
    key = $('set-claude').value;
    payload = { type, key };
  } else if (type === 'niner') {
    key = $('set-niner-key').value;
    const url   = $('set-niner-url').value || 'http://localhost:20128/v1';
    const model = $('set-model-ex').value || 'ag/gemini-3-flash';
    payload = { type, key, url, model };
  }
  if (!key) { showToast('Nhập API key trước', 'warning'); return; }
  showLoading('Đang kiểm tra kết nối...');
  try {
    const r = await fetch('/api/settings/test-ai', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    const data = await r.json();
    showToast(data.message, data.ok ? 'success' : 'error');
  } catch (e) {
    showToast('Lỗi: ' + e.message, 'error');
  } finally {
    hideLoading();
  }
}

async function checkNinerRouter() {
  const badge = $('niner-status-badge');
  badge.textContent = 'Đang kiểm tra...';
  badge.style.background = '#fef3c7';
  badge.style.color = '#92400e';
  try {
    const r = await fetch('/api/check-9router');
    const d = await r.json();
    if (d.running) {
      badge.textContent = 'Đang chạy';
      badge.style.background = '#d1fae5';
      badge.style.color = '#065f46';
      const list = (d.models || []).slice(0, 5).join(', ');
      if (list) showToast('9Router OK — Models: ' + list, 'success', 4000);
      else showToast('9Router đang chạy', 'success');
    } else {
      badge.textContent = 'Chưa chạy';
      badge.style.background = '#fee2e2';
      badge.style.color = '#991b1b';
      showToast('9Router chưa khởi động. Vào thư mục 9router-master → npm run dev', 'warning', 6000);
    }
  } catch {
    badge.textContent = 'Lỗi';
    badge.style.background = '#fee2e2';
    badge.style.color = '#991b1b';
  }
}

async function uploadLogo() {
  const input = $('logo-input');
  input.click();
}

async function onLogoSelected(input) {
  const file = input.files[0];
  if (!file) return;
  const form = new FormData();
  form.append('logo', file);
  const r = await fetch('/api/settings/logo', { method: 'POST', body: form });
  const data = await r.json();
  if (data.success) showToast('Đã cập nhật logo!', 'success');
  else showToast(data.error, 'error');
}

// ── Thư viện (sidebar) ────────────────────────────────────────────
function toggleSidebar() {
  state.isSidebarOpen = !state.isSidebarOpen;
  $('sidebar').classList.toggle('hidden', !state.isSidebarOpen);
  if (state.isSidebarOpen) loadDocumentList();
}

function setSidebarFilter(grade, btn) {
  state.sidebarGradeFilter = grade;
  document.querySelectorAll('.filter-tab').forEach(b => b.classList.remove('active'));
  if (btn) btn.classList.add('active');
  loadDocumentList();
}

function searchDocs(val) {
  const params = new URLSearchParams();
  if (val) params.set('search', val);
  if (state.sidebarGradeFilter) params.set('grade', state.sidebarGradeFilter);
  fetch(`/api/documents?${params}`)
    .then(r => r.json()).then(renderDocList);
}

// ── Zoom ảnh ──────────────────────────────────────────────────────
function zoomImage(src) {
  const overlay = el('div');
  overlay.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,.75);display:flex;align-items:center;justify-content:center;z-index:2000;cursor:zoom-out;';
  const img = document.createElement('img');
  img.src = src;
  img.style.cssText = 'max-width:90vw;max-height:90vh;border-radius:8px;box-shadow:0 8px 30px rgba(0,0,0,.4);';
  overlay.appendChild(img);
  overlay.addEventListener('click', () => overlay.remove());
  document.body.appendChild(overlay);
}

// ── Tiện ích ──────────────────────────────────────────────────────
// Bản JS của importer.split_options_from_text: khối A./B./C./D. tăng dần trong
// đề → { text: đề đã cắt, options: [...] }; không tách được → null
function splitOptionsFromText(text, options) {
  if ((options && options.length >= 2) || !text) return null;
  const re = /\**\(?([A-D])\**(?:\)|\s*\\?[.):\-])\**\s*/g;
  const ms = [];
  let m;
  while ((m = re.exec(text)) !== null) {
    if (m.index === 0 || /\s/.test(text[m.index - 1])) ms.push({ letter: m[1], start: m.index, body: m.index + m[0].length });
  }
  if (ms.length < 2 || ms[0].letter !== 'A') return null;
  for (let i = 1; i < ms.length; i++) if (ms[i].letter.charCodeAt(0) !== ms[i - 1].letter.charCodeAt(0) + 1) return null;
  const opts = ms.map((x, i) => x.letter + '. ' + text.slice(x.body, i + 1 < ms.length ? ms[i + 1].start : text.length).trim());
  return { text: text.slice(0, ms[0].start).trimEnd(), options: opts };
}

function getLetter(idx) {
  return String.fromCharCode(65 + idx); // 0→A, 1→B, 2→C, 3→D
}

function escHtml(str) {
  if (!str) return '';
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

function formatDate(iso) {
  if (!iso) return '';
  const d = new Date(iso);
  return `${d.getDate()}/${d.getMonth()+1}/${d.getFullYear()}`;
}

function buildEmptyState() {
  const div = el('div', 'empty-state');
  div.innerHTML = `
    <div class="empty-icon">📄</div>
    <h3>Chưa có tài liệu</h3>
    <p>Bấm <strong>Nhập file</strong> ở trên để import file PDF hoặc Word,<br>hoặc mở tài liệu đã lưu từ Thư viện.</p>`;
  return div;
}

function toggleAIPanel() {
  state.isAIPanelOpen = !state.isAIPanelOpen;
  $('ai-panel').classList.toggle('collapsed', !state.isAIPanelOpen);
}

function toggleLeftPanel() {
  $('panel-left').classList.toggle('collapsed');
}

function closeModal(id) {
  $(id).classList.remove('show');
  // Dừng poll nền khi đóng modal chuyển đổi — nếu không, request 2s/lần
  // tiếp tục chạy ngầm cho tới khi job xong (file lớn = nhiều phút)
  if (id === 'p2w-modal' && p2wPollTimer) { clearInterval(p2wPollTimer); p2wPollTimer = null; }
  if (id === 'p2md-modal' && p2mdPollTimer) { clearInterval(p2mdPollTimer); p2mdPollTimer = null; }
}

// ── PDF → Word (Marker + Pandoc) ──────────────────────────────────
let p2wPending = [];     // File[] đang chờ gửi
let p2wJobId = null;
let p2wPollTimer = null;

async function openP2W() {
  p2wPending = [];
  p2wJobId = null;
  p2wHideLargeWarn();
  if ($('p2w-page-enable')) { $('p2w-page-enable').checked = false; p2wTogglePageRange(false); }
  if (p2wPollTimer) { clearInterval(p2wPollTimer); p2wPollTimer = null; }
  $('p2w-list').innerHTML = '';
  $('p2w-start-btn').disabled = true;
  const warn = $('p2w-ready-warn');
  warn.style.display = 'none';
  if ($('p2w-java-warn')) $('p2w-java-warn').style.display = 'none';
  if ($('p2w-no-key-note')) $('p2w-no-key-note').style.display = 'none';
  $('p2w-modal').classList.add('show');
  try {
    const [r, sr] = await Promise.all([
      fetch('/api/pdf-to-word/ready'),
      fetch('/api/settings')
    ]);
    const d = await r.json();
    const sd = await sr.json();
    const hasGeminiKey = sd.gemini_api_key && sd.gemini_api_key !== '' && sd.gemini_api_key !== '***';

    // Che do lai dung Gemini Vision (Gemini API key) cho trang phuc tap
    const noteEl = $('p2w-no-key-note');
    if (noteEl) {
      if (!hasGeminiKey) {
        noteEl.innerHTML = '&#8505; Chua co Gemini API key &mdash; trang phuc tap (nhieu cong thuc/hinh/bang) se dung ODL, co the kem chinh xac. Vao <b>Cai dat</b> de them key.';
        noteEl.style.display = 'block';
      } else {
        noteEl.style.display = 'none';
      }
    }
    // Hien thi model dang dung
    const modelInfoEl = $('p2w-model-info');
    if (modelInfoEl) {
      if (hasGeminiKey) modelInfoEl.textContent = 'Trang phuc tap: Gemini Vision (gemini-2.5-flash) — chay song song';
      else modelInfoEl.textContent = 'Chua co Gemini key — chi dung ODL (trang phuc tap co the kem chinh xac)';
    }
    // Canh bao Java (can cho ODL)
    if (!d.java && $('p2w-java-warn')) {
      $('p2w-java-warn').style.display = 'block';
    }
    // Canh bao Pandoc
    if (!d.pandoc) {
      warn.innerHTML = '&#9888; Chua co Pandoc - can cai dat de su dung tinh nang nay.';
      warn.style.display = 'block';
    }
  } catch (e) { /* bo qua */ }
}

function p2wTogglePageRange(checked) {
  const el = $('p2w-page-input');
  if (el) el.style.display = checked ? 'flex' : 'none';
  if (!checked && $('p2w-pages')) $('p2w-pages').value = '';
}

function p2wDrop(ev) {
  ev.preventDefault();
  ev.currentTarget.classList.remove('drag');
  const files = [...(ev.dataTransfer?.files || [])].filter(f => f.name.toLowerCase().endsWith('.pdf'));
  if (files.length) p2wAddFiles(files);
}

function p2wAddFiles(fileList) {
  p2wHideLargeWarn();
  const files = [...fileList].filter(f => f.name.toLowerCase().endsWith('.pdf'));
  for (const f of files) {
    if (!p2wPending.some(p => p.name === f.name && p.size === f.size)) p2wPending.push(f);
  }
  p2wRenderPending();
  $('p2w-start-btn').disabled = p2wPending.length === 0;
}

function p2wRenderPending() {
  const list = $('p2w-list');
  list.innerHTML = '';
  p2wPending.forEach((f, i) => {
    const row = el('div', 'p2w-row');
    row.innerHTML =
      `<span class="p2w-name">📄 ${f.name}</span>` +
      `<span class="p2w-status">${(f.size / 1024 / 1024).toFixed(1)} MB</span>` +
      `<button class="p2w-x" title="Bỏ" onclick="p2wRemove(${i})">✕</button>`;
    list.appendChild(row);
  });
}

function p2wRemove(i) {
  p2wPending.splice(i, 1);
  p2wRenderPending();
  $('p2w-start-btn').disabled = p2wPending.length === 0;
}

async function p2wStart() {
  if (!p2wPending.length) return;
  const fd = new FormData();
  p2wPending.forEach(f => fd.append('files', f));
  fd.append('ocr_mode', 'auto');
  if (p2wAllowLarge) fd.append('allow_large', '1');
  p2wAllowLarge = false;
  const methodEl = document.querySelector('[name=p2wMethod]:checked');
  fd.append('method', methodEl ? methodEl.value : 'auto');
  // Page range
  const pageEnable = $('p2w-page-enable');
  if (pageEnable && pageEnable.checked) {
    const pages = ($('p2w-pages') || {}).value || '';
    if (pages.trim()) fd.append('page_range', pages.trim());
  }
  $('p2w-start-btn').disabled = true;
  $('p2w-start-btn').textContent = '⏳ Đang gửi...';
  try {
    const r = await fetch('/api/pdf-to-word', { method: 'POST', body: fd });
    let d = null;
    try { d = await r.json(); } catch (_) { d = null; }
    if (!r.ok || !d) {
      const msg = (d && d.error) || (r.status === 413
        ? 'File quá lớn — hãy tách PDF thành phần nhỏ hơn rồi thử lại.'
        : 'Lỗi server (' + r.status + ')');
      showToast(msg, 'error'); p2wResetBtn(); return;
    }
    if (d.needs_confirm) {           // tài liệu scan lớn → hỏi trước khi xếp hàng
      p2wShowLargeWarn(d.warnings || []);
      p2wResetBtn();
      return;
    }
    p2wJobId = d.job_id;
    showToast('Đã xếp hàng ' + d.files.length + ' file. Đang xử lý...', 'info');
    p2wRenderStatus(d.files.map(f => ({ ...f, message: 'Đang chờ…' })));
    p2wPollTimer = setInterval(() => p2wPoll(), 2000);
    p2wPoll();
  } catch (e) {
    showToast('Lỗi kết nối: ' + e.message, 'error');
    p2wResetBtn();
  }
}

// ── Tài liệu scan lớn: cảnh báo + gợi ý chia dải ─────────────────
let p2wAllowLarge = false;
let p2wLargeFiles = [];     // giữ file để chuyển tiếp các dải sau (p2wPending bị xoá khi xong)

function p2wHideLargeWarn() {
  const box = $('p2w-large-warn');
  if (box) { box.hidden = true; box.innerHTML = ''; }
  p2wLargeFiles = [];
}

function p2wShowLargeWarn(warns) {
  const box = $('p2w-large-warn');
  if (!box) return;
  box.innerHTML = '';
  box.appendChild(el('div', 'p2w-lw-title', '⚠️ Tài liệu scan lớn — sẽ mất nhiều thời gian'));
  warns.forEach(w => {
    const line = el('div', 'p2w-lw-line');
    line.textContent = `${w.name}: ${w.scan_pages}/${w.pages} trang là bản scan, AI phải đọc `
      + `từng trang — dự kiến khoảng ${w.est_min} phút.`;
    box.appendChild(line);
  });
  const single = warns.length === 1 && p2wPending.length === 1;
  const hint = el('div', 'p2w-lw-line');
  if (single) {
    p2wLargeFiles = p2wPending.slice();
    hint.textContent = 'Khuyên dùng: chuyển theo dải 50 trang — mỗi dải ra một file Word, '
      + 'lỗi giữa chừng không mất cả quyển. Bấm một dải để điền vào ô "Chọn trang":';
    box.appendChild(hint);
    const chips = el('div', 'p2w-lw-chips');
    (warns[0].ranges || []).forEach(rg => {
      const b = el('button', 'p2w-lw-chip');
      b.type = 'button';
      b.textContent = rg;
      b.onclick = () => p2wPickRange(rg, b);
      chips.appendChild(b);
    });
    box.appendChild(chips);
  } else {
    hint.textContent = 'Khuyên dùng: chuyển riêng từng file lớn và chia theo dải 50 trang '
      + '(ô "Chọn trang").';
    box.appendChild(hint);
  }
  const acts = el('div', 'p2w-lw-acts');
  const go = el('button', 'btn btn-secondary');
  go.type = 'button';
  go.textContent = 'Vẫn chuyển cả file';
  go.onclick = () => { p2wAllowLarge = true; p2wHideLargeWarn(); p2wStart(); };
  acts.appendChild(go);
  box.appendChild(acts);
  box.hidden = false;
}

function p2wPickRange(rg, chip) {
  const cb = $('p2w-page-enable');
  if (cb && !cb.checked) { cb.checked = true; p2wTogglePageRange(true); }
  if ($('p2w-pages')) $('p2w-pages').value = rg;
  // Dải trước đã chuyển xong thì p2wPending đã bị xoá → nạp lại đúng file đó
  if (!p2wPending.length && p2wLargeFiles.length) p2wPending = p2wLargeFiles.slice();
  document.querySelectorAll('.p2w-lw-chip.picked').forEach(c => c.classList.remove('picked'));
  if (chip) chip.classList.add('picked', 'used');
  $('p2w-start-btn').disabled = false;
  $('p2w-start-btn').textContent = '🚀 Bắt đầu chuyển';
  showToast('Đã điền dải ' + rg + ' — bấm "Bắt đầu chuyển". Xong dải này thì bấm dải tiếp theo.',
            'info', 6000);
}

function p2wResetBtn() {
  $('p2w-start-btn').disabled = p2wPending.length === 0;
  $('p2w-start-btn').textContent = '🚀 Bắt đầu chuyển';
}

async function p2wPoll() {
  if (!p2wJobId) return;
  try {
    const r = await fetch('/api/pdf-to-word/status/' + p2wJobId);
    const d = await r.json();
    if (!r.ok) return;
    p2wRenderStatus(d.files);
    if (d.all_done) {
      clearInterval(p2wPollTimer); p2wPollTimer = null;
      const ok = d.files.filter(f => f.status === 'done').length;
      const err = d.files.filter(f => f.status === 'error').length;
      showToast(`Hoàn tất: ${ok} file thành công` + (err ? `, ${err} lỗi` : ''),
                err ? 'info' : 'success', 5000);
      $('p2w-start-btn').textContent = '🚀 Chuyển thêm';
      $('p2w-start-btn').disabled = false;
      p2wPending = [];
    }
  } catch (e) { /* tiếp tục poll */ }
}

function p2wRenderStatus(files) {
  const list = $('p2w-list');
  list.innerHTML = '';
  files.forEach(f => {
    const row = el('div', 'p2w-row');
    let icon = '⏳', cls = '';
    if (f.status === 'processing') { icon = '🔄'; cls = 'proc'; }
    else if (f.status === 'done')  { icon = '✅'; cls = 'done'; }
    else if (f.status === 'error') { icon = '❌'; cls = 'err'; }
    let right;
    if (f.status === 'done') {
      const nScan = (f.scan_pages || []).length;
      const ocr = nScan ? ` · đã OCR ${nScan} trang scan` : ' · nhanh';
      right = `<button class="p2w-dl" onclick="p2wDownload(${f.index},'${(f.out_name||'').replace(/'/g,'')}')">⬇ Tải Word</button>`
            + `<span class="p2w-status">${f.seconds || ''}s${ocr}</span>`;
    } else {
      right = `<span class="p2w-status">${f.message || ''}</span>`;
    }
    row.innerHTML = `<span class="p2w-name ${cls}">${icon} ${f.name}</span>${right}`;
    list.appendChild(row);
    // Trang không đọc được: nói rõ trang nào, Word đã chèn ảnh trang gốc ở đó
    const fp = f.failed_pages || [];
    if (f.status === 'done' && fp.length) {
      const warn = el('div', 'p2w-warn');
      warn.textContent = `⚠️ ${fp.length} trang không đọc được chữ (trang `
        + `${fp.slice(0, 12).join(', ')}${fp.length > 12 ? '…' : ''}) — `
        + `Word đã chèn ảnh trang gốc ở đó, nên chuyển lại riêng những trang này.`;
      list.appendChild(warn);
    }
  });
}

function p2wDownload(index, name) {
  const a = document.createElement('a');
  a.href = `/api/pdf-to-word/result/${p2wJobId}/${index}`;
  a.download = name || '';
  document.body.appendChild(a);
  a.click();
  a.remove();
}

// ── Tách hình theo câu (Word → Word chỉ gồm số câu + hình) ────────
let docxImgFile = null;

function openDocxImg() {
  docxImgFile = null;
  $('docximg-name').style.display = 'none';
  $('docximg-name').textContent = '';
  $('docximg-status').style.display = 'none';
  $('docximg-status').innerHTML = '';
  $('docximg-start-btn').disabled = true;
  $('docximg-start-btn').textContent = '🚀 Bắt đầu tách hình';
  const inp = $('docximg-file');
  if (inp) inp.value = '';
  $('docximg-modal').classList.add('show');
}

function docxImgDrop(ev) {
  ev.preventDefault();
  ev.currentTarget.classList.remove('drag');
  docxImgPick(ev.dataTransfer?.files || []);
}

function docxImgPick(fileList) {
  const f = [...fileList].find(x => x.name.toLowerCase().endsWith('.docx'));
  if (!f) {
    showToast('Chỉ nhận file Word .docx (file .doc cũ hãy mở Word và Lưu thành .docx)', 'error');
    return;
  }
  docxImgFile = f;
  const nameEl = $('docximg-name');
  nameEl.textContent = '📄 ' + f.name + '  (' + (f.size / 1024 / 1024).toFixed(1) + ' MB)';
  nameEl.style.display = 'block';
  $('docximg-start-btn').disabled = false;
}

async function docxImgStart() {
  if (!docxImgFile) return;
  const fd = new FormData();
  fd.append('file', docxImgFile);
  fd.append('level', ($('docximg-level') || {}).value || 'manh');

  const btn = $('docximg-start-btn');
  const st = $('docximg-status');
  btn.disabled = true;
  btn.textContent = '⏳ Đang xử lý...';
  st.style.display = 'block';
  st.innerHTML = '<span style="color:var(--navy);">🔄 Đang trích hình, gán số câu và làm nét…</span>';

  try {
    const r = await fetch('/api/docx-images', { method: 'POST', body: fd });
    let d = null;
    try { d = await r.json(); } catch (_) { d = null; }
    if (!r.ok || !d) {
      const msg = (d && d.error) || (r.status === 413
        ? 'File quá lớn — hãy tách nhỏ rồi thử lại.'
        : 'Lỗi server (' + r.status + ')');
      st.innerHTML = '<span style="color:#c0392b;">❌ ' + msg + '</span>';
      btn.disabled = false;
      btn.textContent = '🚀 Bắt đầu tách hình';
      return;
    }
    const cach = d.phuong_phap === 'ai' ? 'AI gán số câu' : 'nhận diện cục bộ (AI không dùng được)';
    let html = `<div style="color:#1e7e34;font-weight:600;">✅ Xong: ${d.so_cau} câu · ${d.so_hinh} hình (${cach})</div>`;
    if (d.hinh_trong_bang)
      html += `<div style="color:#9a5b00;margin-top:6px;">⚠ Có ${d.hinh_trong_bang} hình nằm trong bảng — chưa lấy theo cài đặt hiện tại.</div>`;
    if (d.hinh_loi)
      html += `<div style="color:#9a5b00;margin-top:6px;">⚠ ${d.hinh_loi} hình không đọc được (định dạng EMF/WMF của Word).</div>`;
    if (d.saved_to && d.saved_to.length)
      html += `<div style="color:var(--text-muted);margin-top:6px;font-size:12px;">Đã lưu vào: ${d.saved_to.join(' · ')}</div>`;
    html += `<button class="btn btn-primary" style="margin-top:10px;" onclick="docxImgDownload('${d.filename.replace(/'/g, '')}')">⬇ Tải file Word</button>`;
    st.innerHTML = html;
    showToast(`Đã tách ${d.so_hinh} hình của ${d.so_cau} câu`, 'success', 4000);
    btn.textContent = '🚀 Tách file khác';
    btn.disabled = false;
  } catch (e) {
    st.innerHTML = '<span style="color:#c0392b;">❌ Lỗi kết nối: ' + e.message + '</span>';
    btn.disabled = false;
    btn.textContent = '🚀 Bắt đầu tách hình';
  }
}

function docxImgDownload(name) {
  const a = document.createElement('a');
  a.href = '/api/docx-images/result/' + encodeURIComponent(name);
  a.download = name || '';
  document.body.appendChild(a);
  a.click();
  a.remove();
}

// ── PDF → Markdown (OCR Gemini cho NotebookLM) ────────────────────
let p2mdPending = [];
let p2mdJobId = null;
let p2mdPollTimer = null;

async function openP2MD() {
  p2mdPending = [];
  p2mdJobId = null;
  if ($('p2md-page-enable')) { $('p2md-page-enable').checked = false; p2mdTogglePageRange(false); }
  if (p2mdPollTimer) { clearInterval(p2mdPollTimer); p2mdPollTimer = null; }
  $('p2md-list').innerHTML = '';
  $('p2md-start-btn').disabled = true;
  $('p2md-start-btn').textContent = '🚀 Bắt đầu OCR';
  $('p2md-ready-warn').style.display = 'none';
  if ($('p2md-no-key-note')) $('p2md-no-key-note').style.display = 'none';
  if ($('p2md-bigfile-warn')) $('p2md-bigfile-warn').style.display = 'none';
  $('p2md-modal').classList.add('show');
  try {
    const sr = await fetch('/api/settings');
    const sd = await sr.json();
    const hasKey = (sd.gemini_api_key && sd.gemini_api_key.length > 5)
                || (sd.niner_router_key && sd.niner_router_key.length > 5);
    if (!hasKey && $('p2md-no-key-note')) $('p2md-no-key-note').style.display = 'block';
  } catch (e) { /* bo qua */ }
}

function p2mdTogglePageRange(checked) {
  const el2 = $('p2md-page-input');
  if (el2) el2.style.display = checked ? 'flex' : 'none';
  if (!checked && $('p2md-pages')) $('p2md-pages').value = '';
}

function p2mdDrop(ev) {
  ev.preventDefault();
  ev.currentTarget.classList.remove('drag');
  const files = [...(ev.dataTransfer?.files || [])].filter(f => f.name.toLowerCase().endsWith('.pdf'));
  if (files.length) p2mdAddFiles(files);
}

function p2mdAddFiles(fileList) {
  const files = [...fileList].filter(f => f.name.toLowerCase().endsWith('.pdf'));
  for (const f of files) {
    if (!p2mdPending.some(p => p.name === f.name && p.size === f.size)) p2mdPending.push(f);
  }
  p2mdRenderPending();
  $('p2md-start-btn').disabled = p2mdPending.length === 0;
  // Nhắc chia nhỏ nếu có file (PDF scan dày dễ chạm quota Gemini)
  if ($('p2md-bigfile-warn')) $('p2md-bigfile-warn').style.display = p2mdPending.length ? 'block' : 'none';
}

function p2mdRenderPending() {
  const list = $('p2md-list');
  list.innerHTML = '';
  p2mdPending.forEach((f, i) => {
    const row = el('div', 'p2w-row');
    row.innerHTML =
      `<span class="p2w-name">📄 ${f.name}</span>` +
      `<span class="p2w-status">${(f.size / 1024 / 1024).toFixed(1)} MB</span>` +
      `<button class="p2w-x" title="Bỏ" onclick="p2mdRemove(${i})">✕</button>`;
    list.appendChild(row);
  });
}

function p2mdRemove(i) {
  p2mdPending.splice(i, 1);
  p2mdRenderPending();
  $('p2md-start-btn').disabled = p2mdPending.length === 0;
  if ($('p2md-bigfile-warn')) $('p2md-bigfile-warn').style.display = p2mdPending.length ? 'block' : 'none';
}

async function p2mdStart() {
  if (!p2mdPending.length) return;
  const fd = new FormData();
  p2mdPending.forEach(f => fd.append('files', f));
  fd.append('output', 'markdown');
  fd.append('method', 'gemini');
  const pageEnable = $('p2md-page-enable');
  if (pageEnable && pageEnable.checked) {
    const pages = ($('p2md-pages') || {}).value || '';
    if (pages.trim()) fd.append('page_range', pages.trim());
  }
  $('p2md-start-btn').disabled = true;
  $('p2md-start-btn').textContent = '⏳ Đang gửi...';
  try {
    const r = await fetch('/api/pdf-to-word', { method: 'POST', body: fd });
    let d = null;
    try { d = await r.json(); } catch (_) { d = null; }
    if (!r.ok || !d) {
      const msg = (d && d.error) || (r.status === 413
        ? 'File quá lớn — hãy tách PDF thành phần nhỏ hơn rồi thử lại.'
        : 'Lỗi server (' + r.status + ')');
      showToast(msg, 'error'); p2mdResetBtn(); return;
    }
    p2mdJobId = d.job_id;
    showToast('Đã xếp hàng ' + d.files.length + ' file. Đang OCR...', 'info');
    p2mdRenderStatus(d.files.map(f => ({ ...f, message: 'Đang chờ…' })));
    p2mdPollTimer = setInterval(() => p2mdPoll(), 2000);
    p2mdPoll();
  } catch (e) {
    showToast('Lỗi kết nối: ' + e.message, 'error');
    p2mdResetBtn();
  }
}

function p2mdResetBtn() {
  $('p2md-start-btn').disabled = p2mdPending.length === 0;
  $('p2md-start-btn').textContent = '🚀 Bắt đầu OCR';
}

async function p2mdPoll() {
  if (!p2mdJobId) return;
  try {
    const r = await fetch('/api/pdf-to-word/status/' + p2mdJobId);
    const d = await r.json();
    if (!r.ok) return;
    p2mdRenderStatus(d.files);
    if (d.all_done) {
      clearInterval(p2mdPollTimer); p2mdPollTimer = null;
      const ok = d.files.filter(f => f.status === 'done').length;
      const err = d.files.filter(f => f.status === 'error').length;
      showToast(`Hoàn tất: ${ok} file` + (err ? `, ${err} lỗi` : ''),
                err ? 'info' : 'success', 5000);
      $('p2md-start-btn').textContent = '🚀 OCR thêm';
      $('p2md-start-btn').disabled = false;
      p2mdPending = [];
    }
  } catch (e) { /* tiếp tục poll */ }
}

function p2mdRenderStatus(files) {
  const list = $('p2md-list');
  list.innerHTML = '';
  files.forEach(f => {
    const row = el('div', 'p2w-row');
    let icon = '⏳', cls = '';
    if (f.status === 'processing') { icon = '🔄'; cls = 'proc'; }
    else if (f.status === 'done')  { icon = '✅'; cls = 'done'; }
    else if (f.status === 'error') { icon = '❌'; cls = 'err'; }
    let right;
    if (f.status === 'done') {
      right = `<button class="p2w-dl" onclick="p2mdDownload(${f.index},'${(f.out_name||'').replace(/'/g,'')}')">⬇ Tải Markdown</button>`
            + `<span class="p2w-status">${f.seconds || ''}s</span>`;
    } else {
      right = `<span class="p2w-status">${f.message || ''}</span>`;
    }
    row.innerHTML = `<span class="p2w-name ${cls}">${icon} ${f.name}</span>${right}`;
    list.appendChild(row);
  });
}

function p2mdDownload(index, name) {
  const a = document.createElement('a');
  a.href = `/api/pdf-to-word/result/${p2mdJobId}/${index}`;
  a.download = name || '';
  document.body.appendChild(a);
  a.click();
  a.remove();
}

// Phím tắt Ctrl+S để lưu
document.addEventListener('keydown', (e) => {
  if ((e.ctrlKey || e.metaKey) && e.key === 's') {
    e.preventDefault();
    saveDocument();
  }
});

// Đóng modal khi click overlay
document.addEventListener('click', (e) => {
  if (e.target.classList.contains('modal-overlay')) {
    e.target.classList.remove('show');
  }
  if (e.target.id === 'print-preview') {
    e.target.classList.remove('show');
  }
});

// Splitter resize
(function initSplitter() {
  const splitter = document.getElementById('splitter');
  const left = document.getElementById('panel-left');
  if (!splitter || !left) return;
  let dragging = false, startX = 0, startW = 0;
  splitter.addEventListener('mousedown', e => {
    dragging = true; startX = e.clientX; startW = left.offsetWidth;
    splitter.classList.add('dragging');
    document.body.style.cursor = 'col-resize';
    document.body.style.userSelect = 'none';
  });
  document.addEventListener('mousemove', e => {
    if (!dragging) return;
    const dx = e.clientX - startX;
    const newW = Math.max(200, Math.min(startW + dx, window.innerWidth * 0.65));
    left.style.width = newW + 'px';
  });
  document.addEventListener('mouseup', () => {
    if (!dragging) return;
    dragging = false;
    splitter.classList.remove('dragging');
    document.body.style.cursor = '';
    document.body.style.userSelect = '';
  });
})();
