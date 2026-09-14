import subprocess
from pathlib import Path

from ..config import settings


def ocr_page(image_path: Path) -> str:
    """Run Tesseract OCR with adaptive preprocessing."""
    import cv2
    import numpy as np

    img = cv2.imread(str(image_path), cv2.IMREAD_GRAYSCALE)
    if img is None:
        return ""

    blur = cv2.GaussianBlur(img, (3, 3), 0)
    th = cv2.adaptiveThreshold(blur, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 31, 15)
    prepped = 255 - th

    tmp = image_path.parent / f"_ocr_{image_path.stem}.png"
    cv2.imwrite(str(tmp), prepped)

    try:
        result = subprocess.run(
            [settings.tesseract_path, str(tmp), "stdout", "--psm", "6"],
            capture_output=True,
            text=False,
            timeout=60,
        )
        return result.stdout.decode("utf-8", errors="replace")
    except Exception:
        return ""
    finally:
        tmp.unlink(missing_ok=True)