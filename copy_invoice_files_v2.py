r"""
copy_invoice_files.py
---------------------
Copies files from a Windows network share to a local destination folder.

Conditions (ALL must match):
  1. File type  : PDF (.pdf) or Image (.jpg, .jpeg, .png, .bmp, .tiff, .tif, .gif, .webp)
  2. Invoice    : Filename contains "invoice"  OR  file content contains "invoice" text

Source : \\192.168.100.1\AIC-Infotech\IT-ADMIN\IT-HW\HW-PROCUREMENT
Dest   : C:\Users\saurabhs\OneDrive - AGRICULTURE INSURANCE COMPANY OF INDIA LIMITED\Desktop\Vendor Management System\Invoice\
"""

import os
import shutil
from pathlib import Path
from datetime import datetime
import pytesseract 
pytesseract.pytesseract.tesseract_cmd = r"C:\Program Files\Tesseract-OCR\tesseract.exe"

# ── Optional libraries (install if not present) ───────────────────────────────
# pip install pymupdf pillow pytesseract
# pytesseract also needs Tesseract-OCR installed:
#   https://github.com/UB-Mannheim/tesseract/wiki  (Windows installer)
# ─────────────────────────────────────────────────────────────────────────────

try:
    import fitz                          # PyMuPDF — read text from PDF
    PDF_SUPPORT = True
except ImportError:
    PDF_SUPPORT = False
    print("[WARN] PyMuPDF not installed. PDF content scanning disabled.")
    print("       Run: pip install pymupdf\n")

try:
    from PIL import Image
                      # OCR text from images
    OCR_SUPPORT = True
except ImportError:
    OCR_SUPPORT = False
    print("[WARN] Pillow / pytesseract not installed. Image content scanning disabled.")
    print("       Run: pip install pillow pytesseract\n")


# ─────────────────────────── CONFIGURE HERE ───────────────────────────────────
SOURCE_DIR = r"\\192.168.100.1\AIC-Infotech\IT-ADMIN\IT-HW\HW-PROCUREMENT"
DEST_DIR   = r"C:\Users\saurabhs\OneDrive - AGRICULTURE INSURANCE COMPANY OF INDIA LIMITED\Desktop\Vendor Management System\Invoice\Hardware-consumable"
KEYWORD    = "invoice"   # case-insensitive

PDF_EXTS   = {".pdf"}
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".tiff", ".tif", ".gif", ".webp"}
ALLOWED_EXTS = PDF_EXTS | IMAGE_EXTS
# ──────────────────────────────────────────────────────────────────────────────


def keyword_in_filename(filename: str, keyword: str) -> bool:
    """Return True if keyword appears in the filename (case-insensitive)."""
    return keyword.lower() in filename.lower()


def keyword_in_pdf(filepath: Path, keyword: str) -> bool:
    """Return True if keyword found in extractable text of a PDF."""
    if not PDF_SUPPORT:
        return False
    try:
        doc = fitz.open(str(filepath))
        for page in doc:
            if keyword.lower() in page.get_text().lower():
                doc.close()
                return True
        doc.close()
    except Exception as e:
        print(f"  [WARN]   Could not read PDF content: {filepath.name} — {e}")
    return False


def keyword_in_image(filepath: Path, keyword: str) -> bool:
    """Return True if keyword found in OCR text of an image."""
    if not OCR_SUPPORT:
        return False
    try:
        img  = Image.open(str(filepath))
        text = pytesseract.image_to_string(img)
        return keyword.lower() in text.lower()
    except Exception as e:
        print(f"  [WARN]   Could not OCR image: {filepath.name} — {e}")
    return False


def should_copy(filepath: Path, keyword: str) -> tuple[bool, str]:
    """
    Returns (True, reason) if the file meets copy conditions, else (False, '').
    Conditions:
      - Extension is PDF or image
      - Filename contains keyword  OR  file content contains keyword
    """
    ext = filepath.suffix.lower()

    # Condition 1: file type check
    if ext not in ALLOWED_EXTS:
        return False, ""

    # Condition 2a: keyword in filename
    if keyword_in_filename(filepath.name, keyword):
        return True, "filename match"

    # Condition 2b: keyword in content
    if ext in PDF_EXTS:
        if keyword_in_pdf(filepath, keyword):
            return True, "PDF content match"
    elif ext in IMAGE_EXTS:
        if keyword_in_image(filepath, keyword):
            return True, "image OCR match"

    return False, ""


def copy_invoice_files(source: str, destination: str, keyword: str) -> None:
    source_path = Path(source)
    dest_path   = Path(destination)

    # Validate source
    if not source_path.exists():
        print(f"[ERROR] Source path not accessible: {source_path}")
        print("        Make sure you are connected to the network/VPN.")
        return

    dest_path.mkdir(parents=True, exist_ok=True)

    copied  = []
    skipped = []
    errors  = []

    print(f"\n{'='*65}")
    print(f"  Invoice File Copier  (PDF + Images only)")
    print(f"  Started  : {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  Source   : {source_path}")
    print(f"  Dest     : {dest_path}")
    print(f"  Keyword  : '{keyword}' (filename OR content, case-insensitive)")
    print(f"  Types    : PDF, JPG, JPEG, PNG, BMP, TIFF, GIF, WEBP")
    print(f"{'='*65}\n")

    for root, dirs, files in os.walk(source_path):
        for filename in files:
            src_file = Path(root) / filename
            ext      = src_file.suffix.lower()

            # Quick skip for non-allowed extensions (no message needed)
            if ext not in ALLOWED_EXTS:
                continue

            match, reason = should_copy(src_file, keyword)

            if not match:
                skipped.append(str(src_file))
                print(f"  [SKIP]   {filename}  (no invoice keyword found)")
                continue

            # Handle filename collisions
            dest_file = dest_path / filename
            if dest_file.exists():
                stem    = dest_file.stem
                suffix  = dest_file.suffix
                counter = 1
                while dest_file.exists():
                    dest_file = dest_path / f"{stem}_{counter}{suffix}"
                    counter  += 1
                print(f"  [RENAME] Collision — saving as: {dest_file.name}")

            try:
                shutil.copy2(src_file, dest_file)
                copied.append(str(src_file))
                print(f"  [OK]     {filename}  →  {dest_file.name}  [{reason}]")
            except PermissionError:
                errors.append((str(src_file), "Permission denied"))
                print(f"  [ERROR]  {filename}  — Permission denied")
            except Exception as e:
                errors.append((str(src_file), str(e)))
                print(f"  [ERROR]  {filename}  — {e}")

    # ── Summary ──────────────────────────────────────────────────────────────
    print(f"\n{'='*65}")
    print(f"  Summary")
    print(f"{'='*65}")
    print(f"  Files copied  : {len(copied)}")
    print(f"  Files skipped : {len(skipped)}  (PDF/image but no invoice keyword)")
    print(f"  Errors        : {len(errors)}")

    if errors:
        print(f"\n  Files with errors:")
        for path, reason in errors:
            print(f"    - {Path(path).name}  ({reason})")

    print(f"\n  Finished : {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*65}\n")


if __name__ == "__main__":
    copy_invoice_files(SOURCE_DIR, DEST_DIR, KEYWORD)
