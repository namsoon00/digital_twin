"""Publish verified TypeDB market inference for multi-account reuse."""

from __future__ import annotations

from typing import Dict, Iterable, Mapping

from ..domain.shared_instrument_inference import (
    build_shared_instrument_inference,
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
            }
        ready = bool(symbol_proofs) and set(symbol_proofs) == set(selected)
        return {
            "contractVersion": "shared-instrument-execution-reuse-v1",
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
        report = build_shared_instrument_inference(
            projection_results,
            symbols,
            deployment_id=self.deployment_id,
            release_fingerprint=self.release_fingerprint,
            observed_at=observed_at,
            market_rule_ids=self.market_rule_catalog_ids(),
            market_input_fingerprints=market_input_fingerprints,
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
                "executionMode": "dual-run-published",
                "decisionAuthority": "none",
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
                    "contractVersion": "shared-instrument-inference-v1",
                    "executionMode": "dual-run-published",
                    "decisionAuthority": "none",
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
