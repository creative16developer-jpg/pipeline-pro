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


class ImageProcessor:
    def __init__(
        self,
        output_dir: str = "images/processed",
        raw_dir: str = "images/raw",
        max_size: tuple[int, int] = (1200, 1200),
        webp_quality: int = 85,
    ):
        self.output_dir = Path(output_dir)
        self.raw_dir = Path(raw_dir)
        self.max_size = max_size
        self.webp_quality = webp_quality
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.raw_dir.mkdir(parents=True, exist_ok=True)

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
        out_file = self.output_dir / f"{safe_sku}_{position}.webp"

        with Image.open(raw_path) as img:
            img = img.convert("RGBA")

            img.thumbnail(self.max_size, Image.LANCZOS)

            rgb = Image.new("RGB", img.size, (255, 255, 255))
            rgb.paste(img, mask=img.split()[3])
            rgb.save(str(out_file), "WEBP", quality=self.webp_quality, method=6)

        return str(out_file)
