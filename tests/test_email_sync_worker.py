from __future__ import annotations

import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from sqlalchemy import select

from app.config import Settings
from app.db import (
    Attachment,
    AttachmentParseStatus,
    FamilyMember,
    LabReport,
    ReportType,
    create_session_factory,
    init_database,
)
from app.mail.sync import EmailSyncWorker, FetchedAttachment, FetchedEmail
from app.parsers import ParsedPdf


class _FakePdfParser:
    def __init__(self, responses: list[ParsedPdf]) -> None:
        self._responses = list(responses)

    def parse(self, source_path: Path) -> ParsedPdf:
        response = self._responses.pop(0)
        return ParsedPdf(
            source_path=source_path,
            extracted_text=response.extracted_text,
            patient_name=response.patient_name,
            report_title=response.report_title,
            report_date=response.report_date,
            report_type=response.report_type,
            needs_manual_review=response.needs_manual_review,
        )


class EmailSyncWorkerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        database_path = Path(self.temp_dir.name) / "medbot.db"
        reports_dir = Path(self.temp_dir.name) / "reports"
        self.session_factory = create_session_factory(
            f"sqlite+aiosqlite:///{database_path.as_posix()}"
        )
        await init_database(self.session_factory)
        settings = Settings(
            DATABASE_URL=f"sqlite+aiosqlite:///{database_path.as_posix()}",
            REPORTS_DIR=reports_dir,
            MAIL_USERNAME="user@example.com",
            MAIL_PASSWORD="secret",
            MAIL_POLL_INTERVAL_SECONDS=1,
        )
        self.worker = EmailSyncWorker(settings=settings, session_factory=self.session_factory)

    async def asyncTearDown(self) -> None:
        engine = self.session_factory.kw.get("bind")
        if engine is not None:
            await engine.dispose()
        self.temp_dir.cleanup()

    async def test_creates_family_member_once_and_links_reports(self) -> None:
        self.worker._pdf_parser = _FakePdfParser(
            [
                ParsedPdf(
                    source_path=Path("first.pdf"),
                    extracted_text="ЦЫГАНКОВА ТАТЬЯНА ИВАНОВНА\nКлинический анализ крови",
                    patient_name="Цыганкова Татьяна Ивановна",
                    report_title="Клинический анализ крови",
                    report_date=datetime(2025, 12, 3),
                    report_type=ReportType.BLOOD,
                ),
                ParsedPdf(
                    source_path=Path("second.pdf"),
                    extracted_text="ЦЫГАНКОВА ТАТЬЯНА ИВАНОВНА\nБиохимический анализ крови",
                    patient_name="Цыганкова Татьяна Ивановна",
                    report_title="Биохимический анализ крови",
                    report_date=datetime(2025, 12, 10),
                    report_type=ReportType.BIOCHEMISTRY,
                ),
            ]
        )

        first_email = FetchedEmail(
            uid="uid-1",
            sender="srs@invitro.ru",
            subject="Лаборатория ИНВИТРО. Результаты анализов.",
            received_at=datetime(2025, 12, 6, 0, 12),
            attachments=[
                FetchedAttachment(
                    filename="872488625_344581031_0_ЦЫГАНКОВА.pdf",
                    content=b"fake-pdf-1",
                    checksum="checksum-1",
                )
            ],
        )
        second_email = FetchedEmail(
            uid="uid-2",
            sender="srs@invitro.ru",
            subject="Лаборатория ИНВИТРО. Результаты анализов.",
            received_at=datetime(2025, 12, 10, 0, 12),
            attachments=[
                FetchedAttachment(
                    filename="872488626_344581032_0_ЦЫГАНКОВА.pdf",
                    content=b"fake-pdf-2",
                    checksum="checksum-2",
                )
            ],
        )

        await self.worker._store_email(first_email)
        await self.worker._store_email(second_email)

        async with self.session_factory() as session:
            family_members = (
                await session.execute(select(FamilyMember).order_by(FamilyMember.id))
            ).scalars().all()
            reports = (await session.execute(select(LabReport).order_by(LabReport.id))).scalars().all()
            attachments = (
                await session.execute(select(Attachment).order_by(Attachment.id))
            ).scalars().all()

        self.assertEqual(len(family_members), 1)
        self.assertEqual(family_members[0].full_name, "Цыганкова Татьяна Ивановна")
        self.assertEqual(len(reports), 2)
        self.assertEqual(reports[0].family_member_id, family_members[0].id)
        self.assertEqual(reports[1].family_member_id, family_members[0].id)
        self.assertEqual(reports[1].report_type, ReportType.BIOCHEMISTRY)
        self.assertEqual(attachments[0].parse_status, AttachmentParseStatus.PARSED)
        self.assertEqual(attachments[1].parse_status, AttachmentParseStatus.PARSED)

    async def test_uses_filename_fallback_only_for_existing_family_member(self) -> None:
        self.worker._pdf_parser = _FakePdfParser(
            [
                ParsedPdf(
                    source_path=Path("first.pdf"),
                    extracted_text="ЦЫГАНКОВА ТАТЬЯНА ИВАНОВНА\nКлинический анализ крови",
                    patient_name="Цыганкова Татьяна Ивановна",
                    report_title="Клинический анализ крови",
                    report_date=datetime(2025, 12, 3),
                    report_type=ReportType.BLOOD,
                ),
                ParsedPdf(
                    source_path=Path("fallback.pdf"),
                    extracted_text="Скан распознан частично",
                    patient_name=None,
                    report_title=None,
                    report_date=datetime(2025, 12, 12),
                    report_type=ReportType.OTHER,
                    needs_manual_review=True,
                ),
            ]
        )

        await self.worker._store_email(
            FetchedEmail(
                uid="uid-1",
                sender="srs@invitro.ru",
                subject="Лаборатория ИНВИТРО. Результаты анализов.",
                received_at=datetime(2025, 12, 6, 0, 12),
                attachments=[
                    FetchedAttachment(
                        filename="872488625_344581031_0_ЦЫГАНКОВА.pdf",
                        content=b"fake-pdf-1",
                        checksum="checksum-1",
                    )
                ],
            )
        )
        await self.worker._store_email(
            FetchedEmail(
                uid="uid-2",
                sender="srs@invitro.ru",
                subject="Лаборатория ИНВИТРО. Результаты анализов.",
                received_at=datetime(2025, 12, 12, 0, 12),
                attachments=[
                    FetchedAttachment(
                        filename="999999999_111111111_0_ЦЫГАНКОВА.pdf",
                        content=b"fake-pdf-2",
                        checksum="checksum-2",
                    )
                ],
            )
        )

        async with self.session_factory() as session:
            family_members = (await session.execute(select(FamilyMember))).scalars().all()
            reports = (await session.execute(select(LabReport).order_by(LabReport.id))).scalars().all()
            attachments = (
                await session.execute(select(Attachment).order_by(Attachment.id))
            ).scalars().all()

        self.assertEqual(len(family_members), 1)
        self.assertEqual(len(reports), 2)
        self.assertEqual(reports[1].family_member_id, family_members[0].id)
        self.assertEqual(reports[1].recognized_patient_name, "Цыганкова Татьяна Ивановна")
        self.assertEqual(
            attachments[1].parse_status,
            AttachmentParseStatus.NEEDS_MANUAL_REVIEW,
        )

    async def test_skips_duplicate_attachment_checksum_from_new_email(self) -> None:
        self.worker._pdf_parser = _FakePdfParser(
            [
                ParsedPdf(
                    source_path=Path("first.pdf"),
                    extracted_text="ЦЫГАНКОВА ТАТЬЯНА ИВАНОВНА\nКлинический анализ крови",
                    patient_name="Цыганкова Татьяна Ивановна",
                    report_title="Клинический анализ крови",
                    report_date=datetime(2025, 12, 3),
                    report_type=ReportType.BLOOD,
                )
            ]
        )

        duplicate_attachment = FetchedAttachment(
            filename="872488625_344581031_0_ЦЫГАНКОВА.pdf",
            content=b"same-pdf",
            checksum="same-checksum",
        )

        await self.worker._store_email(
            FetchedEmail(
                uid="uid-1",
                sender="srs@invitro.ru",
                subject="Лаборатория ИНВИТРО. Результаты анализов.",
                received_at=datetime(2025, 12, 6, 0, 12),
                attachments=[duplicate_attachment],
            )
        )
        await self.worker._store_email(
            FetchedEmail(
                uid="uid-2",
                sender="srs@invitro.ru",
                subject="Лаборатория ИНВИТРО. Результаты анализов.",
                received_at=datetime(2025, 12, 7, 0, 12),
                attachments=[duplicate_attachment],
            )
        )

        async with self.session_factory() as session:
            reports = (await session.execute(select(LabReport).order_by(LabReport.id))).scalars().all()
            attachments = (
                await session.execute(select(Attachment).order_by(Attachment.id))
            ).scalars().all()

        self.assertEqual(len(reports), 1)
        self.assertEqual(len(attachments), 1)

    def test_accepts_invitro_emails_by_subject_or_sender(self) -> None:
        self.worker.settings.mail_allowed_subject_fragment = "Лаборатория ИНВИТРО. Результаты анализов."
        self.worker.settings.mail_allowed_senders = "srs@invitro.ru"

        self.assertTrue(
            self.worker._is_supported_email(
                FetchedEmail(
                    uid="uid-1",
                    sender="friend@example.com",
                    subject="Fwd: Лаборатория ИНВИТРО. Результаты анализов.",
                    received_at=datetime(2026, 3, 16, 15, 50),
                    attachments=[],
                )
            )
        )
        self.assertTrue(
            self.worker._is_supported_email(
                FetchedEmail(
                    uid="uid-2",
                    sender="srs@invitro.ru",
                    subject="Ваши документы",
                    received_at=datetime(2026, 3, 16, 15, 50),
                    attachments=[],
                )
            )
        )
        self.assertFalse(
            self.worker._is_supported_email(
                FetchedEmail(
                    uid="uid-3",
                    sender="other@example.com",
                    subject="Просто письмо",
                    received_at=datetime(2026, 3, 16, 15, 50),
                    attachments=[],
                )
            )
        )

    def test_accepts_any_email_when_filters_are_empty(self) -> None:
        self.worker.settings.mail_allowed_subject_fragment = ""
        self.worker.settings.mail_allowed_senders = ""

        self.assertTrue(
            self.worker._is_supported_email(
                FetchedEmail(
                    uid="uid-4",
                    sender="fafsfsa.safasfasf@mail.ru",
                    subject="Тестовое письмо",
                    received_at=datetime(2026, 3, 16, 15, 50),
                    attachments=[],
                )
            )
        )
        self.assertTrue(
            self.worker._is_supported_email(
                FetchedEmail(
                    uid="uid-5",
                    sender="other@example.com",
                    subject="Просто письмо",
                    received_at=datetime(2026, 3, 16, 15, 50),
                    attachments=[],
                )
            )
        )


if __name__ == "__main__":
    unittest.main()
