# Source-bound business research

The central observation lane now has a business research contract alongside its
price observations. This is a research memory, not a qualified predictive model
or a trading action. Existing TypeDB investment-action governance is unchanged.

## Source to analysis

`ObservationEvidenceSession.business_baseline()` reserves annual and quarterly
reports, verified documents, relationship assertions and company/valuation context
before discretionary AI reads. The reservation is bounded to 26 KB and four facts
per family. The complete inventory, omission counts and snapshot identities remain
in the evidence packet; unavailable data cannot masquerade as a completed review.
The observation evidence profile is v3. Historical v1/v2 packets remain readable.

The v15 author schema captures up to two business theses with the business
question, mechanism, prerequisite, alternative explanation, invalidation condition,
current evidence IDs, missing evidence and a 90–730 day horizon. Numeric
checkpoints bind exact source-referenced `reportedValues`; AI cannot choose a
numerical target. Annual and standalone quarterly observations use the following
year's same reporting period (330–400 days, duration difference at most eight days).
A checkpoint therefore requires at least a 365-day horizon. Cumulative reports,
changed currency/provider/accounting scope and missing report duration do not pass.
Earlier v14 and other frozen prompts/schemas retain their replay builders.

Business theses/reviews may omit price followups and price comparisons. Independent
notification review also reviews their mechanism, alternative and invalidation.
Notification hashes include the structured business content. The dashboard shows
these contracts and metric results, and notifications render the business check.

## Durable feedback

`ai_brain_cases` stores `business-thesis` contracts and their exact registration
baseline independently of whether a notification was sent. The original contract
and source evidence are retained; `ai_brain_case_events` stores registration and
reviews in the same transaction as task completion. At most two active contracts
per account/symbol/world enter mandatory memory. A replacement retires the original
and links a newly registered contract, rather than rewriting its prediction.

Before the next model input is frozen, deterministic code compares new reports to
the registered baseline. Both supporting and opposing metric directions are
retained. Same-period corrections, data fetched after the capture cutoff, newly
backfilled old reporting periods and expired observation windows are not outcomes.
The first observed result is immutable; a later restatement remains new source
context for review, never a rewritten successful test. A metric direction says
nothing by itself about causality, thesis-wide correctness or investment return.
Changed observations and weekly due reviews must be explicitly reviewed using
current evidence. Existing dated company research records also enter continuity.

## Connected companies

The existing source-research use case enriches verified issuer filings before
saving evidence and its durable projection events. Versioned extraction currently
supports explicit English/Korean issuer lists of suppliers, customers and
competitors. Unsupported language, mere co-mention, hypothetical statements and
negation create no relationship. This is deliberately partial extraction; it is
not a claim to cover every supply chain or to infer arbitrary causal connections.

An exact, unique full-name match in the instrument catalog binds a listed issuer.
No suffix stripping, parent/subsidiary merging, ticker guessing or fuzzy matching
is allowed. Unknown and ambiguous names remain visible research leads. A new
source revision or a resolved identity is a new assertion. The local MySQL table
`company_relationship_assertions` is append-only and preserves the first recorded
source excerpt, character offsets, document hash, publication/first-known clocks,
reporting period, direction and identity proof. Source changes and retractions
remove active projection eligibility while historical assertions remain auditable.

Projection adds source-backed `ExtractedClaim` ABox nodes. Resolved assertions
reuse `SUPPLIES_TO`, `SELLS_TO`, `COMPETES_WITH` and `Company` from the existing
TBox, with provenance in KnowledgeWorld. Unresolved names never get corporate
edges. The evidence reservation supplies these claims to central analysis and the
UI displays connected companies as additional research candidates. A dated
relationship does not prove that it still exists today; valid dates and economic
exposure remain unknown unless actually disclosed. No revenue share is invented.
No transitive relation or automatic watchlist entry is created.

Existing saved analyses retain their original input contract. New observation
runs use the new contract; graph assembly uses the v21 cache identity so a prior
cached graph cannot hide the new report lineage. Documentary relationship
enrichment runs when the research use case persists source documents. This
release does not silently backfill all previously cached documents.

