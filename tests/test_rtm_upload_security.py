from __future__ import annotations

import io
import unittest
import zipfile

from pypdf import PdfWriter
from pypdf.generic import DictionaryObject, NameObject, TextStringObject
from PIL import Image

from rtm_core.upload_security import (
    DOCX,
    PDF,
    UploadSecurityError,
    detect_document_mime,
    validate_document_bytes,
    validate_docx_archive,
)


def _docx_bytes(*, relationship: bytes | None = None, payload: bytes = b"<w:document/>"):
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", b"<Types/>")
        archive.writestr("word/document.xml", payload)
        if relationship is not None:
            archive.writestr("word/_rels/document.xml.rels", relationship)
    return output.getvalue()


def _pdf_bytes():
    output = io.BytesIO()
    writer = PdfWriter()
    writer.add_blank_page(width=72, height=72)
    writer.write(output)
    return output.getvalue()


def _active_pdf_bytes():
    output = io.BytesIO()
    writer = PdfWriter()
    writer.add_blank_page(width=72, height=72)
    writer.root_object[NameObject("/OpenAction")] = DictionaryObject(
        {
            NameObject("/S"): NameObject("/JavaScript"),
            NameObject("/JS"): TextStringObject("app.alert('x')"),
        }
    )
    writer.write(output)
    return output.getvalue()


class UploadSecurityTest(unittest.TestCase):
    def test_magic_not_declared_mime_determines_type(self):
        data = _pdf_bytes()
        self.assertEqual(detect_document_mime(data), PDF)
        result = validate_document_bytes(
            filename="document.pdf",
            declared_mime="application/octet-stream",
            data=data,
            max_bytes=1024,
        )
        self.assertEqual(result.mime, PDF)
        self.assertEqual(result.extension, ".pdf")

    def test_extension_and_content_mismatch_is_rejected(self):
        with self.assertRaises(UploadSecurityError):
            validate_document_bytes(
                filename="not-an-image.jpg",
                declared_mime="image/jpeg",
                data=_pdf_bytes(),
                max_bytes=16 * 1024,
            )

    def test_docx_external_relationship_is_rejected(self):
        data = _docx_bytes(
            relationship=(
                b'<Relationships><Relationship TargetMode="External" '
                b'Target="https://attacker.invalid/resource"/></Relationships>'
            )
        )
        self.assertEqual(detect_document_mime(data), DOCX)
        with self.assertRaises(UploadSecurityError):
            validate_docx_archive(data)

    def test_docx_path_traversal_is_rejected(self):
        output = io.BytesIO()
        with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("[Content_Types].xml", b"<Types/>")
            archive.writestr("word/document.xml", b"<w:document/>")
            archive.writestr("../outside", b"bad")
        with self.assertRaises(UploadSecurityError):
            validate_docx_archive(output.getvalue())

    def test_valid_minimal_docx_passes_archive_policy(self):
        data = _docx_bytes()
        validate_docx_archive(data)
        result = validate_document_bytes(
            filename="safe.docx",
            declared_mime=DOCX,
            data=data,
            max_bytes=1024 * 1024,
        )
        self.assertEqual(result.mime, DOCX)

    def test_docx_active_content_is_rejected(self):
        data = _docx_bytes(payload=b'<w:document><w:altChunk r:id="remote"/></w:document>')
        with self.assertRaises(UploadSecurityError):
            validate_docx_archive(data)

    def test_parsed_pdf_active_action_is_rejected(self):
        with self.assertRaises(UploadSecurityError):
            validate_document_bytes(
                filename="active.pdf",
                declared_mime=PDF,
                data=_active_pdf_bytes(),
                max_bytes=64 * 1024,
            )

    def test_highly_compressed_oversize_image_dimensions_are_rejected(self):
        output = io.BytesIO()
        Image.new("1", (5000, 5000), 0).save(output, format="PNG", optimize=True)
        data = output.getvalue()
        self.assertLess(len(data), 1024 * 1024)
        with self.assertRaises(UploadSecurityError):
            validate_document_bytes(
                filename="oversize.png",
                declared_mime="image/png",
                data=data,
                max_bytes=1024 * 1024,
            )

    def test_pdf_header_without_complete_structure_is_rejected(self):
        with self.assertRaises(UploadSecurityError):
            validate_document_bytes(
                filename="truncated.pdf",
                declared_mime=PDF,
                data=b"%PDF-1.7\nnot-a-complete-pdf",
                max_bytes=1024,
            )


if __name__ == "__main__":
    unittest.main()
