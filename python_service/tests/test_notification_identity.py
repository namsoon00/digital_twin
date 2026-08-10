import unittest

from digital_twin.application.notification_service import NotificationInstrumentIdentityEnricher
from digital_twin.domain.notifications import NotificationJob


class _Symbol:
    def to_dict(self):
        return {
            "symbol": "028260",
            "name": "삼성물산",
            "market": "KOSPI",
            "source": "KRX KIND Listed Companies",
        }


class _SymbolRepository:
    def get(self, symbol, market=""):
        del market
        return _Symbol() if symbol == "028260" else None


class NotificationInstrumentIdentityTests(unittest.TestCase):
    def test_resolves_code_only_identity_before_ai_and_rendering(self):
        relation = {
            "subject": {"symbol": "028260", "name": "028260", "market": "KR"},
            "facts": {"symbol": "028260", "name": "028260"},
        }
        insight = {
            "subject": "028260",
            "subjectName": "028260",
            "thesis": "028260은 보유 이유를 다시 확인해야 합니다.",
        }
        job = NotificationJob.create(
            "028260",
            message_type="investmentInsight",
            context={
                "rawSymbol": "028260",
                "symbol": "028260",
                "rawTarget": "028260",
                "target": "028260",
                "title": "028260",
                "displayTarget": "028260",
                "displaySymbolName": "028260",
                "symbolDisplayName": "028260",
                "symbolWithCode": "028260",
                "headline": "[관찰] ⚖️ 028260: 보유 유지·다음 조건 확인",
                "ontologyRelationContext": relation,
                "ontologyInsight": insight,
                "metadata": {
                    "ontologyRelationContext": relation,
                    "ontologyInsight": insight,
                },
            },
        )

        NotificationInstrumentIdentityEnricher(_SymbolRepository())(job)

        self.assertEqual("삼성물산", job.context["title"])
        self.assertEqual("삼성물산 / 028260", job.context["displayTarget"])
        self.assertEqual("삼성물산 / 028260", job.context["symbolWithCode"])
        self.assertEqual("종목: 삼성물산 / 028260", job.context["symbolLine"])
        self.assertEqual("대상: 삼성물산 / 028260", job.context["targetLine"])
        self.assertIn("삼성물산:", job.context["headline"])
        self.assertEqual("삼성물산", job.context["ontologyRelationContext"]["subject"]["name"])
        self.assertEqual("삼성물산", job.context["ontologyRelationContext"]["facts"]["name"])
        self.assertEqual("삼성물산", job.context["ontologyInsight"]["subjectName"])
        self.assertTrue(job.context["ontologyInsight"]["thesis"].startswith("삼성물산은"))
        self.assertEqual(
            "삼성물산",
            job.context["metadata"]["ontologyRelationContext"]["subject"]["name"],
        )

    def test_preserves_existing_rich_display_target(self):
        job = NotificationJob.create(
            "Strategy",
            message_type="investmentInsight",
            context={
                "rawSymbol": "028260",
                "title": "삼성물산",
                "displayTarget": "삼성물산 우선 확인 / 028260",
                "ontologyRelationContext": {
                    "subject": {"symbol": "028260", "name": "삼성물산"},
                },
            },
        )

        NotificationInstrumentIdentityEnricher(_SymbolRepository())(job)

        self.assertEqual("삼성물산 우선 확인 / 028260", job.context["displayTarget"])


if __name__ == "__main__":
    unittest.main()
