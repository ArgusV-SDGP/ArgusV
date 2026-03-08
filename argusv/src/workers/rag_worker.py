"""
workers/rag_worker.py — VLM Text to pgvector Embeddings
---------------------------------------------------------
Consumes completed actionable event summaries from the database and 
uses the sentence-transformer to convert them into a 384-dimensional pgvector.
"""

import asyncio
import logging
from db.connection import get_db_session
from db.models import Detection
from sqlalchemy.future import select
from bus import bus
from embeddings.embeddings import vector_db

logger = logging.getLogger("rag")

async def rag_semantic_worker():
    """
    Listens for 'rag_indexing' events (triggered at the end of an incident lock).
    Filters for actionable events, explicitly generates an embedding vector from the 
    VLM text, and stores it into the pgvector column natively.
    """
    logger.info("🟢 [RAG Worker] Started — waiting to embed actionable video evidence...")
    
    while True:
        try:
            # 1. Wait for an incident to finish
            payload = await bus.rag_indexing.get()
            event_id = payload.get("event_id")

            if not event_id:
                bus.rag_indexing.task_done()
                continue
                
            # Allow a tiny delay just in case the DB transaction from VLM isn't fully flushed yet
            await asyncio.sleep(2)

            async with get_db_session() as db:
                stmt = select(Detection).where(Detection.event_id == event_id)
                res = await db.execute(stmt)
                det = res.scalars().first()
                
                # Check 1: Ensure it exists and has summary
                if not det or not det.vlm_summary:
                    bus.rag_indexing.task_done()
                    continue

                # Check 2: ONLY embed actionable items (Threats or high-confidence loitering)
                is_actionable = det.is_threat is True or det.threat_level in ["HIGH", "MEDIUM"]
                
                if not is_actionable:
                    logger.debug(f"[RAG Worker] Event {event_id} is not actionable (Threat: {det.threat_level}). Skipping embedding to save limits.")
                    bus.rag_indexing.task_done()
                    continue

                # 3. Create Vector embedding Array using HuggingFace
                vector_arr = await vector_db.embed_text(det.vlm_summary)
                
                if vector_arr:
                    det.vlm_embedding = vector_arr
                    await db.commit()
                    logger.info(f"[VectorDB] Embedded and saved native pgvector for {event_id} ({det.object_class}/{det.threat_level})")
                else:
                    logger.error(f"[VectorDB] Failed to generate embedding array for event {event_id}")

            bus.rag_indexing.task_done()

        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"[RAG Worker] Loop error: {e}", exc_info=True)
            await asyncio.sleep(5)
