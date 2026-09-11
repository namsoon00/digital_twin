# Web Router Separation

## Boundaries

`python_service/digital_twin/infrastructure/web_server.py` now contains only
server binding, port fallback, and serving. The CLI entry point and
`DigitalTwinHandler` remain available there. Payload helpers are **not**
re-exported from the bootstrap.

The `infrastructure/web/` package owns:

- `handler.py`: HTTP methods, request identity, access-before-dispatch, and the
  existing 400/500/502 error envelopes.
- `access.py`: token exchange, session cookies, local-versus-forwarded access,
  and writable checks, using the existing `share_access` implementation.
- `responses.py`, `static.py`, `websocket.py`: body limits, JSON/gzip/CORS,
  static cache/ETag/path handling, and the existing WebSocket protocol.
- `router.py`: explicit ordered dispatch. Only `NOT_HANDLED` advances to the
  next segment; a response returning `None` has already handled the request.
- `composition.py`: 16 business routers in 35 explicitly ordered segments.
  Repeated segments are intentional: they preserve the original interleaving
  and method-specific fallthrough, including named versus identifier routes.
- `adapters/`: relocated web payload functions, with their original cache
  namespaces, TTLs, locks, and service/read-model calls.
- `cache.py`, `common.py`, `events.py`, `telemetry.py`: shared freshness/query
  helpers, the existing realtime bridge/hub, and process-wide instrumentation.

Business routers are frozen dataclasses with named callback dependencies.
Tests or alternate servers can use `dataclasses.replace(WebRoutes(), ...)`,
`build_api_router(routes)`, and `make_handler(router, access_policy)` without
patching a module namespace. Defaults invoke existing public services and
runtime builders. This does not create new workers or change async boundaries.

URLs, methods, validation branches, status codes, shared-owner restrictions on
`/api/chat` and investment questions, and cached payload semantics are unchanged.
Body parsing is a relocation, not a new validation policy. Existing parser
behavior for unusual lengths or non-object JSON has not been broadened or
hardened as part of this structural change.

## File Inventory

All names below have a `.py` suffix and are relative to
`python_service/digital_twin/infrastructure/web/`:

```text
__init__, access, cache, common, composition, events, handler, responses,
router, static, telemetry, websocket

routes/
  __init__, accounts, calendar, configuration, decisions, instruments,
  market_data, model_registry, notifications, operations, outcomes,
  portfolio, read_models, reasoning, research_evidence, share, workspace

adapters/
  __init__, accounts, brain, calendar, capital_flow, cases, configuration,
  console, external_data, flow_lens, instruments, investment_model,
  market_proxy, notification_configuration, notification_inbox,
  notification_presentation, notification_storage, notification_testing,
  ontology_access, ontology_audit, ontology_catalog, ontology_diagnostics,
  ontology_governance, ontology_lab, ontology_ledger, operations, platforms,
  portfolio, research_evidence, share, strategy_proposals, workspace
```

Additional files in this slice:

- Modified `python_service/digital_twin/infrastructure/web_server.py`.
- New `python_service/tests/test_web_router_boundaries.py`.
- New `python_service/tests/test_web_router_payloads.py`.
- This focused handoff document.

## Test Integration

Legacy tests must import the helper from its new owner and patch the name in
the module that **uses** the dependency. Patching `web.common` alone does not
replace an adapter's explicitly imported `operational_read_settings` binding.
For HTTP tests, prefer injecting the business router callback instead.

Common old `web_server` targets map as follows. Module names are relative to
`digital_twin.infrastructure.web`:

| Existing target | New owner |
| --- | --- |
| `cached_api_payload` | `cache` |
| `operational_read_settings`, parsing/time helpers | `common` |
| `realtime_status_payload`, `RealtimeEventBridge`, hub/frame helpers | `events` |
| `settings_status_payload`, `save_settings_payload` | `adapters.configuration` |
| `app_store`, `read_store`, `snapshot_payload`, chat/memory/item helpers | `adapters.workspace` |
| `notification_queue_store`, template/rule store factories | `adapters.notification_storage` |
| `notification_jobs_payload`, details, receipts, replay | `adapters.notification_inbox` |
| `notification_job_public_payload`, `notification_job_list_payload`, cursors | `adapters.notification_presentation` |
| `list_templates_payload`, rules, schedules, template/rule edits | `adapters.notification_configuration` |
| `notification_template_test_payload`, `build_snapshot`, projection test helpers | `adapters.notification_testing` |
| `console_*` except operations health, console cache instances | `adapters.console` |
| `console_operations_health_api_payload` | `adapters.operations` |
| `investment_brain_*`, hypothesis template/policy caches | `adapters.brain` |
| `ontology_catalog_api_payload`, RuleBox/catalog read caches | `adapters.ontology_catalog` |
| `ontology_audit_sync_rows`, other audit shaping | `adapters.ontology_audit` |
| `run_ontology_rulebox_payload`, language/rule mutations | `adapters.ontology_governance` |
| `ontology_reasoning_status_payload`, engine/time-series status | `adapters.platforms` |
| `flow_lens_*`, persisted snapshot helpers | `adapters.flow_lens` |

Known integration consumers are `legacy_python_service_regression.py`,
`test_web_read_path_performance.py`, `test_web_server_port_fallback.py`,
`test_ontology_worlds.py`, and `test_notification_presentation_boundary.py`.
Tests patching `cached_api_payload` across both console and brain helpers need
to patch both importing modules, or split those assertions by owner.

`scripts/smoke-test.js` also searches the old monolith's source. Its static
asset assertions should inspect `web/static.py`; share/version assertions
should inspect `web/routes/share.py` and `web/routes/operations.py`; calendar
read assertions should inspect `web/adapters/calendar.py`; instrument
timeline/valuation assertions should inspect `web/routes/read_models.py`;
watchlist/refresh assertions should inspect `web/routes/instruments.py` and
`web/adapters/instruments.py`. The async-refresh call now reads
`request.send_payload(202, self.request_symbol_universe_refresh(...))`.
Do not add dead source strings or a re-export facade merely to satisfy these
source-shape checks.

Suggested parent-owned `suite_manifest.json` additions:

```json
{"file": "test_web_router_boundaries.py", "tier": "system", "core": true},
{"file": "test_web_router_payloads.py", "tier": "contract", "core": true}
```

## Validation

```bash
PYTHONPATH=python_service python3 -m unittest discover -s python_service/tests -p 'test_web_router_*.py' -v
python3 -m compileall -q python_service/digital_twin/infrastructure/web_server.py python_service/digital_twin/infrastructure/web python_service/tests/test_web_router_boundaries.py python_service/tests/test_web_router_payloads.py
```

The 37 tests use synthetic callbacks/settings and test-owned loopback servers;
they do not invoke external APIs, account databases, local Codex, or managed
workers. They cover precedence and fallthrough, unauthorized reads/writes,
token/session/role handling, shared local-AI denial, malformed JSON and the
exact body limit, error envelopes, payload/redaction parity, immutable
snapshots, freshness downgrades, cache-first reads, queue-only replay, gzip,
CORS, HEAD, ETags, and static traversal.

A one-time AST comparison against the dirty workspace source captured before
extraction verified 283 relocated definitions/constants, all 35 routing
segments reconstructed into the complete original `handle_api` AST, and 21
access/transport/dispatch methods. No route order, parameter, method, status,
cache setting, or original function body changed in that comparison; only
imports, dependency qualification, and structural placement changed.

Full-suite testing, existing-test adaptation, manifest registration, commit,
push, managed-runtime restart, and handoff notification remain parent-owned.
