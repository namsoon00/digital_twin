import time
import uuid
from typing import Dict, List

from ..domain.events import hypothesis_proposed_event, hypothesis_reviewed_event
from ..domain.investment_brain import NovelHypothesisProposal, stable_id


class HypothesisProposalService:
    def __init__(self, store, advisor=None, event_publisher=None, settings: Dict[str, object] = None, development_service=None):
        self.store = store
        self.advisor = advisor
        self.event_publisher = event_publisher
        self.settings = dict(settings or {})
        self.development_service = development_service

    def propose(
        self,
        account_id: str,
        symbol: str,
        question: Dict[str, object],
        hypothesis_set: Dict[str, object],
        research_run: Dict[str, object] = None,
        relation_context: Dict[str, object] = None,
    ) -> Dict[str, object]:
        if not self.advisor or not hasattr(self.advisor, "propose"):
            return {"status": "disabled", "proposalCount": 0, "proposals": []}
        context = {
            "accountId": str(account_id or ""),
            "symbol": str(symbol or "").upper(),
            "question": dict(question or {}),
            "hypothesisSet": dict(hypothesis_set or {}),
            "researchRun": dict(research_run or {}),
            "inferenceGenerationId": str((relation_context or {}).get("inferenceGenerationId") or ""),
            "inferenceTraces": list(((relation_context or {}).get("graphStoreInference") or {}).get("traces") or [])[:20],
            "inferenceRelations": list(((relation_context or {}).get("graphStoreInference") or {}).get("relations") or [])[:40],
        }
        known_evidence_ids = self.known_evidence_ids(context)
        existing_claims = {
            str(item.get("claim") or "").strip().casefold()
            for item in (hypothesis_set or {}).get("hypotheses") or []
            if isinstance(item, dict)
        }
        if self.store and hasattr(self.store, "list_hypothesis_proposals"):
            for item in self.store.list_hypothesis_proposals("", str(symbol or "").upper(), 100) or []:
                if isinstance(item, dict) and str(item.get("claim") or "").strip():
                    existing_claims.add(str(item.get("claim") or "").strip().casefold())
        rows = []
        development_rows = []
        for item in self.advisor.propose(context) or []:
            if not isinstance(item, dict):
                continue
            claim = " ".join(str(item.get("claim") or "").split())
            evidence_ids = [
                str(value or "").strip()
                for value in item.get("supportingEvidenceIds") or []
                if str(value or "").strip() in known_evidence_ids
            ]
            if not claim or claim.casefold() in existing_claims or not evidence_ids:
                continue
            proposal = NovelHypothesisProposal(
                proposal_id=stable_id("novel-hypothesis-proposal", account_id, symbol, claim),
                account_id=str(account_id or ""),
                symbol=str(symbol or "").upper(),
                title=str(item.get("title") or claim)[:255],
                claim=claim,
                causal_path=[str(value or "").strip() for value in item.get("causalPath") or [] if str(value or "").strip()][:12],
                supporting_evidence_ids=evidence_ids[:20],
                counter_evidence_ids=[str(value or "").strip() for value in item.get("counterEvidenceIds") or [] if str(value or "").strip() in known_evidence_ids][:20],
                required_evidence_types=[str(value or "").strip() for value in item.get("requiredEvidenceTypes") or [] if str(value or "").strip()][:12],
                invalidation_conditions=[str(value or "").strip() for value in item.get("invalidationConditions") or [] if str(value or "").strip()][:8],
                source_question_id=str((question or {}).get("questionId") or ""),
                source=str(item.get("source") or "ai-research-planner"),
            )
            if self.store and hasattr(self.store, "save_hypothesis_proposal"):
                self.store.save_hypothesis_proposal(proposal)
            rows.append(proposal.to_dict())
            self.publish(hypothesis_proposed_event(proposal.to_dict()))
            if self.development_service and hasattr(self.development_service, "ingest_proposal"):
                try:
                    development_rows.append(self.development_service.ingest_proposal(
                        proposal.to_dict(),
                        str(context.get("inferenceGenerationId") or ""),
                    ))
                except Exception as error:  # noqa: BLE001 - the persisted proposal remains available for retry.
                    development_rows.append({
                        "status": "error",
                        "proposalId": proposal.proposal_id,
                        "reason": str(error)[:500],
                    })
        return {
            "status": "review-required" if rows else "no-valid-proposal",
            "proposalCount": len(rows),
            "proposals": rows,
            "governance": "not-usable-for-investment-judgment-until-rulebox-promotion",
            "hypothesisDevelopment": development_rows,
        }

    def known_evidence_ids(self, context: Dict[str, object]) -> set:
        ids = set()
        hypothesis_set = context.get("hypothesisSet") if isinstance(context.get("hypothesisSet"), dict) else {}
        for hypothesis in hypothesis_set.get("hypotheses") or []:
            if not isinstance(hypothesis, dict):
                continue
            for key in ["supportingEvidenceIds", "counterEvidenceIds", "causalPathIds", "causalTraceIds"]:
                ids.update(
                    str(item or "").strip()
                    for item in hypothesis.get(key) or []
                    if str(item or "").strip()
                )
        for item in context.get("inferenceRelations") or []:
            if not isinstance(item, dict):
                continue
            ids.add(str(item.get("id") or stable_id("relation-evidence", item.get("source"), item.get("type"), item.get("target"), item.get("ruleId"))))
        for item in context.get("inferenceTraces") or []:
            if isinstance(item, dict) and item.get("id"):
                ids.add(str(item.get("id")))
        for item in (context.get("researchRun") or {}).get("verifiedClaims") or []:
            if not isinstance(item, dict):
                continue
            for key in ["claimId", "evidenceId"]:
                if item.get(key):
                    ids.add(str(item.get(key)))
        return {item for item in ids if item}

    def list(self, status: str = "", symbol: str = "", limit: int = 50) -> Dict[str, object]:
        rows = self.store.list_hypothesis_proposals(status, symbol, limit) if self.store else []
        return {
            "count": len(rows),
            "proposals": rows,
            "governance": "review-does-not-deploy-rulebox-automatically",
        }

    def review(self, proposal_id: str, status: str, note: str = "") -> Dict[str, object]:
        payload = self.store.review_hypothesis_proposal(proposal_id, status, note)
        self.publish(hypothesis_reviewed_event(payload))
        return {
            "proposal": payload,
            "governance": "approved-means-validated-for-rule-design-not-deployed",
        }

    def publish(self, event) -> None:
        if not self.event_publisher:
            return
        if hasattr(self.event_publisher, "publish"):
            self.event_publisher.publish(event)
        else:
            self.event_publisher.handle(event)


