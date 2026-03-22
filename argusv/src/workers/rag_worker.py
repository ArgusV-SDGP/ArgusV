"""
workers/rag_worker.py — RAG indexing queue drain
-------------------------------------------------
Embeddings are now generated inline in pipeline_worker._process_decision
(text-embedding-3-small via OpenAI, stored directly into Detection.vlm_embedding).

This worker simply drains bus.rag_indexing so the queue never fills up.
"""

import asyncio
import logging

from bus import bus

logger = logging.getLogger("rag-worker")


async def rag_semantic_worker():
    logger.info("[RAG Worker] Started — embeddings handled inline by pipeline worker")
    while True:
        try:
            await bus.rag_indexing.get()
            bus.rag_indexing.task_done()
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"[RAG Worker] Error: {e}")
            await asyncio.sleep(1)
