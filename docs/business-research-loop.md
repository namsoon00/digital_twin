# Source-bound business research

## Answer-led questions

New original-question assessments use `answer-led-research-chain-v1`. The AI
may connect one next research question to an exact `answerParts.confirmed`
entry in the same assessment. The connection records the parent case, answer
index, causal/counter-evidence/persistence/value question, its reporting-period
contract, why it follows, and what a different answer would change. A partial
answer may support an independent next question while the parent's original
requirements and investigation limits remain unchanged.

The existing independent research review checks this additional meaning against
current source facts. Missing parent evidence, a reworded question, the parent's
unresolved requirements, or an unreviewed proposal cannot create a successor.
Each parent can have one successor; one assessment creates at most one, with a
maximum of six successive questions. A chain can stop earlier when further
answers would add no useful distinction. Existing task/token budgets, source
admission, account/world isolation, and the 12-active-question limit remain.

The parent assessment, `inquiryContinuation`, child's `inquiryLineage`, original
child contract, case events and source collection job commit in the existing
transaction. Retrying completion cannot duplicate them. This lineage is research
history, not fresh evidence or an investment action. Each subsequent answer must
cite newly selected usable source facts; the historical answer cannot cite itself.
The question screen and existing research-progress messages show the parent
answer and the next question's purpose.

Activation applies to new assessments without rewriting old questions or forcing
a paid migration pass. Previously completed questions are not silently reopened;
unchanged-source waits continue under their existing policy. Tests use synthetic
model results and the isolated MySQL test database, not production notifications.

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

## Independent documentary answers

Central research keeps its original collection task but now enables the source
owner's cited review. The reviewer cannot add tasks in this mode and does not run
without eligible evidence. Its answer is bound to the original task, the exact
assessment and the saved final-round source packets. Missing metrics, periods or
source bodies prevent a complete answer; partial reviews retain their citations.
A source answer never qualifies the investment hypothesis or changes trade authority.

Official report discovery retains eight quarterly and two annual filings.
Year-over-year questions prioritize the latest and matching prior-year reporting
period, ahead of intermediate quarters and report exhibits. Whole bounded tables
retain headers and units alongside source passages. Publication dates and the
question's authored freshness window remain enforced: rereading an old filing
does not make it current market evidence. An insufficient window is reported.

Research results publish answered, partial or unavailable artifacts to the same
question conversation even when no new records were inserted. Unavailable reports,
missing access configuration, failed review and invalid citations have explicit
causes. Rejected market-observation reviews publish their quality blockers without
accepting the rejected market explanation. Material-state deduplication excludes
retry clocks. Next-check times use KST and describe eligibility, not completion.
Existing frozen historical notifications retain their original payloads.

Collection cooldown is a scheduling state. Central research retains the same
task and case, records `research-wait`, and resumes at the recorded retry time.
It does not publish an unavailable answer or exhaust the failure allowance.
The owning question's waiting state and next-check time commit with the deferred
task, without an answer notification. The web view shows that scheduled wait.
Any partial answer validated against the current receipts remains visible while
the remaining collection waits; an unavailable review is not returned as an answer.
Already collected evidence may still be reviewed during the interval; the
interval limits another collection, not another question's use of that evidence.
The final cited review replaces earlier exploration gaps. Reviewers keep required
coverage within the original question instead of adding optional quantitative
detail to a qualitative question. Numeric comparison requirements remain intact.
If a documentary review cites an unavailable receipt or the wrong assessment
version, one corrective review may read the same current evidence packets. The
failed review and correction remain in the audit. Another invalid response stays
unavailable; code never substitutes citation IDs or accepts an uncited answer.
The pre-task evidence wake consumes at most 100 pending event keys using the
name/time index before loading bodies. Non-target events also receive a consumed
receipt so idle cycles do not repeatedly inspect historical JSON; late commits
remain eligible because completion is tracked by event identity.

Validation: `test_documentary_answers`, `test_ai_continuity_research`,
`test_question_research_progress`, `test_research_progress`, `test_research_replies`
and `test_ai_brain_agenda`; live model quality still requires reviewing actual
source-backed answers after deployment.