class HypothesisProposalQueueRunner:
    """Run bounded AI proposal work outside realtime reasoning latency."""

    def __init__(self, store, proposal_service: HypothesisProposalService, worker_id: str = ""):
        self.store = store
        self.proposal_service = proposal_service
        self.worker_id = str(worker_id or "hypothesis-proposal-" + uuid.uuid4().hex[:12])
        self.last_results: List[Dict[str, object]] = []
        self.last_reconciled_at = 0.0

    def run_once(self, limit: int = 1) -> Dict[str, object]:
        self.last_results = []
        claim = getattr(self.store, "claim_hypothesis_proposal_requests", None)
        requests = claim(self.worker_id, limit=max(1, min(3, int(limit or 1)))) if callable(claim) else []
        for request in requests:
            request_id = str(request.get("requestId") or "")
            try:
                result = self.proposal_service.propose(
                    str(request.get("accountId") or ""),
                    str(request.get("symbol") or ""),
                    dict(request.get("question") or {}),
                    dict(request.get("hypothesisSet") or {}),
                    dict(request.get("researchRun") or {}),
                    dict(request.get("relationContext") or {}),
                )
                self.store.complete_hypothesis_proposal_request(request_id, result)
                self.last_results.append({"requestId": request_id, **dict(result or {})})
            except Exception as error:  # noqa: BLE001 - one AI proposal cannot stop research work.
                self.store.fail_hypothesis_proposal_request(request_id, str(error))
                self.last_results.append({
                    "requestId": request_id,
                    "status": "error",
                    "reason": str(error)[:180],
                })
        reconciliation = self.reconcile_backlog_if_due()
        return {
            "status": "ok",
            "processedCount": len(requests),
            "results": self.last_results,
            "developmentBacklog": reconciliation,
            "queue": self.status(),
        }

    def reconcile_backlog_if_due(self, interval_seconds: int = 3600) -> Dict[str, object]:
        now = time.monotonic()
        if self.last_reconciled_at and now - self.last_reconciled_at < max(60, interval_seconds):
            return {"status": "not-due", "processedCount": 0}
        self.last_reconciled_at = now
        development = getattr(self.proposal_service, "development_service", None)
        reconcile = getattr(development, "reconcile_proposal_backlog", None)
        if not callable(reconcile):
            return {"status": "unavailable", "processedCount": 0}
        try:
            return dict(reconcile(limit=3) or {})
        except Exception as error:  # noqa: BLE001 - backlog recovery is not realtime-critical.
            return {"status": "error", "processedCount": 0, "reason": str(error)[:180]}

    def status(self) -> Dict[str, object]:
        summary = getattr(self.store, "hypothesis_proposal_request_summary", None)
        return dict(summary() or {}) if callable(summary) else {"status": "unavailable"}
