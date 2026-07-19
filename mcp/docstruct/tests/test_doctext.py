import fitz
import doctext


def make_text_pdf(path):
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 100), "Invoice Number INV-001")
    page.insert_text((72, 130), "Total 698")
    doc.save(path)


def make_image_pdf(path):
    src = fitz.open()
    p = src.new_page(width=200, height=100)
    p.insert_text((20, 50), "IMG")
    pix = p.get_pixmap(dpi=100)
    out = fitz.open()
    page = out.new_page(width=200, height=100)
    page.insert_image(page.rect, stream=pix.tobytes("png"))
    out.save(path)


def test_text_layer_pdf(tmp_path):
    p = tmp_path / "t.pdf"
    make_text_pdf(str(p))
    r = doctext.doc_to_text(str(p))
    assert r["source"] == "text-layer"
    assert "INV-001" in r["text"] and r["pages"] == 1 and r["page_errors"] == []


def test_image_only_pdf_falls_back_to_ocr(tmp_path, monkeypatch):
    p = tmp_path / "img.pdf"
    make_image_pdf(str(p))
    monkeypatch.setattr(doctext, "_ocr_image", lambda png_path: "OCRED LINE")  # avoid real OCR in unit test
    r = doctext.doc_to_text(str(p))
    assert r["source"] == "ocr"
    assert "OCRED LINE" in r["text"]


def test_ocr_page_error_recorded_not_fatal(tmp_path, monkeypatch):
    p = tmp_path / "img.pdf"
    make_image_pdf(str(p))
    monkeypatch.setattr(doctext, "_ocr_image", lambda png_path: (_ for _ in ()).throw(RuntimeError("boom")))
    r = doctext.doc_to_text(str(p))
    assert r["source"] == "ocr" and r["text"] == ""
    assert len(r["page_errors"]) == 1 and "boom" in r["page_errors"][0]


def test_non_pdf_and_missing_return_error(tmp_path):
    f = tmp_path / "x.docx"
    f.write_bytes(b"zz")
    assert "error" in doctext.doc_to_text(str(f))
    assert "error" in doctext.doc_to_text(str(tmp_path / "nope.pdf"))
