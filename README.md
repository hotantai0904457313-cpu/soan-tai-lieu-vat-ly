# Ứng dụng Soạn Tài Liệu Dạy Học Vật Lý

Ứng dụng web chạy **local trên Windows** dành cho giáo viên Vật lý THPT (và các môn tự nhiên nói chung): import đề thi / phiếu bài tập từ Word–PDF, chỉnh sửa trực quan, dùng AI tự động giải bài, rồi xuất ra file Word/PDF hoàn chỉnh để in hoặc gửi học sinh.

> 🖥️ Chạy hoàn toàn trên máy cá nhân (localhost) — không cần server, không cần tài khoản, dữ liệu không rời khỏi máy bạn (trừ nội dung gửi lên AI khi bấm giải bài).

## Tính năng chính

- **Import Word / PDF** (kể cả PDF scan): tự nhận diện cấu trúc đề thi 4 phần (Trắc nghiệm · Đúng-Sai · Trả lời ngắn · Tự luận), tách từng câu, giữ hình ảnh và bảng biểu
- **AI giải toàn đề**: gửi đề lên Gemini / Claude / 9Router, nhận lời giải ngắn gọn chèn ngay dưới từng câu; hỗ trợ **Vision** đọc đồ thị, hình vẽ trong câu hỏi
- **Ẩn / hiện đáp án**: xuất "bản học sinh" (chỉ đề) hoặc "bản đáp án" (kèm lời giải) — chọn được từng câu
- **Xuất Word (.docx)**: công thức dạng LaTeX `$...$` chuẩn (tương thích add-in chuyển công thức như AIOMT), bảng số liệu render thành ảnh, header tên giáo viên + trường
- **Xuất PDF**: font Times New Roman nhúng đầy đủ tiếng Việt, header/footer tự động
- **PDF → Word**: chuyển PDF (cả bản scan) thành .docx với công thức toán sửa được — pipeline OpenDataLoader + Gemini Vision lai theo trang, hoặc Marker chạy offline
- **PDF → Markdown**: OCR PDF scan thành file .md sạch để nạp vào NotebookLM
- **Thư viện tài liệu**: lưu SQLite, lọc theo lớp / chương / học kỳ, tìm kiếm
- **Offline-first**: mọi tính năng ngoài AI vẫn chạy khi mất mạng

## Cài đặt

### Yêu cầu

| Thành phần | Ghi chú |
|---|---|
| Windows 10/11 | Ứng dụng thiết kế cho Windows |
| Python 3.10+ | Đã test trên 3.14 |
| [Pandoc](https://pandoc.org/installing.html) | Bắt buộc cho xuất Word chuẩn công thức |
| API key [Google Gemini](https://aistudio.google.com/apikey) | Miễn phí — cần cho tính năng AI |
| Java 21+ | *Tùy chọn* — cho pipeline PDF→Word OpenDataLoader |
| [9Router](https://www.npmjs.com/package/9router) | *Tùy chọn* — gọi AI qua provider miễn phí |

### Các bước

```bash
git clone https://github.com/<username>/soan-tai-lieu-vat-ly.git
cd soan-tai-lieu-vat-ly
pip install -r requirements.txt
python app.py
```

Mở trình duyệt tại **http://127.0.0.1:5000** → vào **Cài đặt** → nhập tên giáo viên, tên trường, API key Gemini.

> 💡 Các file `start.bat`, `launcher.py`, `autostart.bat` chứa đường dẫn tuyệt đối theo máy tác giả — nếu muốn dùng nút bấm khởi động nhanh, hãy sửa đường dẫn Python/Chrome trong đó theo máy bạn. Chạy `python app.py` trực tiếp thì không cần.

### Tùy chọn: PDF → Word offline bằng Marker

Nếu muốn convert PDF scan không cần internet, tạo venv Python 3.12 riêng tên `venv_marker/` và cài `marker-pdf` (xem `scripts_marker/`). Không bắt buộc — mặc định app dùng đường OpenDataLoader + Gemini Vision.

## Quy trình sử dụng điển hình

1. Bấm **[Nhập file]** → chọn đề Word/PDF
2. Kiểm tra các câu đã tách đúng chưa (panel trái xem bản gốc, panel phải chỉnh sửa)
3. Bấm **[Giải toàn đề]** → AI chèn lời giải dưới từng câu
4. Chỉnh sửa lời giải nếu cần, chọn chế độ **Bản học sinh / Bản đáp án**
5. **[Xuất PDF]** hoặc **[Xuất Word]** → file lưu vào Downloads (+ tự copy sang OneDrive nếu cài đặt)

## Kiến trúc

```
app.py                  # Flask backend
core/
├── importer.py         # Đọc & phân tích cấu trúc Word/PDF
├── exporter.py         # Xuất PDF (ReportLab) / Word (python-docx)
├── ai_solver.py        # Gọi Gemini / Claude / 9Router, streaming SSE
├── pdf_to_word.py      # Pipeline PDF→Word (ODL / Marker / Gemini Vision hybrid)
├── latex_normalize.py  # Chuẩn hoá LaTeX (cân bằng $, đơn vị \text{}, Unicode→LaTeX)
├── document_model.py   # Document / Section / Question
└── db.py               # SQLite (WAL mode)
templates/index.html    # Giao diện single-page
static/                 # CSS + JS thuần (không framework)
```

## Giấy phép

Phát hành theo **GNU GPL v3.0** — bạn được tự do dùng, sửa, phân phối lại, với điều kiện bản phân phối lại cũng phải mở mã nguồn theo cùng giấy phép. Xem file [LICENSE](LICENSE).

## Đóng góp

Issue và Pull Request đều được hoan nghênh. Dự án khởi đầu từ nhu cầu thực tế của một giáo viên Vật lý THPT tại Việt Nam — hy vọng hữu ích cho các thầy cô khác. 🇻🇳
