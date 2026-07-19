"""PDF -> text: text layer first (PyMuPDF get_text), OCR fallback (render page -> RapidOCR) for
scanned/image-only PDFs. Pure pipeline, no LLM. RapidOCR is imported lazily (heavy: onnxruntime)."""
import os
import tempfile

import fitz

_OCR = None


def _ocr_image(png_path):
    """OCR one rendered page image -> joined text lines. Isolated for test monkeypatching."""
    global _OCR
    if _OCR is None:
        from rapidocr_onnxruntime import RapidOCR
        _OCR = RapidOCR()
    result, _ = _OCR(png_path)
    return "\n".join(item[1] for item in (result or []))


def doc_to_text(path, dpi=150):
    if not os.path.isfile(path):
        return {"error": "file not found: %s" % path}
    if not path.lower().endswith(".pdf"):
        return {"error": "only PDF is supported here; use markitdown's convert_to_markdown for %s"
                         % os.path.splitext(path)[1]}
    try:
        doc = fitz.open(path)
    except Exception as e:
        return {"error": "cannot open PDF: %s" % e}

    layer = [page.get_text().strip() for page in doc]
    if any(layer):
        return {"source": "text-layer", "pages": len(layer),
                "text": "\n\n".join(layer), "page_errors": []}

    # image-only PDF -> render + OCR each page, best-effort per page
    texts, errors = [], []
    with tempfile.TemporaryDirectory() as tmp:
        for i, page in enumerate(doc):
            try:
                png = os.path.join(tmp, "p%d.png" % i)
                page.get_pixmap(dpi=dpi).save(png)
                texts.append(_ocr_image(png))
            except Exception as e:
                errors.append("page %d: %s" % (i + 1, e))
    return {"source": "ocr", "pages": len(doc),
            "text": "\n\n".join(t for t in texts if t), "page_errors": errors}
