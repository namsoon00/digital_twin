# Insight Evidence Flow Remediation

This work repairs evidence meaning across collection, TypeDB, AI, delivery and
follow-up. It does not grant actions outside the TypeDB envelope, change account
cooldown thresholds, or treat deterministic matching as predictive validation.

## Implementation Order

1. Regression capture: synthetic fixtures cover captured September defects;
   private account captures are replayed locally, never committed.
2. Delivery causes: retain typed before/after values and clocks. An observation
   retry is not a new relation. BTC-sensitive holdings do not inherit an ETH
   trigger without a demonstrated exposure. Empty trigger facts cannot alone
   authorize a customer observation.
3. AI input: protect selected numeric facts, transition details, model features,
   financial packets and exact source windows during compression. Remove
   duplicate descriptions first; reject an impossible budget instead of
   silently discarding required evidence. The normal prompt limit stays 48 KiB.
   Keep these source-window fields in the native-match proof allowlist as well
   as on the model node. A graph-grounding regression verifies that boundary.
4. Meaning validation: separate condition coverage from empirical samples;
   preserve nested model measurements and hypothesis-specific evidence roles.
   A rule name or strength label is not an observed event reaction. Unsupported
   event-absorption and recurring-profit assertions are rejected, not replaced
   by a fabricated conclusion. TypeDB action authority is unchanged.
5. Financial and event context: keep reporting period, comparison basis,
   source and known publication timing. Missing earnings-quality adjustments
   remain unknown. Keep verified article-analysis admission metadata; prioritize
   graph-linked sources and official documents within the three-source budget.
   Record budget omissions separately from ineligible evidence.
6. Follow-up and receipt: require intraday baseline/outcome observations, with
   baseline availability no later than the event. Retry missing/daily baselines
   from original historical windows, never the current quote. Distinguish
   measured horizons from expired horizons, including active legacy rows.
7. Web: show accepted/rejected sentences with actual cited values, source clocks
   and rejection reasons. Show the actual sent body from its delivery receipt,
   not a regenerated template. Desktop/mobile browser checks cover both views.

## Delivery Concurrency and Retention

- AI analysis retains its frozen delivery baseline. Immediately before sending,
  refresh the last successful account/subject delivery and keep both baselines.
- A connection-scoped MySQL named lock covers receipt comparison and transport
  completion. Busy locks or unavailable receipt history cause retry, not a send.
- The composition layer injects the decisions-owned comparison function into
  notification delivery; the two application modules do not import each other.
- Suppress an identical actionless opinion already sent during analysis, but
  preserve authorization for a verified newer observed source and do not use
  unchanged opinion direction to suppress a changed executable action.
- Store rendered message bytes/hash and the comparison baseline in delivery
  attempt metadata. Bodies above 64 KiB retain a hash and explicit omission state.
- `notificationRenderedMessageRetentionDays` defaults to 7, bounded to 1..30.
  Maintenance removes the body only, in bounded batches; delivery receipts and
  hashes remain available for continuity. This does not extend raw ABox retention.

## Verification

Completed targeted checks:

- The final focused run passed 95 tests with no skips in `orbit_alpha_test`.
- It includes all five storage tests,
  including actual SQL granularity/availability selection and competing named locks.
- Frozen input replay: 35/35 inputs, 26/26 transition packets and 35/35 BTC fact
  packets preserved inside the final rendered prompt limit. This is input
  preservation verification, not regeneration of 35 AI answers.
- Browser fixtures verify bundle/module modes, desktop/mobile views, navigation,
  rejected-claim explanations, receipt text and horizontal overflow.
- `npm test` passed: 1,532 curated Python tests, 44 frontend tests and the web
  smoke check. The final proof-allowlist adjustment additionally passed the
  graph-grounding regression and reviewed-definition fingerprint check; the
  legacy completion-label adjustment passed the information UI regressions.
- Managed restart and notification handoff are recorded at final handoff;
  passing tests alone are not deployment or live-delivery evidence.

Reproduce no-send replay (private capture path supplied by the operator):

```bash
python3 scripts/verify-insight-evidence-flow.py /path/to/captured-audits.json.gz
```

The replay reports zero notification sends and DB writes. It calls neither a
new AI model nor native TypeDB, and does not mutate frozen production records.

## Limits and Operating Acceptance

- Forty-three historical model assertions lacked captured feature summaries.
  They are not recoverable from the brief alone. New measurements retain their
  source version; old missing windows remain explicitly unresolved.
- Normalized EPS, subsidy/FX adjustments and event-specific benchmark returns
  require actual source documents/observations. This release does not invent
  them or claim to have completed an automated financial analyst.
- Claim validation checks evidence contracts and selected high-risk language;
  it is not proof of every natural-language causal claim or profitable advice.
- Numerical follow-up conditions use the existing registered observation path.
  A next-quarter filing or other unsupported external condition must be described
  as a required check, not falsely presented as automatically tracked.
- After deployment, inspect a newly completed native generation, AI response,
  publication and delivery receipt together. A running worker alone is not this
  end-to-end proof. New independent outcomes are needed for predictive evaluation.

Private account snapshots remain outside git. Historical inputs missing their
source versions are not silently reconstructed from current observations.
Outcome quality requires later independent observations and cannot be claimed
from successful execution or notification receipts alone.
