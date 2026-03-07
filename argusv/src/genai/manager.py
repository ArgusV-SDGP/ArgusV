"""
genai/manager.py — GenAI provider manager
------------------------------------------
Frigate equivalent: frigate/genai/manager.py

Manages multiple VLM provider backends.
ArgusV currently only has OpenAI; this adds
Gemini, Ollama, LlamaCpp as alternatives.
"""

import logging
import os
from typing import Optional, Protocol

logger = logging.getLogger("genai.manager")

GENAI_PROVIDER = os.getenv("GENAI_PROVIDER", "openai")  # openai | gemini | ollama | llamacpp | disabled


class GenAIProvider(Protocol):
    async def describe(self, event: dict) -> dict:
        """Return {"threat_level":..., "is_threat":..., "summary":...}"""
        ...


class OpenAIProvider:
    """Current default — GPT-4o triage + full analysis."""

    async def describe(self, event: dict) -> dict:
        # Already implemented in pipeline_worker._call_openai()
        # TODO: extract that logic here for clean separation
        from workers.pipeline_worker import _call_openai
        return await _call_openai(event)


class GeminiProvider:
    """
    Frigate equivalent: frigate/genai/gemini.py
    TODO VLM-05: implement Google Gemini Pro Vision
    """
    async def describe(self, event: dict) -> dict:
        # TODO: use google-generativeai library
        raise NotImplementedError("TODO VLM-05: Gemini provider")


class OllamaProvider:
    """
    Frigate equivalent: frigate/genai/ollama.py
    Local VLM via Ollama (LLaVA, BakLLaVA, etc.)
    TODO VLM-05: implement for air-gapped deployments
    """
    async def describe(self, event: dict) -> dict:
        # TODO: POST to http://ollama:11434/api/generate
        raise NotImplementedError("TODO VLM-05: Ollama provider")


class LlamaCppProvider:
    """
    Frigate equivalent: frigate/genai/llama_cpp.py
    Edge-deployed llama.cpp with vision model.
    TODO VLM-05
    """
    async def describe(self, event: dict) -> dict:
        raise NotImplementedError("TODO VLM-05: LlamaCpp provider")


class DisabledProvider:
    async def describe(self, event: dict) -> dict:
        return {"threat_level": "UNKNOWN", "is_threat": False,
                "summary": "GenAI disabled (no provider configured)"}


def get_provider() -> GenAIProvider:
    """Factory — returns configured provider."""
    providers = {
        "openai":   OpenAIProvider,
        "gemini":   GeminiProvider,
        "ollama":   OllamaProvider,
        "llamacpp": LlamaCppProvider,
        "disabled": DisabledProvider,
    }
    cls = providers.get(GENAI_PROVIDER, DisabledProvider)
    logger.info(f"[GenAI] Using provider: {GENAI_PROVIDER}")
    return cls()


# Global provider instance
provider: GenAIProvider = get_provider()
