from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageSequence

# HEIC: 任意
try:
    from pillow_heif import register_heif_opener  # type: ignore

    register_heif_opener()
    _heif = True
except Exception:
    _heif = False


def heif_available() -> bool:
    return _heif


def open_image_pil(path: str | Path) -> tuple[Image.Image, str | None]:
    """
    画像を RGBA として返す。警告メッセージ（あれば）を返す。
    GIF/アニメ: 1 フレーム目。TIFF/複数ページ: 0 番目。
    """
    path = Path(path)
    ext = path.suffix.lower()
    warn: str | None = None

    if ext in (".heic", ".heif") and not _heif:
        raise OSError("HEIC/HEIF の読み込みには pillow-heif のインストールが必要です。")

    im = Image.open(path)

    if getattr(im, "n_frames", 1) > 1:
        if ext in (".gif",) or im.format == "GIF":
            im.seek(0)
            warn = "アニメーションGIFの先頭フレームのみ使用しました。"
        elif ext in (".tif", ".tiff") or (im.format or "").upper() in ("TIFF", "TIF"):
            im.seek(0)
            warn = "複数ページTIFFの1ページ目のみ使用しました。"

    im = im.convert("RGBA")
    return im, warn
