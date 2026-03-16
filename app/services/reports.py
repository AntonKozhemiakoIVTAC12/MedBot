from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from sqlalchemy import Select, desc, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import load_only, selectinload

from app.db.models import AIRecommendation, Attachment, FamilyMember, LabReport, ReportType


_CATEGORY_LABELS: dict[str, str] = {
    "blood": "Анализы крови",
    "urine": "Анализы мочи",
    "biochemistry": "Биохимия",
    "hormones": "Гормоны",
    "other": "Прочее",
}
_CATEGORY_TYPES: dict[str, tuple[ReportType, ...]] = {
    "blood": (ReportType.BLOOD,),
    "urine": (ReportType.URINE,),
    "biochemistry": (ReportType.BIOCHEMISTRY,),
    "hormones": (ReportType.HORMONES,),
    "other": (ReportType.OTHER, ReportType.UNKNOWN),
}
_CATEGORY_ORDER: tuple[str, ...] = tuple(_CATEGORY_LABELS)
_TITLE_KEYS: tuple[str, ...] = (
    "title",
    "analysis_name",
    "report_name",
    "panel_name",
    "summary",
)
_SUMMARY_KEYS: tuple[str, ...] = (
    "summary",
    "short_summary",
    "conclusion",
    "notes",
)


@dataclass(slots=True)
class FamilyMemberItem:
    member_id: int
    display_name: str
    full_name: str


@dataclass(slots=True)
class ReportCategoryItem:
    key: str
    label: str
    report_count: int


@dataclass(slots=True)
class ReportListItem:
    report_id: int
    title: str
    subtitle: str


@dataclass(slots=True)
class ReportCard:
    report_id: int
    family_member_id: int | None
    family_member_name: str
    category_key: str
    category_label: str
    title: str
    report_date_text: str
    patient_name: str
    summary: str
    pdf_path: str | None
    pdf_filename: str | None
    ai_recommendation: str | None


@dataclass(slots=True)
class ServiceOverview:
    family_members: int
    reports: int


