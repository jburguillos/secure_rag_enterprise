"""Ollama client wrappers."""

from __future__ import annotations

from typing import Any

import httpx

from app.config import get_settings


class OllamaClient:
    def __init__(self) -> None:
        self.settings = get_settings()

    async def generate(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.0,
        top_p: float | None = None,
        num_predict: int = 384,
        num_ctx: int = 2048,
        model: str | None = None,
    ) -> str:
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]
        return await self.generate_from_messages(
            messages=messages,
            temperature=temperature,
            top_p=top_p,
            num_predict=num_predict,
            num_ctx=num_ctx,
            model=model,
        )

    async def generate_from_messages(
        self,
        *,
        messages: list[dict[str, Any]],
        temperature: float = 0.2,
        top_p: float | None = None,
        num_predict: int = 256,
        num_ctx: int = 2048,
        model: str | None = None,
    ) -> str:
        filtered_messages: list[dict[str, str]] = []
        for message in messages:
            content = str(message.get("content") or "").strip()
            if not content:
                continue
            role = str(message.get("role") or "user").strip().lower()
            if role not in {"system", "user", "assistant"}:
                role = "user"
            filtered_messages.append({"role": role, "content": content})

        payload = {
            "model": model or self.settings.ollama_chat_model,
            "stream": False,
            "options": {
                "temperature": temperature,
                "num_predict": num_predict,
                "num_ctx": num_ctx,
            },
            "messages": filtered_messages,
        }
        if top_p is not None:
            payload["options"]["top_p"] = top_p
        endpoint = self.settings.ollama_base_url.rstrip("/") + "/api/chat"
        async with httpx.AsyncClient(timeout=300.0) as client:
            response = await client.post(endpoint, json=payload)
            response.raise_for_status()
            data = response.json()

        message = data.get("message") or {}
        return str(message.get("content") or "").strip()
