from __future__ import annotations

import tempfile
import unittest
from datetime import datetime
from pathlib import Path

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
from app.services.reports import ReportService


class ReportServiceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        database_path = Path(self.temp_dir.name) / "medbot.db"
        self.session_factory = create_session_factory(
            f"sqlite+aiosqlite:///{database_path.as_posix()}"
        )
        await init_database(self.session_factory)
        self.service = ReportService(self.session_factory)
        await self._seed_data()

    async def asyncTearDown(self) -> None:
        engine = self.session_factory.kw.get("bind")
        if engine is not None:
            await engine.dispose()
        self.temp_dir.cleanup()

    async def test_lists_categories_and_member_reports(self) -> None:
        members = await self.service.list_family_members()
        self.assertEqual([member.display_name for member in members], ["Анна", "Папа"])

        categories = await self.service.list_categories_for_member(self.anna_id)
        category_counts = {category.key: category.report_count for category in categories}
        self.assertEqual(
            category_counts,
            {
                "blood": 1,
                "urine": 0,
                "biochemistry": 1,
                "hormones": 0,
                "other": 1,
            },
        )

        blood_reports = await self.service.list_reports_for_member(self.anna_id, "blood")
        self.assertEqual(len(blood_reports), 1)
        self.assertEqual(blood_reports[0].title, "Общий анализ крови")
        self.assertIn("Анна", blood_reports[0].subtitle)

        other_reports = await self.service.list_reports_for_member(self.anna_id, "other")
        self.assertEqual(len(other_reports), 1)
        self.assertEqual(other_reports[0].title, "scan")

    async def test_builds_recent_reports_and_report_card(self) -> None:
        recent_reports = await self.service.list_recent_reports(limit=2)
        self.assertEqual(len(recent_reports), 2)
        self.assertEqual(recent_reports[0].title, "Биохимия")
        self.assertEqual(recent_reports[1].title, "Общий анализ крови")

        report_card = await self.service.get_report_card(self.blood_report_id)
        self.assertIsNotNone(report_card)
        assert report_card is not None
        self.assertEqual(report_card.family_member_name, "Анна")
        self.assertEqual(report_card.category_key, "blood")
        self.assertEqual(report_card.report_date_text, "14.03.2026")
        self.assertEqual(report_card.patient_name, "Иванова Анна Сергеевна")
        self.assertIn("Все показатели в пределах нормы", report_card.summary)
        self.assertEqual(report_card.ai_recommendation, "Наблюдение у терапевта при жалобах.")
        self.assertEqual(report_card.pdf_filename, "cbc.pdf")

    async def _seed_data(self) -> None:
        async with self.session_factory() as session:
            anna = FamilyMember(
                full_name="Иванова Анна Сергеевна",
                normalized_name="иванова анна сергеевна",
                display_name="Анна",
            )
            father = FamilyMember(
                full_name="Иванов Сергей Петрович",
                normalized_name="иванов сергей петрович",
                display_name="Папа",
            )
            session.add_all([anna, father])
            await session.flush()

            email = EmailMessage(
                message_uid="uid-1",
                sender="lab@example.com",
                subject="Lab reports",
                received_at=datetime(2026, 3, 14, 8, 0, 0),
            )
            session.add(email)
            await session.flush()

            blood_attachment = Attachment(
                email_id=email.id,
                filename="cbc.pdf",
                storage_path="/tmp/cbc.pdf",
                checksum="checksum-1",
                parse_status=AttachmentParseStatus.PARSED,
            )
            biochemistry_attachment = Attachment(
                email_id=email.id,
                filename="biochemistry.pdf",
                storage_path="/tmp/biochemistry.pdf",
                checksum="checksum-2",
                parse_status=AttachmentParseStatus.PARSED,
            )
            unknown_attachment = Attachment(
                email_id=email.id,
                filename="scan.pdf",
                storage_path="/tmp/scan.pdf",
                checksum="checksum-3",
                parse_status=AttachmentParseStatus.NEEDS_MANUAL_REVIEW,
            )
            father_attachment = Attachment(
                email_id=email.id,
                filename="father.pdf",
                storage_path="/tmp/father.pdf",
                checksum="checksum-4",
                parse_status=AttachmentParseStatus.PARSED,
            )
            session.add_all(
                [
                    blood_attachment,
                    biochemistry_attachment,
                    unknown_attachment,
                    father_attachment,
                ]
            )
            await session.flush()

            blood_report = LabReport(
                attachment_id=blood_attachment.id,
                family_member_id=anna.id,
                report_type=ReportType.BLOOD,
                report_date=datetime(2026, 3, 14),
                recognized_patient_name="Иванова Анна Сергеевна",
                source_text="Гемоглобин 128. Лейкоциты 6.0.",
                summary_json={"summary": "Все показатели в пределах нормы.", "title": "Общий анализ крови"},
            )
            biochemistry_report = LabReport(
                attachment_id=biochemistry_attachment.id,
                family_member_id=anna.id,
                report_type=ReportType.BIOCHEMISTRY,
                report_date=datetime(2026, 3, 15),
                recognized_patient_name="Иванова Анна Сергеевна",
                source_text="АЛТ 18, АСТ 20, билирубин 10.",
                summary_json={"title": "Биохимия"},
            )
            unknown_report = LabReport(
                attachment_id=unknown_attachment.id,
                family_member_id=anna.id,
                report_type=ReportType.UNKNOWN,
                recognized_patient_name="Иванова Анна Сергеевна",
                source_text="Скан без уверенного распознавания.",
            )
            father_report = LabReport(
                attachment_id=father_attachment.id,
                family_member_id=father.id,
                report_type=ReportType.HORMONES,
                report_date=datetime(2026, 2, 20),
                recognized_patient_name="Иванов Сергей Петрович",
                source_text="ТТГ 2.1",
                summary_json={"title": "Гормоны щитовидной железы"},
            )
            session.add_all(
                [blood_report, biochemistry_report, unknown_report, father_report]
            )
            await session.flush()

            session.add(
                AIRecommendation(
                    lab_report_id=blood_report.id,
                    model_name="gigachat",
                    recommendation_text="Наблюдение у терапевта при жалобах.",
                    created_at=datetime(2026, 3, 14, 12, 0, 0),
                )
            )
            await session.commit()

            self.anna_id = anna.id
            self.blood_report_id = blood_report.id


if __name__ == "__main__":
    unittest.main()
