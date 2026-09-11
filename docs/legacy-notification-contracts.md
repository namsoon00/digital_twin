# Legacy Notification Contracts

The six disagreements recorded in [Integrated Modularization](integrated-modularization.md)
were reproduced before this stabilization. This repair changes tests only;
notification policy, web adapters, TypeDB inference, investment actions, AI gates,
queue ownership, and transport behavior are unchanged.

## Corrected Contracts

All legacy names below belong to `legacy_python_service_regression.PythonServiceTests`
and have the `test_` prefix.

| Legacy test suffix | Contract now exercised |
| --- | --- |
| `notification_rule_payload_saves_similarity_bypass_conditions` | Save/reload a raw P/L worsening threshold and its disabled flag while preserving the other conditions and state cooldown. The removed `holding_score_delta` default stays absent. |
| `notification_policy_payload_defaults_to_managed_types` | Nine managed types include factual market observations, holdings snapshots, and portfolio activity observations. Internal evidence signals remain separate, and retired standalone investment types remain excluded. |
| `notification_template_test_send_queues_live_snapshot_message` | An unchanged heartbeat is durably suppressed as `status_noise`; a connection-change event queues using the saved template. Both requests have audit events, but only the admitted event has `notification.job_queued` lineage. Neither calls transport. |
| `investment_insight_test_send_bypasses_policy_and_sends_directly` | Renamed to `investment_insight_test_send_rejects_unqualified_inference_even_with_bypass`. A legacy native trace without a complete governed decision contract returns 422 with a blocked action envelope, no allowed actions, no queued job, and no transport. |
| `investment_insight_test_send_records_typedb_projection_before_type_check` | Projection precedes both actual monitor type checks, and the attached result is reused. Successful projection alone does not qualify an investment event: this incomplete fixture still returns 422 without dispatch. |
| `realtime_status_payload_includes_monitoring_and_queue_state` | Queue counters and nested health metadata have separate assertions. The compact cycle payload contains `snapshotCount`; the durable source still retains `alertCount` and `accountIds`. |

An unavailable or failed TypeDB projection is a distinct 409
`ontologyInferenceMissing` response. An available but unqualified inference is
not the same as a missing graph and must not manufacture an investment event.
The tests do not construct a fallback action, disable the AI gate, enable a
retired message type, or add a direct investment-send path. Native readback
fixtures are boundary test data, not proof of native TypeDB execution.

## Focused Suite

`python_service/tests/test_legacy_notification_contracts.py` contains 12 focused
tests with explicit I/O doubles and real monitor event selection, template
rendering, and delivery-rule evaluation. It does not import the monolithic
legacy test class. MySQL connections and notification transport are forbidden
inside the dispatch fixture.

Coverage includes database-unavailable catalog fallback, raw threshold boundary
and disabled-flag behavior, rejection of score-only repetition bypasses,
preservation of blocked graph judgement through similarity checks, operational
queue admission/suppression, dry-run queue/transport isolation, projection
ordering/reuse/failure, and status payload health metadata/degraded reads.
Investment rejection is checked in normal, `bypassPolicy`, `directSend`, and
`dryRun` modes without authorizing any of them to create an investment event.

The focused contracts are registered in `suite_manifest.json` for core and full
validation with an explicit size budget. No governance check is skipped;
complete core/full runs remain sequential because they share a fixture schema.

## Isolated Validation

Run the focused contracts directly:

```bash
env MYSQL_URL= ONTOLOGY_TYPEDB_ENABLED=0 PYTHONPATH=python_service:python_service/tests \
  python3 -m unittest -v test_legacy_notification_contracts
```

The legacy tests use the existing MySQL fixtures. Give this process its own
schema; never run them concurrently against `orbit_alpha_test`. Fixture cleanup
drops only registered test schemas on process exit.

```bash
schema="orbit_alpha_test_legacy_notifications_$(date +%s)_$$"
env MYSQL_URL= MYSQL_DATABASE="$schema" MYSQL_TEST_DATABASE="$schema" \
  MYSQL_TABLE_PARTITIONING=off ONTOLOGY_TYPEDB_ENABLED=0 \
  PYTHONPATH=python_service:python_service/tests python3 -m unittest -v \
  legacy_python_service_regression.PythonServiceTests.test_notification_rule_payload_saves_similarity_bypass_conditions \
  legacy_python_service_regression.PythonServiceTests.test_notification_policy_payload_defaults_to_managed_types \
  legacy_python_service_regression.PythonServiceTests.test_notification_template_test_send_queues_live_snapshot_message \
  legacy_python_service_regression.PythonServiceTests.test_investment_insight_test_send_rejects_unqualified_inference_even_with_bypass \
  legacy_python_service_regression.PythonServiceTests.test_investment_insight_test_send_records_typedb_projection_before_type_check \
  legacy_python_service_regression.PythonServiceTests.test_realtime_status_payload_includes_monitoring_and_queue_state \
  legacy_python_service_regression.PythonServiceTests.test_investment_insight_test_send_blocks_when_inference_missing \
  legacy_python_service_regression.PythonServiceTests.test_notification_template_test_send_rejects_demo_snapshot_by_default
```

These checks do not use the managed TypeDB server, real accounts, external
model calls, or Telegram. Live investment-delivery and full-suite verification
remain separate work.

## Verification Result

On the shared working tree based on `82a7e7d1f`, the final targeted run passed
20 tests in 58.916 seconds: all 12 focused contracts, the six corrected legacy
tests, and the existing missing-inference and demo-snapshot rejection tests.
Both changed Python files passed `py_compile`, all new test names were unique
across discovered curated modules, and scoped whitespace checks passed.

The final MySQL schema was
`orbit_alpha_test_legacy_notifications_291048af`. A read-only schema inventory
confirmed that this schema and the two earlier isolated run schemas were
absent after fixture cleanup; the focused-only sentinel schema was also absent.
This isolated validation did not modify production adapters or the shared
default schema. Integrated validation and deployment evidence is recorded in
[Backend Operational Stabilization](backend-operational-stabilization.md).
