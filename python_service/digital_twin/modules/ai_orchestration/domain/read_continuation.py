"""A prior read plan can reopen current sources, never an old answer or cursor."""
from digital_twin.modules.reasoning.contracts import content_hash

VERSION = "current-source-read-continuation-v1"
MAX_PLANS = 4
MAX_READS = 2
MAX_BYTES = 24 * 1024


def question_scope(index):
    return content_hash(sorted([(r["caseId"], r.get("questionContract", {})) for r in index], key=lambda r: r[0]))
