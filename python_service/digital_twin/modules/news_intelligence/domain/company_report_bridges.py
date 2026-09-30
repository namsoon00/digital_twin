"""Exact accounting bridges across comparable reports, never causal verdicts."""

from datetime import date
import hashlib
import re

from .company_report_evidence import mapping, number, rows, text


def comparable_periods(current, previous):
    """Require observed annual or same-season duration windows and provenance."""
    try:
        end, old_end = date.fromisoformat(current['period']), date.fromisoformat(previous['period'])
        start, old_start = date.fromisoformat(current['periodStart']), date.fromisoformat(previous['periodStart'])
    except (KeyError, TypeError, ValueError):
        return False
    return (350 <= (end - old_end).days <= 380
            and 0 < (end - start).days <= 380 and 0 < (old_end - old_start).days <= 380
            and abs((end - start).days - (old_end - old_start).days) <= 8
            and all(current.get(key) and current.get(key) == previous.get(key)
                    for key in ('provider', 'currency', 'scope', 'durationBasis')))


def accounting_bridge_cards(evidence):
    from .company_report_reading import _card, _compact_money, _same_basis

    reports = sorted(rows(mapping(evidence).get('recentFinancials')) + rows(mapping(evidence).get('annualFinancials')),
                     key=lambda row: text(row.get('period')), reverse=True)
    cards = []
    definitions = (
        ('net-income-bridge', ('netIncome', 'pretaxIncome', 'taxProvision'), '순이익 변화의 구성',
         '세전이익 변화', '세금 비용 변화의 기여',
         '세금 비용의 변화는 현금 납부액과 다릅니다. 일회성 세금인지, 영업 성장인지 원문에서 구분해야 합니다.',
         '다음 전년 동기 실적에서 세전이익과 세금 비용을 다시 비교하고, 세금 주석의 일회성 항목을 확인합니다.'),
        ('free-cash-flow-bridge', ('freeCashFlow', 'operatingCashFlow', 'capitalExpenditure'), '투자 후 현금 변화의 구성',
         '영업현금 변화', '설비투자 지출 변화의 기여',
         '설비투자는 영업현금흐름에 포함되지 않습니다. 영업현금 감소의 원인은 운전자본·현금 납세 등 현금흐름 주석을 별도로 확인해야 합니다.',
         '다음 전년 동기 실적에서 영업현금과 설비투자를 다시 비교하고, 현금 납세·운전자본의 변화를 확인합니다.'),
    )
    for key, fields, title, first_label, second_label, limit, check in definitions:
        for current in reports:
            new = {m.get('key'): m for m in rows(current.get('metrics'))}
            inputs = [mapping(new.get(field)) for field in fields]
            if not _same_basis(*inputs) or not all(m.get('official') and m.get('sourceDocumentId') and m.get('sourceReferences') for m in inputs):
                continue
            found = False
            for previous in reports:
                old = {m.get('key'): m for m in rows(previous.get('metrics'))}
                before = [mapping(old.get(field)) for field in fields]
                if not _same_basis(*before) or not all(m.get('official') and m.get('sourceDocumentId') and m.get('sourceReferences') for m in before):
                    continue
                if not all(comparable_periods(a, b) for a, b in zip(inputs, before)):
                    continue
                values = [[number(m.get('value')) for m in group] for group in (inputs, before)]
                if key == 'free-cash-flow-bridge':
                    values = [[a, b, abs(c)] for a, b, c in values]
                # Continuing-operation tax and total net income need not reconcile.
                # Reject unmodelled discontinued operations / minority interests.
                if any(abs(a - (b - c)) > max(1, abs(a), abs(b), abs(c)) * 1e-6 for a, b, c in values):
                    continue
                delta, first, second = (values[0][0] - values[1][0], values[0][1] - values[1][1], -(values[0][2] - values[1][2]))
                currency = inputs[0]['currency']
                money = lambda value: ('+' if value > 0 else '') + _compact_money(value, currency)
                fact = (before[0]['period'] + ' → ' + inputs[0]['period'] + ' · 변화 ' + money(delta)
                        + ' = ' + first_label + ' ' + money(first) + ' + ' + second_label + ' ' + money(second))
                meaning = '두 공시 기간의 차이를 회계 항목으로 분해한 결과입니다. ' + limit
                cards.append(_card(key, title, fact, meaning, inputs + before, [check],
                                   ['회계적 기여이며 사업 원인·반복 가능성·정상화 이익을 확정한 결과가 아닙니다.'],
                                   headline=('순이익 증가에는 세전이익 변화뿐 아니라 세금 비용 감소가 함께 반영됐습니다.'
                                             if key == 'net-income-bridge' and delta > 0 and second > 0 else title + '를 공시 수치로 분해했습니다.'),
                                   briefFact=title + " · " + fact, briefCheck=check,
                                   components={'totalChange': delta, 'firstContribution': first, 'secondContribution': second, 'residual': delta - first - second},
                                   currentPeriod=inputs[0]['period'], previousPeriod=before[0]['period']))
                topic = 'income-tax' if key == 'net-income-bridge' else 'operating-cash'
                statements = []
                for document in rows(mapping(evidence).get('documents')):
                    if (not document.get('bodyVerified') or document.get('documentId') != inputs[0]['sourceDocumentId']
                            or document.get('reportDate') != inputs[0]['period']):
                        continue
                    for passage in rows(document.get('passages')):
                        quote = text(passage.get('quote'))
                        if (passage.get('topic') == topic and quote
                                and re.search(r'compared to|year.over.year|increased|decreased|one.time', quote, re.I)
                                and hashlib.sha256(quote.encode()).hexdigest() == passage.get('passageHash')):
                            statements.append({'quote': quote, 'url': document.get('url'),
                                               'documentId': document['documentId'], 'publishedAt': document.get('publishedAt'),
                                               'label': '회사 공시의 설명 · 독립 검증 아님'})
                cards[-1]['sourceStatements'] = statements[:2]
                found = True
                break
            if found:
                break
    return cards
