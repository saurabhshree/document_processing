r"""
copy_invoice_files.py
---------------------
Copies files from a Windows network share to a local destination folder.
Uses parallel processing for fast scanning and copying.

Features:
  • Checkpoint/resume: progress is saved to a JSON file after each file.
    If the program is interrupted, re-running it skips already-processed files.
  • Source cleanup: successfully copied files are deleted from the source.

Conditions (ALL must match):
  1. File type     : PDF (.pdf) or Image (.jpg, .jpeg, .png, .bmp, .tiff, .tif, .gif, .webp)
  2. Invoice match : Filename contains "invoice"
                     OR file content contains ANY of the KEYWORD_CONTENT list

Source : \\192.168.100.1\AIC-Infotech\IT-ADMIN\IT-HW\HW-PROCUREMENT
Dest   : C:\Users\saurabhs\OneDrive - AGRICULTURE INSURANCE COMPANY OF INDIA LIMITED\Desktop\Vendor Management System\Invoice\
"""

import os
import json
import shutil
import time
import threading
from pathlib import Path
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
import pytesseract                   # OCR text from images
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
 
    OCR_SUPPORT = True
except ImportError:
    OCR_SUPPORT = False
    print("[WARN] Pillow / pytesseract not installed. Image content scanning disabled.")
    print("       Run: pip install pillow pytesseract\n")


# ─────────────────────────── CONFIGURE HERE ───────────────────────────────────
SOURCE_DIR       = r"\\192.168.100.1\AIC-Infotech\IT-ADMIN\IT-HW\HW-PROCUREMENT"
DEST_DIR         = r"C:\Users\saurabhs\OneDrive - AGRICULTURE INSURANCE COMPANY OF INDIA LIMITED\Desktop\Vendor Management System\Invoice\Hardware-consumable"

KEYWORD_FILENAME = "invoice"        # checked against filename (case-insensitive)
KEYWORD_CONTENT  = [                # ANY one match in file content is enough
    "invoice date",
    "invoice no",
    
]

PDF_EXTS     = {".pdf"}
IMAGE_EXTS   = {".jpg", ".jpeg", ".png", ".bmp", ".tiff", ".tif", ".gif", ".webp"}
ALLOWED_EXTS = PDF_EXTS | IMAGE_EXTS

MAX_WORKERS         = 8       # optimal for SMB shares (8–16 keeps the pipe full without flooding the server)
MAX_RETRIES         = 5       # number of connection attempts to the file server
RETRY_DELAY_SECONDS = 10      # initial delay between retries (doubles each attempt)

# Checkpoint file — stored next to this script
CHECKPOINT_FILE = Path(__file__).with_name("copy_invoice_checkpoint.json")
DELETE_SOURCE_AFTER_COPY = False   # keep source files intact after copy
# ──────────────────────────────────────────────────────────────────────────────

# ── Thread-safe counters ──────────────────────────────────────────────────────
lock          = threading.Lock()
count_copied  = 0
count_skipped = 0
count_errors  = 0
count_done    = 0   # files fully processed (copied + skipped + errored)
count_resumed = 0   # files skipped because already in checkpoint
total_matched = 0   # files that passed the filter and are queued for copying
# ─────────────────────────────────────────────────────────────────────────────


# ─────────────────────────── CHECKPOINT HELPERS ──────────────────────────────