## AI-directed source exploration

New central documentary research tasks use `official-document` coverage, which
accepts a verified filing or an issuer document without relabelling IR as an
audited filing. The request's original question, reporting requirements and
freshness window remain fixed. The existing source collection runs first. If
its cited review is incomplete and the next collection would repeat the same
request, `SourceExplorationService` runs inside the remaining research round.
`documentaryReviewOnly` still forbids adding unrelated tasks. Exploration is
enabled by default; `researchSourceExplorationEnabled=0` (environment
`RESEARCH_SOURCE_EXPLORATION_ENABLED=0`) disables this fallback.

The AI chooses `search`, `read`, or `finish`, with a reason and specific missing
requirements. Search uses the existing central model execution/accounting path
with live public web search; it needs no additional search subscription or MCP
server. The worker retains an actual search-tool execution receipt. Search
titles and URLs are discovery leads, never verified source text. Document reads
are internal Python functions. The AI can read only a candidate returned by a
search or a previously read page; it cannot pass an arbitrary URL to the reader.
Source decisions and search calls appear as `source-exploration` and
`source-search` in the existing AI call audit. The model policy is unchanged.

One exploration allows six decisions, two search operations, and three document
reads. It stops admitting new operations after its six-minute time budget and
bounds individual model/network/PDF timeouts. Exact decision inputs are saved
before each model call and selected operations are checkpointed before execution.
Checkpoints retain candidate provenance, failed reads, hashes, source passages,
and collected evidence under the original source task. A retry reuses completed
exploration or continues with its consumed budgets; a changed task contract cannot
reuse that audit. No completed historical research run is silently rerun.
Capacity, central usage budget and shared recovery waits propagate to the central
queue, preserving a processing checkpoint. They neither close the question nor
consume another search/decision on retry. A pending search resumes its selected
operation; completed searches and reads are not repeated. Source review uses the
same deferral policy, so waiting for its model slot cannot turn an answer into a
terminal research failure.

The reader uses credential-free HTTPS, validates every redirect and public DNS
address, and pins the actual connection to that address. Private/local addresses,
non-HTTPS URLs, credentials in URLs and oversized bodies are rejected. HTML
tables remain whole. Text PDFs are decoded by a short-lived, resource-bounded
`pypdf==6.20.0` worker; up to 60 pages are examined and omitted pages are explicit.
Scanned PDFs require OCR and currently remain unavailable instead of producing
invented text. Original byte hashes and selected passage hashes are retained.

