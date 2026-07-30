"""
Convert PDF -> Word (.docx) qua Marker + Pandoc.

Pipeline:
  1. Marker:  PDF -> Markdown (công thức dạng LaTeX $...$, ảnh trích ra folder)
  2. Pandoc:  Markdown -> DOCX với --from=markdown+tex_math_dollars
              -> công thức thành Native MS Word Equation (sửa được, không phải ảnh)

Dùng:
  python convert.py <file.pdf> [--pages 0-2] [--out output.docx]

Đây là lõi sẽ chuyển vào core/pdf_to_word.py khi tích hợp vào app.
"""
import os
import sys
import time
import shutil
import subprocess
import argparse
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

HERE = Path(__file__).resolve().parent
PROJECT = HERE.parent
VENV_PY = PROJECT / "venv_marker" / "Scripts" / "python.exe"
MARKER_EXE = PROJECT / "venv_marker" / "Scripts" / "marker_single.exe"


def run_marker(pdf_path: Path, work_dir: Path, pages: str | None, disable_ocr: bool = False) -> Path:
    """Chạy marker_single -> trả về đường dẫn file .md."""
    work_dir.mkdir(parents=True, exist_ok=True)
    cmd = [
        str(MARKER_EXE),
        str(pdf_path),
        "--output_format", "markdown",
        "--output_dir", str(work_dir),
        "--disable_multiprocessing",
    ]
    if pages:
        cmd += ["--page_range", pages]
    if disable_ocr:
        cmd += ["--disable_ocr"]  # PDF có sẵn text layer -> bỏ OCR cho nhanh

    env = dict(os.environ)
    env["TORCH_DEVICE"] = "cpu"

    t0 = time.time()
    print(f"[Marker] Đang xử lý {pdf_path.name} ...", flush=True)
    proc = subprocess.run(cmd, env=env, capture_output=True, text=True, encoding="utf-8", errors="replace")
    dt = time.time() - t0
    if proc.returncode != 0:
        print("[Marker] STDERR:", proc.stderr[-2000:], flush=True)
        raise RuntimeError(f"marker_single thất bại (exit {proc.returncode})")
    print(f"[Marker] Xong sau {dt:.1f}s", flush=True)

    # Marker tạo: work_dir/<tên>/<tên>.md
    md_files = list(work_dir.rglob("*.md"))
    if not md_files:
        raise RuntimeError("Không tìm thấy file .md đầu ra của Marker")
    return md_files[0]


def run_pandoc(md_path: Path, out_docx: Path) -> Path:
    """Markdown -> DOCX, giữ công thức thành Word Equation."""
    out_docx.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "pandoc",
        md_path.name,
        "-o", str(out_docx),
        "--from=markdown+tex_math_dollars",
        "--mathjax",
    ]
    t0 = time.time()
    print(f"[Pandoc] {md_path.name} -> {out_docx.name} ...", flush=True)
    # chạy trong thư mục chứa .md để ảnh (đường dẫn tương đối) resolve đúng
    proc = subprocess.run(cmd, cwd=str(md_path.parent), capture_output=True, text=True, encoding="utf-8", errors="replace")
    dt = time.time() - t0
    if proc.returncode != 0:
        print("[Pandoc] STDERR:", proc.stderr[-2000:], flush=True)
        raise RuntimeError(f"pandoc thất bại (exit {proc.returncode})")
    print(f"[Pandoc] Xong sau {dt:.1f}s", flush=True)
    return out_docx


def convert(pdf_path: Path, out_docx: Path | None = None, pages: str | None = None, disable_ocr: bool = False) -> Path:
    pdf_path = Path(pdf_path)
    if out_docx is None:
        out_docx = PROJECT / "data" / "exports" / (pdf_path.stem + ".docx")
    out_docx = Path(out_docx).resolve()  # tuyệt đối -> Pandoc chạy ở cwd khác vẫn đúng chỗ
    work_dir = PROJECT / "data" / "marker_out"

    t0 = time.time()
    md = run_marker(pdf_path, work_dir, pages, disable_ocr)
    docx = run_pandoc(md, Path(out_docx))
    total = time.time() - t0
    print(f"\n✓ HOÀN TẤT trong {total:.1f}s -> {docx}", flush=True)
    return docx


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("pdf")
    ap.add_argument("--pages", default=None, help="VD: 0-2")
    ap.add_argument("--out", default=None)
    ap.add_argument("--no-ocr", action="store_true", help="Tắt OCR (PDF có text layer)")
    args = ap.parse_args()
    convert(args.pdf, args.out, args.pages, args.no_ocr)
