from __future__ import annotations

import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from sqlalchemy import select

from app.db import (
    AIRecommendation,
    Attachment,
    AttachmentParseStatus,
    EmailMessage,
    FamilyMember,
    LabReport,
    ReportType,
    create_session_factory,
    init_database,
)
from app.services.recommendations import RecommendationService


class _FakeGigaChatClient:
    model_name = "GigaChat"

    def __init__(self) -> None:
        self.calls = []

    async def generate_recommendation(self, payload) -> str:
        self.calls.append(payload)
        return (
            "- Выраженных отклонений по краткой выжимке не видно.\n"
            "- При симптомах стоит обсудить результат с терапевтом.\n\n"
            "Важно: совет ИИ не является диагнозом и не заменяет консультацию врача."
        )


class RecommendationServiceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        database_path = Path(self.temp_dir.name) / "medbot.db"
        self.session_factory = create_session_factory(
            f"sqlite+aiosqlite:///{database_path.as_posix()}"
        )
        await init_database(self.session_factory)
        self.fake_client = _FakeGigaChatClient()
        self.service = RecommendationService(self.session_factory, self.fake_client)
        await self._seed_data()

    async def asyncTearDown(self) -> None:
        engine = self.session_factory.kw.get("bind")
        if engine is not None:
            await engine.dispose()
        self.temp_dir.cleanup()

    async def test_generates_and_persists_recommendation(self) -> None:
        recommendation = await self.service.generate_for_report(self.report_id)

        self.assertIn("совет ИИ не является диагнозом", recommendation)
        self.assertEqual(len(self.fake_client.calls), 1)
        self.assertEqual(self.fake_client.calls[0].patient_name, "Иванова Анна Сергеевна")
        self.assertIn("Все показатели в пределах нормы.", self.fake_client.calls[0].report_summary)

        async with self.session_factory() as session:
            result = await session.execute(
                select(AIRecommendation).where(AIRecommendation.lab_report_id == self.report_id)
            )
            stored = result.scalars().all()

        self.assertEqual(len(stored), 1)
        self.assertEqual(stored[0].model_name, "GigaChat")
        self.assertEqual(stored[0].recommendation_text, recommendation)

    async def _seed_data(self) -> None:
        async with self.session_factory() as session:
            member = FamilyMember(
                full_name="Иванова Анна Сергеевна",
                normalized_name="иванова анна сергеевна",
                display_name="Анна",
            )
            session.add(member)
            await session.flush()

            email = EmailMessage(
                message_uid="uid-1",
                sender="lab@example.com",
                subject="Lab reports",
                received_at=datetime(2026, 3, 14, 8, 0, 0),
            )
            session.add(email)
            await session.flush()

            attachment = Attachment(
                email_id=email.id,
                filename="cbc.pdf",
                storage_path="/tmp/cbc.pdf",
                checksum="checksum-1",
                parse_status=AttachmentParseStatus.PARSED,
            )
            session.add(attachment)
            await session.flush()

            report = LabReport(
                attachment_id=attachment.id,
                family_member_id=member.id,
                report_type=ReportType.BLOOD,
                report_date=datetime(2026, 3, 14),
                recognized_patient_name="Иванова Анна Сергеевна",
                source_text="Гемоглобин 128. Лейкоциты 6.0.",
                summary_json={
                    "summary": "Все показатели в пределах нормы.",
                    "title": "Общий анализ крови",
                },
            )
            session.add(report)
            await session.commit()

            self.report_id = report.id


if __name__ == "__main__":
    unittest.main()
