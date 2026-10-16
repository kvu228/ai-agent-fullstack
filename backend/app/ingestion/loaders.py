"""Extract text from uploaded files. Add a loader here to support a new file type."""

import io
from collections.abc import Callable

from langchain_core.documents import Document as LCDocument
from pypdf import PdfReader


def _load_pdf(data: bytes) -> list[LCDocument]:
    reader = PdfReader(io.BytesIO(data))
    # One LangChain Document per page keeps the page number for citations.
    return [
        LCDocument(page_content=text, metadata={"page": number})
        for number, page in enumerate(reader.pages, start=1)
        if (text := page.extract_text() or "").strip()
    ]


def _load_text(data: bytes) -> list[LCDocument]:
    return [LCDocument(page_content=data.decode("utf-8", errors="replace"))]


# content type -> loader. Also the allow-list of accepted uploads.
LOADERS: dict[str, Callable[[bytes], list[LCDocument]]] = {
    "application/pdf": _load_pdf,
    "text/plain": _load_text,
    "text/markdown": _load_text,
}

EXTENSION_TO_CONTENT_TYPE = {
    ".pdf": "application/pdf",
    ".txt": "text/plain",
    ".md": "text/markdown",
}


def load_document(data: bytes, content_type: str) -> list[LCDocument]:
    try:
        loader = LOADERS[content_type]
    except KeyError:
        raise ValueError(f"Unsupported content type: {content_type}") from None
    return loader(data)
