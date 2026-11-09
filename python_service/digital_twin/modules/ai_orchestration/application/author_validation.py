"""One frozen output correction, sharing the normal observation repair budget."""
import json

from ..domain.planning import validate_plan
from ..domain.execution_input import freeze_repair_input
from ..domain.validation_diagnostic import correction_issue
from ..domain.retrieval import response_audit
from .retrieval import RetrievalLeaseLost


def output_identity(raw):
    # Only the frozen correction input may retain a bounded draft. Failure
    # summaries keep its identity, not arbitrary model-authored private text.
    audit = response_audit(raw)
    audit.pop("text", None)
    return {**audit, "omitted": True}


def validate_author(raw, envelope, input_id, planner, save_input):
    packet = envelope["current"]
    try:
        plan = validate_plan(raw, packet, envelope["researchResults"], require_research=True, require_resolution=True)
        return plan, raw, envelope, input_id, None
    except (ValueError, KeyError, TypeError) as error:
        error.author_validation = {"version": "author-output-audit-v1", "inputId": input_id,
            "response": output_identity(raw), "correctionConsumed": False}
        diagnostic = correction_issue(error)
        # More calls cannot repair missing evidence or an unknown application
        # failure. The immutable draft is retained only in the bounded input.
        if (not diagnostic or not isinstance(raw, dict)
                or packet.get("retrieval", {}).get("status") in {"deferred", "repeated-read", "context-budget"}):
            raise
        correction = freeze_repair_input(envelope, raw, [json.dumps(diagnostic, ensure_ascii=False)], input_id)
    corrected_id = save_input(correction)
    if not corrected_id:
        raise RetrievalLeaseLost()
    corrected_raw = planner(correction, corrected_id)
    try:
        plan = validate_plan(corrected_raw, packet, correction["researchResults"], require_research=True, require_resolution=True)
    except (ValueError, KeyError, TypeError) as error:
        error.author_validation = {"version": "author-output-audit-v1", "inputId": corrected_id,
            "initialInputId": input_id, "response": output_identity(corrected_raw), "correctionConsumed": True}
        raise
    receipt = {"version": "plan-correction-v1", "initialInputId": input_id, "inputId": corrected_id,
               "diagnostic": diagnostic, "status": "validated", "authority": "output-contract-only"}
    return plan, corrected_raw, correction, corrected_id, receipt
