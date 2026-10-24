"""Preserve DART XML statement headings, period/unit tables and cell boundaries."""
import re
from html.parser import HTMLParser
from xml.etree import ElementTree as ET

from .sec_report_passages import document_blocks, question_document_blocks

VERSION = "dart-statement-passages-v1"


def text(node):
    return re.sub(r"\s+", " ", "".join(node.itertext())).strip()


class _Document(HTMLParser):
    """DART originals may contain bare ampersands and literal '<신 설>'.

    A tolerant tree keeps their text without parsing DTDs or fetching entities.
    """
    VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = ET.Element("document")
        self.stack = [self.root]

    def handle_starttag(self, tag, attrs):
        if tag in self.VOID:
            self.handle_data(" ")
            return
        self.stack.append(ET.SubElement(self.stack[-1], tag, dict(attrs)))

    def handle_endtag(self, tag):
        for index in range(len(self.stack)-1, 0, -1):
            if self.stack[index].tag == tag:
                self.stack = self.stack[:index]
                break

    def handle_data(self, value):
        parent = self.stack[-1]
        if len(parent):
            parent[-1].tail = (parent[-1].tail or "") + value
        else:
            parent.text = (parent.text or "") + value


def document_passages(markup):
    if not re.search(r"<(?:TABLE-GROUP|TE|TU)(?:\s|>)", markup, re.I):
        return question_document_blocks(markup, include_context=True)
    parser = _Document(); parser.feed(markup); parser.close()
    root = parser.root
    blocks = []

    def walk(node, titles):
        title = next((text(child) for child in node if child.tag == "title"), "")
        context = [*titles, title] if title else list(titles)
        context = [row for row in context[-4:] if len(row) <= 500]
        headers = []
        for child in node:
            if child.tag in {"title", "script", "style", "noscript", "ix:hidden", "ix:header"}:
                continue
            if child.tag != "table":
                walk(child, context)
                continue
            rows = [" | ".join(text(cell) for cell in row if cell.tag in {"te", "tu", "td", "th"})
                    for row in child.iter("tr")]
            value = "\n".join(row for row in rows if row).strip()
            if not value or len(value) > 12000:
                headers = []
                continue
            # DART puts period labels and currency/scale in a separate
            # borderless table immediately before the numeric statement.
            if child.get("border") == "0" and len(value) <= 1400 and len(rows) <= 8:
                headers = [*headers, value][-2:]
                continue
            block = "\n".join([*context, *headers, value])
            if len(block) <= 14000:
                blocks.append(block)
            headers = []  # Never reuse another statement/period's labels.

    walk(root, [])
    return blocks + document_blocks(markup)
