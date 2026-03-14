"""
genai/manager.py — GenAI provider manager
------------------------------------------
Supports: openai | gemini | ollama | llamacpp | disabled

Set GENAI_PROVIDER env var to select. Each provider implements:
    async def describe(event: dict) -> dict
and returns:
    {"threat_level": "HIGH|MEDIUM|LOW", "is_threat": bool, "summary": str}
"""

import json
import logging
import re
from typing import Optional, Protocol

import httpx

import config as cfg

logger = logging.getLogger("genai.manager")

# ── Shared helpers ────────────────────────────────────────────────────────────

_FULL_PROMPT_TMPL = (
    "You are a security analyst reviewing camera footage. "
    "A {object_class} was detected in '{zone_name}' (dwell: {dwell_sec}s, type: {event_type}). "
    "Analyse this scene. Respond with ONLY valid JSON: "
    '{"threat_level":"HIGH|MEDIUM|LOW","is_threat":true|false,'
    '"summary":"<1 sentence>","recommended_action":"ALERT|MONITOR|IGNORE"}'
)

_TRIAGE_PROMPT_TMPL = (
    "Security camera alert: {object_class} detected in '{zone_name}'. "
    "Event type: {event_type}. Dwell time: {dwell_sec}s. "
    "Is this worth escalating? Reply ONE word: YES or NO."
)


def _build_prompts(event: dict) -> tuple[str, str]:
    ctx = {
        "object_class": event.get("object_class", "unknown"),
        "zone_name":    event.get("zone_name", "unknown"),
        "dwell_sec":    event.get("dwell_sec", 0),
        "event_type":   event.get("event_type", "DETECTED"),
    }
    return _TRIAGE_PROMPT_TMPL.format(**ctx), _FULL_PROMPT_TMPL.format(**ctx)


def _parse_vlm_json(text: str) -> dict:
    """Extract and parse the JSON object from raw LLM output."""
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group())
        except json.JSONDecodeError:
            pass
    return {"threat_level": "MEDIUM", "is_threat": True, "summary": text[:200].strip()}


# ── Provider protocol ─────────────────────────────────────────────────────────

class GenAIProvider(Protocol):
    async def describe(self, event: dict) -> dict:
        """Return {"threat_level":..., "is_threat":..., "summary":...}"""
        ...


# ── OpenAI ────────────────────────────────────────────────────────────────────

class OpenAIProvider:
    """GPT-4o-mini triage → GPT-4o full analysis (default)."""

    async def describe(self, event: dict) -> dict:
        from workers.pipeline_worker import _call_openai
        return await _call_openai(event)


# ── Gemini ────────────────────────────────────────────────────────────────────

class GeminiProvider:
    """
    Google Gemini Vision via REST.
    Triage: gemini-2.0-flash (cheap/fast)
    Full:   gemini-1.5-pro   (with vision if frame available)
    """

    _BASE = "https://generativelanguage.googleapis.com/v1beta/models"

    async def describe(self, event: dict) -> dict:
        if not cfg.GEMINI_API_KEY:
            logger.warning("[Gemini] GEMINI_API_KEY not set")
            return {"threat_level": "UNKNOWN", "is_threat": False, "summary": "No Gemini API key"}

        triage_prompt, full_prompt = _build_prompts(event)
        frame_b64: Optional[str] = event.get("trigger_frame_b64")

        async with httpx.AsyncClient(timeout=30) as client:
            # ── Triage (text-only, cheap) ─────────────────────────────────
            if cfg.USE_TIERED_VLM:
                triage_resp = await client.post(
                    f"{self._BASE}/{cfg.GEMINI_MODEL}:generateContent",
                    params={"key": cfg.GEMINI_API_KEY},
                    json={"contents": [{"parts": [{"text": triage_prompt}]}]},
                )
                triage_resp.raise_for_status()
                triage_text = (
                    triage_resp.json()
                    .get("candidates", [{}])[0]
                    .get("content", {})
                    .get("parts", [{}])[0]
                    .get("text", "YES")
                    .strip()
                    .upper()
                )
                if triage_text == "NO":
                    return {"threat_level": "LOW", "is_threat": False, "summary": "Triage: not suspicious"}

            # ── Full analysis (with optional vision) ─────────────────────
            parts: list[dict] = [{"text": full_prompt}]
            if frame_b64:
                parts.append({
                    "inline_data": {
                        "mime_type": "image/jpeg",
                        "data": frame_b64,
                    }
                })

            full_resp = await client.post(
                f"{self._BASE}/{cfg.GEMINI_VISION_MODEL}:generateContent",
                params={"key": cfg.GEMINI_API_KEY},
                json={"contents": [{"parts": parts}]},
            )
            full_resp.raise_for_status()
            content = (
                full_resp.json()
                .get("candidates", [{}])[0]
                .get("content", {})
                .get("parts", [{}])[0]
                .get("text", "")
            )

        return _parse_vlm_json(content)


