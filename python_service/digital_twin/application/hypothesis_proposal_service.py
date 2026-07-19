from typing import Dict, Iterable, List

from ..domain.events import hypothesis_proposed_event, hypothesis_reviewed_event
from ..domain.investment_brain import NovelHypothesisProposal, stable_id


class HypothesisProposalService:
    def __init__(self, store, advisor=None, event_publisher=None, settings: Dict[str, object] = None):
        self.store = store
        self.advisor = advisor
        self.event_publisher = event_publisher
        self.settings = dict(settings or {})

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
        return {
            "status": "review-required" if rows else "no-valid-proposal",
            "proposalCount": len(rows),
            "proposals": rows,
            "governance": "not-usable-for-investment-judgment-until-rulebox-promotion",
        }

    def known_evidence_ids(self, context: Dict[str, object]) -> set:
        ids = set()
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
