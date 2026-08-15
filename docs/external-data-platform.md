# External Data Platform

## Purpose

External market APIs are collected by dataset, not by one portfolio snapshot call. Request paths only read normalized current facts. The dedicated worker owns vendor calls, schedules, rate limits, retries, circuit state, and telemetry.

This boundary supplies source facts. It does not decide `BUY`, `SELL`, or `HOLD`. Material source changes are recorded as `external_data.fact_changed`; portfolio monitoring combines those facts with account and market state before requesting investment reasoning.

## Runtime Flow

1. `ExternalDatasetRegistry` loads typed adapters.
2. The worker derives active partitions from current account-focus symbols.
3. MySQL leases due work from `external_dataset_state` with `FOR UPDATE SKIP LOCKED`.
4. Independent providers run concurrently; work for the same provider runs serially.
5. `external_provider_state` atomically enforces provider call spacing, dataset request budgets, and circuit state.
6. Vendor I/O runs outside database transactions.
7. A short transaction updates `external_fact_current`, optionally appends `external_fact_revision`, records a material source event, and releases the lease.
8. `ExternalSignalsReadModelService` merges relevant current facts into the compatibility `externalSignals` shape.

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
```

Web status is available at `GET /api/external-data/status`. It reports configured policies, partition backlog, current fact storage, provider state, and 24-hour latency/error aggregates without exposing API keys or raw credentials.
