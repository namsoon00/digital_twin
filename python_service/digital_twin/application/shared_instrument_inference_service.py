"""Publish verified TypeDB market inference for multi-account reuse."""

from __future__ import annotations

from copy import deepcopy
import json
from typing import Dict, Iterable, Mapping

from ..domain.shared_instrument_inference import (
    SHARED_EXECUTION_REUSE_VERSION,
    SHARED_INSTRUMENT_INFERENCE_VERSION,
    account_overlay_input_fingerprint,
    build_shared_instrument_inference,
    decision_input_fingerprint,
    market_shared_rule_ids,
    market_snapshot_input_fingerprint,
    market_source_revision_fingerprint,
)
from ..domain.portfolio import account_snapshot_from_monitor_state


def _text(value: object) -> str:
    return str(value or "").strip()


def _symbol(value: object) -> str:
    return _text(value).upper()


def _position_symbols(values: Iterable[object]) -> list:
    symbols = []
    for value in values or []:
        if hasattr(value, "is_cash") and value.is_cash():
            continue
        symbol = _symbol(getattr(value, "symbol", "") if not isinstance(value, Mapping) else value.get("symbol"))
        if symbol and symbol not in symbols:
            symbols.append(symbol)
    return symbols


def _state_collection_symbols(values: object) -> list:
    if not isinstance(values, Mapping):
        return _position_symbols(values or [])
    symbols = []
    for key, item in values.items():
        symbol = _symbol(
            item.get("symbol") if isinstance(item, Mapping) and item.get("symbol") else key
        )
        if not symbol or symbol in {"CASH", "KRW", "USD"}:
            continue
        if isinstance(item, Mapping) and item.get("isCash") is True:
            continue
        if symbol not in symbols:
            symbols.append(symbol)
    return symbols


