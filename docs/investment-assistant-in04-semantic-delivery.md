# IN-04 의미 변화, 성공 receipt와 AI 실패 경계

- 구현일: 2026-09-25, Asia/Seoul
- 범위: 투자 AI 작업 coalescing, 최종 성공 전달 비교, AI 실패 진단
- 유지한 정책: 사용자 cadence, 장 시간 advisory, 투자 행동 envelope, TypeDB 권한
- 운영 변경: 알림 재발송, 과거 결과 migration, release 승격 없음

## 실제 전달 경로와 책임

투자 알림은 세 단계로 나뉜다.

1. TypeDB subject case가 `PUBLISH_TYPEDB`, `HANDOFF_AI`, `ARCHIVE`, `INVALID` 중 route를 고정한다.
2. AI queue는 같은 종목의 진행 중·대기 요청과 **의미 지문**을 비교해 같은 작업을 합치거나,
   중요한 변화가 있을 때만 새 요청으로 교체한다.
3. 최종 notification worker는 종목별 MySQL named lock을 잡은 뒤 마지막 성공 transport receipt를
   다시 읽고, 최종 gate를 통과한 실제 렌더링 본문을 발송한다.

AI 요청 성공, AI 작성 계약 통과, 알림 job 완료, transport 성공은 서로 다른 상태다. 성공 전달
baseline에는 `notification_delivery_attempts.status=delivered`가 있고 AI 작성·publication 계약도
통과한 insight만 들어간다. 실패 발송, suppressed, web-only, TypeDB fallback은 성공 baseline을
대체하지 않는다. job을 `done`으로 바꾼 사실만으로 전달 성공을 만들 수도 없다.

## 의미 지문

기존 지문이 이미 보존하던 행동 envelope, rule, 가설 계열, 독립 source event, lifecycle 및
추적 조건 전환에 다음 구조화 항목을 추가했다.

- 회사 `materialRevision`, section revision, 재무 `decisionFingerprint`와 보고 기간
- 평가 model/version, 입력 EPS·배수·시나리오, 평가 범위, 계산 적격성과 제외 이유
- assessment bundle의 evidence/opinion/execution/monitoring 상태
- 가설의 assumption ID와 구조화된 무효화 조건

제목, 문장 표현, 사용자 표시 시각, 수집 시각, generation ID, 단순 현재가 polling은 지문에 넣지
않는다. 공식 사건 revision, 재무 정정, 평가 입력, 행동, 가정, 무효화 임계값이 바뀌면 새 의미다.
자연어만 다른 같은 판단은 같은 작업으로 합친다.

이 확장은 기존 `material_fingerprint` 열과 receipt reader를 그대로 사용한다. 이전 runtime이 새
필드를 몰라도 저장된 request와 receipt는 파괴되지 않는다. rollback 뒤 AI 작업이 다시 계산되더라도
최종 성공 receipt 비교가 같은 publishable insight의 재발송을 막는다.

## AI 실패 진단

`notification-ai-failure-v1`의 기존 저장 형식을 유지하면서 범주를 더 구체화했다.

| category | 의미 | 재시도 |
| --- | --- | --- |
| `usage-limit` | provider rate/usage/quota/credit 한도 | 가능 |
| `timeout` | 모델 실행 deadline 초과 | 가능 |
| `response-format` | JSON/응답 형식을 파싱할 수 없음 | 자동 반복 안 함 |
| `contract-invalid` | 형식은 읽었지만 투자 publication 계약 위반 | 자동 반복 안 함 |
| `prompt-contract-budget` | 최소 DecisionCore를 prompt budget에 보존할 수 없음 | 자동 반복 안 함 |
| `capacity`, `model-process`, `execution` | 실행 슬롯, 프로세스, 그 밖의 실행 오류 | 가능 |

실패 메시지 일부만 보고 모든 오류를 사용량 한도로 분류하지 않는다. 실패 fallback은 AI 작성
판단이 아니며 새 HOLD/WATCH나 “AI 판단이 곧 도착한다”는 약속을 만들지 않는다.

## 검증 시나리오

| 시나리오 | 기대·결과 |
| --- | --- |
| 같은 입력 20회 | AI request 1건, 나머지 19건 `coalesced-identical` |
| 두 worker가 같은 계좌·종목 발송 경쟁 | 첫 worker만 named lock 획득, 두 번째는 성공 이력 재확인 후 재시도 |
| 제목·표시 시각만 변경 | 같은 의미 지문 |
| 회사 material revision 변경 | 다른 의미 지문 |
| EPS 평가 입력 변경 | 다른 의미 지문 |
| 가정 또는 무효화 임계값 변경 | 다른 의미 지문 |
| 실패·suppressed·job done이나 receipt 없음 | 성공 전달 이력에서 제외 |
| 실제 발송 본문 | transport 직전 SHA-256과 bounded 원문을 attempt receipt에 보존 |
| 사용량·timeout·형식·계약·일반 오류 | 서로 다른 failure category |

기존 회귀 fixture는 최초 유효 AI insight, 행동 변경, 새 source evidence, verified follow-up 전환,
중대한 graph/lifecycle 변화가 blanket suppression을 우회하는지 함께 검증한다. 반복 감소만 보고
중요 알림 누락을 성공으로 판단하지 않는다.

## 운영 읽기 전용 감사

최근 보존된 투자 알림/attempt 77행을 계좌·종목·본문 없이 집계했다. 성공 transport receipt는
10건, 실패 attempt는 65건이었다. 성공 10건의 실제 렌더링 SHA-256은 모두 달랐고 동일 본문
재전달은 0건이었다. AI request material fingerprint가 있는 성공 receipt는 2개 그룹 2건이며
같은 지문의 중복 성공은 0건이었다.

보존된 투자 AI request 41건은 모두 terminal `completed`였지만 실제 결과는 AI 작성 9건,
TypeDB fallback 32건이었다. contract failure code는 `hypothesis-contract-mismatch` 2건,
`model-or-contract-error` 29건, `missing-narrative-sections` 1건이었다. 압축 실행 audit 200건의
failure category 출현은 `contract-invalid` 40, 일반 `execution` 24,
`prompt-contract-budget` 3이었다. 한 artifact 안의 중복 위치를 포함한 출현 횟수이므로 서로 다른
실패 건수로 해석하지 않는다. 과거 원문 오류는 retention 이후 재분류하지 않고, 새 실행부터
`usage-limit`과 `response-format`을 별도로 기록한다.

감사 중 운영 쓰기, 외부 수집, AI 요청, 알림 생성·발송은 없었다.
