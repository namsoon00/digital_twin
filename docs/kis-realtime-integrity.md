# KIS Realtime Integrity

## Incident

On 2026-09-14, a malformed quote entered the monitoring snapshot as KRW 24,382
for a stock whose adjacent snapshots were KRW 2,975. The same observation
contained negative gross trading value and a trade-strength value of 1,740,000.
The raw outbox accepted a +729.3% alert and advanced its anchor to 24,382.
The next valid quote then produced a false -87.8% reversal. This was input
corruption, not a percentage-formula error or a reason to reduce cooldowns.

A read-only live subscription confirmed that `H0STCNT0` carries 47 fields per
record and `H0STASP0` carries 63. The old decoder assumed 46 and 59. Its fixed
stride shifted subsequent records in batched messages, padded short numeric
values into stock codes, accepted incomplete rows, and persisted them as actual
quotes. The original incident's raw socket bytes were not retained; its frozen
normalized source snapshot and outbox anchors establish the contamination chain.

## Contract

- Supported trade record widths: 46 (legacy) and 47 (observed live extension).
- Supported orderbook widths: 59 (legacy) and 63 (observed live extension).
- Determine record width from the exact field count divided by the declared
  positive record count. Slice every record using that width, not prefix length.
- The known prefix retains its field mapping. Appended fields have no assigned
  investment meaning and are not projected as price or flow evidence.
- Reject unknown widths, partial batches, encrypted/non-quote messages, malformed
  symbols/clocks, non-finite prices, negative gross quantities/amounts and prices
  outside the same tick's declared positive daily low/high. No percentage-change
  cap is used: valid large moves and cumulative alert thresholds remain supported.
- Validate the entire batch before any cache write, event, ABox input or alert.
- Store `validationVersion`, `wireFieldCount` and stage-local `values` with the
  coverage record. REST refreshes and orderbook ticks must not relabel inherited
  trade values with a newer or unrelated source clock. Merge only validated
  stage-local values; unvalidated legacy WebSocket caches must be refreshed.
- Transport telemetry records `rejectedFrameCount` and `wireValidationVersion`.
  Rejected-only collection is `invalid-data`, not a successful empty collection.
  Counters do not publish rejected values into market facts.

The socket port uses `websockets==15.0.1` for frame assembly, ping/pong, UTF-8 and
timeouts. The old hand-written receiver could discard partially received bytes
on timeout and returned fragments as whole messages. The compatibility port name
does not retain that implementation. KIS JSON `PINGPONG` is echoed separately.

## Validation

`test_kis_realtime_integrity.py` covers legacy/live multi-record formats,
wrong lengths/counts, malformed symbols, incident-shaped corrupt values,
valid large moves, stage isolation, legacy-cache exclusion and fragmented
messages across a receive timeout. `test_market_observation_delivery.py` retains
the outbox concurrency/cadence tests. Run `npm test` before deployment.

Live checks use a temporary in-memory quote cache without source-event or
notification writes. Stop only the managed KIS worker while probing, then
restore it even if the probe fails; the supervisor may independently restart it.
After deployment, verify stage contracts in `market_quote_cache` and collection
telemetry. Do not rewrite historical source snapshots or old delivered messages
as if they had originally been correct. A corrupted historical anchor requires
an explicitly scoped, provenance-backed repair, not a blanket reset of every
subject's cumulative baseline.

The incident audit also found one corrupted 3-minute observation (8,732) and
three affected rollups in the active MySQL time series. A scoped repair backed
up those four rows in the ignored local maintenance directory, removed only
that proven-invalid observation, and recalculated the affected high/low/sample
counts from remaining observations. Corrected rollups received the repair's
availability timestamp; frozen source snapshots and delivered notifications
were not rewritten. No affected row or pending replay was found in the shadow
backend at repair time. This repair does not reset unrelated notification
baselines or invent a replacement quote for the missing observation.

Protocol references:
[KIS official examples](https://github.com/koreainvestment/open-trading-api/blob/main/examples_user/domestic_stock/domestic_stock_functions_ws.py)
and [websockets synchronous client](https://websockets.readthedocs.io/en/15.0.1/reference/sync/client.html).
The KIS example lists the legacy prefix; supported extensions above were checked
against actual incoming records rather than inferred from their values.
