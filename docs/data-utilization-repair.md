# Source data utilization repair

This repair preserves already collected facts through projection and AI input.
It does not introduce a new market hypothesis, action rule, provider subscription,
or a claim that incomplete valuations are ready for investment decisions.

## Repaired boundaries

- Projection input v3 retains 1/5/20-day rate changes, comparison dates, the
  yield-spread observation date, and Korean macro units, changes and year-over-year
  comparisons. Source history remains bounded; raw vendor archives are not copied
  into the graph. Unknown numeric rate facts are omitted; measured zero survives.
- Each external observation owns its source clock. A recent account-wide fetch
  or another provider's freshness no longer supplies an observation timestamp.
  The yield spread uses its own observation date, not the merged macro root's
  clock. Source fetch time is retained separately as transport metadata.
- AI decision routing v13 carries the rate windows and observation dates. Macro
  entities retain the measured units and comparisons as graph properties. No new
  TypeQL investment rule or delivery authorization is added.
- The yfinance adapter retains period-end and estimate currency from the cached
  earnings-trend response already loaded by its public estimate accessors. Those
  fields were previously lost by the DataFrame conversion. Missing vendor cache
  metadata remains explicitly missing, with no extra unbudgeted vendor request.
  EPS observation identity includes period and currency. Quote/listing currency
  does not supply an EPS or revenue-consensus currency. Revenue estimates may use
  the provider's explicit financial currency when no estimate-specific unit is
  supplied; they never fall back to quote/history currency.
- Consensus rows expose missing period, currency and publication-clock fields;
  collection freshness alone does not imply full input completeness. Collection
  time and insider transaction dates never date an analyst consensus publication.
- Source lineage exposes `revisionPersistence`: `retained`, `current-only`, or
  `unverified`. Current-only price/option/crypto observations are content identities,
  not promises of immutable history availability. Exact revision lookup still
  fails closed. Older missing history is not fabricated from a newer source
  response. The existing retention policy remains a separate operating limit.

## Data still required

The local audit found secondary financial statements already collected for the
Korean subjects with incomplete official DCF rows. Some secondary statements lack
stock-based compensation, and reporting periods differ. Arbitrarily mixing these
with a partial official annual report would change the reporting basis. They
remain separate source-bound candidates; missing values are not replaced with
zero. Negative pretax income and anomalous consensus growth retain their existing
model exclusions.

Yahoo may not provide the consensus publication clock or all estimate currency
metadata. Relative horizons alone do not prove a precise fiscal period. New
collection preserves whatever the provider actually supplies; historical missing
metadata remains unknown. Full point-in-time consensus history still requires a
source with such history and cannot be recovered by repeatedly fetching today's
estimate.

BLS release HTML is denied with HTTP 403 in the local environment; the official
BLS statistics API remains a distinct functioning source. GDELT's collection
deadline expires and its existing circuit breaker delays retries. Neither limit
is addressed by bypassing access restrictions, resetting the circuit, increasing
request volume or reporting a failed collection as successful. Existing alternate
news/statistics sources remain independent. Additional API purchase is not a
prerequisite for these repairs.

## Verification

`test_data_utilization_contract.py` replays synthetic source facts into projection,
ABox and AI decision facts, and checks nonzero/zero/missing differences, source
clock isolation, source-only revision markers, estimate period identity and unit
handling. Existing financial and external-data tests cover the adjacent contracts.

Run `python3 scripts/verify-data-utilization.py` against local retained data. It
opens a MySQL read-only transaction, emits counts and quality gaps without account
values or credentials, and performs no vendor request, LLM call, TypeDB write or
notification send. It verifies all retained macro series' selected measurements
and the source-to-AI rate fields. This is an input preservation replay, not native
TypeDB inference, generated-answer validation, or delivery replay. `npm test` is
the repository regression gate. Historical decisions and frozen inputs are not
rewritten by this repair.

The local retained-data replay verified 10 macro series and no current/retained
revision hash mismatches. A bounded `yfinance.analyst` refresh for two subjects
completed successfully through the normal collection runner. Its four returned
EPS estimates all preserved period end and currency; all four still lacked a
vendor publication clock. Successful collection therefore remains partial input
quality, not evidence of a complete point-in-time consensus.
