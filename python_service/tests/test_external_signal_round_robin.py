import unittest

from digital_twin.infrastructure.external_signal_provider_core import ExternalSignalCoreMixin


class ExternalSignalHarness(ExternalSignalCoreMixin):
    def __init__(self):
        self.settings = {"targetLimit": "2"}
        self.provider_state = {}


class ExternalSignalRoundRobinTests(unittest.TestCase):
    def test_bulk_cap_rotates_targets_instead_of_starving_the_tail(self):
        provider = ExternalSignalHarness()
        values = ["A", "B", "C", "D", "E"]

        first_signals = {}
        second_signals = {}
        third_signals = {}
        first = provider.limited_targets(first_signals, "OpenDART", values, "targetLimit", 2)
        second = provider.limited_targets(second_signals, "OpenDART", values, "targetLimit", 2)
        third = provider.limited_targets(third_signals, "OpenDART", values, "targetLimit", 2)

        self.assertEqual(["A", "B"], first)
        self.assertEqual(["C", "D"], second)
        self.assertEqual(["E", "A"], third)
        self.assertEqual(["E", "A"], third_signals["statuses"][-1]["selectedTargets"])
        self.assertEqual(1, third_signals["statuses"][-1]["nextCursor"])


if __name__ == "__main__":
    unittest.main()
