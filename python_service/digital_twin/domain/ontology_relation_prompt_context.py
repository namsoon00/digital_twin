from typing import Dict, List, Optional

from .ontology_prompt_registry import default_ai_prompt_policy_text
from .ontology_relation_contracts import AI_PROMPT_REGISTRY_VERSION, OntologyRuleMatch
from .ontology_relation_settings import prompt_template


def build_ai_prompt_context(
    prompt_id: str,
    facts: Dict[str, object],
    matches: List[OntologyRuleMatch],
    settings: Optional[Dict[str, object]] = None,
    execution_plan: Optional[Dict[str, object]] = None,
) -> Dict[str, object]:
    settings = settings or {}
    template = prompt_template(prompt_id, settings)
    policy = str(settings.get("aiPromptPolicy") or default_ai_prompt_policy_text()).strip()
    return {
        "promptVersion": template.version,
        "promptRegistryVersion": AI_PROMPT_REGISTRY_VERSION,
        "promptId": template.prompt_id,
        "promptTemplate": template.to_dict(),
        "promptPolicy": policy,
        "inputContract": {
            "subject": {
                "symbol": facts.get("symbol"),
                "name": facts.get("name"),
                "market": facts.get("market"),
                "sector": facts.get("sector"),
            },
            "requiredBlocks": ["facts", "trendDynamics", "researchEvidence", "matchedRules", "missingData", "deliveryContext"],
            "forbidden": ["inventing_missing_market_data", "mixing_delivery_priority_with_investment_judgment"],
        },
        "outputSchema": {
            "activeInvestmentOpinion": {
                "action": "BUY|ADD|HOLD|TRIM|SELL|AVOID",
                "conviction": "number 0-100",
                "thesis": "string",
                "evidence": ["ResearchEvidence"],
                "counterEvidence": ["ResearchEvidence"],
                "invalidationCondition": "string",
                "sourceUrls": ["string"],
                "executionPlan": "ExecutionPlan",
            }
        },
        "facts": dict(facts or {}),
        "trendDynamics": dict(facts.get("trendDynamics") or {}),
        "executionPlan": dict(execution_plan or {}),
        "matchedRules": [item.to_dict() for item in matches if item.matched],
        "missingData": list(facts.get("missingData") or []),
        "guardrails": list(template.guardrails),
    }
