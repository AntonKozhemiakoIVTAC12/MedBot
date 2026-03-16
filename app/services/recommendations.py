from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import selectinload

from app.ai.client import (
    GigaChatClient,
    GigaChatClientError,
    GigaChatConfigurationError,
    RecommendationRequest,
)
from app.db.models import AIRecommendation, LabReport
from app.services.reports import ReportService

_SUMMARY_KEYS: tuple[str, ...] = (
    "summary",
    "short_summary",
    "conclusion",
    "notes",
)


class RecommendationGenerationError(RuntimeError):
    """Raised when recommendation generation fails."""


class RecommendationService:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        gigachat_client: GigaChatClient,
    ) -> None:
        self._session_factory = session_factory
        self._gigachat_client = gigachat_client

    async def generate_for_report(self, report_id: int) -> str:
        async with self._session_factory() as session:
            result = await session.execute(
                select(LabReport)
                .options(
                    selectinload(LabReport.family_member),
                    selectinload(LabReport.attachment),
                )
                .where(LabReport.id == report_id)
            )
            report = result.scalar_one_or_none()

        if report is None:
            raise RecommendationGenerationError("Отчет не найден.")

        payload = RecommendationRequest(
            patient_name=report.recognized_patient_name
            or (report.family_member.full_name if report.family_member else "Не указано"),
            report_type=ReportService.get_category_label(report.report_type),
            report_summary=self._build_report_summary(report.summary_json, report.source_text),
        )

        try:
            recommendation_text = await self._gigachat_client.generate_recommendation(payload)
        except GigaChatConfigurationError as error:
            raise RecommendationGenerationError(str(error)) from error
        except GigaChatClientError as error:
            raise RecommendationGenerationError(
                f"Не удалось получить совет ИИ. {error}"
            ) from error

        async with self._session_factory() as session:
            session.add(
                AIRecommendation(
                    lab_report_id=report_id,
                    model_name=self._gigachat_client.model_name,
                    recommendation_text=recommendation_text,
                )
            )
            await session.commit()

        return recommendation_text

    def _build_report_summary(
        self,
        summary_json: dict[str, object] | None,
        source_text: str,
    ) -> str:
        lines: list[str] = []

        if isinstance(summary_json, dict):
            for key in _SUMMARY_KEYS:
                value = summary_json.get(key)
                if isinstance(value, str) and value.strip():
                    lines.append(self._trim(value, 800))

            if not lines:
                for key, value in summary_json.items():
                    if value is None:
                        continue
                    lines.append(f"{self._pretty_key(key)}: {self._trim(str(value), 120)}")
                    if len(lines) >= 5:
                        break

        if not lines:
            compact_source = "\n".join(
                line.strip() for line in source_text.splitlines() if line.strip()
            )
            if compact_source:
                return self._trim(compact_source, 1_200)
            return "Сводка анализа недоступна."

        return "\n".join(lines)

    @staticmethod
    def _pretty_key(key: str) -> str:
        return key.replace("_", " ").strip().capitalize()

    @staticmethod
    def _trim(value: str, limit: int) -> str:
        compact = " ".join(value.split())
        if len(compact) <= limit:
            return compact
        return f"{compact[: limit - 1].rstrip()}…"
