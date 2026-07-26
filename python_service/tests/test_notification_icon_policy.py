import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from digital_twin.domain.message_types import MESSAGE_TYPE_EMOJIS, notification_message_types, public_message_catalog  # noqa: E402
from digital_twin.domain.notification_icon_policy import notification_message_icon  # noqa: E402
from digital_twin.domain.notification_title_rules import notification_title_icon  # noqa: E402
from digital_twin.domain.portfolio import AlertEvent  # noqa: E402


EXPECTED_BASE_ICONS = {
    "default": "🔔",
    "investmentInsight": "🧭",
    "portfolioHoldingsSnapshot": "📋",
    "investmentCalendarReminder": "🗓️",
    "newsDigest": "🗞️",
    "modelBuy": "🟢",
    "modelSell": "🔴",
    "watchlistBuyCandidate": "🎯",
    "watchlistQuote": "👀",
    "watchlistQuotePending": "⏳",
    "watchlistOntologySignal": "🧭",
    "holdingTiming": "⚖️",
    "ontologyInferenceMissing": "⚠️",
    "ontologyReasoningQueue": "⏳",
    "monitorHeartbeat": "💓",
    "monitorConnection": "🔌",
    "monitorPositionChange": "↔️",
    "monitorPnlChange": "📊",
    "monitorValueChange": "💵",
    "monitorTrendChange": "📊",
    "monitorCashChange": "💵",
    "monitorDecisionChange": "🔁",
    "externalEquityMove": "🇺🇸",
    "externalCryptoMove": "🪙",
    "externalMacroShift": "🏦",
    "externalDartDisclosure": "📄",
    "externalDataConnection": "🛰️",
    "modelReview": "🧠",
    "workHandoff": "📦",
    "operatorReasoningReport": "🛠️",
    "notification": "🔔",
}


def title_icon(rule, lines):
    event = AlertEvent("account-1", "계좌", "WATCH", rule, rule + "-test", "테스트", lines)
    return notification_title_icon(rule, event.lines, event)


class NotificationIconPolicyTests(unittest.TestCase):
    def test_every_message_type_has_the_reviewed_baseline_icon(self):
        self.assertEqual(EXPECTED_BASE_ICONS, MESSAGE_TYPE_EMOJIS)

        catalog = public_message_catalog()
        for message_type in notification_message_types():
            self.assertEqual(EXPECTED_BASE_ICONS[message_type], catalog[message_type]["icon"])

    def test_title_icons_reflect_the_message_direction_or_action(self):
        self.assertEqual("🎯", title_icon("watchlistBuyCandidate", []))
        self.assertEqual("📈", title_icon("watchlistQuote", ["변화: +3.1%"]))
        self.assertEqual("📉", title_icon("watchlistQuote", ["변화: -3.1%"]))
        self.assertEqual("➕", title_icon("monitorPositionChange", ["신규 보유 감지"]))
        self.assertEqual("➖", title_icon("monitorPositionChange", ["보유 제외 감지"]))
        self.assertEqual("💵", title_icon("monitorCashChange", ["변화: +4.0%p"]))
        self.assertEqual("💸", title_icon("monitorCashChange", ["변화: -4.0%p"]))

    def test_operations_and_job_list_icons_use_current_status(self):
        self.assertEqual("⏳", notification_message_icon("ontologyReasoningQueue"))
        self.assertEqual("💓", notification_message_icon("monitorHeartbeat", {"state": "healthy"}))
        self.assertEqual("🔌", notification_message_icon("monitorConnection", {"state": "healthy"}))
        self.assertEqual("🛰️", notification_message_icon("externalDataConnection", {"apiStatus": "healthy"}))
        self.assertEqual("🔐", notification_message_icon("externalDataConnection", {
            "pipelineHealth": {"state": "failed", "reason": "HTTP Error 401: Unauthorized"},
        }))
        self.assertEqual("💸", notification_message_icon("monitorCashChange", {"titleIcon": "💸"}))


if __name__ == "__main__":
    unittest.main()
