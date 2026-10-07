"""
DOCINT — Tesseract OCR Engine
Implements the OCREngine interface by calling the Tesseract CLI directly via
subprocess — no extra Python packages required (pytesseract is NOT needed).
Best suited for scanned / image-only PDF pages where selectable text is absent.
"""
from __future__ import annotations

import io
import subprocess
import csv
from PIL import Image

from app.core.config import settings
from app.ocr.base import OCREngine, OCRResult, OCRPage, OCRLine, BoundingBox
from app.core.logging import get_logger

logger = get_logger(__name__)

# Default install path for the UB-Mannheim Windows installer
_DEFAULT_WIN_TESSERACT_CMD = r"C:\Program Files\Tesseract-OCR\tesseract.exe"


class TesseractOCREngine(OCREngine):
    """
    Subprocess wrapper around the Tesseract CLI for full-page OCR.
    Unlike TrOCR, Tesseract includes its own line detector, so full pages
    can be passed in directly and it returns structured line-level output.

    No extra Python packages are required — only the Tesseract binary itself.
    """

    def __init__(self) -> None:
        self._is_loaded = False
        self._cmd: str = _DEFAULT_WIN_TESSERACT_CMD

    # ── Abstract property implementations ────────────────────────────────────

    @property
    def engine_name(self) -> str:
        return "tesseract"

    @property
    def engine_version(self) -> str:
        try:
            result = subprocess.run(
                [self._cmd, "--version"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            first_line = (result.stdout or result.stderr).splitlines()[0]
            return first_line.strip()
        except Exception:
            return "unknown"

    # ── Model loading ─────────────────────────────────────────────────────────

    def load_model(self) -> None:
        """
        Tesseract has no Python-side models to load — the binary does it all.
        We just verify the binary is reachable and configure the path.
        """
        if self._is_loaded:
            return

        # Resolve tesseract binary path: env setting → default Windows path
        self._cmd = settings.TESSERACT_CMD or _DEFAULT_WIN_TESSERACT_CMD

        # Quick sanity check — will raise if binary is missing / not executable
        try:
            result = subprocess.run(
                [self._cmd, "--version"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            version_line = (result.stdout or result.stderr).splitlines()[0] if (result.stdout or result.stderr) else "?"
            logger.info("tesseract_loaded", cmd=self._cmd, version=version_line.strip())
        except FileNotFoundError:
            raise RuntimeError(
                f"Tesseract binary not found at '{self._cmd}'. "
                "Please install Tesseract and set TESSERACT_CMD in .env if needed."
            )
        except Exception as exc:
            raise RuntimeError(
                f"Failed to verify Tesseract at '{self._cmd}': {exc}"
            )

        self._is_loaded = True

    # ── Core OCR ──────────────────────────────────────────────────────────────

    def process_images(self, images: list[Image.Image]) -> OCRResult:
        """
        Runs Tesseract on each PIL Image and returns a standard OCRResult.
        Each image is treated as one page; Tesseract handles internal line
        segmentation via PSM 3 (fully automatic page segmentation).

        Strategy:
          1. Write the PIL image to a temp PNG file.
          2. Call:  tesseract <in.png> <out_base> --psm 3 tsv
          3. Parse the TSV output into OCRLine / OCRPage objects.
          4. Clean up temp files.
        """
        if not self._is_loaded:
            self.load_model()

        logger.info("tesseract_processing", pages=len(images))

        ocr_pages: list[OCRPage] = []

        for i, img in enumerate(images):
            # Ensure RGB (Tesseract can choke on RGBA / palette modes)
            if img.mode != "RGB":
                img = img.convert("RGB")

            width, height = img.size
            ocr_lines = self._ocr_image(img, page_index=i)

            page = OCRPage(
                page_number=i + 1,
                width=width,
                height=height,
                lines=ocr_lines,
            )
            ocr_pages.append(page)

        logger.info("tesseract_complete", pages=len(ocr_pages))

        return OCRResult(
            pages=ocr_pages,
            engine_name=self.engine_name,
            engine_version=self.engine_version,
        )

    def process_region(self, region_image: Image.Image) -> tuple[list[OCRLine], float]:
        """
        Process a single cropped region and return the lines and average confidence.
        """
        if not self._is_loaded:
            self.load_model()
            
        lines = self._ocr_image(region_image, page_index=0)
        if not lines:
            return [], 0.0
            
        avg_conf = sum([line.confidence for line in lines]) / len(lines)
        return lines, avg_conf

    # ── Private helpers ───────────────────────────────────────────────────────

    def _ocr_image(self, img: Image.Image, page_index: int) -> list[OCRLine]:
        """
        Calls the Tesseract CLI on a single PIL Image using stdin/stdout pipe mode.

        Pipe mode eliminates two disk I/O operations per page (write temp PNG,
        read temp TSV) by passing image bytes directly to Tesseract stdin and
        capturing TSV output from stdout.

        Tesseract 4.x+ supports:
            tesseract stdin stdout --psm 6 --oem 3 tsv
        where "stdin" / "stdout" are treated as special filenames.
        """
        if img.mode != "RGB":
            img = img.convert("RGB")

        # Encode image to PNG bytes for Tesseract stdin
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        img_bytes = buf.getvalue()

        cmd = [
            self._cmd,
            "stdin",         # Read image from stdin
            "stdout",        # Write TSV output to stdout (no file created)
            "--psm", "6",    # Uniform block of text — best for structured docs
            "--oem", "3",    # LSTM neural net engine
            "tsv",           # Output format
        ]

        try:
            result = subprocess.run(
                cmd,
                input=img_bytes,
                capture_output=True,
                timeout=settings.TESSERACT_TIMEOUT,
            )
            if result.returncode != 0:
                logger.warning(
                    "tesseract_page_failed",
                    page=page_index + 1,
                    stderr=result.stderr[:500].decode("utf-8", errors="replace"),
                )
                return []
        except subprocess.TimeoutExpired:
            logger.error(
                "tesseract_timeout",
                page=page_index + 1,
                timeout_s=settings.TESSERACT_TIMEOUT,
            )
            return []

        # Decode TSV from stdout
        tsv_text = result.stdout.decode("utf-8", errors="replace")
        if not tsv_text.strip():
            logger.warning("tesseract_empty_output", page=page_index + 1)
            return []

        return self._parse_tsv_to_lines(tsv_text)

    @staticmethod
    def _parse_tsv_to_lines(tsv_text: str) -> list[OCRLine]:
        """
        Tesseract TSV format has these columns:
          level, page_num, block_num, par_num, line_num, word_num,
          left, top, width, height, conf, text

        We re-group words by (block_num, par_num, line_num) and merge into lines.
        """
        from collections import defaultdict

        reader = csv.DictReader(io.StringIO(tsv_text), delimiter="\t")

        # Group words by (block, paragraph, line)
        line_groups: dict[tuple, list[dict]] = defaultdict(list)

        for row in reader:
            word_text = row.get("text", "").strip()
            conf_raw = row.get("conf", "-1").strip()

            # Skip empty words and Tesseract's confidence=-1 (non-word rows)
            if not word_text or conf_raw == "-1":
                continue

            try:
                conf = float(conf_raw) / 100.0  # normalize 0-100 → 0-1
                x = int(row["left"])
                y = int(row["top"])
                w = int(row["width"])
                h = int(row["height"])
                block = int(row["block_num"])
                par = int(row["par_num"])
                line = int(row["line_num"])
            except (ValueError, KeyError):
                continue

            key = (block, par, line)
            line_groups[key].append(
                {"text": word_text, "conf": conf, "x": x, "y": y, "w": w, "h": h}
            )

        ocr_lines: list[OCRLine] = []

        for key in sorted(line_groups.keys()):
            words = line_groups[key]
            if not words:
                continue

            line_text = " ".join(w["text"] for w in words)

            # Compute bounding box that encompasses all words in the line
            x1 = min(w["x"] for w in words)
            y1 = min(w["y"] for w in words)
            x2 = max(w["x"] + w["w"] for w in words)
            y2 = max(w["y"] + w["h"] for w in words)

            avg_conf = sum(w["conf"] for w in words) / len(words)

            ocr_lines.append(
                OCRLine(
                    text=line_text,
                    bbox=BoundingBox(x1=x1, y1=y1, x2=x2, y2=y2),
                    confidence=avg_conf,
                )
            )

        return ocr_lines
