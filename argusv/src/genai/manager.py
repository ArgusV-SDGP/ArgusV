"""
genai/manager.py — GenAI provider manager
------------------------------------------
Supports: openai | gemini | ollama | llamacpp | disabled

Set GENAI_PROVIDER env var to select. Each provider implements:
    async def describe(event: dict) -> dict
and returns:
    {"threat_level": "HIGH|MEDIUM|LOW", "is_threat": bool, "summary": str}
"""

import asyncio
import json
import logging
import re
from typing import Optional, Protocol

import httpx

import config as cfg

logger = logging.getLogger("genai.manager")

# ── Shared helpers ────────────────────────────────────────────────────────────

_THREAT_RUBRIC = (
    "Threat rubric (natural language definitions): "
    "HIGH = immediate danger or active crime (weapon visible, violence, forced entry, fire/smoke, person collapsed, "
    "clear emergency). "
    "MEDIUM = suspicious behavior that may require intervention (loitering in restricted zone, repeated boundary testing, "
    "tailgating, attempted intrusion, unusual after-hours presence). "
    "LOW = routine or benign activity with no immediate risk (normal walking, authorized presence, ordinary traffic)."
)

_FULL_PROMPT_TMPL = (
    "You are a security analyst examining a single surveillance camera frame. "
    "Context: {object_class} detected in zone '{zone_name}' (dwell: {dwell_sec}s, trigger: {event_type}). "
    f"{_THREAT_RUBRIC} "
    "Describe only what is directly visible in this frame — do not speculate about events before or after. "
    "Respond with ONLY valid JSON (no markdown): "
    '{{"threat_level":"HIGH|MEDIUM|LOW","is_threat":true|false,'
    '"summary":"<one sentence: who/what is visible and where>","recommended_action":"ALERT|MONITOR|IGNORE"}}'
)

_TRIAGE_PROMPT_TMPL = (
    "Security camera frame: {object_class} detected in zone '{zone_name}'. "
    "Trigger: {event_type}. Dwell: {dwell_sec}s. "
    f"{_THREAT_RUBRIC} "
    "Is this worth a full analysis? Reply ONE word: YES or NO."
)


def _build_prompts(event: dict) -> tuple[str, str]:
    ctx = {
        "object_class": event.get("object_class", "unknown"),
        "zone_name":    event.get("zone_name", "unknown"),
        "dwell_sec":    event.get("dwell_sec", 0),
        "event_type":   event.get("event_type", "DETECTED"),
    }
    return _TRIAGE_PROMPT_TMPL.format(**ctx), _FULL_PROMPT_TMPL.format(**ctx)


async def _load_prompt(key: str, default: str) -> str:
    """
    Load a prompt string from Redis cache (TTL=60s) → RagConfig DB → fallback default.
    Keys stored in RagConfig with group='prompts' and value=json.dumps("<string>").
    """
    redis_key = f"argus:prompt:{key}"
    try:
        import redis.asyncio as aioredis
        r = aioredis.from_url(cfg.REDIS_URL, decode_responses=True)
        try:
            cached = await r.get(redis_key)
            if cached:
                return cached
        finally:
            await r.aclose()

        def _db_lookup() -> Optional[str]:
            from db.connection import get_db_sync
            from db.models import RagConfig
            db = get_db_sync()
            try:
                row = db.query(RagConfig).filter(
                    RagConfig.key == key,
                    RagConfig.group == "prompts"
                ).first()
                if row and row.value:
                    v = json.loads(row.value)
                    return v if isinstance(v, str) else None
                return None
            finally:
                db.close()

        value = await asyncio.to_thread(_db_lookup)
        if value:
            r2 = aioredis.from_url(cfg.REDIS_URL, decode_responses=True)
            try:
                await r2.setex(redis_key, 60, value)
            finally:
                await r2.aclose()
            return value
    except Exception as e:
        logger.debug(f"[Prompts] _load_prompt({key}) unavailable: {e}")
    return default


