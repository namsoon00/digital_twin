import os
from typing import Dict, List

from ..domain.ontology_rulebox_governance import build_rule_change_candidate_prompt, rule_change_candidates_from_text
from .model_reviewer import (
    background_codex_process_arguments,
    codex_model_label,
    run_background_ai_prompt,
)
from .settings import runtime_settings


class RuleChangeCandidateAdvisor:
    def propose(self, context: Dict[str, object]) -> List[Dict[str, object]]:
        raise NotImplementedError

    def metadata(self) -> Dict[str, object]:
        return {"configured": False, "mode": self.__class__.__name__}


class LocalRuleChangeCandidateAdvisor(RuleChangeCandidateAdvisor):
    def propose(self, context: Dict[str, object]) -> List[Dict[str, object]]:
        return []

    def metadata(self) -> Dict[str, object]:
        return {
            "configured": False,
            "mode": "local-empty-fallback",
            "reason": "Rule candidate AI command is not configured; local advisor intentionally returns no candidates.",
        }


class CommandRuleChangeCandidateAdvisor(RuleChangeCandidateAdvisor):
    def __init__(self, command, timeout_seconds: int = 120, source: str = "AI", settings=None):
        self.command = command
        self.timeout_seconds = max(30, int(timeout_seconds or 120))
        self.source = str(source or "AI")
        self.settings = dict(settings or {})

    def propose(self, context: Dict[str, object]) -> List[Dict[str, object]]:
        if not self.command:
            raise RuntimeError("rule candidate AI command is not configured")
        prompt = build_rule_change_candidate_prompt(context)
        completed = run_background_ai_prompt(
            self.command,
            prompt,
            self.timeout_seconds,
            self.settings,
        )
        output = completed.stdout.strip()
        if completed.returncode != 0:
            raise RuntimeError((completed.stderr or output or "rule candidate AI command failed").strip())
        if not output:
            raise RuntimeError("rule candidate AI command returned empty output")
        candidates = rule_change_candidates_from_text(output, context)
        for candidate in candidates:
            candidate["source"] = self.source
        return candidates

    def metadata(self) -> Dict[str, object]:
        return {
            "configured": bool(self.command),
            "mode": "command",
            "source": self.source,
            "timeoutSeconds": self.timeout_seconds,
        }


class FallbackRuleChangeCandidateAdvisor(RuleChangeCandidateAdvisor):
    def __init__(self, primary: RuleChangeCandidateAdvisor, fallback: RuleChangeCandidateAdvisor = None):
        self.primary = primary
        self.fallback = fallback or LocalRuleChangeCandidateAdvisor()

    def propose(self, context: Dict[str, object]) -> List[Dict[str, object]]:
        try:
            return self.primary.propose(context)
        except Exception as error:  # noqa: BLE001 - ontology reasoning worker must keep running after AI failures.
            candidates = self.fallback.propose(context)
            for candidate in candidates:
                warnings = list(candidate.get("validationWarnings") or [])
                warnings.append("AI 제안 실패로 로컬 fallback을 사용했습니다: " + str(error)[:140])
                candidate["validationWarnings"] = warnings
            return candidates

    def metadata(self) -> Dict[str, object]:
        primary = self.primary.metadata() if hasattr(self.primary, "metadata") else {"mode": self.primary.__class__.__name__}
        fallback = self.fallback.metadata() if hasattr(self.fallback, "metadata") else {"mode": self.fallback.__class__.__name__}
        return {
            "configured": bool(primary.get("configured")),
            "mode": "fallback",
            "primary": primary,
            "fallback": fallback,
        }


def rule_change_candidate_advisor_from_settings(settings: Dict[str, str] = None) -> RuleChangeCandidateAdvisor:
    settings = settings or runtime_settings()
    use_codex = str(settings.get("ontologyRuleCandidateAiUseCodex") or os.environ.get("ONTOLOGY_RULE_CANDIDATE_AI_USE_CODEX") or "1").strip() != "0"
    timeout = int(settings.get("ontologyRuleCandidateAiTimeoutSeconds") or os.environ.get("ONTOLOGY_RULE_CANDIDATE_AI_TIMEOUT_SECONDS") or 120)
    if use_codex:
        command = background_codex_process_arguments()
        if command:
            return FallbackRuleChangeCandidateAdvisor(CommandRuleChangeCandidateAdvisor(command, timeout, "Codex AI (" + codex_model_label() + ")", settings))
    return LocalRuleChangeCandidateAdvisor()
