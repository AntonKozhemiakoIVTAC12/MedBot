from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING
from typing import Any
from uuid import uuid4

from app.config import Settings

if TYPE_CHECKING:
    import httpx

_SYSTEM_PROMPT = (
    "Ты помощник, который дает только осторожные общие комментарии по лабораторным "
    "анализам на русском языке. Не ставь диагноз, не назначай лечение, не упоминай "
    "лекарства и дозировки, не делай категоричных выводов. Используй только данные, "
    "которые переданы в сообщении. Если информации недостаточно, прямо скажи об этом. "
    "В ответе дай 1-3 коротких пункта наблюдений и при необходимости укажи, к какому "
    "врачу имеет смысл обратиться."
)
_DISCLAIMER = (
    "Важно: совет ИИ не является диагнозом и не заменяет консультацию врача."
)


@dataclass(slots=True)
class RecommendationRequest:
    patient_name: str
    report_type: str
    report_summary: str


class GigaChatClientError(RuntimeError):
    """Raised when GigaChat cannot produce a response."""


class GigaChatConfigurationError(GigaChatClientError):
    """Raised when GigaChat credentials are missing."""


def _require_httpx():
    try:
        import httpx
    except ModuleNotFoundError as error:
        raise GigaChatConfigurationError(
            "Для советов ИИ не установлена зависимость httpx."
        ) from error
    return httpx


class GigaChatClient:
    def __init__(
        self,
        settings: Settings,
        *,
        http_client: "httpx.AsyncClient | None" = None,
    ) -> None:
        self._settings = settings
        self._http_client = http_client

    @property
    def model_name(self) -> str:
        return self._settings.gigachat_model

    @property
    def is_configured(self) -> bool:
        return bool(self._settings.gigachat_authorization_key)

    async def generate_recommendation(self, payload: RecommendationRequest) -> str:
        if not self.is_configured:
            raise GigaChatConfigurationError(
                "GigaChat не настроен. Заполните GIGACHAT_AUTHORIZATION_KEY."
            )

        httpx = _require_httpx()
        managed_client = self._http_client is None
        client = self._http_client or httpx.AsyncClient(
            timeout=self._settings.gigachat_timeout_seconds
        )

        try:
            token = await self._request_access_token(client)
            response = await client.post(
                self._chat_completions_url(),
                headers={
                    "Accept": "application/json",
                    "Authorization": f"Bearer {token}",
                },
                json={
                    "model": self._settings.gigachat_model,
                    "temperature": 0.2,
                    "max_tokens": 220,
                    "messages": self._build_messages(payload),
                },
            )
            response.raise_for_status()
        except httpx.HTTPError as error:
            raise GigaChatClientError(
                f"Не удалось получить ответ от GigaChat: {self._format_http_error(error)}"
            ) from error
        finally:
            if managed_client:
                await client.aclose()

        content = self._extract_message_content(response.json())
        if not content:
            raise GigaChatClientError("GigaChat вернул пустой ответ.")
        return self._finalize_recommendation(content)

    async def _request_access_token(self, client: "httpx.AsyncClient") -> str:
        httpx = _require_httpx()
        try:
            response = await client.post(
                self._settings.gigachat_auth_url,
                headers={
                    "Accept": "application/json",
                    "Authorization": (
                        f"Basic {self._settings.gigachat_authorization_key.strip()}"
                    ),
                    "Content-Type": "application/x-www-form-urlencoded",
                    "RqUID": str(uuid4()),
                },
                data={"scope": self._settings.gigachat_scope},
            )
            response.raise_for_status()
        except httpx.HTTPError as error:
            raise GigaChatClientError(
                f"Не удалось получить токен GigaChat: {self._format_http_error(error)}"
            ) from error

        access_token = response.json().get("access_token")
        if not isinstance(access_token, str) or not access_token.strip():
            raise GigaChatClientError("GigaChat не вернул access_token.")
        return access_token

    def _build_messages(self, payload: RecommendationRequest) -> list[dict[str, str]]:
        report_summary = self._trim(payload.report_summary, 2_000)
        patient_name = payload.patient_name.strip() or "Не указано"
        report_type = payload.report_type.strip() or "Не указано"

        user_prompt = (
            "Подготовь краткий и безопасный комментарий по анализу.\n"
            f"Пациент: {patient_name}\n"
            f"Категория анализа: {report_type}\n"
            "Краткая выжимка анализа:\n"
            f"{report_summary}\n\n"
            "Формат ответа:\n"
            "1. 1-3 коротких пункта с общими наблюдениями.\n"
            "2. Если есть повод, одной строкой укажи профиль врача.\n"
            "3. Если данных мало, напиши об этом прямо.\n"
            "Не проси дополнительные персональные данные и не выходи за рамки присланной выжимки."
        )

        return [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ]

    def _chat_completions_url(self) -> str:
        base_url = self._settings.gigachat_base_url.rstrip("/")
        if not base_url.endswith("/api/v1"):
            base_url = f"{base_url}/api/v1"
        return f"{base_url}/chat/completions"

    def _extract_message_content(self, payload: dict[str, Any]) -> str:
        choices = payload.get("choices")
        if not isinstance(choices, list) or not choices:
            return ""

        message = choices[0].get("message")
        if not isinstance(message, dict):
            return ""

        content = message.get("content")
        if isinstance(content, str):
            return content
        return ""

    def _finalize_recommendation(self, content: str) -> str:
        normalized_lines = [line.strip() for line in content.replace("\r", "").split("\n")]
        normalized = "\n".join(line for line in normalized_lines if line)
        normalized = self._trim(normalized, 1_000)
        if _DISCLAIMER not in normalized:
            normalized = f"{normalized}\n\n{_DISCLAIMER}" if normalized else _DISCLAIMER
        return normalized

    @staticmethod
    def _trim(value: str, limit: int) -> str:
        compact = value.strip()
        if len(compact) <= limit:
            return compact
        return f"{compact[: limit - 1].rstrip()}…"

    @staticmethod
    def _format_http_error(error: Exception) -> str:
        httpx = _require_httpx()
        if isinstance(error, httpx.HTTPStatusError):
            try:
                details = error.response.text.strip()
            except Exception:
                details = ""
            if details:
                return f"{error.response.status_code}: {details[:300]}"
            return str(error.response.status_code)
        return str(error)
