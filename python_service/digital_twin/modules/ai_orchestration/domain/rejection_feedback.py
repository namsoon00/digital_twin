"""Past validation mistakes are process memory, never investment evidence."""
from copy import deepcopy
from .insight_contract import narrative_digest
from .insight_quality import local_quality, accept_review
from .memory_reference import continuity, size

VERSION = "rejected-observation-feedback-v1"
KIND = "review-feedback"
MAX_ROWS = 2
MAX_BYTES = 4096
FIELDS = ("kind", "version", "taskId", "executionInputId", "reviewInputId", "draftHash",
          "accountId", "symbol", "worldId", "knownAt", "stage", "issues", "authority", "purpose")


def extract(result, task_id, known_at, review_envelope=None):
    """The adapter must prove the completed author/review calls before use."""
    quality = result.get("quality") or {}
    if quality.get("status") != "rejected" or not result.get("summary"):
        return None
    local = local_quality(result)
    issues = []
    if local["status"] == "rejected" and local.get("errors") == quality.get("errors"):
        stage = "local-validation"
        issues = [{"section": "validation", "reason": error[:180]} for error in local["errors"][:4]]
    elif review_envelope and quality.get("draftHash") == narrative_digest(result):
        reviewed = {**review_envelope["draft"], "input": review_envelope["current"]}
        if narrative_digest(reviewed) != quality["draftHash"]:
            return None
        checked = accept_review(result, quality.get("review"), quality.get("reviewInputId"))
        if checked.get("status") != "rejected" or checked.get("errors") != quality.get("errors"):
            return None
        stage = "independent-review"
        review = quality["review"]
        checks = {key: review.get(key) for key in ("investmentMeaning", "analystAssessment", "notificationContribution", "judgmentChange")}
        checks.update(review.get("sections", {}))
        issues = [{"section": section, "reason": row["reason"][:180]}
                  for section, row in checks.items() if isinstance(row, dict) and isinstance(row.get("reason"), str)
                  and any(row.get(key) is False for key in
                          ("supported", "substantive", "discriminating", "expectationsSupported", "followUpLinked"))][:4]
        if len(issues) < 4 and isinstance(review.get("reason"), str):
            issues.append({"section": "overall", "reason": review["reason"][:180]})
    else:
        return None
    if not issues:
        return None
    return {"kind": KIND, "version": VERSION, "taskId": task_id,
            "executionInputId": result["executionInputId"],
            "reviewInputId": quality.get("reviewInputId", ""), "draftHash": narrative_digest(result),
            **{k: result["input"][k] for k in ("accountId", "symbol", "worldId")},
            "knownAt": known_at, "stage": stage, "issues": issues,
            "authority": "rejected-draft-feedback-only",
            "purpose": "과거 검증 지적입니다. 현재 근거로 해결 여부를 확인하며, 사실·승인된 의견·재호출·발송 허가로 사용하지 않습니다."}


def admit(history, research, candidates):
    """Optional feedback cannot displace required questions or approved history."""
    selected, used, seen = [], 0, set()
    for row in candidates:
        key = str(row.get("issues"))
        if row.get("version") != VERSION or row.get("kind") != KIND or key in seen:
            continue
        count = size(row)
        if used + count > MAX_BYTES:
            continue
        try:
            continuity(history, research + selected + [row])
        except ValueError:
            continue
        selected.append(deepcopy(row)); used += count; seen.add(key)
        if len(selected) == MAX_ROWS:
            break
    return selected
