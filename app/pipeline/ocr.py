import subprocess
import tempfile
from pathlib import Path

from ..config import settings


def ocr_page(image_path: Path) -> str:
    """Run Tesseract OCR with adaptive preprocessing, with RapidOCR fallback."""
    import cv2
    import numpy as np

    img = cv2.imread(str(image_path), cv2.IMREAD_GRAYSCALE)
    if img is None:
        return ""

    blur = cv2.GaussianBlur(img, (3, 3), 0)
    th = cv2.adaptiveThreshold(blur, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 31, 15)
    prepped = 255 - th

    # Write preprocessed image to a proper temp file, not next to the source image
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tf:
        tmp = Path(tf.name)
    cv2.imwrite(str(tmp), prepped)

    try:
        result = subprocess.run(
            [settings.tesseract_path, str(tmp), "stdout", "--psm", "6"],
            capture_output=True,
            text=False,
            timeout=60,
        )
        if result.returncode == 0 and result.stdout:
            out = result.stdout.decode("utf-8", errors="replace")
            if out.strip():
                return out
    except Exception:
        pass
    finally:
        tmp.unlink(missing_ok=True)

    # Fallback to RapidOCR
    try:
        from rapidocr_onnxruntime import RapidOCR

        engine = RapidOCR()
        res, _ = engine(str(image_path))
        if res:
            return "\n".join(item[1] for item in res if len(item) > 1 and item[1])
    except Exception:
        pass

    return ""