The persistence projection also retains each selected stock's financial states,
relationship claims, source links and resolved corporate edges independently of
which relations the current RuleBox consumes. Connected companies do not become
additional rule subjects. Relationship-bearing documents retain exact bounded
proof during input assembly (up to twelve claims and 120,000 source characters);
only excerpts and provenance enter the ABox, not the document body.

## Validation and limits

Contract tests cover forced report inclusion, source-reference and report-basis
validation, misses and corrections, point-in-time exclusions, immutable prompt
replay, scoped review/replacement, source negation, entity ambiguity and the
ABox/provenance boundary. Existing task, agenda, publication and frontend checks
remain required. Storage tests use `orbit_alpha_test`, never production accounts.

The initial change supports one-hop, source-stated candidate discovery. It does
not yet provide unrestricted document extraction, automated cross-company
financial valuation, a multi-hop opportunity ranking, a quantified market
expectations model or a validated investment edge. Orders/backlog/unit economics
need source-specific metric contracts before becoming automatic checkpoints.
Empirical usefulness needs subsequent real filings and user review; tests and
narrative approval cannot supply future evidence.
# 연구 진행 알림

`researchProgress`는 계정의 기존 알림 채널로 연구 질문·사업 가설의 등록,
답변 검토, 보류·종료, 가설 수정과 등록 지표의 관측 상태 변경을 전달한다.
자료가 실제로 갱신된 첫 조사 반환은 `답변 검토 대기`로 표시한다.
수집 실패·쿨다운·주기적 유지 검토·문장만 바뀐 동일 상태는 반복 발송하지 않는다.
기존 이력을 일괄 재발송하지 않고 연결 이후 저장되는 새 사건부터 적용한다.

연구 질문, 연결 과정, 가정, 경쟁 설명, 재검토 조건, 부족 자료와 검토 예정 시각을
저장된 사건에서 표시한다. AI 답변·지표 방향 관측은 실증된 예측 성과나 매매 권고로
표시하지 않는다. 투자 해석 알림의 검토·발송 영수증에는 합산하지 않는다.
이 알림은 연구 업무 상태를 알리는 별도 계약이며 새 투자 판단 권한을 만들지 않는다.

과제 상태·사건·발송 대기 작업은 같은 MySQL 트랜잭션에서 저장한다. 실패하면 함께
롤백하며, 사건 ID에 기반한 발송 키와 과제별 상태 지문으로 중복을 방지한다.
알림 일정의 `연구 진행` 활성화 설정(`alertRules`)으로 수신 여부를 관리하고 기존 계정 야간 제한과 전송 재시도를
따른다. 큐 접수는 전송 완료가 아니며 최종 전송 상태는 알림 영수증으로 확인한다.

### Research message replies and Korean display clocks

Research progress uses Telegram replies to the first verified message for the
same account, world, symbol and question. A thesis with exactly one source
question shares that conversation; a thesis with several source questions owns
its own conversation to avoid attaching it to an arbitrary question. The small
`notification_research_threads` ledger retains verified message IDs by bot/chat
fingerprint independently of large notification payload retention. Existing
question jobs with retained checkpoints can supply the original anchor.

The worker serializes each research conversation. A pending original question
defers its results without consuming transport retry attempts. Successful first
chunks persist the anchor and delivery checkpoint in one transaction; subsequent
chunks resume with the same body, destination and reply target. Retry-exhausted or
suppressed originals without a receipt allow a new standalone anchor. Telegram
`reply_parameters.allow_sending_without_reply` permits delivery when the owner
has deleted the referenced message; the result still contains its question.
No synthetic research messages are sent to verify this behavior.

New notification bodies and notification screens display Asia/Seoul (KST).
Date-only report periods stay dates; UTC source timestamps, evidence URLs and
frozen already-delivered/partially-delivered message bytes stay unchanged. The
final AI delivery comparison applies the same display policy, preserving its
exact-body validation. Telegram's own message timestamp is controlled by the
recipient's Telegram device settings, separately from these message-body clocks.

Validation: `test_research_replies` covers reply payloads, parse fallback, split
retries, destination/world isolation, original-question ordering, durable and
legacy anchors, calendar rollover and display-only conversion;
`notification-evidence-audit.test.mjs` covers browser KST display.
