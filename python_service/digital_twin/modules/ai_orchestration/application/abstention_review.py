"""One optional omission review and one grounded rewrite, within existing budgets."""
from ..domain import abstention_review as review, observation_abstention as policy
from ..domain.budget import AIControlBudgetWait
from ..domain.execution_resilience import AIExecutionDeferred
from ..domain.execution_input import freeze_repair_input
from ..domain.planning import validate_plan, stamp
from ..domain.continuity import judgment_continuity
from ..domain.memory_reference import historical_facts
from ..domain.validation_diagnostic import task_failure
from .retrieval import RetrievalLeaseLost


def reconsider(result, raw, envelope, input_id, history, save, reviewer, planner):
    if not policy.enabled(result["input"]):
        return raw, envelope, input_id, False
    keys = review.history(history)
    result["abstentionReviewHistory"] = keys
    selected = review.candidate(result)
    if not selected:
        return raw, envelope, input_id, False
    if selected["key"] in keys:
        previous = next((row.get("abstentionReview") for row in history
                         if row.get("abstentionReview", {}).get("key") == selected["key"]), {})
        result["abstentionReview"] = {**previous, "version": review.VERSION, "key": selected["key"],
            "cached": True, "status": previous.get("status", "unavailable"), "authority": "no-publication-authority"}
        return raw, envelope, input_id, False
    audit = {"version": review.VERSION, "key": selected["key"], "status": "pending",
             "authority": "no-publication-authority"}
    # Mark attempts too: outages and budget exhaustion must not create a paid
    # loop over the same source/receipt context on every quote tick.
    result["abstentionReviewHistory"] = [selected["key"], *keys][:review.HISTORY_LIMIT]
    result["abstentionReview"] = audit
    stage = "abstention-review"
    try:
        if reviewer is None:
            audit["status"] = "unavailable"
            return raw, envelope, input_id, False
        try:
            frozen = review.freeze(result)
        except ValueError:
            audit["status"] = "context-budget"
            return raw, envelope, input_id, False
        review_id = save(frozen)
        if not review_id:
            raise RetrievalLeaseLost()
        audit["inputId"] = review_id
        verdict = review.accept(reviewer(frozen, review_id), frozen)
        audit.update(status="completed", **verdict)
        if verdict["decision"] == "reconsider":
            stage = "abstention-rewrite"
            correction = freeze_repair_input(envelope, raw,
                ["보류 재검토에서 추가 설명 후보가 확인됐습니다. 근거·이전 전달과 비교하고 발송 여부를 다시 판단하세요."],
                input_id, {"abstentionReview": verdict})
            correction_id = save(correction)
            if not correction_id:
                raise RetrievalLeaseLost()
            audit["rewriteInputId"] = correction_id
            revised_raw = planner(correction, correction_id)
            plan = validate_plan(revised_raw, result["input"], correction["researchResults"],
                                 require_research=True, require_resolution=True)
            result.pop("conditionValidation", None)
            result.pop("documentaryReview", None)
            result.update(plan, executionInputId=correction_id, observedAt=stamp(),
                          memoryCoverage=correction["memoryCoverage"],
                          judgmentContinuity=judgment_continuity(correction),
                          comparisonFacts=historical_facts(correction["previousAnalyses"]))
            audit["resolution"] = "reauthored"
            return revised_raw, correction, correction_id, True
    except RetrievalLeaseLost:
        raise
    except AIControlBudgetWait:
        audit["status"] = "budget-wait"
    except AIExecutionDeferred:
        audit["status"] = "execution-wait"
    except Exception as error:
        audit.update(status="failed", failure=task_failure(stage, error)[1])
    return raw, envelope, input_id, False
