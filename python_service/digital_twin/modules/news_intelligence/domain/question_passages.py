"""Question-directed source selection; matches are excerpts, never answers."""
import hashlib
import re
from datetime import date


VERSION = 'question-passages-v1'
_TOPICS = (
    (r'순손실|적자|손실|net loss', ('net loss', 'operating loss', 'losses')),
    (r'순이익|수익성|profitability', ('net income', 'profitability', 'operating income')),
    (r'매출|revenue|sales', ('revenue', 'net sales')),
    (r'데이터.?센터|data.?center', ('data center', 'datacenter')),
    (r'영업.?이익|operating income', ('operating income', 'income from operations')),
    (r'운전.?자본|working capital', ('working capital', 'accounts receivable', 'inventories', 'accounts payable')),
    (r'활성.?고객|고객|customer', ('active customers', 'customers', 'customer')),
    (r'마진|margin', ('margin', 'gross profit')),
    (r'한국|korea', ('korea', 'korean')),
    (r'소비|수요|demand|consumer', ('consumer', 'consumption', 'spending', 'demand')),
    (r'전망|가이던스|outlook|guidance', ('outlook', 'guidance', 'expect', 'anticipated')),
    (r'원인|요인|cause|driver', ('due to', 'driven by', 'primarily', 'attributable', 'because')),
    (r'현금.?흐름|cash flow', ('cash flow', 'operating activities')),
    (r'투자.?지출|설비.?투자|capital', ('capital expenditure', 'capital spending', 'purchases of property', 'payments for property')),
    (r'세금|법인세|tax', ('income tax', 'tax provision', 'tax expense')),
)


def question_terms(tasks):
    text = ' '.join(str(t.get('question') or '') + ' ' + ' '.join(t.get('queryTerms') or [])
                    for t in tasks if isinstance(t, dict)).casefold()
    terms = {term for pattern, aliases in _TOPICS if re.search(pattern, text) for term in aliases}
    # Literal terms also cover topics outside the small bilingual vocabulary.
    terms.update(token for token in re.findall(r'[a-z][a-z-]{3,}|[가-힣]{2,}', text)
                 if token not in {'최근', '공식', '분기보고서', '경영진', '무엇인가', '대한', '확인',
                                  'what', 'which', 'latest', 'recent', 'report', 'official'})
    return sorted(terms)[:64]


def select_question_passages(blocks, tasks, limit=6000):
    terms = question_terms(tasks)
    candidates = []
    for index, block in enumerate(dict.fromkeys(blocks)):
        if not 80 <= len(block) <= (12000 if '\n' in block else 2800):
            continue
        matched = [term for term in terms if term in block.casefold()]
        if matched:
            candidates.append((len(matched), index, block, matched))
    candidates.sort(key=lambda row: (-row[0], row[1]))
    remaining, result = max(500, min(20000, int(limit))), []
    for _, _, quote, matched in candidates:
        size = len(quote) + (2 if result else 0)
        if size > remaining:
            continue
        result.append({'quote': quote, 'matchedTerms': matched,
                       'passageHash': hashlib.sha256(quote.encode()).hexdigest()})
        remaining -= size
        if len(result) >= 8:
            break
    return result


def select_question_filings(filings, tasks, cutoff):
    text = ' '.join(str(t.get('question') or '') + ' ' + ' '.join(t.get('queryTerms') or [])
                    for t in tasks if isinstance(t, dict))
    annual = bool(re.search(r'연간|연차|annual|10-K|20-F', text, re.I))
    preferred = {'10-K', '20-F', '40-F'} if annual else {'10-Q'}
    allowed = preferred | {'10-K', '10-Q', '20-F', '40-F', '8-K', '6-K'}
    selected = {}
    for row in filings:
        if not isinstance(row, dict):
            continue
        form = str(row.get('form') or '').upper().removesuffix('/A')
        filing_date = str(row.get('filingDate') or '')[:10]
        try:
            date.fromisoformat(filing_date)
        except ValueError:
            continue
        if form not in allowed or filing_date > cutoff[:10]:
            continue
        identity = str(row.get('accessionNumber') or '')
        if identity:
            selected.setdefault(identity, row)
    rows = sorted(selected.values(), key=lambda row: str(row['filingDate']), reverse=True)
    rows.sort(key=lambda row: 0 if str(row['form']).upper().removesuffix('/A') in preferred
              else 1 if str(row['form']).upper().removesuffix('/A') in {'8-K', '6-K'} else 2)
    if rows and re.search(r'전년|전년도|동기|year.over.year|prior.year|previous.year|yoy', text, re.I):
        latest = rows[0]
        try:
            anchor = date.fromisoformat(str(latest.get('reportDate') or '')[:10])
            # Fiscal calendars may move by a few days (52/53-week years).
            peers = [row for row in rows[1:]
                     if str(row.get('form')).upper().removesuffix('/A') == str(latest['form']).upper().removesuffix('/A')
                     and row.get('reportDate')
                     and 330 <= (anchor - date.fromisoformat(str(row['reportDate'])[:10])).days <= 400]
            if peers:
                prior = min(peers, key=lambda row: abs((anchor - date.fromisoformat(row['reportDate'][:10])).days - 365))
                rows = [latest, prior] + [row for row in rows[1:] if row is not prior]
        except ValueError:
            pass  # Missing/malformed reporting periods are never guessed.
    return rows[:3]