async def _build_prompts_async(event: dict) -> tuple[str, str]:
    """Async version of _build_prompts — loads templates from DB/Redis with 60s cache."""
    triage_tmpl = await _load_prompt("vlm.triage_prompt", _TRIAGE_PROMPT_TMPL)
    full_tmpl   = await _load_prompt("vlm.analysis_prompt", _FULL_PROMPT_TMPL)
    ctx = {
        "object_class": event.get("object_class", "unknown"),
        "zone_name":    event.get("zone_name", "unknown"),
        "dwell_sec":    event.get("dwell_sec", 0),
        "event_type":   event.get("event_type", "DETECTED"),
    }
    try:
        return triage_tmpl.format(**ctx), full_tmpl.format(**ctx)
    except KeyError:
        return _TRIAGE_PROMPT_TMPL.format(**ctx), _FULL_PROMPT_TMPL.format(**ctx)


def _normalize_threat_payload(payload: dict) -> dict:
    """Coerce provider output into a consistent threat schema."""
    allowed_levels = {"HIGH", "MEDIUM", "LOW"}
    raw_level = str(payload.get("threat_level", "")).strip().upper()
    summary = str(payload.get("summary", "")).strip()

    if raw_level not in allowed_levels:
        s = summary.lower()
        if any(k in s for k in ("weapon", "gun", "knife", "fire", "smoke", "fight", "bleeding", "collapsed", "forced entry", "break in", "break-in")):
            raw_level = "HIGH"
        elif any(k in s for k in ("loiter", "suspicious", "tailgating", "intrusion", "restricted", "unauthorized", "after-hours", "after hours", "boundary")):
            raw_level = "MEDIUM"
        else:
            raw_level = "LOW"

    is_threat_raw = payload.get("is_threat")
    if isinstance(is_threat_raw, bool):
        is_threat = is_threat_raw
    elif isinstance(is_threat_raw, str):
        is_threat = is_threat_raw.strip().lower() in {"1", "true", "yes", "y"}
    else:
        is_threat = raw_level in {"HIGH", "MEDIUM"}

    action = str(payload.get("recommended_action", "")).strip().upper()
    if action not in {"ALERT", "MONITOR", "IGNORE"}:
        action = "ALERT" if raw_level == "HIGH" else ("MONITOR" if raw_level == "MEDIUM" else "IGNORE")

    if not summary:
        summary = "Scene analyzed with limited details"

    return {
        "threat_level": raw_level,
        "is_threat": is_threat,
        "summary": summary[:200],
        "recommended_action": action,
    }


def _parse_vlm_json(text: str) -> dict:
    """Extract and parse the JSON object from raw LLM output."""
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if m:
        try:
            return _normalize_threat_payload(json.loads(m.group()))
        except json.JSONDecodeError:
            pass
    return _normalize_threat_payload({"threat_level": "MEDIUM", "is_threat": True, "summary": text[:200].strip()})


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

        triage_prompt, full_prompt = await _build_prompts_async(event)
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
        _, full_prompt = await _build_prompts_async(event)
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
        _, full_prompt = await _build_prompts_async(event)
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


# ── Text Embeddings ───────────────────────────────────────────────────────────

async def embed_text(text: str) -> Optional[list[float]]:
    """
    Embed a text string using OpenAI text-embedding-3-small (1536 dims).
    Returns None if no API key or request fails — Detection is still saved without embedding.
    """
    if not cfg.OPENAI_API_KEY or not text:
        return None
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                "https://api.openai.com/v1/embeddings",
                headers={"Authorization": f"Bearer {cfg.OPENAI_API_KEY}"},
                json={"model": cfg.EMBEDDING_MODEL, "input": text.strip()},
            )
            resp.raise_for_status()
            return resp.json()["data"][0]["embedding"]
    except Exception as e:
        logger.warning(f"[Embeddings] embed_text failed: {e}")
        return None
