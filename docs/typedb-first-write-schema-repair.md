# TypeDB first-write schema repair

## Reproduction

A real candidate request failed before its first ABox node transaction committed.
Its first query was 53,325 UTF-8 bytes and contained ten nodes. The transport
reported `Request generated error`; a previous attempt also reported an HTTP/2
excessive-load error. Neither message established the underlying cause.

The exact local request was replayed on an isolated TypeDB 3.12.0 server with a
copy of the deployed schema. Tests rolled back each variant to retain the same
empty data state:

- The first one, two and five nodes succeeded.
- Six through ten nodes failed, including the exact original query.
- The sixth node, a `ValuationAssumption`, failed individually at 7,424 bytes.
- The other tested individual nodes succeeded.
- A successful node still succeeded with comment padding beyond 100 KB.
- Removing only `ontology-pe-ratio` from the failing node made it executable.

These results identify a schema ownership mismatch, not a batch-size limit or
an exclusively long-lived connection failure. Diagnostic requests and source
payloads remain in private local state and are not committed.

## Cause and change

The runtime writer admitted `ontology-pe-ratio` for the strategy-thesis context,
but the deployed `ontology-context-strategy-thesis` did not own it. The attribute
already existed and was owned by other types, so checking global attribute/type
existence did not find the missing capability.

The resumable schema planner previously extended only the two core storage
roots; existing context and semantic types were skipped. Schema readiness could
therefore accept an incomplete schema merely because every type name existed,
even when a contract marker claimed it was current.

The planner now emits bounded additive `owns`/`plays` extensions for existing
types, considering inherited capabilities to avoid redundant subtype ownership.
Cold readiness, resumed candidate readiness and the persisted-marker fast path
all require an empty remaining schema plan before marking the schema ready.
Successful process-local readiness caching is retained. A failed schema commit
must not mark readiness.

For the captured deployed schema, the resulting plan is exactly one statement:

```typeql
define
ontology-context-strategy-thesis owns ontology-pe-ratio;
```

This adds physical storage ownership. It does not remove the value, change source
facts, rewrite the authored TBox/RuleBox, replace an active generation, or recreate
a database.

## Verification and limits

After applying that plan on the isolated server, all five original queries
succeeded, all 47 nodes committed, and readback returned 47 nodes including the
valuation node's promoted PER attribute. Unit regressions cover existing-type
ownership, inherited ownership, misleading current markers, resumed candidates,
failed commits and readiness.

Production acceptance additionally requires successful ABox/inference execution,
backlog recovery, and matching source identities through AI and notification
receipts when delivery is eligible. A short successful replay does not replace
the ongoing 72-hour and extended domestic-market continuity observation.
