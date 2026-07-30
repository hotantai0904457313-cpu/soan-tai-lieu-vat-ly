"""Kiểm tra PDF có sẵn lớp text không (quyết định có tắt OCR được không)."""
import sys
import pypdfium2 as pdfium

sys.stdout.reconfigure(encoding="utf-8")

pdf = pdfium.PdfDocument(sys.argv[1])
n = min(3, len(pdf))
for i in range(n):
    page = pdf[i]
    tp = page.get_textpage()
    txt = tp.get_text_range()
    print(f"--- Trang {i+1}: {len(txt)} ký tự text ---")
    print(repr(txt[:200]))
    print()
