from dataclasses import fields
from typing import Dict, List, Optional, Tuple

from ..domain.investment_brain import (
    InvestmentQuestion,
    decision_episode_from_context,
    hypothesis_set_from_relation_context,
)
from ..domain.message_types import INVESTMENT_INSIGHT
from ..domain.ontology_inference_context import relation_context_from_inferencebox
from ..domain.portfolio import PortfolioSummary, Position


class InvestmentBrainService:
    def __init__(
        self,
        monitor_store,
        ontology_repository,
        reviewer,
        decision_episode_store,
        settings: Dict[str, object] = None,
    ):
        self.monitor_store = monitor_store
        self.ontology_repository = ontology_repository
        self.reviewer = reviewer
        self.decision_episode_store = decision_episode_store
        self.settings = dict(settings or {})

    def ask(self, message: str, account_id: str = "", symbol: str = "") -> Dict[str, object]:
        message = " ".join(str(message or "").split())
        if not message:
            raise ValueError("투자 질문을 입력하세요.")
        state, position, source = self.resolve_subject(message, account_id, symbol)
        if not state or not position:
            return {
                "status": "blocked",
                "engine": "ontology-investment-brain",
                "reply": "최신 계좌 스냅샷에서 질문 대상을 찾지 못했습니다. 회사명 또는 종목을 질문에 포함해 주세요.",
                "missing": ["account-snapshot", "investment-subject"],
            }
        resolved_account_id = str(state.get("accountId") or account_id or "")
        question = InvestmentQuestion.create(
            message,
            subject_symbol=position.symbol,
            subject_name=position.name,
            account_id=resolved_account_id,
        )
        relation_context = self.load_relation_context(state, position, source)
        if not relation_context:
            return {
                "status": "blocked",
                "engine": "ontology-investment-brain",
                "question": question.to_dict(),
                "reply": "TypeDB InferenceBox에서 이 종목과 연결된 추론 관계를 찾지 못해 투자 답변을 만들지 않았습니다.",
                "missing": ["typedb-inference-relations"],
            }
        brain = hypothesis_set_from_relation_context(relation_context, question)
        relation_context.update({
            "investmentBrain": brain,
            "hypothesisSet": brain.get("hypothesisSet") or {},
            "researchPlan": brain.get("researchPlan") or {},
            "selfQuestions": brain.get("selfQuestions") or [],
            "epistemicState": brain.get("epistemicState") or {},
        })
        context = {
            "messageType": INVESTMENT_INSIGHT,
            "accountId": resolved_account_id,
            "accountLabel": state.get("accountLabel") or "",
            "displayTarget": position.name or position.symbol,
            "title": position.name or position.symbol,
            "referenceDate": relation_context.get("inferenceGenerationAt") or state.get("generatedAt") or "",
            "rawLines": ["사용자 투자 질문: " + message],
            "criteria": ["TypeDB 경쟁 가설 비교", "반대 근거와 데이터 공백 검증"],
            "ontologyRelationContext": relation_context,
            "investmentBrainQuestion": question.to_dict(),
        }
        response = self.reviewer.review(context)
        response_payload = response.to_dict()
        episode = decision_episode_from_context(context, response_payload, job_id=question.question_id)
        if episode and self.decision_episode_store:
            facts = dict(relation_context.get("facts") or {})
            facts["inferenceGenerationId"] = relation_context.get("inferenceGenerationId") or ""
            self.decision_episode_store.record_observation(
                resolved_account_id,
                position.symbol,
                facts,
                str(relation_context.get("inferenceGenerationAt") or ""),
            )
            self.decision_episode_store.save(episode)
        return {
            "status": "answered",
            "engine": "ontology-investment-brain",
            "reply": answer_text(response_payload),
            "question": question.to_dict(),
            "answer": response_payload,
            "hypothesisSet": brain.get("hypothesisSet") or {},
            "researchPlan": brain.get("researchPlan") or {},
            "decisionEpisodeId": episode.episode_id if episode else "",
            "inferenceGenerationId": relation_context.get("inferenceGenerationId") or "",
            "graphStore": relation_context.get("graphStore") or "",
        }

    def episodes(self, account_id: str = "", symbol: str = "", limit: int = 50) -> Dict[str, object]:
        rows = self.decision_episode_store.list(account_id, symbol, limit) if self.decision_episode_store else []
        return {
            "engine": "ontology-investment-brain",
            "count": len(rows),
            "episodes": [item.to_dict() for item in rows],
        }

    def learning_proposals(self, status: str = "", limit: int = 50) -> Dict[str, object]:
        rows = self.decision_episode_store.list_learning_proposals(status, limit) if self.decision_episode_store else []
        return {
            "engine": "ontology-investment-brain",
            "governance": "human-review-required-no-automatic-rulebox-deployment",
            "count": len(rows),
            "proposals": rows,
        }

    def review_learning_proposal(self, proposal_id: str, status: str, note: str = "") -> Dict[str, object]:
        proposal = self.decision_episode_store.review_learning_proposal(proposal_id, status, note)
        return {
            "engine": "ontology-investment-brain",
            "governance": "reviewed-not-deployed",
            "proposal": proposal,
        }

    def resolve_subject(self, message: str, account_id: str = "", symbol: str = "") -> Tuple[Dict[str, object], Optional[Position], str]:
        states = self.monitor_store.load_previous() if hasattr(self.monitor_store, "load_previous") else dict(getattr(self.monitor_store, "previous", {}) or {})
        requested_symbol = str(symbol or "").upper().strip()
        candidates = []
        for state_account_id, state in (states or {}).items():
            if not isinstance(state, dict) or (account_id and str(state_account_id) != str(account_id)):
                continue
            for source_key, source in [("positions", "holding"), ("watchlist", "watchlist")]:
                rows = state.get(source_key) if isinstance(state.get(source_key), dict) else {}
                for item_symbol, payload in rows.items():
                    if not isinstance(payload, dict):
                        continue
                    item = position_from_payload(payload, item_symbol, source)
                    score = subject_match_score(message, requested_symbol, item)
                    if score:
                        candidates.append((score, state, item, source))
        if not candidates:
            return {}, None, ""
        _, state, item, source = max(candidates, key=lambda row: row[0])
        return state, item, source

    def load_relation_context(self, state: Dict[str, object], position: Position, source: str) -> Dict[str, object]:
        decision_rows = state.get("decisions") if isinstance(state.get("decisions"), dict) else {}
        stored_decision = decision_rows.get(position.symbol) if isinstance(decision_rows.get(position.symbol), dict) else {}
        stored_context = stored_decision.get("relation_rule_context") or stored_decision.get("relationRuleContext")
        inferencebox = {}
        if self.ontology_repository and hasattr(self.ontology_repository, "inferencebox_snapshot"):
            try:
                inferencebox = self.ontology_repository.inferencebox_snapshot(symbols=[position.symbol], limit=120)
            except Exception:  # noqa: BLE001 - stored graph context is the read fallback.
                inferencebox = {}
        if isinstance(inferencebox, dict) and (inferencebox.get("relations") or inferencebox.get("traces")):
            return relation_context_from_inferencebox(
                position,
                portfolio_from_payload(state.get("portfolio") or {}),
                inferencebox,
                external_signals=state.get("externalSignals") if isinstance(state.get("externalSignals"), dict) else {},
                settings=self.settings,
                source=source,
                prompt_id="investmentBrainQuestion",
            )
        return dict(stored_context or {}) if isinstance(stored_context, dict) else {}


