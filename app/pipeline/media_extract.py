import cv2
from pathlib import Path

from ..config import settings
from ..storage import storage


def crop_media(page_render_path: Path, bbox: list, media_id: str, media_type: str) -> tuple[str, str]:
    """Crop a media region from a high-res page render.

    bbox is normalized [x0, y0, x1, y1] (0-1).
    Saves the original crop (PNG) and an optimized thumbnail (JPEG).
    Returns (orig_key, thumb_key).
    """
    img = cv2.imread(str(page_render_path))
    if img is None:
        raise ValueError(f"cannot read {page_render_path}")

    h, w = img.shape[:2]
    x0, y0, x1, y1 = bbox
    x0 = max(0, min(1.0, x0))
    x1 = max(x0 + 0.001, min(1.0, x1))
    y0 = max(0, min(1.0, y0))
    y1 = max(y0 + 0.001, min(1.0, y1))

    px0, py0 = int(x0 * w), int(y0 * h)
    px1, py1 = int(x1 * w), int(y1 * h)

    crop = img[py0:py1, px0:px1]
    if crop.size == 0:
        raise ValueError("empty crop")

    # slight padding so figures aren't clipped to the exact text/ink edge
    pad = int(0.01 * min(w, h))
    py0 = max(0, py0 - pad)
    px0 = max(0, px0 - pad)
    py1 = min(h, py1 + pad)
    px1 = min(w, px1 + pad)
    crop = img[py0:py1, px0:px1]

    orig_key = f"media/{media_id}/orig.png"
    thumb_key = f"media/{media_id}/thumb.jpg"
    storage.put_bytes(orig_key, cv2.imencode(".png", crop)[1].tobytes())

    # thumbnail: max width 500, JPEG quality 80
    th = crop.copy()
    max_w = 500
    if th.shape[1] > max_w:
        ratio = max_w / th.shape[1]
        th = cv2.resize(th, (max_w, int(th.shape[0] * ratio)), interpolation=cv2.INTER_AREA)
    storage.put_bytes(thumb_key, cv2.imencode(".jpg", th, [int(cv2.IMWRITE_JPEG_QUALITY), 80])[1].tobytes())

    return orig_key, thumb_key