from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import JSON, DateTime, Enum, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


def utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class AttachmentParseStatus(StrEnum):
    PENDING = "pending"
    PROCESSING = "processing"
    PARSED = "parsed"
    FAILED = "failed"
    NEEDS_MANUAL_REVIEW = "needs_manual_review"


class ReportType(StrEnum):
    BLOOD = "blood"
    URINE = "urine"
    BIOCHEMISTRY = "biochemistry"
    HORMONES = "hormones"
    OTHER = "other"
    UNKNOWN = "unknown"


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(),
        default=utcnow,
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(),
        default=utcnow,
        onupdate=utcnow,
        nullable=False,
    )


class FamilyMember(TimestampMixin, Base):
    __tablename__ = "family_members"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    full_name: Mapped[str] = mapped_column(String(255), nullable=False)
    normalized_name: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)

    lab_reports: Mapped[list["LabReport"]] = relationship(
        back_populates="family_member",
    )


class EmailMessage(TimestampMixin, Base):
    __tablename__ = "emails"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    message_uid: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    sender: Mapped[str] = mapped_column(String(255), nullable=False)
    subject: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    received_at: Mapped[datetime] = mapped_column(DateTime(), nullable=False)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(), nullable=True)

    attachments: Mapped[list["Attachment"]] = relationship(
        back_populates="email",
        cascade="all, delete-orphan",
    )


class Attachment(TimestampMixin, Base):
    __tablename__ = "attachments"
    __table_args__ = (
        UniqueConstraint("email_id", "checksum", name="uq_attachment_email_checksum"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    email_id: Mapped[int] = mapped_column(ForeignKey("emails.id"), nullable=False)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    storage_path: Mapped[str] = mapped_column(String(500), nullable=False)
    checksum: Mapped[str] = mapped_column(String(128), nullable=False)
    parse_status: Mapped[AttachmentParseStatus] = mapped_column(
        Enum(AttachmentParseStatus, native_enum=False),
        default=AttachmentParseStatus.PENDING,
        nullable=False,
    )

    email: Mapped["EmailMessage"] = relationship(back_populates="attachments")
    lab_report: Mapped["LabReport | None"] = relationship(
        back_populates="attachment",
        cascade="all, delete-orphan",
        uselist=False,
    )


class LabReport(TimestampMixin, Base):
    __tablename__ = "lab_reports"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    attachment_id: Mapped[int] = mapped_column(
        ForeignKey("attachments.id"),
        nullable=False,
        unique=True,
    )
    family_member_id: Mapped[int | None] = mapped_column(
        ForeignKey("family_members.id"),
        nullable=True,
    )
    report_type: Mapped[ReportType] = mapped_column(
        Enum(ReportType, native_enum=False),
        default=ReportType.UNKNOWN,
        nullable=False,
    )
    report_date: Mapped[datetime | None] = mapped_column(DateTime(), nullable=True)
    recognized_patient_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    source_text: Mapped[str] = mapped_column(Text(), nullable=False, default="")
    summary_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)

    attachment: Mapped["Attachment"] = relationship(back_populates="lab_report")
    family_member: Mapped["FamilyMember | None"] = relationship(back_populates="lab_reports")
    ai_recommendations: Mapped[list["AIRecommendation"]] = relationship(
        back_populates="lab_report",
        cascade="all, delete-orphan",
    )


class AIRecommendation(Base):
    __tablename__ = "ai_recommendations"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    lab_report_id: Mapped[int] = mapped_column(
        ForeignKey("lab_reports.id"),
        nullable=False,
    )
    model_name: Mapped[str] = mapped_column(String(255), nullable=False)
    recommendation_text: Mapped[str] = mapped_column(Text(), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(), default=utcnow, nullable=False)

    lab_report: Mapped["LabReport"] = relationship(back_populates="ai_recommendations")
