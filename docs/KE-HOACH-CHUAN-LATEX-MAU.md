# Kế hoạch: Xuất Word đúng chuẩn LaTeX theo file mẫu

> **✅ HOÀN THÀNH 04/07/2026** — Bước 0: AIOMT PASS cả 7 test (`\circ` + `\text{}` OK,
> đảo ngược ghi chú 16/06). Bước 1-4: `to_mau_standard()` đã viết, cắm vào 2 đường xuất,
> prompt AI cập nhật, 24/24 unit + E2E PASS. Còn lại: thầy verify file
> `Downloads\test-xuat-word-chuan-mau.docx` qua AIOMT + dùng thật 1 đề dài.
>
> Tạo: 04/07/2026 · Nguồn chuẩn: `mẫu soạn latex.pdf` (đề "Lớp Lý Thầy Quân")
> Mục tiêu: mọi đường xuất Word (Xuất Word từ editor + PDF→Word) ra ký hiệu/phương trình
> Vật lý đúng chuẩn `$...$` như file mẫu, để AIOMT chuyển thành công thức Word chính xác.

## A. Chuẩn đích (rút từ file mẫu)

| # | Quy tắc | Ví dụ trong mẫu |
|---|---------|-----------------|
| 1 | Số + đơn vị ĐƠN GIẢN → text thường, KHÔNG bọc `$` | `1,5J` · `20N` · `30 m` · `5kg` · `1475 J` |
| 2 | Có cấu trúc toán (mũ, =, phân số…) → bọc `$...$`, đơn vị trong `\text{...}` | `$1,0\text{cm}^2$` · `$g = 10\text{m/s}^2$` |
| 3 | Nhiệt độ trong math → `^\circ\text{C}` (KHÔNG dùng ký tự ° thật) | `$25^\circ\text{C}$` · `$0,235^\circ\text{C}$` |
| 4 | Ký hiệu khoa học → `.10^n` (dấu chấm, KHÔNG `\cdot`) | `$2,0.10^5\text{Pa}$` |
| 5 | Số mũ đơn vị NGOÀI `\text{}` | `\text{cm}^2` · `\text{m/s}^2` |
| 6 | Dấu thập phân phẩy TRẦN trong math | `0,460` (không phải `0{,}460`) |
| 7 | Đơn vị ghép giữ nguyên dấu chấm trong `\text{}` | `\text{kJ/kg.K}` · `\text{m/s}` |

## B. Hiện trạng lệch chuẩn

| Chỗ | Đang làm | Phải thành |
|-----|----------|-----------|
| `latex_normalize.py` `unicode_to_latex` | đơn vị để trần, không `\text{}` | bọc `\text{}` khi nằm trong `$` |
| `latex_normalize.py` (° policy) | ° thật, text thường (ghi chú 16/06: "AIOMT không hiểu `\circ`") | `$...^\circ\text{C}$` — **cần test lại AIOMT** |
| `_fix_unit_exponents` | `m/s2` → `m/s²` (Unicode ²) | → `$\text{m/s}^2$` |
| Prompt `_GEMINI_PROMPT` (pdf_to_word.py) | dạy `$1{,}5 \cdot 10^{6}$ Hz` (đơn vị ngoài $) | dạy `$1,5.10^{6}\text{Hz}$` |
| Prompt ai_solver.py | không quy định `\text{}` / `^\circ` | thêm quy tắc chuẩn mẫu |

## C. Các bước thực hiện

### Bước 0 — Chốt chuẩn với AIOMT (LÀM TRƯỚC, quyết định cả kế hoạch)
Tạo file Word test tay chứa đúng các chuỗi: `$25^\circ\text{C}$`, `$1,0\text{cm}^2$`,
`$2,0.10^5\text{Pa}$`, `$g = 10\text{m/s}^2$`, `$c = 0,460\text{kJ/kg.K}$`.
Thầy chạy AIOMT Premium V6.7 → xác nhận:
- [ ] `\circ` chuyển đúng? (ghi chú cũ nói KHÔNG — mẫu lại dùng → phải test lại)
- [ ] `\text{...}` chuyển đúng?
- [ ] `2,0.10^5` (phẩy trần + chấm nhân) chuyển đúng?
→ Nếu `\circ` fail: phương án B = `$25°\text{C}$` (° thật trong math). Nếu `\text{}` fail: dừng, bàn lại.

