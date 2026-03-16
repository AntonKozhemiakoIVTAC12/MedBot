from __future__ import annotations

import asyncio
import hashlib
import imaplib
import logging
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from email import message_from_bytes, policy
from email.header import decode_header
from email.message import Message
from email.utils import parsedate_to_datetime, parseaddr
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.db import (
    Attachment,
    AttachmentParseStatus,
    EmailMessage,
    FamilyMember,
    LabReport,
)
from app.parsers import ParsedPdf, PdfParser

logger = logging.getLogger(__name__)

_FILENAME_SANITIZER = re.compile(r"[^A-Za-z0-9._-]+")
_FILENAME_FAMILY_NAME_RE = re.compile(
    r"_(?P<family>[А-ЯЁA-Z-]+)(?:\s*\(\d+\))?\.pdf$",
    re.IGNORECASE,
)


@dataclass(slots=True)
class FetchedAttachment:
    filename: str
    content: bytes
    checksum: str


@dataclass(slots=True)
class FetchedEmail:
    uid: str
    sender: str
    subject: str
    received_at: datetime
    attachments: list[FetchedAttachment]


class EmailSyncWorker:
    def __init__(
        self,
        settings: Settings,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        self.settings = settings
        self.session_factory = session_factory
        self.poll_interval_seconds = settings.mail_poll_interval_seconds
        self._pdf_parser = PdfParser()
        self._imap_fetch_attempts = 3

    async def run_forever(self) -> None:
        logger.info(
            "Email sync worker started with interval=%s seconds",
            self.poll_interval_seconds,
        )
        while True:
            try:
                synced_count = await self.sync_once()
                logger.info("Email sync cycle finished. New emails processed: %s", synced_count)
            except Exception:
                logger.exception("Email sync cycle failed.")

            await asyncio.sleep(self.poll_interval_seconds)

    async def sync_once(self) -> int:
        if not self.settings.mail_username or not self.settings.mail_password:
            logger.warning("Mail sync skipped because IMAP credentials are not configured.")
            return 0

        known_uids = await self._load_existing_uids()
        fetched_emails = await asyncio.to_thread(self._fetch_new_messages, known_uids)

        synced_count = 0
        for fetched_email in fetched_emails:
            try:
                stored = await self._store_email(fetched_email)
            except Exception:
                logger.exception("Failed to store email uid=%s", fetched_email.uid)
                continue

            if stored:
                synced_count += 1

        return synced_count

    async def _load_existing_uids(self) -> set[str]:
        async with self.session_factory() as session:
            result = await session.execute(select(EmailMessage.message_uid))
            return {row[0] for row in result}

    def _fetch_new_messages(self, known_uids: set[str]) -> list[FetchedEmail]:
        last_error: imaplib.IMAP4.abort | None = None
        for attempt in range(1, self._imap_fetch_attempts + 1):
            try:
                return self._fetch_new_messages_once(known_uids)
            except imaplib.IMAP4.abort as error:
                last_error = error
                logger.warning(
                    "IMAP fetch attempt %s/%s failed with connection abort: %s",
                    attempt,
                    self._imap_fetch_attempts,
                    error,
                )
                if attempt == self._imap_fetch_attempts:
                    raise

        if last_error is not None:
            raise last_error
        return []

    def _fetch_new_messages_once(self, known_uids: set[str]) -> list[FetchedEmail]:
        client = self._create_imap_client()
        try:
            login_status, _ = client.login(
                self.settings.mail_username,
                self.settings.mail_password,
            )
            if login_status != "OK":
                raise RuntimeError("IMAP login failed.")

            select_status, _ = client.select(self.settings.mail_folder)
            if select_status != "OK":
                raise RuntimeError(f"Unable to select folder {self.settings.mail_folder!r}.")

            search_status, data = client.uid("search", None, "ALL")
            if search_status != "OK":
                raise RuntimeError("Unable to search IMAP messages.")

            candidate_uids = [
                uid.decode("utf-8")
                for uid in (data[0].split() if data and data[0] else [])
                if uid.decode("utf-8") not in known_uids
            ]

            fetched_messages: list[FetchedEmail] = []
            for uid in candidate_uids:
                fetch_status, fetch_data = client.uid("fetch", uid, "(BODY.PEEK[])")
                if fetch_status != "OK":
                    logger.warning("Skipping email uid=%s because IMAP fetch failed.", uid)
                    continue

                raw_message = self._extract_raw_message(fetch_data)
                if raw_message is None:
                    logger.warning("Skipping email uid=%s because IMAP returned no body.", uid)
                    continue

                parsed_message = message_from_bytes(raw_message, policy=policy.default)
                fetched_email = self._build_fetched_email(uid, parsed_message)
                if not fetched_email.attachments:
                    continue
                if not self._is_supported_email(fetched_email):
                    logger.info(
                        "Skipping email uid=%s because it does not match configured mail filters.",
                        uid,
                    )
                    continue
                fetched_messages.append(fetched_email)

            return fetched_messages
        finally:
            try:
                client.close()
            except imaplib.IMAP4.error:
                pass
            try:
                client.logout()
            except imaplib.IMAP4.error:
                pass

    def _create_imap_client(self) -> imaplib.IMAP4_SSL:
        return imaplib.IMAP4_SSL(
            host=self.settings.mail_imap_host,
            port=self.settings.mail_imap_port,
        )

    async def _store_email(self, fetched_email: FetchedEmail) -> bool:
        async with self.session_factory() as session:
            existing_email = await session.scalar(
                select(EmailMessage).where(EmailMessage.message_uid == fetched_email.uid)
            )
            if existing_email is not None:
                return False

            email_row = EmailMessage(
                message_uid=fetched_email.uid,
                sender=fetched_email.sender,
                subject=fetched_email.subject,
                received_at=fetched_email.received_at,
                processed_at=datetime.now(UTC).replace(tzinfo=None),
            )
            session.add(email_row)
            await session.flush()

            stored_attachment_count = 0
            stored_files: list[Path] = []
            try:
                for attachment in fetched_email.attachments:
                    existing_attachment = await session.scalar(
                        select(Attachment).where(Attachment.checksum == attachment.checksum)
                    )
                    if existing_attachment is not None:
                        logger.info(
                            "Skipping attachment %s from email uid=%s because checksum already exists.",
                            attachment.filename,
                            fetched_email.uid,
                        )
                        continue

                    storage_path = await self._persist_attachment_file(
                        message_uid=fetched_email.uid,
                        attachment=attachment,
                    )
                    stored_files.append(storage_path)
                    attachment_row = Attachment(
                        email_id=email_row.id,
                        filename=attachment.filename,
                        storage_path=str(storage_path),
                        checksum=attachment.checksum,
                    )
                    session.add(attachment_row)
                    await session.flush()
                    try:
                        await self._parse_attachment(session, attachment_row)
                    except Exception:
                        attachment_row.parse_status = AttachmentParseStatus.FAILED
                        logger.exception(
                            "Failed to parse attachment %s from email uid=%s",
                            attachment.filename,
                            fetched_email.uid,
                        )
                    stored_attachment_count += 1

                await session.commit()
            except IntegrityError:
                await session.rollback()
                await self._cleanup_files(stored_files)
                logger.warning("Detected duplicate email during commit for uid=%s.", fetched_email.uid)
                return False
            except Exception:
                await session.rollback()
                await self._cleanup_files(stored_files)
                raise

        logger.info(
            "Stored email uid=%s with %s PDF attachment(s).",
            fetched_email.uid,
            stored_attachment_count,
        )
        return True

    async def _persist_attachment_file(
        self,
        message_uid: str,
        attachment: FetchedAttachment,
    ) -> Path:
        target_dir = self.settings.reports_dir / "mail" / message_uid
        target_dir.mkdir(parents=True, exist_ok=True)

        safe_name = self._sanitize_filename(attachment.filename)
        target_path = target_dir / f"{attachment.checksum[:12]}-{safe_name}"
        await asyncio.to_thread(target_path.write_bytes, attachment.content)
        return target_path

    async def _parse_attachment(
        self,
        session: AsyncSession,
        attachment_row: Attachment,
    ) -> None:
        parsed = await asyncio.to_thread(
            self._pdf_parser.parse,
            Path(attachment_row.storage_path),
        )
        family_member, recognized_name = await self._resolve_family_member(session, parsed, attachment_row)

        attachment_row.parse_status = (
            AttachmentParseStatus.NEEDS_MANUAL_REVIEW
            if parsed.needs_manual_review
            else AttachmentParseStatus.PARSED
        )

        session.add(
            LabReport(
                attachment_id=attachment_row.id,
                family_member_id=family_member.id if family_member is not None else None,
                report_type=parsed.report_type,
                report_date=parsed.report_date,
                recognized_patient_name=recognized_name,
                source_text=parsed.extracted_text,
                summary_json=self._build_summary_json(parsed, attachment_row.filename),
            )
        )

    async def _resolve_family_member(
        self,
        session: AsyncSession,
        parsed: ParsedPdf,
        attachment_row: Attachment,
    ) -> tuple[FamilyMember | None, str | None]:
        if parsed.patient_name:
            member = await self._get_or_create_family_member(session, parsed.patient_name)
            return member, parsed.patient_name

        family_name = self._extract_family_name_from_filename(attachment_row.filename)
        if not family_name:
            return None, None

        member = await self._find_existing_member_by_family_name(session, family_name)
        if member is None:
            member = await self._get_or_create_family_member(session, family_name)
            return member, family_name

        return member, member.full_name

    async def _get_or_create_family_member(
        self,
        session: AsyncSession,
        full_name: str,
    ) -> FamilyMember:
        normalized_name = self._normalize_person_name(full_name)
        existing = await session.scalar(
            select(FamilyMember).where(FamilyMember.normalized_name == normalized_name)
        )
        if existing is not None:
            return existing

        family_name = full_name.split()[0] if full_name.split() else full_name
        fallback_match = await self._find_existing_member_by_family_name(session, family_name)
        if fallback_match is not None:
            if len(fallback_match.full_name.split()) < len(full_name.split()):
                fallback_match.full_name = full_name
                fallback_match.normalized_name = normalized_name
                fallback_match.display_name = full_name
                await session.flush()
            return fallback_match

        member = FamilyMember(
            full_name=full_name,
            normalized_name=normalized_name,
            display_name=full_name,
        )
        session.add(member)
        await session.flush()
        return member

    async def _find_existing_member_by_family_name(
        self,
        session: AsyncSession,
        family_name: str,
    ) -> FamilyMember | None:
        normalized_family_name = self._normalize_person_name(family_name)
        result = await session.execute(
            select(FamilyMember)
            .where(FamilyMember.normalized_name.like(f"{normalized_family_name}%"))
            .order_by(FamilyMember.id)
        )
        matches = result.scalars().all()
        if len(matches) == 1:
            return matches[0]
        return None

    def _build_summary_json(
        self,
        parsed: ParsedPdf,
        filename: str,
    ) -> dict[str, str]:
        title = parsed.report_title or Path(filename).stem.replace("_", " ")
        summary = self._build_short_summary(parsed.extracted_text)
        return {
            "title": title,
            "summary": summary,
        }

    @staticmethod
    def _build_short_summary(extracted_text: str) -> str:
        lines = [
            line.strip()
            for line in extracted_text.splitlines()
            if line.strip()
            and "результаты исследований не являются диагнозом" not in line.lower()
            and "перейти на исходный" not in line.lower()
        ]
        return "\n".join(lines[:8])[:1200].strip() or "Сводка анализа недоступна."

    async def _cleanup_files(self, paths: list[Path]) -> None:
        for path in paths:
            await asyncio.to_thread(path.unlink, missing_ok=True)

    @staticmethod
    def _extract_raw_message(fetch_data: list[object]) -> bytes | None:
        for item in fetch_data:
            if isinstance(item, tuple) and len(item) > 1 and isinstance(item[1], bytes):
                return item[1]
        return None

    def _build_fetched_email(self, uid: str, parsed_message: Message) -> FetchedEmail:
        sender_name, sender_address = parseaddr(parsed_message.get("From", ""))
        sender = sender_address or sender_name or "unknown"
        subject = self._decode_mime_header(parsed_message.get("Subject", ""))
        received_at = self._parse_received_at(parsed_message.get("Date"))
        attachments = self._extract_pdf_attachments(parsed_message)

        return FetchedEmail(
            uid=uid,
            sender=sender,
            subject=subject,
            received_at=received_at,
            attachments=attachments,
        )

    def _is_supported_email(self, fetched_email: FetchedEmail) -> bool:
        normalized_subject = self._normalize_for_matching(fetched_email.subject)
        normalized_sender = self._normalize_for_matching(fetched_email.sender)
        allowed_subject_fragment = self.settings.mail_allowed_subject_fragment_normalized
        allowed_senders = self.settings.mail_allowed_senders_list

        if not allowed_subject_fragment and not allowed_senders:
            return True

        subject_matches = bool(allowed_subject_fragment) and (
            allowed_subject_fragment in normalized_subject
        )
        sender_matches = any(allowed_sender in normalized_sender for allowed_sender in allowed_senders)
        return subject_matches or sender_matches

    def _extract_pdf_attachments(self, parsed_message: Message) -> list[FetchedAttachment]:
        attachments: list[FetchedAttachment] = []
        seen_checksums: set[str] = set()

        for index, part in enumerate(parsed_message.walk(), start=1):
            if part.is_multipart():
                continue
            if not self._is_pdf_part(part):
                continue

            content = part.get_payload(decode=True) or b""
            if not content:
                continue

            checksum = hashlib.sha256(content).hexdigest()
            if checksum in seen_checksums:
                continue

            seen_checksums.add(checksum)
            attachments.append(
                FetchedAttachment(
                    filename=self._resolve_attachment_filename(part, index),
                    content=content,
                    checksum=checksum,
                )
            )

        return attachments

    @staticmethod
    def _is_pdf_part(part: Message) -> bool:
        filename = part.get_filename()
        content_type = part.get_content_type().lower()
        disposition = (part.get_content_disposition() or "").lower()

        if content_type == "application/pdf":
            return True
        if filename and filename.lower().endswith(".pdf"):
            return True
        return disposition == "attachment" and content_type.endswith("/pdf")

    def _resolve_attachment_filename(self, part: Message, index: int) -> str:
        filename = part.get_filename()
        if filename:
            decoded = self._decode_mime_header(filename).strip()
            if decoded:
                return decoded
        return f"attachment-{index}.pdf"

    @staticmethod
    def _decode_mime_header(raw_value: str) -> str:
        decoded_parts: list[str] = []
        for value, encoding in decode_header(raw_value):
            if isinstance(value, bytes):
                decoded_parts.append(value.decode(encoding or "utf-8", errors="replace"))
            else:
                decoded_parts.append(value)
        return "".join(decoded_parts).strip()

    @staticmethod
    def _parse_received_at(raw_date: str | None) -> datetime:
        if not raw_date:
            return datetime.now(UTC).replace(tzinfo=None)

        try:
            parsed = parsedate_to_datetime(raw_date)
        except (TypeError, ValueError, IndexError):
            return datetime.now(UTC).replace(tzinfo=None)

        if parsed.tzinfo is None:
            return parsed
        return parsed.astimezone(UTC).replace(tzinfo=None)

    @staticmethod
    def _sanitize_filename(filename: str) -> str:
        normalized = _FILENAME_SANITIZER.sub("_", filename.strip())
        if not normalized:
            return "attachment.pdf"
        if normalized.lower().endswith(".pdf"):
            return normalized
        return f"{normalized}.pdf"

    @staticmethod
    def _normalize_for_matching(value: str) -> str:
        return " ".join(value.lower().replace("ё", "е").split())

    @staticmethod
    def _normalize_person_name(value: str) -> str:
        return " ".join(value.lower().replace("ё", "е").split())

    @staticmethod
    def _extract_family_name_from_filename(filename: str) -> str | None:
        match = _FILENAME_FAMILY_NAME_RE.search(filename)
        if not match:
            return None

        family_name = match.group("family").replace("-", " ").strip()
        if not family_name:
            return None

        return " ".join(part[:1].upper() + part[1:].lower() for part in family_name.split())