class ReportService:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def list_family_members(self) -> list[FamilyMemberItem]:
        async with self._session_factory() as session:
            result = await session.execute(
                select(FamilyMember).order_by(FamilyMember.display_name, FamilyMember.full_name)
            )
            members = result.scalars().all()

        return [
            FamilyMemberItem(
                member_id=member.id,
                display_name=member.display_name,
                full_name=member.full_name,
            )
            for member in members
        ]

    async def get_family_member(self, member_id: int) -> FamilyMemberItem | None:
        async with self._session_factory() as session:
            member = await session.get(FamilyMember, member_id)

        if member is None:
            return None

        return FamilyMemberItem(
            member_id=member.id,
            display_name=member.display_name,
            full_name=member.full_name,
        )

    async def list_categories_for_member(self, member_id: int) -> list[ReportCategoryItem]:
        async with self._session_factory() as session:
            result = await session.execute(
                select(LabReport.report_type, func.count(LabReport.id))
                .where(LabReport.family_member_id == member_id)
                .group_by(LabReport.report_type)
            )
            counts_by_type = {
                report_type: report_count
                for report_type, report_count in result.all()
            }

        items: list[ReportCategoryItem] = []
        for key in _CATEGORY_ORDER:
            report_count = sum(counts_by_type.get(report_type, 0) for report_type in _CATEGORY_TYPES[key])
            items.append(
                ReportCategoryItem(
                    key=key,
                    label=_CATEGORY_LABELS[key],
                    report_count=report_count,
                )
            )

        return items

    async def list_reports_for_member(
        self,
        member_id: int,
        category_key: str,
    ) -> list[ReportListItem]:
        statement = (
            self._build_reports_statement()
            .where(LabReport.family_member_id == member_id)
            .where(self._build_category_filter(category_key))
            .limit(50)
        )
        reports = await self._fetch_reports(statement)
        return [self._to_report_list_item(report) for report in reports]

    async def list_recent_reports(self, limit: int = 10) -> list[ReportListItem]:
        reports = await self._fetch_reports(self._build_reports_statement().limit(limit))
        return [self._to_report_list_item(report) for report in reports]

    async def get_report_card(self, report_id: int) -> ReportCard | None:
        async with self._session_factory() as session:
            result = await session.execute(
                select(LabReport)
                .options(
                    selectinload(LabReport.attachment),
                    selectinload(LabReport.family_member),
                    selectinload(LabReport.ai_recommendations),
                )
                .where(LabReport.id == report_id)
            )
            report = result.scalar_one_or_none()

        if report is None:
            return None

        pdf_path = report.attachment.storage_path if report.attachment else None
        pdf_filename = report.attachment.filename if report.attachment else None

        return ReportCard(
            report_id=report.id,
            family_member_id=report.family_member_id,
            family_member_name=report.family_member.display_name if report.family_member else "Не определен",
            category_key=self.get_category_key(report.report_type),
            category_label=self.get_category_label(report.report_type),
            title=self._build_title(report),
            report_date_text=self._format_date(report.report_date),
            patient_name=report.recognized_patient_name or "Не распознано",
            summary=self._build_summary(report.summary_json, report.source_text),
            pdf_path=pdf_path,
            pdf_filename=pdf_filename,
            ai_recommendation=self._get_latest_recommendation(report.ai_recommendations),
        )

    async def get_overview(self) -> ServiceOverview:
        async with self._session_factory() as session:
            family_members = await session.scalar(select(func.count(FamilyMember.id)))
            reports = await session.scalar(select(func.count(LabReport.id)))

        return ServiceOverview(
            family_members=int(family_members or 0),
            reports=int(reports or 0),
        )

    @staticmethod
    def get_category_label(report_type: ReportType) -> str:
        return _CATEGORY_LABELS[ReportService.get_category_key(report_type)]

    @staticmethod
    def get_category_title(category_key: str) -> str:
        return _CATEGORY_LABELS.get(category_key, _CATEGORY_LABELS["other"])

    @staticmethod
    def get_category_key(report_type: ReportType) -> str:
        if report_type == ReportType.BLOOD:
            return "blood"
        if report_type == ReportType.URINE:
            return "urine"
        if report_type == ReportType.BIOCHEMISTRY:
            return "biochemistry"
        if report_type == ReportType.HORMONES:
            return "hormones"
        return "other"

    async def _fetch_reports(self, statement: Select[tuple[LabReport]]) -> list[LabReport]:
        async with self._session_factory() as session:
            result = await session.execute(statement)
            return result.scalars().all()

    @staticmethod
    def _build_reports_statement() -> Select[tuple[LabReport]]:
        return (
            select(LabReport)
            .options(
                load_only(
                    LabReport.id,
                    LabReport.attachment_id,
                    LabReport.family_member_id,
                    LabReport.report_type,
                    LabReport.report_date,
                    LabReport.recognized_patient_name,
                    LabReport.summary_json,
                    LabReport.created_at,
                ),
                selectinload(LabReport.attachment).load_only(
                    Attachment.filename,
                    Attachment.storage_path,
                ),
                selectinload(LabReport.family_member).load_only(
                    FamilyMember.display_name,
                    FamilyMember.full_name,
                ),
            )
            .order_by(
                desc(LabReport.report_date).nullslast(),
                desc(LabReport.created_at),
                desc(LabReport.id),
            )
        )

    @staticmethod
    def _build_category_filter(category_key: str):
        report_types = _CATEGORY_TYPES.get(category_key, _CATEGORY_TYPES["other"])
        if len(report_types) == 1:
            return LabReport.report_type == report_types[0]
        return or_(*(LabReport.report_type == report_type for report_type in report_types))

    def _to_report_list_item(self, report: LabReport) -> ReportListItem:
        return ReportListItem(
            report_id=report.id,
            title=self._build_title(report),
            subtitle=self._build_subtitle(report),
        )

    def _build_title(self, report: LabReport) -> str:
        if isinstance(report.summary_json, dict):
            for key in _TITLE_KEYS:
                value = report.summary_json.get(key)
                if isinstance(value, str) and value.strip():
                    return self._clean_text(value, 80)

        if report.attachment and report.attachment.filename:
            return self._clean_text(Path(report.attachment.filename).stem.replace("_", " "), 80)

        return f"{self.get_category_label(report.report_type)} #{report.id}"

    def _build_subtitle(self, report: LabReport) -> str:
        parts = [self._format_date(report.report_date)]
        if report.family_member and report.family_member.display_name:
            parts.append(report.family_member.display_name)
        if report.recognized_patient_name:
            parts.append(report.recognized_patient_name)
        return " | ".join(parts)

    def _build_summary(self, summary_json: dict[str, object] | None, source_text: str) -> str:
        lines: list[str] = []
        if isinstance(summary_json, dict):
            for key in _SUMMARY_KEYS:
                value = summary_json.get(key)
                if isinstance(value, str) and value.strip():
                    lines.append(self._clean_text(value, 800))

            if not lines:
                for key, value in summary_json.items():
                    if value is None:
                        continue
                    lines.append(f"{self._pretty_key(key)}: {self._clean_text(str(value), 120)}")
                    if len(lines) >= 5:
                        break

        if not lines:
            compact_source = "\n".join(line.strip() for line in source_text.splitlines() if line.strip())
            if compact_source:
                return self._clean_text(compact_source, 800)
            return "Сводка пока недоступна."

        return "\n".join(lines)

    @staticmethod
    def _get_latest_recommendation(recommendations: list[AIRecommendation]) -> str | None:
        if not recommendations:
            return None

        latest = max(recommendations, key=lambda item: item.created_at)
        return latest.recommendation_text.strip() or None

    @staticmethod
    def _format_date(value: datetime | None) -> str:
        return value.strftime("%d.%m.%Y") if value is not None else "Дата не указана"

    @staticmethod
    def _pretty_key(key: str) -> str:
        return key.replace("_", " ").strip().capitalize()

    @staticmethod
    def _clean_text(value: str, limit: int) -> str:
        compact = " ".join(value.split())
        if len(compact) <= limit:
            return compact
        return f"{compact[: limit - 1].rstrip()}…"
