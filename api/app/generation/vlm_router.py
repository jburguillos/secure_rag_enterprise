"""Placeholder VLM router for future GPU multimodal upgrade path."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class VLMResult:
    used_vlm: bool
    answer: str


class VLMRouter:
    """No-op CPU default; designed to be replaced by GPU VLM backend."""

    def __init__(self, enabled: bool = False) -> None:
        self.enabled = enabled

    async def maybe_route(self, prompt: str, image_paths: list[str]) -> VLMResult:
        if not self.enabled:
            return VLMResult(used_vlm=False, answer="")
        return VLMResult(used_vlm=True, answer="VLM response placeholder")