Issuer identity comes from the existing official IR registry, including NVIDIA's
[official newsroom](https://nvidianews.nvidia.com/) as an alternative to its IR
host. Exact PDF links from a read official page and PDF redirects originating
at that issuer retain their provenance. A company mention or AI assertion cannot
register a new trusted publisher. Other public sites may be explored as leads,
but unverified issuer identity or publication dates prevent their admission.
PDF creation dates, HTTP modification times and search snippets are not substituted
for publication dates. A 403, missing parser, empty search, unknown publisher,
stale report and exhausted budget stay distinct. None proves that the information
was never publicly disclosed.

Publication parsing accepts ISO timestamps and prefers the article's own metadata
or header over dates in related-story widgets. Conflicting primary publication
dates still prevent admission. An exact document link in a previously read
official index can supply a date only when its issuer host, link label and parent
document hash are preserved and agree; an AI-provided date is not sufficient.

Collected documents pass the existing evidence governance, source persistence,
projection events and `IssuerIRDocument` ABox mapping. Only then can they enter
subsequent investment reasoning. The source answer itself remains research-only.
The v2 question coverage contract retains AI-authored missing requirements; an
`addressed` claim with missing requirements cannot close a question. Independent
review must still cite the exact assessment and original source packets.

The central AI page displays the source answer, citation links, missing items and
search/read counts under the original question. Full exploration inputs and
receipts remain in `ResearchRun.executedPlan.sourceExploration`. These records
describe investigation and source coverage, not prediction accuracy or return.

Validation: `test_source_exploration` covers the API-miss → search → original
read → source review → original question → ABox path, trust/freshness rejection,
PDF decoding, query privacy, private-address/redirect rejection, checkpoint reuse,
publication metadata and operation budgets. `test_research_cooldown` covers cached
source review during collection waits, durable resumption of the same question,
lease ownership and preservation of actual failure retries.
`ai-source-exploration.test.mjs` covers answer visibility
and unsafe citation links. Current Codex search configuration follows the
[official configuration documentation](https://learn.chatgpt.com/docs/config-file/config-basic).

Live acceptance on 2026-10-11 used the configured central model and real public
HTTPS sources with an empty fixed-source response and isolated in-memory result
stores. For a NVIDIA quarterly Data Center revenue question, one search and three
reads (including an explicit 403) collected one dated official newsroom document;
independent review returned a cited answer with no missing requirements. No
operational question, evidence row or investment notification was rewritten by
this validation. This verifies that path, not coverage of every question/source.

## 보유·즐겨찾기 기업의 조사 준비 상태

기존 투자 연구 작업자가 활성 계좌의 보유 종목과 현재 즐겨찾기를 함께
순환 점검한다. 두 목록에 동시에 있는 종목은 계좌·종목별로 한 번 처리하며,
해제된 즐겨찾기는 보유 중인 경우에만 대상에 남는다. 새 작업 프로세스나
별도의 AI 실행 경로는 추가하지 않는다.

- 즐겨찾기 저장, 도메인 이벤트, `monitor_account_jobs` 갱신 요청은 같은
  MySQL 트랜잭션으로 기록한다. 실행 중인 모니터의 임대를 유지하며, 웹
  프로세스 재시작 후에도 후속 갱신 요청이 남는다.
- 등록 → 자료 수집 → 기업 조사 → 관계 확인 → 최초 TypeDB 근거 읽기 →
  중앙 AI 최초 분석을 기업 보고서의 **기업 조사 진행**에서 확인한다.
  사업 설명, 공식 최근 재무, 동일 기준 전년 비교, 현금흐름, 차입금,
  검증된 공식 문서와 기존 가치평가 입력 상태를 따로 표시한다.
- 부족한 항목은 기존 중앙 AI `research-plan` 큐로 전달한다. 동일 계좌·종목·
  세계·자료 기간·부족 항목 조합은 같은 작업 ID를 사용한다. 중앙 AI의
  임대, 예산, 재시도, 출처 검증과 연구 진행 알림 정책을 그대로 적용한다.
  대기나 작업 생성 자체를 분석 완료로 간주하지 않는다.
- 관계 보강은 저장된 최신 공식 문서 최대 4건에서 명시적인 직접 관계만
  다시 확인한다. 원문 발행 시각과 기존 주장의 최초 인지 시각을 유지하고,
  바뀐 자료만 기존 증거 저장소와 TypeDB 요청 아웃박스에 함께 기록한다.
  과거 문서 재처리를 새로운 투자 알림으로 발송하지 않는다. 정확히 식별할
  수 없는 회사는 미해결 상태로 남기며 관계 기업을 즐겨찾기에 자동 추가하지 않는다.
- 검토 통과한 실제 중앙 AI 관측의 TypeDB 스냅샷 식별자와 시각으로 최초
  분석을 확인한다. 최초 분석 기록은 재시작과 최근 작업 보존 기간 이후에도
  유지하되 다른 계좌 세계로 승계하지 않는다. 최초 분석 완료는 모든 최신
  자료의 재검토 완료나 매수 적합성을 의미하지 않는다.

한 작업 주기에 기본 2종목(상한 5종목)을 처리한다. 부분 상태는 15분,
준비 완료 상태는 60분 후 다시 점검하고 실패하면 5분 후 재시도한다.
계좌 스냅샷에 아직 없는 신규 종목은 모니터 반영부터 요청한다.
진행 상태와 순환 위치는 MySQL에 저장하고 변경 이력은 최근 8건으로 제한한다.
이 상태는 운영 정보로만 제공하며 재무 변경 알림의 비교 지문에는 포함하지 않는다.
