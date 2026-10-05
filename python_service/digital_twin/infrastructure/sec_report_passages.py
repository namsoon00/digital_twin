"""Bounded, verbatim passages from SEC report paragraphs, not causal inference."""

import hashlib
import re
from html.parser import HTMLParser

REPORT_TEXT_VERSION = 'report-passages-v2'
REPORT_FORMS = {'10-K', '10-K/A', '10-Q', '10-Q/A', '20-F', '20-F/A', '40-F', '40-F/A'}


class _Paragraphs(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.skip = 0
        self.parts = []
        self.blocks = []

    def handle_starttag(self, tag, attrs):
        if tag in {'script', 'style', 'noscript', 'svg', 'ix:header', 'ix:hidden'}:
            self.skip += 1
        if tag in {'div', 'p', 'tr', 'br'} and not self.skip:
            self.flush()

    def handle_endtag(self, tag):
        if tag in {'script', 'style', 'noscript', 'svg', 'ix:header', 'ix:hidden'}:
            self.skip = max(0, self.skip - 1)
        if tag in {'div', 'p', 'tr'} and not self.skip:
            self.flush()

    def handle_data(self, data):
        if not self.skip:
            self.parts.append(data)

    def flush(self):
        line = re.sub(r'\s+', ' ', ''.join(self.parts)).strip()
        if line:
            self.blocks.append(line)
        self.parts = []


def document_blocks(raw_html):
    parser = _Paragraphs()
    parser.feed(str(raw_html or ''))
    parser.flush()
    return parser.blocks


def report_passages(raw_html, limit=6000):
    blocks = document_blocks(raw_html)
    topics = (
        ('income-tax', r'income tax|tax expense|tax provision|effective tax'),
        ('operating-cash', r'cash.*operating activities|operating.*cash flow'),
        ('capital-spending', r'capital expenditure|payments.*property|capital spending'),
        ('segments', r'segment.*(?:sales|revenue)|(?:sales|revenue).*segment'),
    )
    selected, seen, remaining = [], set(), max(500, min(20000, int(limit)))
    for topic, pattern in topics:
        candidates = [block for block in blocks if 100 <= len(block) <= 2200
                      and re.search(pattern, block, re.I) and block not in seen]
        # Prefer explanatory prose over index/table headings; preserve exact text.
        def relevance(block):
            causal = bool(re.search(r'due to|primarily|driven by|attributable|reflects|as a result', block, re.I))
            comparison = bool(re.search(r'compared to|year.over.year|increased|decreased', block, re.I))
            exceptional = bool(re.search(r'one.time|non.recurring|unusual', block, re.I))
            return (comparison and causal, exceptional and causal, comparison, causal)
        candidates.sort(key=relevance, reverse=True)
        for quote in candidates[:2]:
            if len(quote) > remaining:
                continue  # Never sever the qualification at the end of a sentence.
            selected.append({'topic': topic, 'quote': quote,
                             'passageHash': hashlib.sha256(quote.encode()).hexdigest()})
            seen.add(quote)
            remaining -= len(quote)
    return selected
