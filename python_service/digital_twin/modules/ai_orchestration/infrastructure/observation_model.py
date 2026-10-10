"""Structured model adapter; process/vendor implementation is injected by composition."""
import json
import os
from pathlib import Path
import tempfile

from digital_twin.modules.ai_orchestration.domain.execution_input import validate_execution_input
from digital_twin.modules.ai_orchestration.domain.retrieval import RETRIEVAL_PROMPT_VERSION
from ..domain.working_retrieval import VERSION as WORKING_RETRIEVAL_VERSION


class StructuredObservationModel:
    def __init__(self, command_builder, run_prompt, parse_response):
        self.command_builder = command_builder
        self.run_prompt = run_prompt
        self.parse_response = parse_response

    def __call__(self, envelope, settings):
        validate_execution_input(envelope)
        with tempfile.TemporaryDirectory(prefix="orbit-observation-schema-") as directory:
            schema = Path(directory) / "response.json"
            schema.write_text(json.dumps(envelope["outputSchema"], ensure_ascii=False), encoding="utf-8")
            os.chmod(schema, 0o600)
            result = self.run_prompt(self.command_builder(schema), envelope["prompt"], 240, settings)
            if envelope["promptVersion"] not in {RETRIEVAL_PROMPT_VERSION, WORKING_RETRIEVAL_VERSION}:
                return self.parse_response(result.stdout)
            # An invalid model response is locally correctable. Transport/runtime
            # failures still propagate to operational retry without being relabelled.
            try:
                parsed = self.parse_response(result.stdout)
            except (ValueError, TypeError):
                parsed = None
            return parsed if parsed else {"unparseableResponse": result.stdout}
