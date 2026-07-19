import json
import os
import re
import subprocess
from typing import Dict, List

from .model_reviewer import codex_command
from .settings import ROOT_DIR, runtime_settings


class HypothesisProposalAdvisor:
    def propose(self, context: Dict[str, object]) -> List[Dict[str, object]]:
        raise NotImplementedError


class LocalHypothesisProposalAdvisor(HypothesisProposalAdvisor):
    def propose(self, context: Dict[str, object]) -> List[Dict[str, object]]:
        return []


class CommandHypothesisProposalAdvisor(HypothesisProposalAdvisor):
    def __init__(self, command: str, timeout_seconds: int = 120):
        self.command = str(command or "").strip()
        self.timeout_seconds = max(30, int(timeout_seconds or 120))

    def propose(self, context: Dict[str, object]) -> List[Dict[str, object]]:
        if not self.command:
            return []
        completed = subprocess.run(
            self.command,
            input=hypothesis_proposal_prompt(context),
            text=True,
            shell=True,
            cwd=str(ROOT_DIR),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=self.timeout_seconds,
            env=dict(os.environ),
        )
        if completed.returncode != 0:
            raise RuntimeError((completed.stderr or completed.stdout or "hypothesis proposal AI failed").strip())
        return proposal_rows_from_text(completed.stdout)


class FallbackHypothesisProposalAdvisor(HypothesisProposalAdvisor):
    def __init__(self, primary, fallback=None):
        self.primary = primary
        self.fallback = fallback or LocalHypothesisProposalAdvisor()

    def propose(self, context: Dict[str, object]) -> List[Dict[str, object]]:
        try:
            return self.primary.propose(context)
        except Exception:  # noqa: BLE001 - proposal generation cannot block investment judgement.
            return self.fallback.propose(context)


def hypothesis_proposal_prompt(context: Dict[str, object]) -> str:
    return (
        "당신은 투자 온톨로지의 신규 가설 제안자입니다. 기존 가설로 설명되지 않는 인과 경로만 제안하세요. "
        "입력에 있는 evidence ID만 사용하고 새 사실을 만들지 마세요. 제안은 운영 판단에 즉시 사용되지 않습니다. "
        "출력은 JSON 객체 하나이며 proposals 배열 각 항목은 title, claim, causalPath, supportingEvidenceIds, "
        "counterEvidenceIds, requiredEvidenceTypes, invalidationConditions를 포함합니다. 유효한 신규 가설이 없으면 빈 배열입니다.\n"
        + json.dumps(context, ensure_ascii=False, sort_keys=True)
    )


def proposal_rows_from_text(text: str) -> List[Dict[str, object]]:
    raw = str(text or "").strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.DOTALL)
    if fenced:
        raw = fenced.group(1)
    else:
        start = raw.find("{")
        end = raw.rfind("}")
        if start >= 0 and end > start:
            raw = raw[start:end + 1]
    try:
        payload = json.loads(raw)
    except (TypeError, ValueError):
        return []
    return [item for item in payload.get("proposals") or [] if isinstance(item, dict)][:3]


def hypothesis_proposal_advisor_from_settings(settings: Dict[str, object] = None):
    settings = settings or runtime_settings()
    enabled = str(settings.get("investmentBrainNovelHypothesisAiEnabled", "1")).strip().lower() not in {"0", "false", "off", "disabled"}
    if not enabled:
        return LocalHypothesisProposalAdvisor()
    command = str(settings.get("investmentBrainNovelHypothesisAiCommand") or os.environ.get("INVESTMENT_BRAIN_HYPOTHESIS_AI_COMMAND") or "").strip()
    timeout = int(settings.get("investmentBrainNovelHypothesisAiTimeoutSeconds") or 120)
    if not command:
        command = codex_command(str(settings.get("notificationAiModel") or "gpt-5.4")) or ""
    if command:
        return FallbackHypothesisProposalAdvisor(CommandHypothesisProposalAdvisor(command, timeout))
    return LocalHypothesisProposalAdvisor()
