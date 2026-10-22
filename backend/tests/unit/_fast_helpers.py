"""Synthetic PDFs for the fast_ocr tests (no real documents needed)."""
from __future__ import annotations

import fitz


def make_digital_pdf(path: str) -> str:
    """One A4 page: three 'Label : value' rows and a ruled 4x3 table."""
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    page.insert_text((50, 80), "Name : Rajesh Kumar", fontsize=12)
    page.insert_text((50, 100), "Date : 12 Jun 2026", fontsize=12)
    page.insert_text((50, 120), "Ref No : IV/2041", fontsize=12)
    xs, ys = [50, 200, 350, 500], [200, 230, 260, 290, 320]
    for y in ys:
        page.draw_line((xs[0], y), (xs[-1], y))
    for x in xs:
        page.draw_line((x, ys[0]), (x, ys[-1]))
    grid = [["Item", "Qty", "Price"], ["Pen", "2", "10.50"], ["Book", "1", "99.00"], ["Bag", "3", "450.00"]]
    for r, row in enumerate(grid):
        for c, text in enumerate(row):
            page.insert_text((xs[c] + 8, ys[r] + 20), text, fontsize=11)
    doc.save(path)
    doc.close()
    return path


def make_scan_like_pdf(path: str) -> str:
    """A page that is one big picture plus a tiny watermark text layer — what many
    scanned PSG/MMS files look like. It must NOT be treated as a digital page."""
    src = fitz.open()
    p = src.new_page(width=595, height=842)
    p.insert_text((50, 100), "Scanned content that only OCR can read", fontsize=14)
    png = p.get_pixmap(matrix=fitz.Matrix(2, 2)).tobytes("png")
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    page.insert_image(page.rect, stream=png)
    page.insert_text((20, 830), "131.11.1.166 15-JUL-2025 PSG_MISO_IHQA 08 WATERMARK_PAGE 131.11.1.216 Jul 25 15:35 Hrs", fontsize=6)
    doc.save(path)
    doc.close()
    return path
