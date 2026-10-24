"""Outcomes-owned bounded reuse after every authoritative history read."""

from collections import OrderedDict
from copy import deepcopy
from hashlib import sha256
import marshal
from threading import Lock


class ExactPerformanceReportCache:
    def __init__(self, maximum_entries=4):
        self.maximum_entries = max(1, int(maximum_entries))
        self._reports = OrderedDict()
        self._lock = Lock()

    def evaluate(self, episodes, minimum_sample_count, evaluator):
        # Store no source documents. A repair, qualification receipt, account,
        # clock, ordering or threshold change produces a different key.
        try:
            # A process-local binary key avoids re-encoding tens of MB of JSON.
            # Only builtin data can be encoded; no deserialization is performed.
            encoded = marshal.dumps(episodes, 4)
        except (TypeError, ValueError):
            return evaluator(episodes, minimum_sample_count=minimum_sample_count)
        key = (evaluator, minimum_sample_count, sha256(encoded).digest())
        del encoded
        with self._lock:
            if key in self._reports:
                self._reports.move_to_end(key)
                return deepcopy(self._reports[key])
        result = evaluator(episodes, minimum_sample_count=minimum_sample_count)
        with self._lock:
            self._reports[key] = deepcopy(result)
            self._reports.move_to_end(key)
            while len(self._reports) > self.maximum_entries:
                self._reports.popitem(last=False)
        return result


PERFORMANCE_REPORT_CACHE = ExactPerformanceReportCache()
