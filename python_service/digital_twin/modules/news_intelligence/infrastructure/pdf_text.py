"""Short-lived PDF text worker; no network, credentials, or application stores."""
import io
import json
import sys


def extract_pdf(data):
    from pypdf import PdfReader
    reader = PdfReader(io.BytesIO(data), strict=False)
    if reader.is_encrypted:
        raise ValueError("encrypted-document")
    pages, omitted = [], []
    for index, page in enumerate(reader.pages[:60]):
        contents = page.get_contents()
        if contents is None:
            continue
        if contents is not None and len(contents.get_data()) > 8_000_000:
            omitted.append(index + 1)
            continue
        text = (page.extract_text(extraction_mode="layout") or "").strip()
        # Keep entire pages, including table headings. Never imply a truncated
        # table has retained its currency, period or scope qualifiers.
        if len(text) > 14000:
            omitted.append(index + 1)
        elif text:
            pages.append({"page": index + 1, "text": text})
    return {"pages": pages, "pageCount": len(reader.pages),
            "omittedPages": omitted, "unreadPageCount": max(0, len(reader.pages) - 60)}


if __name__ == "__main__":
    import resource
    resource.setrlimit(resource.RLIMIT_CPU, (15, 15))
    try:
        resource.setrlimit(resource.RLIMIT_AS, (1024 ** 3, 1024 ** 3))
    except (ValueError, OSError):
        pass  # Process timeout remains enforced on hosts without RLIMIT_AS.
    try:
        data = sys.stdin.buffer.read(8_000_001)
        if len(data) > 8_000_000:
            raise ValueError("document-size-budget-exhausted")
        print(json.dumps(extract_pdf(data), ensure_ascii=False))
    except Exception as error:
        print(json.dumps({"errorKind": type(error).__name__}))
        sys.exit(1)