def position_from_payload(payload: Dict[str, object], symbol: str, source: str) -> Position:
    allowed = {item.name for item in fields(Position)}
    values = {key: value for key, value in payload.items() if key in allowed}
    values["symbol"] = str(values.get("symbol") or symbol).upper()
    values["name"] = str(values.get("name") or values["symbol"])
    values["source"] = source
    return Position(**values)


def portfolio_from_payload(payload: Dict[str, object]) -> PortfolioSummary:
    payload = payload if isinstance(payload, dict) else {}
    return PortfolioSummary(
        total=float(payload.get("total") or 0),
        invested=float(payload.get("invested") or 0),
        cash=float(payload.get("cash") or 0),
        markets=list(payload.get("markets") or []),
        sectors=list(payload.get("sectors") or []),
        concentration=float(payload.get("concentration") or 0),
    )


def subject_match_score(message: str, requested_symbol: str, position: Position) -> int:
    if requested_symbol and requested_symbol == position.symbol.upper():
        return 100
    compact = str(message or "").lower().replace(" ", "")
    symbol = position.symbol.lower().replace(" ", "")
    name = position.name.lower().replace(" ", "")
    if symbol and symbol in compact:
        return 90
    if name and name in compact:
        return 80
    return 0


def answer_text(payload: Dict[str, object]) -> str:
    action_label = str(payload.get("actionLabel") or payload.get("action") or "")
    summary = str(payload.get("summary") or "")
    opinion = str(payload.get("opinion") or "")
    epistemic = str(payload.get("epistemicSummary") or "")
    rows = [item for item in [action_label + (": " if action_label and summary else "") + summary, opinion, epistemic] if item]
    return "\n\n".join(rows)
