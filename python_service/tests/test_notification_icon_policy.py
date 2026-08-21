import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from digital_twin.domain.message_types import MESSAGE_TYPE_EMOJIS, notification_message_types, public_message_catalog  # noqa: E402
from digital_twin.domain.notification_icon_policy import (  # noqa: E402
    investment_notification_icon,
    notification_message_icon,
    notification_title_with_context_icon,
)
from digital_twin.domain.notification_title_rules import notification_title_icon  # noqa: E402
from digital_twin.domain.notification_title_policy import investment_notification_title  # noqa: E402
from digital_twin.domain.notification_templates import NotificationTemplate, alert_context, render_notification  # noqa: E402
from digital_twin.domain.portfolio import AlertEvent  # noqa: E402


EXPECTED_BASE_ICONS = {
    "default": "🔔",
    "investmentInsight": "🧭",
    "marketObservation": "↔️",
    "portfolioHoldingsSnapshot": "📋",
    "portfolioActivityObservation": "↔️",
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
    "operationalStorageCapacity": "💾",
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
    "cryptoOntologySignal": "🪙",
    "portfolioOntologySignal": "⚖️",
    "portfolioRebalanceReview": "🔄",
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


def investment_context(previous_action="", action="HOLD", status="ENTRY_OBSERVING", watchlist=True, data_state="sufficient"):
    relation = {
        "facts": {"source": "watchlist"} if watchlist else {"source": "holding"},
        "targetRole": "watchlist" if watchlist else "holding",
        "actionPolicy": "ENTRY_ONLY" if watchlist else "",
        "decision": {"candidateAction": action},
        "actionEnvelope": {
            "status": status,
            "preferredAction": action,
            "targetRole": "watchlist" if watchlist else "holding",
            "actionPolicy": "ENTRY_ONLY" if watchlist else "",
            "dataReadiness": {"state": "ready", "usable": data_state != "unavailable"},
        },
    }
    return {
        "messageType": "investmentInsight",
        "titleIcon": "🧭",
        "headline": "[관찰] 🧭 NVIDIA: 관심 유지·진입 조건 확인",
        "decisionTransition": {
            "kind": "action-changed",
            "previousAction": previous_action,
            "currentAction": action,
            "currentStatus": status,
        },
        "ontologyRelationContext": relation,
        "notificationAiValidatedResponse": {
            "action": action,
            "dataState": data_state,
            "validationState": "ready",
        },
    }


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

    def test_market_observation_title_uses_price_direction_without_repeating_observation(self):
        event = AlertEvent(
            "account-1",
            "계좌",
            "WATCH",
            "marketObservation",
            "market-observation-066570-down",
            "LG전자",
            ["현재가: 191,300원", "기준 대비: -2.2%"],
            symbol="066570",
            metadata={"marketObservation": {"changePct": -2.2, "direction": "down"}},
        )

        context = alert_context(event)

        self.assertEqual("📉", context["titleIcon"])
        self.assertEqual("기준가 대비 -2.2% 하락", context["titleHeadline"])
        self.assertEqual("[시세] 📉 LG전자: 기준가 대비 -2.2% 하락", context["headline"])
        self.assertEqual("LG전자 / 066570", context["displayTarget"])
        rendered = render_notification(
            NotificationTemplate("marketObservation", "{telegramMessage}"),
            context,
        )
        self.assertTrue(rendered.startswith("<b>🔔 새 알림 · LG전자</b>"))
        self.assertIn("<b>[시세] 📉 LG전자: 기준가 대비 -2.2% 하락</b>", rendered)
        self.assertNotIn("LG전자 시세 관측", rendered)

        rising = AlertEvent(
            "account-1",
            "계좌",
            "WATCH",
            "marketObservation",
            "market-observation-066570-up",
            "LG전자",
            ["현재가: 199,900원", "기준 대비: +2.2%"],
            symbol="066570",
            metadata={"marketObservation": {"changePct": 2.2, "direction": "up"}},
        )
        rising_context = alert_context(rising)
        self.assertEqual("📈", rising_context["titleIcon"])
        self.assertEqual("[시세] 📈 LG전자: 기준가 대비 +2.2% 상승", rising_context["headline"])

    def test_operations_and_job_list_icons_use_current_status(self):
        self.assertEqual("⏳", notification_message_icon("ontologyReasoningQueue"))
        self.assertEqual("💓", notification_message_icon("monitorHeartbeat", {"state": "healthy"}))
        self.assertEqual("🔌", notification_message_icon("monitorConnection", {"state": "healthy"}))
        self.assertEqual("🛰️", notification_message_icon("externalDataConnection", {"apiStatus": "healthy"}))
        self.assertEqual("🔐", notification_message_icon("externalDataConnection", {
            "pipelineHealth": {"state": "failed", "reason": "HTTP Error 401: Unauthorized"},
        }))
        self.assertEqual("💸", notification_message_icon("monitorCashChange", {"titleIcon": "💸"}))

    def test_news_icons_distinguish_disclosures_updates_and_breaking_events(self):
        self.assertEqual("📄", notification_message_icon("newsDigest", {
            "newsDigest": {"eventKind": "disclosure"},
        }))
        self.assertEqual("↻", notification_message_icon("newsDigest", {
            "newsDigest": {"deliveryMode": "story-update"},
        }))
        self.assertEqual("⚡", notification_message_icon("newsDigest", {
            "newsDigest": {"urgency": "breaking"},
        }))

    def test_investment_icons_follow_structured_action_and_data_states(self):
        cases = [
            (investment_context("BUY", "HOLD", "ENTRY_DEFERRED"), "⏸️"),
            (investment_context("HOLD", "BUY", "ENTRY_ELIGIBLE"), "🟢"),
            (investment_context("", "HOLD", "ENTRY_OBSERVING"), "👀"),
            (investment_context("HOLD", "AVOID", "ENTRY_BLOCKED"), "🛡️"),
            (investment_context("HOLD", "TRIM", "HOLDING_REVIEW", watchlist=False), "↘️"),
            (investment_context("", "HOLD", "JUDGEMENT_BLOCKED", data_state="unavailable"), "⚠️"),
        ]

        for context, expected in cases:
            self.assertEqual(expected, investment_notification_icon("investmentInsight", context))
            self.assertEqual(expected, notification_message_icon("investmentInsight", context))

    def test_transition_icon_replaces_stale_title_icon_for_alert_and_web_title(self):
        context = investment_context("BUY", "HOLD", "ENTRY_DEFERRED")
        event = AlertEvent(
            "account-1",
            "계좌",
            "WATCH",
            "investmentInsight",
            "nvda-transition",
            "NVIDIA",
            [],
            metadata=context,
        )

        self.assertEqual("⏸️", notification_title_icon("investmentInsight", event.lines, event))
        self.assertEqual(
            "[관찰] ⏸️ NVIDIA: 관심 유지·진입 조건 확인",
            notification_title_with_context_icon("investmentInsight", context["headline"], context),
        )
        legacy_context = dict(context)
        legacy_context.pop("titleIcon")
        self.assertEqual(
            "⏸️ NVIDIA",
            notification_title_with_context_icon("investmentInsight", "NVIDIA", legacy_context),
        )

    def test_structured_title_shows_the_final_action_transition(self):
        context = investment_context("HOLD", "BUY", "ENTRY_ELIGIBLE")
        context["displayTarget"] = "NVIDIA / NVDA"

        self.assertEqual(
            "🟢 NVIDIA · 관심 유지 → 소액 진입 검토",
            investment_notification_title("investmentInsight", context),
        )

    def test_final_ai_transition_wins_over_a_graph_candidate_change(self):
        context = investment_context("HOLD", "BUY", "ENTRY_ELIGIBLE")
        context.update({
            "displayTarget": "NVIDIA / NVDA",
            "notificationAiValidatedResponse": {"action": "HOLD"},
            "aiDecisionTransition": {
                "historyAvailable": True,
                "kind": "unchanged",
                "previousAction": "HOLD",
                "currentAction": "HOLD",
            },
        })

        self.assertEqual(
            "👀 NVIDIA · 관심 유지",
            investment_notification_title("investmentInsight", context),
        )

    def test_non_material_graph_transition_is_not_presented_as_an_action_change(self):
        context = investment_context("HOLD", "BUY", "ENTRY_ELIGIBLE")
        context["displayTarget"] = "NVIDIA / NVDA"
        context["decisionTransition"]["material"] = False

        self.assertEqual(
            "🟢 NVIDIA · 소액 진입 검토",
            investment_notification_title("investmentInsight", context),
        )

    def test_unavailable_data_uses_a_blocked_judgement_title(self):
        context = investment_context("", "HOLD", "JUDGEMENT_BLOCKED", data_state="unavailable")
        context["displayTarget"] = "NVIDIA / NVDA"

        self.assertEqual(
            "⚠️ NVIDIA · 판단 보류",
            investment_notification_title("investmentInsight", context),
        )

    def test_holding_title_uses_one_action_icon_and_plain_action_label(self):
        context = investment_context("", "HOLD", "HOLDING_REVIEW", watchlist=False)
        context.update({"displayTarget": "Strategy / MSTR", "companyName": "Strategy"})

        self.assertEqual(
            "⚖️ Strategy · 보유 유지",
            investment_notification_title("investmentInsight", context),
        )

    def test_rendered_investment_message_has_one_title_and_no_generic_badge(self):
        context = investment_context("HOLD", "BUY", "ENTRY_ELIGIBLE")
        event = AlertEvent(
            "account-1",
            "계좌",
            "WATCH",
            "investmentInsight",
            "nvda-entry",
            "NVIDIA",
            [],
            symbol="NVDA",
            metadata=context,
        )

        rendered = render_notification(
            NotificationTemplate("investmentInsight", "{telegramMessage}"),
            alert_context(event),
        )

        title = "🟢 NVIDIA · 관심 유지 → 소액 진입 검토"
        self.assertTrue(rendered.startswith("<b>" + title + "</b>"))
        self.assertEqual(1, rendered.count(title))
        self.assertNotIn("🔔 새 알림", rendered)
        self.assertNotIn("[관찰]", rendered)


if __name__ == "__main__":
    unittest.main()
