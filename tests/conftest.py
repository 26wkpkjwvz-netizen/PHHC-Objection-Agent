import io
import os
import tempfile

# Point storage at a throwaway directory before the app modules are imported.
os.environ.setdefault("PHHC_DATA_DIR", tempfile.mkdtemp(prefix="phhc-test-"))
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")

import pytest
from pypdf import PdfWriter


def make_pdf(sizes_in: list[tuple[float, float]]) -> bytes:
    writer = PdfWriter()
    for w, h in sizes_in:
        writer.add_blank_page(width=w * 72, height=h * 72)
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


@pytest.fixture
def pdf_bytes():
    return make_pdf([(8.5, 14)] * 3 + [(8.27, 11.69)] * 2)
