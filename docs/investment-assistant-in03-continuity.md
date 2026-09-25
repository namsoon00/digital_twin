# IN-03 판단 연속성: source cutoff 기반 이전 판단 연결

- 구현일: 2026-09-25, Asia/Seoul
- 범위: `decisions`의 이전 판단 조회, 연속성 packet, 최종 AI prompt 압축 계약
- 운영 변경: 과거 episode migration, 알림 생성·발송, release 승격 없음
- 원칙: 잘못된 시각은 현재 시각으로 대체하지 않고 현재 사실 분석과 과거 판단 비교를 분리한다.

## 구현 결과

새 요청의 판단 연속성 cutoff는 다음 순서로 선택한다.

1. 이미 고정한 `decisionContinuityCutoffAt`
2. 이미 고정된 continuity packet의 `capturedAt`
3. `ontologyRelationContext.inferenceGenerationAt`
4. 검증된 source snapshot의 `generatedAt`
5. relation의 `sourceObservedAt`
6. event의 `eventGeneratedAt`
7. legacy `referenceDate`

1~6은 timezone을 포함한 ISO source/case machine timestamp만 허용한다. `referenceDate`는 기존 요청을 읽기 위한 명시적
compatibility 경로로만 남겼다. 공개 투자 timestamp parser가 ISO와 `KST`, `UTC`, `GMT`를
검증해 UTC ISO로 정규화하며, `PST`처럼 지원하지 않는 zone과 잘못된 문자열은 거부한다.
missing/invalid cutoff는 현재 시각으로 바꾸지 않고 `invalid-cutoff`로 기록한다.

이전 판단 조회는 `decided_at <= cutoff`를 DB 쿼리에 포함한다. 따라서 가장 최신 판단이
cutoff 이후여도 `(account_id, symbol, decided_at)` 인덱스에서 cutoff 이전 최신 판단을 찾는다.
조회 결과는 최대 12개로 제한하고, 현재 episode, 다른 계좌·종목, 미래 판단은 application
경계에서도 다시 제외한다.

연속성 상태는 다음을 구분한다.

| 상태 | 의미 |
| --- | --- |
| `available` | cutoff 이전의 유효한 이전 판단을 찾음 |
| `no-prior-before-cutoff` | 조회는 성공했으나 cutoff 이전 이력이 없음 |
| `history-read-error` | 이력 저장소 조회에 실패함 |
| `invalid-cutoff` | cutoff가 없거나 파싱할 수 없음 |
| `partial` | 이전 판단은 있으나 outcome 등 보조 source 조회가 일부 실패함 |

이력 오류나 clock 오류를 HOLD/WATCH로 채우지 않는다. 현재 요청의 회사·시장 사실은 그대로
분석할 수 있지만, 과거 판단을 근거로 한 “유지/변경” 주장은 만들 수 없다.

새 packet은 `decision-continuity-packet-v4-cutoff-bound`다. 기존 v3 packet은 읽을 수 있어
rollback과 보존 이력의 additive compatibility를 유지한다. 분석 당시 고정한 packet과
`previousInvestmentAIInsightEpisode`는 이후 발송 상태로 다시 쓰지 않는다. 사용자가 마지막으로
받은 판단은 `previousDeliveredInvestmentAIInsightEpisode`로 별도 유지한다.

최종 압축 prompt의 `continuityDelta.previousDecision`에는 `episodeId`, `decidedAt`, `action`을
보존한다. 모델과 감사 로직은 어떤 시점의 어느 판단을 비교했는지 확인할 수 있다.

## 검증

합성 회귀 검증은 다음 경계를 포함한다.

- 같은 시각의 ISO와 legacy KST가 같은 UTC cutoff가 됨
- 알 수 없는 timezone, 잘못된 시각, missing cutoff를 명시적으로 거부
- 미래 관측과 cutoff 이후 판단 제외
- 다른 계좌·종목과 현재 episode 제외
- 최신 행이 cutoff 이후일 때 cutoff 이전 판단 선택
- 저장소 실패를 이력 없음과 구분
- 최종 prompt에 이전 판단 ID와 시각 보존
- AI 판단 이력과 성공 전달 이력을 별도 보존

운영 DB는 read-only transaction으로 최근 보존된 `investmentInsight` 알림 26건을 익명 집계했다.
26건 모두 `inferenceGenerationAt`을 가졌고 parser 결과는 `valid`였다. 연속성 packet이 남은
9건은 v3 8건, 기존 v2 1건이었으며, packet의 이전 판단 시각이 packet cutoff 뒤인 사례는
0건이었다. 최근 AI request 41건의 `context_json`은 retention 후 빈 객체였으므로, 운영 clock
분포는 원문을 보존한 notification payload에서 확인했다.

실제 `EXPLAIN`은 새 조회가 `idx_decision_episodes_account_symbol_time`을 사용해
`account_id + symbol + decided_at <= cutoff` 범위를 역방향으로 읽는 것을 확인했다. 감사 중
운영 쓰기, 외부 수집, AI 요청, 알림 생성·발송은 없었다.

과거 episode를 새 필드로 migration하지 않는다. 새 요청부터 machine cutoff와 v4 packet을
기록하고, 보존된 v3 packet은 기존 의미 그대로 읽는다.
