"""Async client untuk agentmemory REST API (port 3111).

Episodic memory layer untuk Anisa. Server jalan via npx -y @agentmemory/agentmemory
dengan iii-engine native binary di ~/.local/bin/iii.

Semua fungsi safe-default: kalau server mati atau timeout, return string kosong /
False — bot tetap jalan pakai fallback SQLite (memory/memory_engine.py).
"""
import asyncio
import logging
import os

import httpx

logger = logging.getLogger('bima_core')

BASE_URL = os.environ.get("AGENTMEMORY_URL", "http://127.0.0.1:3111")
RECALL_TIMEOUT_S = 0.8
SAVE_TIMEOUT_S = 3.0
HEALTH_TIMEOUT_S = 1.5


async def recall(query: str, limit: int = 5) -> str:
    """Cari konteks relevan di agentmemory. Return formatted text untuk diinject
    ke prompt manager. String kosong kalau gagal/timeout."""
    if not query:
        return ""
    try:
        async with httpx.AsyncClient(timeout=RECALL_TIMEOUT_S) as client:
            r = await client.post(
                f"{BASE_URL}/agentmemory/search",
                json={"query": query, "limit": limit},
            )
            r.raise_for_status()
            data = r.json()
    except (httpx.TimeoutException, httpx.HTTPError, asyncio.TimeoutError) as e:
        logger.debug(f"[agentmemory] recall gagal: {e}")
        return ""
    except Exception as e:
        logger.warning(f"[agentmemory] recall error unexpected: {e}")
        return ""

    results = data.get("results", []) if isinstance(data, dict) else []
    if not results:
        return ""

    lines = []
    for r in results[:limit]:
        obs = r.get("observation", {}) if isinstance(r, dict) else {}
        score = r.get("score", 0.0)
        narrative = obs.get("narrative") or obs.get("title") or ""
        if narrative:
            lines.append(f"- [score {score:.2f}] {narrative.strip()}")
    return "\n".join(lines)


async def save(user_request: str, assistant_reply: str) -> None:
    """Simpan turn ke agentmemory (fire-and-forget). Tidak raise kalau gagal."""
    content = f"User: {user_request}\nAnisa: {assistant_reply}".strip()
    if len(content) < 5:
        return
    try:
        async with httpx.AsyncClient(timeout=SAVE_TIMEOUT_S) as client:
            r = await client.post(
                f"{BASE_URL}/agentmemory/remember",
                json={"content": content},
            )
            r.raise_for_status()
    except (httpx.TimeoutException, httpx.HTTPError, asyncio.TimeoutError) as e:
        logger.debug(f"[agentmemory] save gagal: {e}")
    except Exception as e:
        logger.warning(f"[agentmemory] save error unexpected: {e}")


async def health() -> bool:
    """Cek server hidup. True = ready, False = down/timeout."""
    try:
        async with httpx.AsyncClient(timeout=HEALTH_TIMEOUT_S) as client:
            r = await client.get(f"{BASE_URL}/agentmemory/health")
            r.raise_for_status()
            data = r.json()
            return bool(data.get("status") == "healthy")
    except Exception:
        return False
