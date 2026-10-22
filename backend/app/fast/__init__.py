"""
DOCINT - fast_ocr pipeline
==========================
A separate pipeline that sits beside the existing ones (nothing in surya /
tesseract / learned_anchor is changed by it).

    triage      PyMuPDF text layer first: digital pages need no OCR at all
    reader      one Tesseract pass per scanned page, cached on disk
    structure   lines -> paragraphs / headings in reading order
    fields      Label: value pairs matched to the template, then validation
    tables      ruled tables read from line geometry (no model, no re-OCR)
    vlm         re-reads ONE cropped field, answer must be backed by OCR text
    events      versioned live events (Redis Stream -> SSE)
    runner      RQ entry point for one job
"""