### Bước 1 — Module chuẩn hoá mới trong `core/latex_normalize.py`
Hàm `to_mau_standard(text)` (chạy SAU `unicode_to_latex` + `normalize_latex`):
1. **Trong `$...$` sẵn có:** nhận diện đơn vị trần → bọc `\text{}`; số mũ đơn vị tách ra ngoài
   (`m/s^2` → `\text{m/s}^2`); `°C`/`\circ C` → `^\circ\text{C}`; `\cdot 10^` → `.10^`;
   `{,}` → `,`.
2. **Ngoài `$`:** cụm có cấu trúc toán (số mũ Unicode ²³, `.10^n`, °C) → bọc `$...$` theo chuẩn;
   cụm số+đơn vị đơn giản GIỮ NGUYÊN text (theo mẫu — không bọc thừa).
3. Từ điển đơn vị: mở rộng từ `_UNIT_EXP_RE` hiện có (thêm V, A, T, Wb, K, mol, l/lít, g/cm3,
   J/kg.K, kJ/kg.K, W/m2, µF, mH, eV, MeV…).
4. Sửa `_fix_unit_exponents`: thay đích Unicode ² bằng đích `$\text{...}^n$`.
5. GIỮ nguyên các guard đã có: lớp 10C/11C/12C, Coulomb 200C (ngữ cảnh nhiệt ±40 ký tự),
   `f(a) = g(b)` không unwrap.

### Bước 2 — Cắm vào 2 đường xuất Word
- `core/exporter.py` `export_word`: gọi `to_mau_standard()` ngay trước `write_math_text`
  (sau unicode_to_latex hiện có). `write_math_text` giữ nguyên — vẫn ghi `$...$` literal.
- `core/pdf_to_word.py` `_convert_via_odl` / `_convert_via_hybrid`: thêm `to_mau_standard()`
  vào chuỗi post-process (sau `normalize_latex`, trước Pandoc).
- Pandoc vẫn `--from=markdown-tex_math_dollars-raw_tex` (giữ `$` literal) — không đổi.

### Bước 3 — Cập nhật prompt AI (giảm gánh cho regex)
- `_GEMINI_PROMPT` + `_NOTEBOOKLM_PROMPT` (pdf_to_word.py): đổi quy tắc 3c theo chuẩn mẫu,
  thêm mục "đơn vị bọc \text{}, nhiệt độ ^\circ\text{C}, thập phân phẩy trần".
- `ai_solver.py` (prompt lời giải + Vision): thêm cùng quy tắc.
- Regex Bước 1 vẫn chạy sau AI làm lưới an toàn (AI không tuân 100%).

### Bước 4 — Test
1. **Unit test** (script nhanh, theo skill tdd): ~20 case lấy từ chính file mẫu
   (VÍ DỤ 9, 10) + regression case cũ: `10C` lớp, `200C` Coulomb, `f(a)=g(b)`,
   `4 , 5` thập phân, đáp án `$$A. 15...$$`.
2. **End-to-end:** import 1 PDF đề thật → Xuất Word → thầy chạy AIOMT → so từng công thức
   với file mẫu. Chạy cả đường PDF→Word hybrid.
3. Regression: xuất PDF (reportlab) không vỡ — đường PDF render `$...$` bằng matplotlib,
   cần chắc matplotlib mathtext hiểu `\text{}` (nếu không: strip `\text{}` riêng cho đường PDF).

### Bước 5 — Chốt sổ
- Cập nhật CLAUDE.md (mục tiến độ + quyết định kỹ thuật).
- Cập nhật memory `aiomt-word-latex-tool.md` theo kết quả test Bước 0
  (đặc biệt nếu kết luận về `\circ` đảo ngược ghi chú 16/06).

## D. Rủi ro chính

1. **Mâu thuẫn `\circ`**: memory 16/06 nói AIOMT để đỏ `\circ`, mẫu lại dùng — Bước 0 phân xử.
2. **matplotlib mathtext** (đường xuất PDF + render bảng) không hỗ trợ `\text{}` đầy đủ
   → có thể cần bản "strip" riêng cho đường PDF.
3. **Đơn vị "m", "s" đơn lẻ** dễ bọc nhầm chữ thường → chỉ bọc khi đi liền số VÀ có cấu trúc
   toán (mũ/độ/khoa học), đúng tinh thần mẫu.
4. AI không tuân prompt 100% → regex Bước 1 là chốt chặn cuối, phải đủ mạnh độc lập.