def load_checkpoint() -> dict:
    """
    Load the checkpoint file.
    Returns a dict mapping source-file path (str) -> status info dict.
    Example entry:
        "\\\\server\\share\\folder\\invoice.pdf": {
            "status": "copied",
            "dest":   "C:\\dest\\invoice.pdf",
            "timestamp": "2025-06-18T12:30:00"
        }
    """
    if CHECKPOINT_FILE.exists():
        try:
            data = json.loads(CHECKPOINT_FILE.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
        except (json.JSONDecodeError, OSError) as e:
            print(f"  [WARN] Could not read checkpoint file — starting fresh. ({e})")
    return {}


def save_checkpoint(checkpoint: dict) -> None:
    """Atomically write the checkpoint dict to disk (write-then-rename)."""
    tmp_path = CHECKPOINT_FILE.with_suffix(".tmp")
    try:
        tmp_path.write_text(json.dumps(checkpoint, indent=2, ensure_ascii=False),
                            encoding="utf-8")
        # Atomic rename (on Windows, need to remove target first)
        if CHECKPOINT_FILE.exists():
            CHECKPOINT_FILE.unlink()
        tmp_path.rename(CHECKPOINT_FILE)
    except OSError as e:
        print(f"\n  [WARN] Could not save checkpoint: {e}")


# Shared mutable checkpoint dict (guarded by `lock`)
checkpoint: dict = {}

# ─────────────────────────────────────────────────────────────────────────────


def keyword_in_filename(filename: str, keyword: str) -> bool:
    return keyword.lower() in filename.lower()


def any_keyword_in_text(text: str, keywords: list) -> str:
    """Return first matched keyword found in text, else empty string."""
    text_lower = text.lower()
    for kw in keywords:
        if kw.lower() in text_lower:
            return kw
    return ""


def keyword_in_pdf(filepath: Path, keywords: list) -> str:
    if not PDF_SUPPORT:
        return ""
    try:
        doc = fitz.open(str(filepath))
        for page in doc:
            matched = any_keyword_in_text(page.get_text(), keywords)
            if matched:
                doc.close()
                return matched
        doc.close()
    except Exception as e:
        print(f"  [WARN]   Could not read PDF: {filepath.name} — {e}")
    return ""


def keyword_in_image(filepath: Path, keywords: list) -> str:
    if not OCR_SUPPORT:
        return ""
    try:
        img     = Image.open(str(filepath))
        text    = pytesseract.image_to_string(img)
        matched = any_keyword_in_text(text, keywords)
        return matched
    except Exception as e:
        print(f"  [WARN]   Could not OCR image: {filepath.name} — {e}")
    return ""


def should_copy(filepath: Path) -> tuple[bool, str]:
    """
    Check if file matches all conditions.
    Returns (True, reason) or (False, '').
    """
    ext = filepath.suffix.lower()

    if ext not in ALLOWED_EXTS:
        return False, ""

    if keyword_in_filename(filepath.name, KEYWORD_FILENAME):
        return True, f"filename contains '{KEYWORD_FILENAME}'"

    if ext in PDF_EXTS:
        matched = keyword_in_pdf(filepath, KEYWORD_CONTENT)
        if matched:
            return True, f"PDF content contains '{matched}'"
    elif ext in IMAGE_EXTS:
        matched = keyword_in_image(filepath, KEYWORD_CONTENT)
        if matched:
            return True, f"image OCR contains '{matched}'"

    return False, ""


def print_progress():
    """Print a live progress line (overwrites previous line)."""
    with lock:
        processed = count_done
        remaining = total_matched - count_done
        line = (
            f"  Progress → "
            f"Total matched: {total_matched} | "
            f"Copied: {count_copied} | "
            f"Skipped: {count_skipped} | "
            f"Errors: {count_errors} | "
            f"Processed: {processed} | "
            f"Remaining: {remaining}"
        )
    # \r overwrites the same line in the terminal
    print(f"\r{line:<120}", end="", flush=True)


def copy_file_task(src_file: Path, dest_path: Path) -> dict:
    """
    Worker function run in a thread.
    Copies src_file to dest_path if it matches conditions.
    On success, deletes the source file and records progress in the checkpoint.
    Returns a result dict.
    """
    global count_copied, count_skipped, count_errors, count_done

    src_key = str(src_file)
    result  = {"file": src_file.name, "src_path": src_key, "status": "", "reason": ""}

    match, reason = should_copy(src_file)

    if not match:
        # Record skipped files in checkpoint so they aren't re-scanned
        with lock:
            count_skipped += 1
            count_done    += 1
            checkpoint[src_key] = {
                "status":    "skipped",
                "reason":    "no keyword match",
                "timestamp": datetime.now().isoformat(),
            }
            save_checkpoint(checkpoint)
        result["status"] = "skipped"
        result["reason"] = "no keyword match"
        print_progress()
        return result

    # Handle filename collisions
    dest_file = dest_path / src_file.name
    if dest_file.exists():
        stem    = dest_file.stem
        suffix  = dest_file.suffix
        counter = 1
        while dest_file.exists():
            dest_file = dest_path / f"{stem}_{counter}{suffix}"
            counter  += 1
        result["renamed_to"] = dest_file.name

    try:
        shutil.copy2(src_file, dest_file)

        # ── Source cleanup ────────────────────────────────────────────────
        if DELETE_SOURCE_AFTER_COPY:
            try:
                src_file.unlink()
                result["source_deleted"] = True
            except Exception as del_err:
                result["source_deleted"] = False
                result["delete_error"]   = str(del_err)
                print(f"\n  [WARN] Copied but could not delete source: {src_file.name} — {del_err}")

        with lock:
            count_copied += 1
            count_done   += 1
            checkpoint[src_key] = {
                "status":    "copied",
                "dest":      str(dest_file),
                "reason":    reason,
                "source_deleted": result.get("source_deleted", False),
                "timestamp": datetime.now().isoformat(),
            }
            save_checkpoint(checkpoint)
        result["status"] = "copied"
        result["reason"] = reason

    except PermissionError:
        with lock:
            count_errors += 1
            count_done   += 1
            checkpoint[src_key] = {
                "status":    "error",
                "reason":    "Permission denied",
                "timestamp": datetime.now().isoformat(),
            }
            save_checkpoint(checkpoint)
        result["status"] = "error"
        result["reason"] = "Permission denied"

    except Exception as e:
        with lock:
            count_errors += 1
            count_done   += 1
            checkpoint[src_key] = {
                "status":    "error",
                "reason":    str(e),
                "timestamp": datetime.now().isoformat(),
            }
            save_checkpoint(checkpoint)
        result["status"] = "error"
        result["reason"] = str(e)

    print_progress()
    return result


def collect_candidate_files(source_path: Path) -> list[Path]:
    """
    Walk source directory and return all PDF/image files.
    (Fast scan — no content reading yet.)
    """
    candidates = []
    print(f"  Scanning source directory... ", end="", flush=True)
    for root, dirs, files in os.walk(source_path):
        for filename in files:
            filepath = Path(root) / filename
            if filepath.suffix.lower() in ALLOWED_EXTS:
                candidates.append(filepath)
    print(f"{len(candidates)} PDF/image file(s) found.\n")
    return candidates


def connect_to_source(source_path: Path,
                      max_retries: int = MAX_RETRIES,
                      base_delay: float = RETRY_DELAY_SECONDS) -> bool:
    """
    Attempt to reach the network share with exponential-backoff retries.
    Returns True if the source becomes accessible, False otherwise.
    """
    for attempt in range(1, max_retries + 1):
        if source_path.exists():
            if attempt > 1:
                print(f"  [OK]   Connected on attempt {attempt}.\n")
            return True

        delay = base_delay * (2 ** (attempt - 1))   # exponential backoff
        if attempt < max_retries:
            print(
                f"  [RETRY {attempt}/{max_retries}]  "
                f"Source path not accessible. Retrying in {delay:.0f}s…"
            )
            time.sleep(delay)
        else:
            print(
                f"  [FAIL {attempt}/{max_retries}]  "
                f"Source path still not accessible after {max_retries} attempts."
            )

    return False


def copy_invoice_files():
    global total_matched, count_copied, count_skipped, count_errors, count_done
    global count_resumed, checkpoint

    source_path = Path(SOURCE_DIR)
    dest_path   = Path(DEST_DIR)

    # Validate source with retries
    if not connect_to_source(source_path):
        print(f"\n[ERROR] Source path not accessible: {source_path}")
        print("        Make sure you are connected to the network/VPN.")
        return

    dest_path.mkdir(parents=True, exist_ok=True)

    # ── Load checkpoint ──────────────────────────────────────────────────────
    checkpoint = load_checkpoint()
    if checkpoint:
        # Only count entries that were fully successful (copied/skipped)
        done_keys = {k for k, v in checkpoint.items()
                     if v.get("status") in ("copied", "skipped")}
        print(f"  [RESUME] Loaded checkpoint with {len(done_keys)} "
              f"previously processed file(s).")
        # Remove error entries so they get retried
        error_keys = [k for k, v in checkpoint.items()
                      if v.get("status") == "error"]
        for k in error_keys:
            del checkpoint[k]
        if error_keys:
            print(f"  [RESUME] {len(error_keys)} previously errored file(s) "
                  f"will be retried.")
            save_checkpoint(checkpoint)
    else:
        done_keys = set()
        print("  [FRESH]  No checkpoint found — starting from scratch.")

    print(f"\n{'='*65}")
    print(f"  Invoice File Copier  —  Parallel Edition (with Checkpoint)")
    print(f"  Started          : {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  Source           : {source_path}")
    print(f"  Dest             : {dest_path}")
    print(f"  Filename keyword : '{KEYWORD_FILENAME}'  (case-insensitive)")
    print(f"  Content keywords : {KEYWORD_CONTENT}")
    print(f"  File types       : PDF, JPG, JPEG, PNG, BMP, TIFF, GIF, WEBP")
    print(f"  Parallel workers : {MAX_WORKERS}")
    print(f"  Source cleanup   : {'ON' if DELETE_SOURCE_AFTER_COPY else 'OFF'}")
    print(f"  Checkpoint file  : {CHECKPOINT_FILE}")
    print(f"{'='*65}\n")

    # Step 1: collect all candidate files (fast walk, no content read)
    candidates = collect_candidate_files(source_path)

    # Step 2: filter out already-processed files
    remaining_candidates = []
    for f in candidates:
        if str(f) in done_keys:
            count_resumed += 1
        else:
            remaining_candidates.append(f)

    total_matched = len(remaining_candidates)

    if count_resumed:
        print(f"  [RESUME] {count_resumed} file(s) skipped (already processed).")

    if total_matched == 0:
        print("  No new PDF/image files to process. Nothing to do.")
        return

    print(f"  {total_matched} file(s) remaining to process.\n")
    print(f"  Starting parallel processing with {MAX_WORKERS} workers...\n")

    results = []

    # Step 3: process files in parallel
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {
            executor.submit(copy_file_task, f, dest_path): f
            for f in remaining_candidates
        }
        for future in as_completed(futures):
            try:
                result = future.result()
                results.append(result)
            except Exception as e:
                src = futures[future]
                results.append({"file": src.name, "status": "error", "reason": str(e)})
                with lock:
                    count_errors += 1
                    count_done   += 1
                    checkpoint[str(src)] = {
                        "status":    "error",
                        "reason":    str(e),
                        "timestamp": datetime.now().isoformat(),
                    }
                    save_checkpoint(checkpoint)
                print_progress()

    # Move to new line after progress bar
    print("\n")

    # ── Detailed log ─────────────────────────────────────────────────────────
    print(f"  {'─'*75}")
    print(f"  {'FILE':<45} {'STATUS':<10} REASON")
    print(f"  {'─'*75}")
    for r in sorted(results, key=lambda x: x["status"]):
        name    = r.get("renamed_to", r["file"])
        status  = r["status"].upper()
        reason  = r["reason"]
        deleted = ""
        if r.get("source_deleted") is True:
            deleted = " [src removed]"
        elif r.get("source_deleted") is False:
            deleted = " [src kept — delete failed]"
        print(f"  {name:<45} {status:<10} {reason}{deleted}")

    # ── Final Summary ─────────────────────────────────────────────────────────
    print(f"\n{'='*65}")
    print(f"  Final Summary")
    print(f"{'='*65}")
    print(f"  PDF/image files found in source  : {len(candidates)}")
    print(f"  Skipped (already in checkpoint)  : {count_resumed}")
    print(f"  Files copied (matched criteria)  : {count_copied}")
    print(f"  Files skipped (no keyword match) : {count_skipped}")
    print(f"  Errors                           : {count_errors}")
    print(f"  Finished : {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*65}\n")

    if count_errors == 0:
        print("  ✓ All files processed successfully.\n")
    else:
        print(f"  ⚠ {count_errors} error(s) occurred — re-run to retry failed files.\n")


if __name__ == "__main__":
    copy_invoice_files()
