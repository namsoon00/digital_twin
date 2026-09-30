"""Durable factual research continuity; no hypothesis qualification or action."""

from copy import deepcopy
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
import hashlib
import json

from .company_report_evidence import amount, mapping, number, rows, text
from .company_report_bridges import comparable_periods

VERSION = 'company-research-record-v1'
HISTORY_LIMIT = 24


def _clock(value):
    try:
        parsed = datetime.fromisoformat(text(value).replace('Z', '+00:00'))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:32]


def _facts(report, cutoff):
    evidence = mapping(report.get('evidence'))
    selected = {}
    for row in rows(evidence.get('annualFinancials')) + rows(evidence.get('recentFinancials')):
        published = _clock(row.get('publishedAt'))
        if published and published > cutoff:
            continue
        for metric in rows(row.get('metrics')):
            if metric.get('official') is not True or number(metric.get('value')) is None:
                continue
            required = ('key', 'period', 'durationBasis', 'scope', 'provider', 'currency', 'sourceDocumentId')
            refs = rows(metric.get('sourceReferences'))
            clocks = [_clock(ref.get('fetchedAt')) for ref in refs if ref.get('fetchedAt')]
            period = _clock(metric.get('period'))
            if (not all(metric.get(key) for key in required) or not refs or not all(ref.get('datasetId') and ref.get('revisionId') for ref in refs) or not period or period > cutoff
                    or any(clock is None or clock > cutoff for clock in clocks)):
                continue
            identity = '|'.join(text(metric.get(key)) for key in ('key', 'period', 'durationBasis', 'scope', 'provider', 'currency'))
            selected[identity] = {**deepcopy(metric), 'publishedAt': text(row.get('publishedAt')),
                                  'observedAt': max(clocks).isoformat() if clocks else cutoff.isoformat(),
                                  'observationClockBasis': 'source-receipt' if clocks else 'snapshot'}
    return selected


def _material(facts):
    return {key: {field: value.get(field) for field in ('value', 'periodStart', 'sourceDocumentId', 'sourceMetric')}
            for key, value in facts.items()}


def _changes(current, previous):
    result = []
    for identity, metric in current.items():
        old = previous.get(identity)
        if old:
            if old['value'] != metric['value']:
                result.append({'kind': 'same-period-revision', 'metric': metric['key'], 'before': old, 'after': metric,
                               'label': '같은 기간 수치 수정 · 다음 실적 검증에 포함하지 않음'})
            continue
        family = [value for value in previous.values() if all(value.get(key) == metric.get(key)
                  for key in ('key', 'durationBasis', 'scope', 'provider', 'currency'))]
        if not family or metric['period'] <= max(value['period'] for value in family):
            continue  # Newly collected historical facts are not new-period outcomes.
        comparable = [value for value in family if comparable_periods(metric, value)]
        before = max(comparable, key=lambda value: value['period'], default=None)
        result.append({'kind': 'new-period', 'metric': metric['key'], 'before': before, 'after': metric,
                       'change': metric['value'] - before['value'] if before else None,
                       'label': '새 실적 관측 · 전년 동기 비교' if before else '새 실적 관측 · 비교 가능한 전년 동기 없음'})
    return result


def advance_company_research_record(report, previous, registered_at):
    """Called under the subject lock. First registration never backfills outcomes."""
    previous = deepcopy(mapping(previous))
    now, cutoff = _clock(registered_at), _clock(report.get('sourceCutoffAt'))
    if not now or not cutoff or cutoff > now:
        return previous
    if previous and (previous.get('symbol') != report.get('symbol') or previous.get('accountId') != report.get('accountId')):
        return previous  # Caller must not relabel an existing account/subject record.
    previous_cutoff = _clock(previous.get('sourceCutoffAt'))
    if previous_cutoff and cutoff < previous_cutoff:
        return previous
    facts = _facts(report, cutoff)
    if not facts:
        return previous
    reading = mapping(report.get('reading'))
    documents = [{key: doc.get(key) for key in ('documentId', 'reportDate', 'publishedAt', 'sourceRevision', 'url', 'excerpt', 'passages')}
                 for doc in rows(mapping(report.get('evidence')).get('documents'))
                 if doc.get('bodyVerified') and _clock(doc.get('publishedAt')) and _clock(doc['publishedAt']) <= cutoff
                 and _clock(doc.get('observedAt')) and _clock(doc['observedAt']) <= cutoff]
    insight = mapping(reading.get('insight'))
    insight_clock = _clock(insight.get('asOf'))
    if insight.get('state') != 'available' or not insight_clock or insight_clock > cutoff:
        insight = {}
    # Source refresh clocks/revisions alone do not create new research revisions.
    material = {'facts': _material(facts), 'documents': documents,
                'interpretation': {key: insight.get(key) for key in ('thesis', 'meaning', 'mechanism', 'invalidation', 'risks')}}
    fingerprint = _digest(material)
    if previous.get('fingerprint') == fingerprint:
        return previous
    cards = rows(reading.get('financial'))
    questions = list(dict.fromkeys(check for card in cards for check in card.get('nextChecks', [])))
    baseline = {'registeredAt': now.isoformat(), 'sourceCutoffAt': cutoff.isoformat(), 'facts': facts,
                'questions': questions, 'interpretation': insight, 'documents': documents}
    if not previous:
        return {'version': VERSION, 'recordId': 'company-research:' + _digest([report.get('accountId'), report.get('symbol')]),
                'accountId': report.get('accountId'), 'symbol': report.get('symbol'), 'registeredAt': now.isoformat(),
                'baseline': baseline, 'latestFacts': facts, 'questions': questions, 'interpretation': insight, 'documents': documents,
                'sourceCutoffAt': cutoff.isoformat(), 'fingerprint': fingerprint,
                'status': 'awaiting-new-report', 'revision': 1, 'history': [], 'archivedRevisionCount': 0,
                'boundary': '자료 변화와 확인 질문의 기록입니다. 투자 가설의 적중·실패나 매매 판단을 판정하지 않습니다.'}
    changes = _changes(facts, mapping(previous.get('latestFacts')))
    # Retain dated interpretation in history when its exact current binding expires.
    event = {'revision': previous.get('revision', 1) + 1, 'registeredAt': now.isoformat(),
             'sourceCutoffAt': cutoff.isoformat(), 'changes': changes, 'questions': questions,
             'interpretation': insight, 'previousInterpretation': previous.get('interpretation', {}), 'documents': documents,
             'kind': 'new-period' if any(item['kind'] == 'new-period' for item in changes)
                     else 'same-period-revision' if changes else 'evidence-or-interpretation-update'}
    history = rows(previous.get('history')) + [event]
    previous.update(latestFacts=facts, questions=questions, interpretation=insight, documents=documents,
                    fingerprint=fingerprint, sourceCutoffAt=cutoff.isoformat(), revision=event['revision'],
                    status='review-required' if changes else previous.get('status', 'awaiting-new-report'),
                    history=history[-HISTORY_LIMIT:],
                    archivedRevisionCount=previous.get('archivedRevisionCount', 0) + max(0, len(history) - HISTORY_LIMIT))
    return previous