class SharedInstrumentInferenceService:
    """Application boundary for shared publication, routing, and lineage.

    The service is deliberately downstream of successful TypeDB execution.
    Publication failure is visible in telemetry but cannot turn a verified
    investment result into a different action.
    """

    def __init__(
        self,
        store,
        deployment_id: str,
        release_fingerprint: str = "",
        rule_catalog_provider=None,
    ):
        self.store = store
        self.deployment_id = _text(deployment_id)
        self.release_fingerprint = _text(release_fingerprint)
        self.rule_catalog_provider = rule_catalog_provider
        self._subscription_index_reconciled = False

    def market_rule_catalog_ids(self) -> list:
        if not callable(self.rule_catalog_provider):
            return []
        try:
            rules = list(self.rule_catalog_provider() or [])
        except Exception:
            return []
        return market_shared_rule_ids(rules)

    def execution_reuse_proof(
        self,
        reasoning_context: Mapping[str, object],
        symbols: Iterable[str],
        snapshot: object = None,
    ) -> Dict[str, object]:
        """Read an exact-revision market proof for account-local rule selection."""

        selected = sorted({_symbol(value) for value in symbols or [] if _symbol(value)})
        reader = getattr(self.store, "latest", None)
        if not selected or not callable(reader):
            return {"status": "unavailable", "reuseEligible": False, "symbols": {}}
        expected_catalog = self.market_rule_catalog_ids()
        if not expected_catalog:
            return {
                "status": "unavailable-rule-ownership",
                "reuseEligible": False,
                "symbols": {},
            }
        symbol_proofs = {}
        for symbol in selected:
            expected_revision = market_source_revision_fingerprint(reasoning_context, symbol)
            expected_market_input = market_snapshot_input_fingerprint(snapshot, symbol)
            if not expected_revision or not expected_market_input:
                continue
            try:
                row = dict(reader(self.deployment_id, symbol) or {})
            except Exception:
                row = {}
            payload = dict(row.get("payload") or {}) if isinstance(row.get("payload"), Mapping) else {}
            catalog = sorted({_text(value) for value in payload.get("marketRuleCatalogIds") or [] if _text(value)})
            if (
                not row
                or _text(row.get("release_fingerprint")) != self.release_fingerprint
                or _text(payload.get("sourceRevisionFingerprint")) != expected_revision
                or _text(payload.get("marketInputFingerprint")) != expected_market_input
                or catalog != expected_catalog
                or _text(row.get("consistency_status")) != "equivalent"
            ):
                continue
            symbol_proofs[symbol] = {
                "snapshotId": _text(row.get("snapshot_id")),
                "semanticFingerprint": _text(row.get("semantic_fingerprint")),
                "sourceRevisionFingerprint": expected_revision,
                "marketInputFingerprint": expected_market_input,
                "sourceAsOf": _text(row.get("source_as_of")),
                "marketRuleCatalogIds": catalog,
                "matchedMarketRuleIds": sorted({
                    _text(value)
                    for value in payload.get("matchedMarketRuleIds") or []
                    if _text(value) in set(catalog)
                }),
                "relations": [
                    dict(value)
                    for value in payload.get("relations") or []
                    if isinstance(value, Mapping)
                ][:240],
                "traces": [
                    dict(value)
                    for value in payload.get("traces") or []
                    if isinstance(value, Mapping)
                ][:240],
            }
        ready = bool(symbol_proofs) and set(symbol_proofs) == set(selected)
        return {
            "contractVersion": SHARED_EXECUTION_REUSE_VERSION,
            "status": "ready" if ready else "revision-miss",
            "reuseEligible": ready,
            "deploymentId": self.deployment_id,
            "releaseFingerprint": self.release_fingerprint,
            "targetSymbols": selected,
            "marketRuleCatalogIds": expected_catalog if ready else [],
            "matchedMarketRuleIds": sorted({
                rule_id
                for proof in symbol_proofs.values()
                for rule_id in proof.get("matchedMarketRuleIds") or []
            }) if ready else [],
            "symbols": symbol_proofs,
        }

    @staticmethod
    def _deduplicate_rows(rows: Iterable[Mapping[str, object]]) -> list:
        selected = []
        seen = set()
        for row in rows or []:
            if not isinstance(row, Mapping):
                continue
            value = dict(row)
            identity = _text(
                value.get("id")
                or value.get("traceId")
                or value.get("inferenceTraceId")
                or value.get("relationId")
            )
            if not identity:
                identity = json.dumps(
                    value,
                    ensure_ascii=True,
                    sort_keys=True,
                    separators=(",", ":"),
                    default=str,
                )
            if identity in seen:
                continue
            seen.add(identity)
            selected.append(value)
        return selected[:480]

    def reusable_portfolio_projection(
        self,
        reasoning_context: Mapping[str, object],
        symbols: Iterable[str],
        snapshot: object,
    ) -> Dict[str, object]:
        """Return a private prior TypeDB result for the exact same inputs.

        This is not a Python inference fallback. The packet was produced by a
        completed TypeDB generation and is reusable only when the release,
        market revision, market input and complete projection source all
        match. Any mismatch falls through to a fresh TypeDB execution.
        """

        selected = sorted({_symbol(value) for value in symbols or [] if _symbol(value)})
        account_id = _text(getattr(snapshot, "account_id", ""))
        overlay_reader = getattr(self.store, "latest_overlay", None)
        if not selected or not account_id or not callable(overlay_reader):
            return {"status": "unavailable", "reuseEligible": False}
        proof = self.execution_reuse_proof(reasoning_context, selected, snapshot=snapshot)
        if not proof.get("reuseEligible"):
            return {
                "status": _text(proof.get("status")) or "market-proof-miss",
                "reuseEligible": False,
                "marketProof": proof,
            }
        expected_decision_input = decision_input_fingerprint(snapshot)
        if not expected_decision_input:
            return {
                "status": "decision-input-unavailable",
                "reuseEligible": False,
                "marketProof": proof,
            }
        overlays = {}
        replays = []
        for symbol in selected:
            try:
                overlay = dict(overlay_reader(self.deployment_id, account_id, symbol) or {})
            except Exception:
                overlay = {}
            payload = dict(overlay.get("payload") or {}) if isinstance(
                overlay.get("payload"), Mapping
            ) else {}
            proof_symbol = dict((proof.get("symbols") or {}).get(symbol) or {})
            shared_ids = sorted({
                _text(value)
                for value in overlay.get("sharedSnapshotIds") or []
                if _text(value)
            })
            expected_shared_id = _text(proof_symbol.get("snapshotId"))
            replay = dict(payload.get("projectionReplay") or {}) if isinstance(
                payload.get("projectionReplay"), Mapping
            ) else {}
            if (
                not overlay
                or _text(overlay.get("overlay_status") or overlay.get("status")) != "ready"
                or _text(payload.get("releaseFingerprint")) != self.release_fingerprint
                or _text(payload.get("decisionInputFingerprint")) != expected_decision_input
                or not expected_shared_id
                or expected_shared_id not in shared_ids
                or not replay
            ):
                return {
                    "status": "account-overlay-miss",
                    "reuseEligible": False,
                    "marketProof": proof,
                    "missingSymbol": symbol,
                }
            overlays[symbol] = overlay
            replays.append(replay)
        identities = {
            (
                _text((replay.get("inferenceBox") or {}).get("sourceAboxSnapshotId")),
                _text((replay.get("inferenceBox") or {}).get("inferenceGenerationId")),
            )
            for replay in replays
            if isinstance(replay.get("inferenceBox"), Mapping)
        }
        if len(identities) != 1 or not next(iter(identities))[0]:
            return {
                "status": "incoherent-account-overlay",
                "reuseEligible": False,
                "marketProof": proof,
            }
        projection = deepcopy(replays[0])
        inference = dict(projection.get("inferenceBox") or {})
        relation_rows = []
        trace_rows = []
        for replay in replays:
            replay_inference = dict(replay.get("inferenceBox") or {})
            relation_rows.extend(replay_inference.get("relations") or [])
            trace_rows.extend(replay_inference.get("traces") or [])
        inference.update({
            "status": "ok",
            "graphStore": "typedb",
            "source": "typedbInferenceBox",
            "nativeTypeDbReasoningUsed": True,
            "nativeTypeDbReasoningCompleted": True,
            "typedbNativeRuleEvaluationCompleted": True,
            "generationAligned": True,
            "relations": self._deduplicate_rows(relation_rows),
            "traces": self._deduplicate_rows(trace_rows),
            "reusedExactInference": True,
            "reuseContractVersion": SHARED_EXECUTION_REUSE_VERSION,
        })
        inference["relationCount"] = len(inference["relations"])
        inference["traceCount"] = len(inference["traces"])
        projection.update({
            "saved": False,
            "status": "reused-shared-account-inference",
            "graphStore": "typedb",
            "accountId": account_id,
            "inferenceBox": inference,
            "preservedActiveGeneration": True,
            "reason": "Exact shared market and private account TypeDB inference inputs were reused.",
        })
        context_by_symbol = {
            symbol: {
                "contractVersion": SHARED_INSTRUMENT_INFERENCE_VERSION,
                "executionMode": "shared-head-account-overlay-replay",
                "decisionAuthority": "typedb-shared-market-plus-portfolio-overlay",
                "overlayId": _text(overlays[symbol].get("overlay_id")),
                "overlayStatus": _text(
                    overlays[symbol].get("overlay_status") or overlays[symbol].get("status")
                ),
                "sharedSnapshotIds": list(overlays[symbol].get("sharedSnapshotIds") or []),
                "sharedSemanticFingerprints": [
                    _text(((proof.get("symbols") or {}).get(symbol) or {}).get("semanticFingerprint"))
                ],
                "sharedSourceAsOf": _text(
                    ((proof.get("symbols") or {}).get(symbol) or {}).get("sourceAsOf")
                ),
                "sharedMarketRuleIds": list(proof.get("matchedMarketRuleIds") or []),
                "accountRuleIds": list(
                    (overlays[symbol].get("payload") or {}).get("accountRuleIds") or []
                ),
                "reuseEligible": True,
                "inferenceGenerationId": _text(inference.get("inferenceGenerationId")),
                "sourceAboxSnapshotId": _text(inference.get("sourceAboxSnapshotId")),
            }
            for symbol in selected
        }
        self.attach_context(
            {account_id: projection},
            [snapshot],
            {account_id: context_by_symbol},
        )
        return {
            "status": "ready",
            "reuseEligible": True,
            "projection": projection,
            "marketProof": proof,
            "contextByAccount": {account_id: context_by_symbol},
        }

    def attach_shared_market_evidence(
        self,
        projection: Mapping[str, object],
        proof: Mapping[str, object],
    ) -> Dict[str, object]:
        """Compose exact TypeDB-authored shared evidence into a private result."""

        result = dict(projection or {})
        if not bool((proof or {}).get("reuseEligible")):
            return result
        inference = dict(result.get("inferenceBox") or {})
        if not inference:
            return result
        shared_relations = [
            dict(row)
            for symbol_proof in dict((proof or {}).get("symbols") or {}).values()
            for row in (symbol_proof or {}).get("relations") or []
            if isinstance(row, Mapping)
        ]
        shared_traces = [
            dict(row)
            for symbol_proof in dict((proof or {}).get("symbols") or {}).values()
            for row in (symbol_proof or {}).get("traces") or []
            if isinstance(row, Mapping)
        ]
        relations = self._deduplicate_rows(
            list(inference.get("relations") or []) + shared_relations
        )
        traces = self._deduplicate_rows(
            list(inference.get("traces") or []) + shared_traces
        )
        inference.update({
            "relations": relations,
            "traces": traces,
            "relationCount": len(relations),
            "traceCount": len(traces),
            "sharedMarketEvidenceReused": True,
            "sharedMarketEvidenceContractVersion": SHARED_EXECUTION_REUSE_VERSION,
            "sharedMarketRelationCount": len(shared_relations),
            "sharedMarketTraceCount": len(shared_traces),
            "sharedMarketSnapshotIds": sorted({
                _text(symbol_proof.get("snapshotId"))
                for symbol_proof in dict((proof or {}).get("symbols") or {}).values()
                if _text(symbol_proof.get("snapshotId"))
            }),
        })
        result["inferenceBox"] = inference
        return result

    def account_ids_for_symbols(self, symbols: Iterable[str]):
        reader = getattr(self.store, "account_ids_for_symbols", None)
        return list(reader(symbols) or []) if callable(reader) else []

    def ensure_subscription_index(self, accounts: Iterable[object], states: Mapping[str, object]) -> Dict[str, object]:
        """Repair the reverse index once per warm worker from durable current state."""

        if self._subscription_index_reconciled:
            return {"status": "already-reconciled"}
        reconcile = getattr(self.store, "reconcile_subscriptions", None)
        if not callable(reconcile):
            return {"status": "not-supported"}
        repaired = 0
        failures = []
        for account in accounts or []:
            account_id = _text(getattr(account, "account_id", ""))
            state = dict((states or {}).get(account_id) or {})
            positions = state.get("positions") or []
            watchlist = state.get("watchlist") or []
            holding_symbols = _state_collection_symbols(positions)
            watchlist_symbols = _state_collection_symbols(watchlist)
            watchlist_symbols.extend(list(getattr(account, "watchlist_symbols", []) or []))
            try:
                reconcile(
                    account_id,
                    holding_symbols,
                    watchlist_symbols,
                    source_revision=_text(state.get("generatedAt")),
                    source_as_of=_text(state.get("generatedAt")),
                )
                repaired += 1
            except Exception as error:  # noqa: BLE001 - fallback scan remains available.
                failures.append({"accountId": account_id, "reason": str(error)[:180]})
        self._subscription_index_reconciled = not failures
        return {
            "status": "partial" if failures else "ready",
            "repairedAccountCount": repaired,
            "failures": failures[:10],
        }

    def reconcile_snapshot_subscriptions(self, snapshots: Iterable[object]) -> Dict[str, object]:
        reconcile = getattr(self.store, "reconcile_subscriptions", None)
        if not callable(reconcile):
            return {"status": "not-supported", "accountCount": 0, "subscriptionCount": 0}
        account_count = 0
        subscription_count = 0
        failures = []
        for snapshot in snapshots or []:
            account_id = _text(getattr(snapshot, "account_id", ""))
            if not account_id:
                continue
            try:
                result = dict(reconcile(
                    account_id,
                    _position_symbols(getattr(snapshot, "positions", []) or []),
                    _position_symbols(getattr(snapshot, "watchlist", []) or []),
                    source_revision=_text(getattr(snapshot, "generated_at", "")),
                    source_as_of=_text(getattr(snapshot, "generated_at", "")),
                ) or {})
                account_count += 1
                subscription_count += int(result.get("activeCount") or 0)
            except Exception as error:  # noqa: BLE001 - routing index is repairable.
                failures.append({"accountId": account_id, "reason": str(error)[:180]})
        return {
            "status": "partial" if failures else "ready",
            "source": "account-snapshots",
            "accountCount": account_count,
            "subscriptionCount": subscription_count,
            "failures": failures[:10],
        }

    def reconcile_state_subscriptions(self, states: Mapping[str, object]) -> Dict[str, object]:
        """Refresh routing from the exact immutable states consumed by V1."""

        reconcile = getattr(self.store, "reconcile_subscriptions", None)
        if not callable(reconcile):
            return {"status": "not-supported", "accountCount": 0, "subscriptionCount": 0}
        account_count = 0
        subscription_count = 0
        failures = []
        for account_id, raw_state in dict(states or {}).items():
            state = dict(raw_state or {}) if isinstance(raw_state, Mapping) else {}
            if not _text(account_id):
                continue
            try:
                result = dict(reconcile(
                    _text(account_id),
                    _state_collection_symbols(state.get("positions") or []),
                    _state_collection_symbols(state.get("watchlist") or []),
                    source_revision=_text(state.get("generatedAt")),
                    source_as_of=_text(state.get("generatedAt")),
                ) or {})
                account_count += 1
                subscription_count += int(result.get("activeCount") or 0)
            except Exception as error:  # noqa: BLE001 - a later snapshot can repair the index.
                failures.append({"accountId": _text(account_id), "reason": str(error)[:180]})
        return {
            "status": "partial" if failures else "ready",
            "source": "immutable-reasoning-states",
            "accountCount": account_count,
            "subscriptionCount": subscription_count,
            "failures": failures[:10],
        }

    def publish_verified_results(
        self,
        projection_results: Mapping[str, object],
        symbols: Iterable[str],
        *,
        snapshots: Iterable[object] = (),
        states: Mapping[str, object] = None,
        observed_at: str = "",
    ) -> Dict[str, object]:
        snapshot_rows = list(snapshots or [])
        subscription_receipt = (
            self.reconcile_state_subscriptions(states)
            if states
            else self.reconcile_snapshot_subscriptions(snapshot_rows)
        )
        snapshots_by_account = {
            _text(getattr(snapshot, "account_id", "")): snapshot
            for snapshot in snapshot_rows
            if _text(getattr(snapshot, "account_id", ""))
        }
        for account_id, state in dict(states or {}).items():
            if _text(account_id) in snapshots_by_account:
                continue
            snapshot = account_snapshot_from_monitor_state(
                dict(state or {}) if isinstance(state, Mapping) else {}
            )
            if snapshot is not None:
                snapshots_by_account[_text(account_id)] = snapshot
        market_input_fingerprints = {
            account_id: {
                symbol: fingerprint
                for symbol in {_symbol(value) for value in symbols or [] if _symbol(value)}
                for fingerprint in [market_snapshot_input_fingerprint(snapshot, symbol)]
                if fingerprint
            }
            for account_id, snapshot in snapshots_by_account.items()
        }
        account_input_fingerprints = {
            account_id: {
                symbol: fingerprint
                for symbol in {_symbol(value) for value in symbols or [] if _symbol(value)}
                for fingerprint in [account_overlay_input_fingerprint(snapshot, symbol)]
                if fingerprint
            }
            for account_id, snapshot in snapshots_by_account.items()
        }
        decision_input_fingerprints = {
            account_id: fingerprint
            for account_id, snapshot in snapshots_by_account.items()
            for fingerprint in [decision_input_fingerprint(snapshot)]
            if fingerprint
        }
        report = build_shared_instrument_inference(
            projection_results,
            symbols,
            deployment_id=self.deployment_id,
            release_fingerprint=self.release_fingerprint,
            observed_at=observed_at,
            market_rule_ids=self.market_rule_catalog_ids(),
            market_input_fingerprints=market_input_fingerprints,
            account_input_fingerprints=account_input_fingerprints,
            decision_input_fingerprints=decision_input_fingerprints,
        )
        persistence = dict(self.store.publish(report) or {})
        overlays = list(report.get("overlays") or [])
        snapshots_by_id = {
            item.snapshot_id: item
            for item in report.get("snapshots") or []
        }
        context_by_account: Dict[str, Dict[str, object]] = {}
        for overlay in overlays:
            shared = [
                snapshots_by_id[snapshot_id]
                for snapshot_id in overlay.shared_snapshot_ids
                if snapshot_id in snapshots_by_id
            ]
            context_by_account.setdefault(overlay.account_id, {})[overlay.symbol] = {
                "contractVersion": overlay.payload.get("contractVersion"),
                "executionMode": "shared-head-account-overlay-refresh",
                "decisionAuthority": "typedb-shared-market-plus-portfolio-overlay",
                "overlayId": overlay.overlay_id,
                "overlayStatus": overlay.status,
                "sharedSnapshotIds": list(overlay.shared_snapshot_ids),
                "sharedSemanticFingerprints": [item.semantic_fingerprint for item in shared],
                "sharedSourceAsOf": max([item.source_as_of for item in shared] or [""]),
                "sharedMarketRuleIds": sorted({
                    rule_id
                    for item in shared
                    for rule_id in item.payload.get("ruleIds") or []
                }),
                "accountRuleIds": list(overlay.payload.get("accountRuleIds") or []),
                "reuseEligible": bool(overlay.shared_snapshot_ids and overlay.status == "ready"),
                "inferenceGenerationId": overlay.inference_generation_id,
                "sourceAboxSnapshotId": overlay.source_abox_snapshot_id,
            }
        self.attach_context(projection_results, snapshot_rows, context_by_account)
        return {
            **persistence,
            "contractVersion": report.get("contractVersion"),
            "subscriptionIndex": subscription_receipt,
            "contextByAccount": context_by_account,
            "conflictCount": len([
                value for value in (report.get("consistencyBySymbol") or {}).values()
                if value == "conflict"
            ]),
        }

    @staticmethod
    def attach_context(projection_results, snapshots, context_by_account) -> None:
        if isinstance(projection_results, dict):
            for account_id, raw_projection in projection_results.items():
                if not isinstance(raw_projection, dict):
                    continue
                account_context = dict(context_by_account.get(_text(account_id)) or {})
                summary = {
                    "contractVersion": SHARED_INSTRUMENT_INFERENCE_VERSION,
                    "executionMode": "shared-head-account-overlay-refresh",
                    "decisionAuthority": "typedb-shared-market-plus-portfolio-overlay",
                    "symbols": account_context,
                    "sharedSymbolCount": len([
                        value for value in account_context.values() if value.get("reuseEligible")
                    ]),
                }
                raw_projection["sharedInstrumentInference"] = summary
                inference = raw_projection.get("inferenceBox")
                if isinstance(inference, dict):
                    inference["sharedInstrumentInference"] = summary
        for snapshot in snapshots or []:
            account_id = _text(getattr(snapshot, "account_id", ""))
            metadata = getattr(snapshot, "metadata", None)
            if not isinstance(metadata, dict):
                continue
            ontology = metadata.get("ontology") if isinstance(metadata.get("ontology"), dict) else {}
            projection = ontology.get("projection") if isinstance(ontology.get("projection"), dict) else {}
            source_projection = (
                projection_results.get(account_id)
                if isinstance(projection_results, Mapping)
                else None
            )
            if isinstance(source_projection, dict):
                ontology["projection"] = source_projection
                metadata["ontology"] = ontology
