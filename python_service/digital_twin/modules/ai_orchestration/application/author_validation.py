"""One frozen output correction, sharing the normal observation repair budget."""
import json

from ..domain.planning import validate_plan
from ..domain.execution_input import freeze_repair_input
from ..domain.validation_diagnostic import correction_issue
from .retrieval import RetrievalLeaseLost


def validate_author(raw, envelope, input_id, planner, save_input):
    packet = envelope["current"]
    try:
        plan = validate_plan(raw, packet, envelope["researchResults"], require_research=True, require_resolution=True)
        return plan, raw, envelope, input_id, None
    except ValueError as error:
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
    plan = validate_plan(corrected_raw, packet, correction["researchResults"], require_research=True, require_resolution=True)
    receipt = {"version": "plan-correction-v1", "initialInputId": input_id, "inputId": corrected_id,
               "diagnostic": diagnostic, "status": "validated", "authority": "output-contract-only"}
    return plan, corrected_raw, correction, corrected_id, receipt
