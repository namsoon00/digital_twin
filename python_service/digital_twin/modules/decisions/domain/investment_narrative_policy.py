"""Presentation contracts, never investment-action selection rules."""

import re


_ACTION = r"(?:추가\s*매수|분할\s*(?:축소|매도)|전량\s*매도|매수|매도|보유|BUY|ADD|HOLD|TRIM|SELL|AVOID)"
_DIRECTIVE = re.compile(
    r"(?:행동|대응|결론|의견)(?:은|는|\s*:)\s*" + _ACTION
    + r"|" + _ACTION + r"(?:를|을|는|은|만)?\s*(?:해야|하(?:세요|십시오|되|며|는\s*(?:것|쪽|편))|(?:유지|보류|권장|추천)(?:해야|하|합니다))",
    re.IGNORECASE,
)
_INTERNAL = re.compile(
    r"(?<![A-Za-z0-9_])(?:actionEnvelope|TypeDB|InferenceBox|RuleBox|regime-transition-risk|cross-asset-residual-risk)(?![A-Za-z0-9_])"
)


def narrative_presentation_errors(action, texts):
    errors = []
    for text in texts:
        value = str(text or "")
        if str(action).upper() == "NO_ACTION" and _DIRECTIVE.search(value):
            errors.append("actionless-narrative-contains-investment-directive")
        if _INTERNAL.search(value):
            errors.append("customer-narrative-contains-internal-identifier")
    return sorted(set(errors))