def research_record_section(record):
    record = mapping(record)
    if not record:
        return {'key': 'researchRecord', 'title': '시간을 따라 확인할 기록',
                'paragraphs': ['아직 저장된 추적 기준이 없습니다. 자료를 확인한 뒤 운영 작업에서 등록합니다.']}
    history = rows(record.get('history'))
    changes = [item for event in history[-3:] for item in rows(event.get('changes'))]
    labels = []
    for item in changes:
        after, before = mapping(item.get('after')), mapping(item.get('before'))
        line = text(item.get('label')) + ' · ' + text(after.get('label') or after.get('key')) + ' · ' + text(after.get('period'))
        if before:
            line += ' · ' + amount(before.get('value'), before.get('currency')) + ' → ' + amount(after.get('value'), after.get('currency'))
        labels.append(line)
    return {'key': 'researchRecord', 'title': '시간을 따라 확인할 기록',
            'paragraphs': ['기준 등록 ' + (_clock(record.get('registeredAt')).astimezone(ZoneInfo('Asia/Seoul')).strftime('%Y-%m-%d %H:%M KST') if _clock(record.get('registeredAt')) else '시각 미확인') + ' · 기록 ' + str(record.get('revision', 1)) + '회',
                           '새 실적을 확인했습니다. 원래 질문과 함께 재검토가 필요합니다.' if record.get('status') == 'review-required'
                           else '기준을 저장했습니다. 다음 공시 자료를 기다리고 있습니다.', text(record.get('boundary'))],
            'rows': labels + [text(item) for item in mapping(record.get('baseline')).get('questions', [])],
            'recordId': record.get('recordId')}


def company_research_memory(record, account_id, symbol, cutoff_at):
    """Dated questions/interpretations only; current facts still come from ABox."""
    record = mapping(record)
    cutoff, registered = _clock(cutoff_at), _clock(record.get('registeredAt'))
    if (not cutoff or not registered or registered > cutoff
            or record.get('accountId') != account_id or record.get('symbol') != symbol):
        return {}
    baseline = mapping(record.get('baseline'))
    eligible = [event for event in rows(record.get('history'))
                if _clock(event.get('registeredAt')) and _clock(event.get('sourceCutoffAt'))
                and _clock(event['registeredAt']) <= cutoff and _clock(event['sourceCutoffAt']) <= cutoff]
    if record.get('archivedRevisionCount', 0) and not eligible:
        return {}  # Never reconstruct a missing historical review from today's state.
    latest = eligible[-1] if eligible else baseline
    insight = mapping(latest.get('interpretation'))
    return {'recordId': record.get('recordId'), 'accountId': account_id, 'symbol': symbol,
            'registeredAt': record.get('registeredAt'), 'reviewedAt': latest.get('registeredAt'),
            'usage': 'dated-research-questions-not-current-facts-or-qualified-hypotheses',
            'originalQuestions': list(baseline.get('questions', []))[:4],
            'currentQuestions': list(latest.get('questions', []))[:4],
            'previousInterpretation': {key: insight.get(key) for key in ('asOf', 'thesis', 'mechanism', 'invalidation') if insight.get(key)},
            'observations': [{'kind': event.get('kind'), 'observedAt': event.get('registeredAt'),
                              'periods': sorted(set(text(mapping(change.get('after')).get('period')) for change in rows(event.get('changes'))))}
                             for event in eligible[-3:]],
            'qualification': 'not-evaluated'}
