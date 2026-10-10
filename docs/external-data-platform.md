# External Data Platform

## Purpose

External market APIs are collected by dataset, not by one portfolio snapshot call. Request paths only read normalized current facts. The dedicated worker owns vendor calls, schedules, rate limits, retries, circuit state, and telemetry.

This boundary supplies source facts. It does not decide `BUY`, `SELL`, or `HOLD`. Material source changes are recorded as `external_data.fact_changed`; portfolio monitoring combines those facts with account and market state before requesting investment reasoning.

## Runtime Flow

1. `ExternalDatasetRegistry` loads typed adapters.
2. The worker derives active partitions from current account-focus symbols.
   Holdings and watchlist symbols are coverage-critical: provider batch caps
   may delay execution, but cannot remove those symbols from the durable
   schedule. New account-focus symbols become due work on the next partition
   sync.
3. MySQL leases due work from `external_dataset_state` with `FOR UPDATE SKIP LOCKED`.
4. Independent providers run concurrently; work for the same provider runs serially.
5. `external_provider_state` atomically enforces provider call spacing, dataset request budgets, and circuit state.
6. Vendor I/O runs outside database transactions.
7. A short transaction updates `external_fact_current`, optionally appends `external_fact_revision`, records a material source event, and releases the lease.
8. `ExternalSignalsReadModelService` merges relevant current facts into the compatibility `externalSignals` shape.
9. The fitness read model evaluates each subject by decision purpose, so a
   successful provider call is not confused with data that is usable for a
   price, valuation, disclosure, news, consensus, derivative, macro, or crypto
   decision.
10. The coverage gate compares every holding/watchlist subject with its
    supported purpose schedules. `complete` means every subject has the
    required durable partitions; missing schedules stay visible and are made
    eligible for normal worker collection rather than disappearing from the
    read model.

## Datasets

| Dataset | Default cadence | Default freshness | Role |
| --- | ---: | ---: | --- |
| `coingecko.market` | 10 min | 25 min | Bulk crypto market observation |
| `fred.macro` | 6 h | 48 h | Published US rate observations |
| `alpha.quote` | 6 h | 24 h | Low-budget US quote fallback |
| `sec.submissions` | 15 min | 1 h | SEC filing metadata and recent Form 3/4/5, 13F, 8-K, 10-Q, 10-K filings |
| `sec.company_facts` | 6 h | 24 h | SEC company facts |
| `opendart.disclosures` | 10 min | 30 min | Korean disclosure documents |
| `opendart.company_facts` | 24 h | 48 h | Korean company and financial facts |
| `opendart.xbrl_facts` | follow-up once | 540 d | Annual report XBRL candidates linked by receipt number |
| `yfinance.price` | 30 min | 1 h | Price history and quote context |
| `yfinance.options` | 1 h | 2 h | Options context |
| `yfinance.news` | 24 h | 48 h | Vendor news metadata only |
| `yfinance.analyst` | 7 d | 14 d | Analyst and estimate context |
| `yfinance.fundamental` | 24 h | 48 h | Financial and valuation context |

News article collection remains in the news bounded context. Toss/KIS live price and microstructure remain in the market-data bounded context.

## Storage

- `external_dataset_state`: durable partition schedule, lease, watermark, and retry state.
- `external_fact_current`: one canonical current fact per dataset and subject.
- `external_fact_revision`: source revisions only; volatile price datasets do not append every poll.
- `external_provider_state`: provider-wide call spacing plus dataset budget and circuit health.
- `external_collection_runs`: bounded duration, byte size, outcome, and material-change telemetry.

The previous `app_store.external_signals` aggregate is imported once and replaced by a compact migration receipt. Dedicated company-knowledge and crypto caches remain independent.

## Adding A Provider

1. Implement `ExternalDatasetAdapter` under `infrastructure/external_api/adapters/`.
2. Declare a `DatasetDescriptor` with capability, cadence, freshness, priority, rate limit, budget, revision mode, and materiality policy.
3. Return global or subject partitions without making a vendor call.
4. Return a `SourceObservation` from `fetch()` with stable source revision and source timestamp.
5. Register the adapter in `default_external_dataset_registry()`.
6. Add settings defaults, environment examples, transition tests, and adapter tests.

The scheduler and MySQL stores require no provider-specific branching.

## Operations

```bash
npm run python:external-data:status
npm run python:external-data:once
npm run python:external-data:once -- --force
npm run python:external-data:watch

# Repair selected durable facts without disturbing other partitions.
python3 python_service/service.py external-data refresh \
  --datasets sec.company_facts,opendart.company_facts,opendart.xbrl_facts \
  --symbols NVDA,000660 \
  --max-batches 20
```

`opendart.company_facts`가 최신 사업보고서 접수번호를 확인하면
`opendart.xbrl_facts` 후속 partition을 만든다. 첫 refresh에서 partition이 새로 생성된 경우 같은
명령을 한 번 더 실행하면 즉시 수집할 수 있고, 상시 worker는 다음 loop에서 자동 처리한다.

The refresh command validates dataset and subject identifiers, marks only that
scope due, and drains bounded batches through the normal rate-limit, retry, and
circuit-breaker path. It does not deactivate unrelated subjects or datasets.

Web status is available at `GET /api/external-data/status`. It reports configured policies, partition backlog, current fact storage, provider state, purpose-specific fitness, account-focus `coverageGate`, and 24-hour latency/error aggregates without exposing API keys or raw credentials. Fitness states are `fresh`, `partial`, `stale`, `unsupported`, `failed`, and `not-collected`; `partial` means the minimum usable source exists but the configured cross-check source does not. A successful current poll with no matching disclosure is recorded as fresh empty coverage, while a provider response that explicitly does not support the symbol is `unsupported`. Neither is mislabeled as an unattempted collection.

### OpenDART document error envelopes

The document endpoint may return an XML `result/status` error instead of a ZIP
archive. Parse this envelope before extracting filing text. Code `014` alone
marks the official file unavailable. Maintenance (`800`) uses the durable
`service-maintenance` deferral path: the document dataset pauses for 30 minutes,
including across worker restarts, then retries through the normal collector.
Repeated maintenance responses extend that pause without incrementing provider
failure counters or emitting system-error/connection-error alerts. Other APIs
from the same provider remain eligible. No fixed maintenance end date is baked
into code; a successful body download clears the maintenance state. The read
model exposes the deferred source as unavailable, not freshly collected data.
Request limits (`020`), authentication failures and other errors retain their
existing retry/circuit behavior. All paths preserve any prior usable fact.
Diagnostics retain the status code and
a fixed reason, not arbitrary vendor text or credentials. Error messages must
never become verified disclosure bodies even if they exceed the body length
threshold. See the [official document API guide](https://opendart.fss.or.kr/guide/detail.do?apiGrpCd=DS001&apiId=2019003).

On 2026-10-09, a bounded live check returned `800` (service maintenance),
explaining the observed metadata-only failures. The adapter repair makes that
cause explicit; it does not establish that the external service has recovered.
Offline tests cover retryable errors, previous-fact preservation, missing files
and valid ZIP bodies in `test_external_data_platform.py`. The isolated MySQL
test in `test_information_followup_storage.py` verifies durable, dataset-scoped
maintenance admission, retry expiry and recovery without calling OpenDART.
