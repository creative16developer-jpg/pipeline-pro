"""
Image processing pipeline:
  1. Download image from URL
  2. Lossless compress (PNG) or quality-optimise (JPEG/WebP)
  3. Save as WebP for web delivery

Watermarking has been removed entirely per client requirement (Developer
Guidelines v2.0, Section 9.2): no watermark setting, no watermark processing.

Usage:
    processor = ImageProcessor(output_dir="images/processed")
    result = await processor.process(url, sku)
"""

import asyncio
import hashlib
import os
from pathlib import Path
from typing import Optional

import httpx
from PIL import Image


_SETTINGS_FILE = Path(__file__).resolve().parent.parent / "config_store" / "image_settings.json"
_SETTINGS_DEFAULTS = {
    "output_format": "webp", "max_width": 1200, "max_height": 1200,
    "keep_original_size": False, "exact_size": True,
    "compression_enabled": True, "compression_quality": 85,
    "max_images_per_product": 5, "skip_last_image": False,
}


def load_image_settings() -> dict:
    """Settings -> Image Processing (config_store/image_settings.json).
    Before, this page was saved but never read: Process always used
    1200x1200 max, WebP 85, 5 images."""
    import json
    s = dict(_SETTINGS_DEFAULTS)
    try:
        if _SETTINGS_FILE.exists():
            s.update(json.loads(_SETTINGS_FILE.read_text()) or {})
    except Exception as e:
        print(f"[ImageProcessor] image settings not readable ({e}) -- defaults used")
    return s


def pick_images(items: list, settings: dict) -> list:
    """Which of Sunsky's images to use: drop the last one when "Skip Last
    Image" is on (and there is more than one), then the first N."""
    items = list(items)
    if settings.get("skip_last_image") and len(items) > 1:
        items = items[:-1]
    try:
        n = int(settings.get("max_images_per_product") or 5)
    except (TypeError, ValueError):
        n = 5
    return items[:max(1, min(n, 10))]


class ImageProcessor:
    def __init__(
        self,
        output_dir: str = "images/processed",
        raw_dir: str = "images/raw",
        max_size: tuple[int, int] = (1200, 1200),
        webp_quality: int = 85,
        output_format: str = "webp",
        exact_size: bool = False,
        keep_original_size: bool = False,
    ):
        self.output_dir = Path(output_dir)
        self.raw_dir = Path(raw_dir)
        self.max_size = max_size
        self.webp_quality = webp_quality
        self.output_format = output_format if output_format in ("webp", "jpeg", "png") else "webp"
        self.exact_size = exact_size
        self.keep_original_size = keep_original_size
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.raw_dir.mkdir(parents=True, exist_ok=True)

    @classmethod
    def from_settings(cls, settings: Optional[dict] = None) -> "ImageProcessor":
        s = settings or load_image_settings()
        def _int(k, d, lo, hi):
            try:
                return max(lo, min(hi, int(s.get(k) or d)))
            except (TypeError, ValueError):
                return d
        quality = _int("compression_quality", 85, 1, 100) if s.get("compression_enabled", True) else 95
        return cls(max_size=(_int("max_width", 1200, 100, 5000), _int("max_height", 1200, 100, 5000)),
                   webp_quality=quality, output_format=str(s.get("output_format") or "webp").lower(),
                   exact_size=bool(s.get("exact_size", True)),
                   keep_original_size=bool(s.get("keep_original_size", False)))

    async def process_from_bytes(self, image_bytes: bytes, sku: str, position: int = 0, ext: str = "jpg") -> Optional[str]:
        """
        Process an image supplied as raw bytes (e.g. extracted from a ZIP archive).
        Returns the saved WebP file path, or None on failure.
        """
        try:
            safe_sku = sku.replace("/", "_").replace("\\", "_")
            if ext not in ("jpg", "jpeg", "png", "webp", "gif"):
                ext = "jpg"
            raw_filename = f"{safe_sku}_{position}.{ext}"
            raw_path = self.raw_dir / raw_filename
            raw_path.write_bytes(image_bytes)

            processed_path = await asyncio.get_event_loop().run_in_executor(
                None, self._process_sync, raw_path, sku, position
            )
            return processed_path
        except Exception as e:
            print(f"[ImageProcessor] Error processing bytes for {sku} pos {position}: {e}")
            return None

    async def process(self, url: str, sku: str, position: int = 0) -> Optional[str]:
        """
        Download, process, and save an image. Returns the saved file path.
        """
        try:
            raw_path = await self._download(url, sku, position)
            if not raw_path:
                return None
            processed_path = await asyncio.get_event_loop().run_in_executor(
                None, self._process_sync, raw_path, sku, position
            )
            return processed_path
        except Exception as e:
            print(f"[ImageProcessor] Error processing {url}: {e}")
            return None

    async def _download(self, url: str, sku: str, position: int) -> Optional[Path]:
        safe_sku = sku.replace("/", "_").replace("\\", "_")
        ext = url.split(".")[-1].split("?")[0].lower()
        if ext not in ("jpg", "jpeg", "png", "webp", "gif"):
            ext = "jpg"
        filename = f"{safe_sku}_{position}.{ext}"
        dest = self.raw_dir / filename

        if dest.exists():
            return dest

        try:
            async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
                resp = await client.get(url)
                resp.raise_for_status()
                dest.write_bytes(resp.content)
            return dest
        except Exception as e:
            print(f"[ImageProcessor] Download failed {url}: {e}")
            return None

    def _process_sync(self, raw_path: Path, sku: str, position: int) -> str:
        safe_sku = sku.replace("/", "_").replace("\\", "_")
        ext = {"webp": "webp", "jpeg": "jpg", "png": "png"}[self.output_format]
        out_file = self.output_dir / f"{safe_sku}_{position}.{ext}"

        with Image.open(raw_path) as img:
            img = img.convert("RGBA")
            w, h = self.max_size
            if self.keep_original_size:
                pass
            elif self.exact_size:
                # Every image exactly W x H (client: set 1200x1200, but
                # Sunsky's 800/1000 px photos stayed 800/1000 because
                # thumbnail() only ever shrinks). Scaled up or down to fit,
                # centred on white so the proportions are never stretched.
                scale = min(w / img.width, h / img.height)
                if scale != 1:
                    img = img.resize((max(1, round(img.width * scale)), max(1, round(img.height * scale))),
                                     Image.LANCZOS)
                if img.size != (w, h):
                    canvas = Image.new("RGBA", (w, h), (255, 255, 255, 255))
                    canvas.paste(img, ((w - img.width) // 2, (h - img.height) // 2), img)
                    img = canvas
            else:
                img.thumbnail(self.max_size, Image.LANCZOS)   # only shrinks

            rgb = Image.new("RGB", img.size, (255, 255, 255))
            rgb.paste(img, mask=img.split()[3])
            if self.output_format == "jpeg":
                rgb.save(str(out_file), "JPEG", quality=self.webp_quality, optimize=True, progressive=True)
            elif self.output_format == "png":
                rgb.save(str(out_file), "PNG", optimize=True)
            else:
                rgb.save(str(out_file), "WEBP", quality=self.webp_quality, method=6)

        return str(out_file)
