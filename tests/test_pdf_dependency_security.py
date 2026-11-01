"""Real PDF parsing rejects malformed input within a bounded child process."""

from __future__ import annotations

import io
import subprocess
import sys
from pathlib import Path

import pytest


def _pdf(objects: list[bytes]) -> bytes:
    result = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, value in enumerate(objects, 1):
        offsets.append(len(result))
        result.extend(f"{number} 0 obj\n".encode() + value + b"\nendobj\n")
    xref = len(result)
    result.extend(f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode())
    for offset in offsets:
        result.extend(f"{offset:010d} 00000 n \n".encode())
    result.extend(
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    )
    return bytes(result)


def _exercise(case: str) -> None:
    from pypdf import PdfReader, PdfWriter, overwrite_configuration
    from pypdf.errors import LimitReachedError, PdfReadError

    from app.rag.document_parsers.base import DocumentParseError
    from app.rag.document_parsers.pdf import PdfDocumentParser

    if case == "missing-trailer":
        malformed = b"%PDF-1.4\nstartxref\n28\n%%EOF\nxref\n1 0\naaa"
        try:
            PdfDocumentParser().extract(malformed, "incomplete.pdf", "application/pdf")
        except DocumentParseError as exc:
            assert isinstance(exc.__cause__, PdfReadError)
        else:
            raise AssertionError("Malformed cross-reference input must be rejected")
    elif case == "cyclic-dictionary":
        data = _pdf([
            b"<< /Type /Catalog /Pages 2 0 R >>",
            b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] /Contents [4 0 R] >>",
            b"<< /Length 0 /N 5 0 R >>\nstream\n\nendstream",
            b"<< /N 6 0 R /T (first) >>",
            b"<< /N 5 0 R /T (second) >>",
        ])
        writer = PdfWriter()
        writer.append(io.BytesIO(data))
        assert len(writer.pages) == 1
    elif case == "shared-page-resources":
        # A small isolated limit exercises amplification without allocating a large tree.
        overwrite_configuration(page_tree_maximum_page_entries=20)
        data = _pdf([
            b"<< /Type /Catalog /Pages 2 0 R >>",
            b"<< /Type /Pages /Kids [3 0 R 3 0 R 3 0 R] /Count 9 >>",
            b"<< /Type /Pages /Kids [4 0 R 4 0 R 4 0 R] /Count 3 >>",
            b"<< /Type /Page /MediaBox [0 0 9 9] /K1 0 /K2 0 /K3 0 >>",
        ])
        reader = PdfReader(io.BytesIO(data))
        try:
            len(reader.pages)
        except LimitReachedError:
            assert reader.flattened_pages is None
        else:
            raise AssertionError("Repeated page entries must respect the resource limit")
    elif case == "text-layer":
        content = b"BT /F1 12 Tf 20 100 Td (hello safe PDF) Tj ET"
        data = _pdf([
            b"<< /Type /Catalog /Pages 2 0 R >>",
            b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] "
            b"/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>",
            f"<< /Length {len(content)} >>\nstream\n".encode() + content + b"\nendstream",
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        ])
        parsed = PdfDocumentParser().extract(data, "text.pdf", "application/pdf")
        assert "hello safe PDF" in parsed.text
    else:
        raise AssertionError("Unknown PDF exercise")


@pytest.mark.parametrize("case", [
    "missing-trailer", "cyclic-dictionary", "shared-page-resources", "text-layer",
])
def test_real_pdf_dependency_terminates_safely(case: str) -> None:
    result = subprocess.run(
        [sys.executable, "-c",
         "import runpy,sys; runpy.run_path(sys.argv[1])['_exercise'](sys.argv[2])",
         str(Path(__file__).resolve()), case],
        capture_output=True, text=True, encoding="utf-8", timeout=10, check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
