"""Ollama client wrappers."""

from __future__ import annotations

import httpx

from app.config import get_settings


class OllamaClient:
    def __init__(self) -> None:
        self.settings = get_settings()

    async def generate(self, *, system_prompt: str, user_prompt: str, temperature: float = 0.0) -> str:
        payload = {
            "model": self.settings.ollama_chat_model,
            "stream": False,
            "options": {
                "temperature": temperature,
                "num_predict": 128,
                "num_ctx": 2048,
            },
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        }
        endpoint = self.settings.ollama_base_url.rstrip("/") + "/api/chat"
        async with httpx.AsyncClient(timeout=300.0) as client:
            response = await client.post(endpoint, json=payload)
            response.raise_for_status()
            data = response.json()

        message = data.get("message") or {}
        return str(message.get("content") or "").strip()
