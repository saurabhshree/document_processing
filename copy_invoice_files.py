r"""
copy_invoice_files.py
---------------------
Copies all files (recursively) whose filename contains "invoice" (case-insensitive)
from a Windows network share to a local destination folder.

Source : \\192.168.100.1\AIC-Infotech\IT-ADMIN\IT-HW\HW-PROCUREMENT
Dest   : C:\Users\saurabhs\OneDrive - AGRICULTURE INSURANCE COMPANY OF INDIA LIMITED\Desktop\Vendor Management System\Invoice\
"""

import os
import sys
import shutil
from pathlib import Path
from datetime import datetime

# Force UTF-8 output so filenames with special characters print correctly on Windows
sys.stdout.reconfigure(encoding='utf-8', errors='replace')

# ─────────────────────────── CONFIGURE HERE ───────────────────────────────────
SOURCE_DIR = r"\\192.168.100.1\AIC-Infotech\IT-ADMIN\IT-HW\HW-PROCUREMENT"
DEST_DIR   = r"C:\Users\saurabhs\OneDrive - AGRICULTURE INSURANCE COMPANY OF INDIA LIMITED\Desktop\Vendor Management System\Invoice"
KEYWORD    = "invoice"          # case-insensitive match against filename
# ──────────────────────────────────────────────────────────────────────────────


def copy_invoice_files(source: str, destination: str, keyword: str) -> None:
    source_path = Path(source)
    dest_path   = Path(destination)

    # Validate source
    if not source_path.exists():
        print(f"[ERROR] Source path not accessible: {source_path}")
        print("        Make sure you are connected to the network and the share is mounted.")
        return

    # Create destination if it doesn't exist
    dest_path.mkdir(parents=True, exist_ok=True)

    copied  = []
    skipped = []
    errors  = []

    print(f"\n{'='*60}")
    print(f"  Invoice File Copier")
    print(f"  Started : {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  Source  : {source_path}")
    print(f"  Dest    : {dest_path}")
    print(f"  Keyword : '{keyword}' (case-insensitive)")
    print(f"{'='*60}\n")

    # Walk the entire source tree recursively
    for root, dirs, files in os.walk(source_path):
        for filename in files:
            if keyword.lower() in filename.lower():
                src_file  = Path(root) / filename
                dest_file = dest_path / filename

                # Handle filename collisions: append a counter suffix
                if dest_file.exists():
                    stem      = dest_file.stem
                    suffix    = dest_file.suffix
                    counter   = 1
                    while dest_file.exists():
                        dest_file = dest_path / f"{stem}_{counter}{suffix}"
                        counter  += 1
                    print(f"  [RENAME] Collision detected - saving as: {dest_file.name}")

                try:
                    shutil.copy2(src_file, dest_file)   # copy2 preserves metadata
                except PermissionError as e:
                    errors.append((str(src_file), str(e)))
                    print(f"  [SKIP]   {src_file.name} - Permission denied (file in use)")
                    continue
                except Exception as e:
                    errors.append((str(src_file), str(e)))
                    print(f"  [ERROR]  {src_file.name} - {e}")
                    continue

                # Only reached if copy succeeded
                copied.append(str(src_file))
                print(f"  [OK]     {src_file.name} -> {dest_file.name}")

    # ── Summary ──────────────────────────────────────────────────────────────
    print(f"\n{'='*60}")
    print(f"  Summary")
    print(f"{'='*60}")
    print(f"  Files copied  : {len(copied)}")
    print(f"  Errors/Skipped: {len(errors)}")

    if errors:
        print(f"\n  Files that could not be copied:")
        for path, reason in errors:
            print(f"    - {path}")
            print(f"      Reason: {reason}")

    print(f"\n  Finished : {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    copy_invoice_files(SOURCE_DIR, DEST_DIR, KEYWORD)
