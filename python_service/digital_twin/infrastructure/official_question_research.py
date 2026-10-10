"""Bounded official document reads driven by an existing research task."""
import hashlib
import re
from datetime import datetime, timezone
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

from digital_twin.modules.market_data.contracts import ExternalCallDeferred
from digital_twin.modules.news_intelligence.contracts import (
    QUESTION_PASSAGES_VERSION as VERSION, sec_research_evidence,
    question_terms, select_question_filings, select_question_passages,
)
from .sec_report_passages import question_document_blocks


def official_document_url(url, cik, accession):
    try:
        parsed = urlsplit(str(url or ''))
    except ValueError:
        return False
    if (parsed.scheme != 'https' or parsed.netloc != 'www.sec.gov' or parsed.query or parsed.fragment
            or not str(cik).isdigit() or not re.fullmatch(r'\d{10}-\d{2}-\d{6}', str(accession))):
        return False
    prefix = '/Archives/edgar/data/' + str(int(cik)) + '/' + accession.replace('-', '') + '/'
    return (parsed.path.startswith(prefix)
            and bool(re.fullmatch(r'[A-Za-z0-9_.-]+\.(?:htm|html|txt)', parsed.path[len(prefix):], re.I)))


class _Exhibits(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.href, self.text, self.links = '', [], []

    def handle_starttag(self, tag, attrs):
        if tag == 'a':
            self.href = dict(attrs).get('href', '')
            self.text = []

    def handle_data(self, data):
        if self.href:
            self.text.append(data)

    def handle_endtag(self, tag):
        if tag == 'a' and self.href:
            if re.search(r'ex(?:hibit)?[\s_-]*99|earnings|press.?release|results', self.href + ' ' + ' '.join(self.text), re.I):
                self.links.append(self.href)
            self.href = ''


def collect_question_documents(provider, target, signals, tasks):
    tasks = [t for t in tasks if isinstance(t, dict)][:2]
    if not tasks or not question_terms(tasks):
        return [], []
    symbol = target.normalized_symbol()
    sec = (signals.get('secFilings') or {}).get(symbol) or {}
    base = {'source': 'official-question-research', 'symbol': symbol, 'version': VERSION,
            'taskIds': [t.get('taskId', '') for t in tasks], 'queryTerms': question_terms(tasks)}
    if not sec:
        return [], [{**base, 'ok': True, 'status': 'unsupported-document-source', 'count': 0}]
    if not callable(getattr(provider, 'sec_document_access_configured', None)) or not provider.sec_document_access_configured():
        return [], [{**base, 'ok': False, 'status': 'configuration-required', 'count': 0}]
    now = datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')
    filings = [*(sec.get('reportFilings') or []), sec.get('latestFiling') or {}, *(sec.get('recentFilings') or [])]
    candidates = [(row, str(row.get('url') or ''), '') for row in select_question_filings(filings, tasks, now)]
    seen, evidence, statuses, fetched = set(), [], [], 0
    # The same bounded, provider-guarded transport serves primary documents and
    # exhibits. URLs are discovered from issuer filings, never supplied by AI.
    while candidates and fetched < 3:
        metadata, url, parent = candidates.pop(0)
        accession, cik = str(metadata.get('accessionNumber') or ''), str(sec.get('cik') or '')
        if url in seen:
            continue
        seen.add(url)
        if not official_document_url(url, cik, accession):
            statuses.append({**base, 'ok': False, 'status': 'invalid-document-identity'})
            continue
        fetched += 1
        try:
            raw = provider.guarded_call('SEC EDGAR', 'question-document:' + hashlib.sha256(url.encode()).hexdigest(),
                                        lambda: provider.fetch_text(url, provider.sec_document_headers()))
            if len(str(raw).encode()) > 5 * 1024 * 1024:
                raise ValueError('document-size-budget')
            if any(marker in str(raw).lower() for marker in ('undeclared automated tool', 'request rate threshold exceeded', 'access denied')):
                raise ValueError('official-document-unavailable')
            passages = select_question_passages(question_document_blocks(raw), tasks, provider.sec_document_text_max_chars())
            if not parent:
                links = _Exhibits(); links.feed(str(raw))
                exhibits = [urljoin(url, href) for href in links.links]
                extra = [(metadata, link, url) for link in dict.fromkeys(exhibits)
                         if official_document_url(link, cik, accession)][:1]
                # For comparison reports keep the prior-year primary document
                # ahead of exhibits; event filings still need their attachment.
                if str(metadata.get('form') or '').upper() in {'8-K', '6-K'}:
                    candidates[0:0] = extra
                else:
                    candidates.extend(extra)
            if not passages:
                statuses.append({**base, 'ok': True, 'status': 'no-matching-passages', 'url': url})
                continue
            selected = {**metadata, 'url': url, 'documentText': '\n\n'.join(p['quote'] for p in passages),
                        'documentTextQuality': 'body', 'documentTextVersion': VERSION,
                        'documentTextScope': 'question-selected-passages', 'documentPassages': passages}
            rows = sec_research_evidence(symbol, {**sec, 'latestFiling': selected, 'facts': {}})
            for row in rows:
                digest = hashlib.sha256((url + '|' + '|'.join(p['passageHash'] for p in passages)).encode()).hexdigest()[:24]
                row.evidence_id = 'research:' + symbol + ':sec-question:' + digest
                row.observed_at = now
                row.raw_payload.update(documentIssuerIdentity={'symbol': symbol, 'cik': cik, 'accessionNumber': accession, 'sourceUrl': url, 'verification': 'sec-discovered-issuer-document'}, researchTaskIds=base['taskIds'], researchQueryTerms=base['queryTerms'],
                                       parentDocumentUrl=parent, sourceDocumentHash=hashlib.sha256(str(raw).encode()).hexdigest())
            evidence.extend(rows)
        except ExternalCallDeferred as error:
            statuses.append({**base, 'ok': False, 'status': 'deferred',
                             'reason': error.reason, 'retryAt': error.retry_at})
            break  # A provider circuit applies to all remaining SEC documents.
        except Exception as error:
            # Keep one failed provider/document from discarding other receipts.
            statuses.append({**base, 'ok': False, 'status': 'document-unavailable', 'errorKind': type(error).__name__})
    statuses.append({**base, 'ok': True, 'status': 'passages-collected' if evidence else 'unresolved',
                     'count': len(evidence), 'documentsAttempted': fetched, 'remainingCandidates': len(candidates)})
    return evidence, statuses
