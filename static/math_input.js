/**
 * Bảng ký tự Vật lý — chèn ký hiệu vào vị trí con trỏ
 */
const PHYSICS_SYMBOLS = [
  // Chữ Hy Lạp thường dùng
  { sym: 'α', title: 'alpha' }, { sym: 'β', title: 'beta' },
  { sym: 'γ', title: 'gamma' }, { sym: 'δ', title: 'delta' },
  { sym: 'Δ', title: 'Delta' }, { sym: 'ε', title: 'epsilon' },
  { sym: 'λ', title: 'lambda' }, { sym: 'μ', title: 'mu' },
  { sym: 'π', title: 'pi' }, { sym: 'ρ', title: 'rho' },
  { sym: 'σ', title: 'sigma' }, { sym: 'τ', title: 'tau' },
  { sym: 'φ', title: 'phi' }, { sym: 'Φ', title: 'Phi' },
  { sym: 'θ', title: 'theta' }, { sym: 'Θ', title: 'Theta' },
  { sym: 'ω', title: 'omega' }, { sym: 'Ω', title: 'Omega' },
  // Toán học
  { sym: '²', title: 'bình phương' }, { sym: '³', title: 'lập phương' },
  { sym: '√', title: 'căn bậc hai' }, { sym: '∛', title: 'căn bậc ba' },
  { sym: '∞', title: 'vô cực' }, { sym: '≈', title: 'xấp xỉ' },
  { sym: '±', title: 'cộng trừ' }, { sym: '×', title: 'nhân' },
  { sym: '÷', title: 'chia' }, { sym: '≠', title: 'khác' },
  { sym: '≤', title: 'nhỏ hơn hoặc bằng' }, { sym: '≥', title: 'lớn hơn hoặc bằng' },
  { sym: '∫', title: 'tích phân' }, { sym: '∑', title: 'tổng' },
  // Vật lý
  { sym: '→', title: 'vector' }, { sym: '⃗', title: 'mũi tên trên' },
  { sym: '°', title: 'độ' }, { sym: '℃', title: 'độ C' },
  { sym: '‰', title: 'phần nghìn' }, { sym: 'Ω', title: 'Ohm' },
  { sym: 'μ', title: 'micro' }, { sym: 'η', title: 'eta (hiệu suất)' },
];

let _savedRange = null;

function saveCaret(el) {
  const sel = window.getSelection();
  if (sel.rangeCount > 0) {
    const range = sel.getRangeAt(0);
    // Đảm bảo range nằm trong el
    if (el && el.contains(range.commonAncestorContainer)) {
      _savedRange = range.cloneRange();
    }
  }
}

function insertAtCaret(text) {
  const sel = window.getSelection();
  let range;
  if (_savedRange) {
    range = _savedRange;
    sel.removeAllRanges();
    sel.addRange(range);
  } else if (sel.rangeCount > 0) {
    range = sel.getRangeAt(0);
  } else {
    return;
  }
  range.deleteContents();
  const textNode = document.createTextNode(text);
  range.insertNode(textNode);
  range.setStartAfter(textNode);
  range.setEndAfter(textNode);
  sel.removeAllRanges();
  sel.addRange(range);
  _savedRange = range.cloneRange();
}

function buildMathPanel() {
  const panel = document.getElementById('math-panel');
  if (!panel) return;

  const container = document.createElement('div');
  container.className = 'math-symbols';

  PHYSICS_SYMBOLS.forEach(({ sym, title }) => {
    const btn = document.createElement('button');
    btn.className = 'sym-btn';
    btn.textContent = sym;
    btn.title = title;
    btn.addEventListener('mousedown', (e) => {
      e.preventDefault(); // giữ focus editor
      insertAtCaret(sym);
    });
    container.appendChild(btn);
  });

  panel.querySelector('.math-symbols')?.remove();
  panel.insertBefore(container, panel.querySelector('hr') || panel.firstChild.nextSibling);
}

function toggleMathPanel(btn) {
  const panel = document.getElementById('math-panel');
  if (!panel) return;
  if (panel.classList.contains('show')) {
    panel.classList.remove('show');
    return;
  }
  const rect = btn.getBoundingClientRect();
  panel.style.top  = (rect.bottom + 6) + 'px';
  panel.style.left = rect.left + 'px';
  panel.classList.add('show');
}

// Đóng panel khi click ngoài
document.addEventListener('mousedown', (e) => {
  const panel = document.getElementById('math-panel');
  const btn   = document.getElementById('btn-math');
  if (panel && !panel.contains(e.target) && e.target !== btn) {
    panel.classList.remove('show');
  }
});

document.addEventListener('DOMContentLoaded', buildMathPanel);
