#!/usr/bin/env python3
"""Replay actual captured facts through the real AI and production validators.

No notification queue or transport capability is constructed. Account data and
model outputs are saved only in an operator-selected private local directory.
"""
import argparse
import copy
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'python_service'))
from digital_twin.infrastructure.settings import runtime_settings
from digital_twin.infrastructure.composition.ai_orchestration import call_observation_model
from digital_twin.modules.ai_orchestration.domain.planning import validate_plan, observation_fingerprint, stamp
from digital_twin.modules.ai_orchestration.domain.execution_input import freeze_execution_input, freeze_review_input, validate_execution_input
from digital_twin.modules.ai_orchestration.domain.insight_quality import local_quality, accept_review, quality_block
from digital_twin.modules.ai_orchestration.domain.publication import publication_block
from digital_twin.modules.ai_orchestration.domain.insight_contract import instant
from digital_twin.modules.notifications.application.ai_observation_message import render_ai_observation
from digital_twin.modules.reasoning.domain.observation_evidence import select_evidence, EVIDENCE_PROTOCOL, EVIDENCE_PROFILE


def private_write(path, text):
    with open(path, 'w', encoding='utf-8', opener=lambda p, flags: os.open(p, flags, 0o600)) as stream:
        stream.write(text)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('capture', type=Path)
    parser.add_argument('--symbols', default='MSTR,TSLA')
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    capture = json.loads(args.capture.read_text())
    packets = {}
    for row in capture.get('records', []):
        saved = row.get('context', {}).get('aiControlObservation', {})
        if saved.get('input'):
            packets.setdefault(saved['input']['symbol'], saved['input'])
    args.output.mkdir(parents=True, exist_ok=True, mode=0o700)
    settings = runtime_settings()
    results = []
    for symbol in args.symbols.split(','):
        packet = copy.deepcopy(packets[symbol])
        # Historical v0 captures lacked inventory accounting. Reclassify only
        # their preserved facts; never enrich them with today's data.
        if not packet.get('protocolVersion'):
            packet['facts'], packet['coverage'] = select_evidence(packet['facts'])
            packet.update(protocolVersion=EVIDENCE_PROTOCOL, profile=EVIDENCE_PROFILE)
        envelope = freeze_execution_input(packet, [], [])
        private_write(args.output / (symbol + '-input.json'), json.dumps(envelope, ensure_ascii=False, indent=2))
        validate_execution_input(envelope)
        print(symbol + ': real model analysis started', flush=True)
        raw = call_observation_model(envelope, {**settings, 'aiWorkload': 'observation-replay'})
        private_write(args.output / (symbol + '-response.json'), json.dumps(raw, ensure_ascii=False, indent=2))
        result = {**validate_plan(raw, packet), 'input': packet, 'observedAt': stamp(), 'inputFingerprint': observation_fingerprint(packet, [])}
        result['quality'] = local_quality(result)
        if result['notification']['send'] and result['quality']['status'] == 'awaiting-review':
            review_input = freeze_review_input(result)
            private_write(args.output / (symbol + '-review-input.json'), json.dumps(review_input, ensure_ascii=False, indent=2))
            validate_execution_input(review_input)
            print(symbol + ': real model independent review started', flush=True)
            review_response = call_observation_model(review_input, {**settings, 'aiWorkload': 'observation-replay-review'})
            result['quality'] = accept_review(result, review_response, 'local-replay-review')
        reason = publication_block(result, now=instant(packet['capturedAt'])) or quality_block(result)
        # Gate time is the original capture time. This is NOT live publication.
        result['replay'] = {'transportCalls': 0, 'queueWrites': 0, 'publicationGateAtOriginalTime': reason or 'accepted',
                            'historicalCaptureAt': packet['capturedAt'], 'actualModelUsed': True}
        private_write(args.output / (symbol + '-result.json'), json.dumps(result, ensure_ascii=False, indent=2))
        text = render_ai_observation(result) if not reason else '발송 보류: ' + reason + '\n' + '\n'.join(result['quality'].get('errors', []))
        private_write(args.output / (symbol + '-message.txt'), '과거 입력 재생 · 실제 발송 없음\n\n' + text)
        row = {'symbol': symbol, 'sendRequested': result['notification']['send'], 'quality': result['quality']['status'],
               'gate': reason or 'accepted', 'messageFile': str(args.output / (symbol + '-message.txt'))}
        results.append(row)
        print(json.dumps(row, ensure_ascii=False), flush=True)
    private_write(args.output / 'report.json', json.dumps({'cases': results, 'transportCalls': 0, 'queueWrites': 0}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
