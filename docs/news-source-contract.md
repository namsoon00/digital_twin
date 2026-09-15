# News Source Contract

## Scope

The first notification-audit remediation targets false article summaries and
event classification. Native account-policy comparisons were repaired in
`4bd2a29ae`; this change preserves that TypeDB V7 implementation and reruns its
contract tests. It does not implement AI follow-up registration or delivered
decision memory. Those remain separate, higher-level work.

## Source Boundary

The news module owns passage selection and summary validation. Existing
collection, enrichment jobs, evidence storage, graph projection and notification
admission remain the integration boundaries. No new worker or vendor is added.

- Remove publisher navigation even when it starts before 80 characters, and
  remove market countdown widgets before extracting article facts.
- Preserve the main headline event, complete numeric sentences, adjacent
  explanatory sentences and counterpoints in source order. Generic action words
  in a background quotation cannot remove the main event. A recommended-story
  tail cannot activate a second filter that discards the valid main paragraphs.
- Keep the input bounded to 5,000 source characters and 3,200 selected characters.
  Sentence boundaries retain month abbreviations and do not split numeric dates.
- Never recycle an existing generated summary as the original feed description.
  A copied headline alone does not count as a body passage.
- Classify vehicle sales and exports with the existing product category. Bare
  `offering` in consumer incentives is not a securities issuance event.

## Analysis And Validation

Analysis/prompt V17 supplies content-addressed source passages. The model returns
`sourceGrounding`: headline confirmation or refutation, primary-event passage
IDs, summary passage IDs and an explanation. Lexical primary-event candidates
are explicitly candidates, not a semantic proof or an investment decision.

The domain checks that cited passages exist in this source packet, that the
primary event has not been replaced by unrelated background, and that summary
numbers occur in the cited passages. This covers both summary fields and the
supporting facts. The stored quality report retains the fingerprint, cited text,
IDs and diagnostic reasons. A declared mismatch, unavailable source, missing
citations or ungrounded numbers cannot receive `summaryQualityState=ready`.

This validates source consistency and numerical provenance, not independent
truth or guaranteed semantic entailment. It does not prove that a publisher's
claim is correct or that an investment interpretation will be profitable.

## Bounded Repair

Failed external summaries become `source-review`. The existing enrichment queue
retries after the configured `newsAiAnalysisRetryMinutes` interval (30 minutes by
default), with the failed checks included in the next prompt. There is no inline
retry burst. Three failed analyses of the same source and analysis version become
`source-invalid`; a changed source or analysis release can start a new attempt.

Source validation can revoke an older completed summary in storage. Subsequent
collector refreshes cannot resurrect that summary, erase the repair counter, or
restore it from an old enrichment snapshot. A corrected validated analysis can
replace the rejection. Archive records and prior sent messages are not rewritten.

V17 reuses the durable work revision contract and existing batch/rate limits.
Historical articles are not force-sent to demonstrate a repair. New analyses
still pass the existing freshness, duplicate, source and materiality policies.

## Validation

Focused tests reproduce main export facts disappearing behind an old management
quote, publisher chrome replacing a school-support story, cash incentives being
classified as share issuance, and body-less headline reuse. Other tests cover
cross-article citations, background-only citations, numbers from uncited text,
repair cooldown/exhaustion/recovery, and persistence across collector replay.

The read-only local replay report is in
`data/reports/news-source-repair-validation-2026-09-15.json`. It is private and
excluded from git. Historical source identities are compared before replay;
changed current sources are not presented as the original sent source.

The 2026-09-15 replay inspected 24 historical news admissions against current
stored sources: 20 still had the exact admitted source revision, while four had
changed. One source contained publisher navigation rather than the article and
correctly supplied no verifiable passage. Restoring its actual body remains a
collection-recovery task, not a summary-generation task.

A live, source-pinned Sol/max replay of the previously mis-summarized export
article recovered its primary export growth and contrasting EV decline. A first
corrected answer omitted citations for auxiliary figures and failed validation;
repair feedback produced a ready summary with all 16 checked numerical values
grounded in cited passages. This replay used copies and did not write production
evidence or send historical news notifications. It is a source-consistency test,
not an independent verification of the publisher's statistics.

Migration baselines remain frozen. Intentional V17 domain/storage differences
are recorded in `closed_loop_semantic_changes.json` with the regression tests.