# ── Ollama ────────────────────────────────────────────────────────────────────

class OllamaProvider:
    """
    Local Ollama server (LLaVA or any vision-capable model).
    No tiering — single call since there's no API cost.
    """

    async def describe(self, event: dict) -> dict:
        _, full_prompt = _build_prompts(event)
        frame_b64: Optional[str] = event.get("trigger_frame_b64")

        payload: dict = {
            "model":  cfg.OLLAMA_MODEL,
            "prompt": full_prompt,
            "stream": False,
        }
        if frame_b64:
            payload["images"] = [frame_b64]

        try:
            async with httpx.AsyncClient(timeout=60) as client:
                resp = await client.post(
                    f"{cfg.OLLAMA_BASE_URL}/api/generate",
                    json=payload,
                )
                resp.raise_for_status()
                text = resp.json().get("response", "")
        except httpx.ConnectError:
            logger.error(f"[Ollama] Cannot connect to {cfg.OLLAMA_BASE_URL}")
            return {"threat_level": "UNKNOWN", "is_threat": False, "summary": "Ollama unreachable"}
        except Exception as e:
            logger.error(f"[Ollama] Error: {e}")
            return {"threat_level": "UNKNOWN", "is_threat": False, "summary": str(e)[:100]}

        return _parse_vlm_json(text)


# ── LlamaCpp ──────────────────────────────────────────────────────────────────

class LlamaCppProvider:
    """
    llama.cpp server with OpenAI-compatible API (/v1/chat/completions).
    Supports vision models (LLaVA) via image_url content parts.
    """

    async def describe(self, event: dict) -> dict:
        _, full_prompt = _build_prompts(event)
        frame_b64: Optional[str] = event.get("trigger_frame_b64")

        content: list[dict] | str
        if frame_b64:
            content = [
                {"type": "text", "text": full_prompt},
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{frame_b64}"}},
            ]
        else:
            content = full_prompt

        payload: dict = {
            "messages": [{"role": "user", "content": content}],
            "max_tokens": 200,
            "temperature": 0.1,
        }
        if cfg.LLAMACPP_MODEL:
            payload["model"] = cfg.LLAMACPP_MODEL

        try:
            async with httpx.AsyncClient(timeout=120) as client:
                resp = await client.post(
                    f"{cfg.LLAMACPP_BASE_URL}/v1/chat/completions",
                    json=payload,
                )
                resp.raise_for_status()
                text = resp.json()["choices"][0]["message"]["content"].strip()
        except httpx.ConnectError:
            logger.error(f"[LlamaCpp] Cannot connect to {cfg.LLAMACPP_BASE_URL}")
            return {"threat_level": "UNKNOWN", "is_threat": False, "summary": "LlamaCpp unreachable"}
        except Exception as e:
            logger.error(f"[LlamaCpp] Error: {e}")
            return {"threat_level": "UNKNOWN", "is_threat": False, "summary": str(e)[:100]}

        return _parse_vlm_json(text)


# ── Disabled ──────────────────────────────────────────────────────────────────

class DisabledProvider:
    async def describe(self, event: dict) -> dict:
        return {
            "threat_level": "UNKNOWN",
            "is_threat": False,
            "summary": "GenAI disabled (GENAI_PROVIDER=disabled)",
        }


# ── Factory ───────────────────────────────────────────────────────────────────

def get_provider() -> GenAIProvider:
    providers = {
        "openai":   OpenAIProvider,
        "gemini":   GeminiProvider,
        "ollama":   OllamaProvider,
        "llamacpp": LlamaCppProvider,
        "disabled": DisabledProvider,
    }
    cls = providers.get(cfg.GENAI_PROVIDER, DisabledProvider)
    if cls is DisabledProvider and cfg.GENAI_PROVIDER not in providers:
        logger.warning(f"[GenAI] Unknown provider '{cfg.GENAI_PROVIDER}', using DisabledProvider")
    logger.info(f"[GenAI] Active provider: {cfg.GENAI_PROVIDER}")
    return cls()


# Singleton — imported by pipeline_worker
provider: GenAIProvider = get_provider()
