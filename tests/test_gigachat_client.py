from __future__ import annotations

import json
import unittest
from types import SimpleNamespace

import httpx

from app.ai.client import GigaChatClient, RecommendationRequest


class GigaChatClientTests(unittest.IsolatedAsyncioTestCase):
    async def test_generates_recommendation_with_disclaimer(self) -> None:
        requests: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            if request.url.path == "/api/v2/oauth":
                return httpx.Response(
                    200,
                    json={"access_token": "test-token", "expires_at": 1},
                )
            if request.url.path == "/api/v1/chat/completions":
                return httpx.Response(
                    200,
                    json={
                        "choices": [
                            {
                                "message": {
                                    "content": "- Нужна очная консультация при жалобах."
                                }
                            }
                        ]
                    },
                )
            return httpx.Response(404, json={"error": "unexpected path"})

        transport = httpx.MockTransport(handler)
        settings = SimpleNamespace(
            gigachat_model="GigaChat",
            gigachat_timeout_seconds=30.0,
            gigachat_authorization_key="test-basic-key",
            gigachat_auth_url="https://ngw.devices.sberbank.ru:9443/api/v2/oauth",
            gigachat_scope="GIGACHAT_API_PERS",
            gigachat_base_url="https://gigachat.devices.sberbank.ru/api/v1",
        )

        async with httpx.AsyncClient(transport=transport) as http_client:
            client = GigaChatClient(settings, http_client=http_client)
            result = await client.generate_recommendation(
                RecommendationRequest(
                    patient_name="Иванова Анна",
                    report_type="Биохимия",
                    report_summary="АЛТ 18, АСТ 20, билирубин 10.",
                )
            )

        self.assertIn("Важно: совет ИИ не является диагнозом", result)
        self.assertEqual(len(requests), 2)
        self.assertEqual(requests[0].headers["Authorization"], "Basic test-basic-key")
        self.assertEqual(requests[1].headers["Authorization"], "Bearer test-token")

        payload = json.loads(requests[1].content.decode("utf-8"))
        self.assertEqual(payload["model"], "GigaChat")
        self.assertEqual(payload["messages"][0]["role"], "system")
        self.assertIn("Не ставь диагноз", payload["messages"][0]["content"])
        self.assertIn("АЛТ 18, АСТ 20", payload["messages"][1]["content"])


if __name__ == "__main__":
    unittest.main()
