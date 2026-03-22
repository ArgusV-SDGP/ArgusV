"""
One-time script: backfill real Gemini descriptions + embeddings + thumbnails
for segments that are missing any of them.

Run from:  cd argusv && python backfill_descriptions.py
"""
import sys, asyncio, subprocess, tempfile, json, base64
import httpx
from pathlib import Path
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

sys.path.insert(0, "src")
import config as cfg
from db.models import Segment as _Seg

KEY   = cfg.GEMINI_API_KEY
MODEL = cfg.GEMINI_VISION_MODEL
BASE  = "https://generativelanguage.googleapis.com/v1beta"
UP    = "https://generativelanguage.googleapis.com/upload/v1beta"
PLACEHOLDER = "Motion detected (No VLM analysis)"

engine = create_engine("postgresql://argus:password@localhost:5434/argus_db_new")
BASE_DIR = Path(__file__).parent  # argusv/

# ── Frame extraction ──────────────────────────────────────────────────────────
def extract_frames(seg_path: str, n: int = 4) -> tuple[list[bytes], float]:
    duration = float(cfg.SEGMENT_DURATION_SEC)
    try:
        probe = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json", seg_path],
            capture_output=True, text=True, timeout=10,
        )
        if probe.returncode == 0:
            duration = float(json.loads(probe.stdout).get("format", {}).get("duration", duration))
    except Exception:
        pass

    margin = max(0.2, duration * 0.05)
    usable = duration - 2 * margin
    seek_times = [margin + i * usable / (n - 1) for i in range(n)] if n > 1 else [duration / 2]

    frames: list[bytes] = []
    
    for seek in seek_times:    
        tmp = Path(tempfile.mktemp(suffix=".jpg"))
        try:
            r = subprocess.run(
                ["ffmpeg", "-y", "-i", seg_path, "-ss", f"{seek:.3f}",
                 "-frames:v", "1", "-vf", "scale=854:-1", str(tmp)],
                capture_output=True, timeout=15,
            )
            if r.returncode == 0 and tmp.exists() and tmp.stat().st_size > 0:
                frames.append(tmp.read_bytes())
        
        except Exception:
            pass
        
        finally:
            if tmp.exists():
                tmp.unlink()

    return frames, duration


# ── Gemini description ────────────────────────────────────────────────────────

async def describe_with_gemini(seg_path: str, duration: float) -> str | None:
    tmp_mp4 = Path(tempfile.mktemp(suffix=".mp4"))
    try:
        r = subprocess.run(
            ["ffmpeg", "-y", "-i", seg_path, "-c", "copy", "-movflags", "+faststart", str(tmp_mp4)],
            capture_output=True, timeout=30,
        )

        if r.returncode != 0 or not tmp_mp4.exists():
            return None
        video = tmp_mp4.read_bytes()
    finally:
        if tmp_mp4.exists():
            tmp_mp4.unlink()

    async with httpx.AsyncClient(timeout=90) as c:
        up = await c.post(f"{UP}/files", params={"key": KEY, "uploadType": "media"},
                          headers={"Content-Type": "video/mp4"}, content=video)
        fn = up.json().get("file", {}).get("name")
        if not fn:
            return None

        file_uri = None
        for _ in range(20):
            await asyncio.sleep(2)
            pr = await c.get(f"{BASE}/{fn}", params={"key": KEY})
            pd = pr.json()
            if pd.get("state") == "ACTIVE":
                file_uri = pd.get("uri")
                break
            if pd.get("state") == "FAILED":
                break

        await c.delete(f"{BASE}/{fn}", params={"key": KEY})

        if not file_uri:
            return None

        gr = await c.post(
            f"{BASE}/models/{MODEL}:generateContent",
            params={"key": KEY},
            json={"contents": [{"parts": [
                {"text": f"You are reviewing a {duration:.0f}-second security camera clip. "
                         "Describe what happens in 2-3 sentences: who/what is present, "
                         "what activity occurs, anything noteworthy for security. Be factual."},
                {"file_data": {"mime_type": "video/mp4", "file_uri": file_uri}},
            ]}]},
            timeout=45,
        )
        return (gr.json().get("candidates", [{}])[0]
                .get("content", {}).get("parts", [{}])[0]
                .get("text", "").strip()) or None


# ── OpenAI embed ──────────────────────────────────────────────────────────────

async def embed_text(text: str) -> list[float] | None:
    if not cfg.OPENAI_API_KEY or not text:
        return None
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.post(
            "https://api.openai.com/v1/embeddings",
            headers={"Authorization": f"Bearer {cfg.OPENAI_API_KEY}"},
            json={"model": "text-embedding-3-small", "input": text},
        )
        if r.is_success:
            return r.json()["data"][0]["embedding"]
    return None


# ── Main ──────────────────────────────────────────────────────────────────────

async def main():
    with engine.connect() as conn:
        rows = conn.execute(text(
            "SELECT segment_id, camera_id, minio_path, description, "
            "       description_embedding IS NULL as needs_embed, "
            "       thumbnail_url IS NULL as needs_thumb "
            "FROM segments "
            "WHERE description IS NULL OR description = :p "
            "   OR description_embedding IS NULL "
            "   OR thumbnail_url IS NULL "
            "ORDER BY start_time DESC"
        ), {"p": PLACEHOLDER}).fetchall()

    print(f"Segments to process: {len(rows)}")
    if not rows:
        print("Nothing to do.")
        return

    for seg_id, cam_id, path_rel, desc, needs_embed, needs_thumb in rows:
        print(f"\n{str(seg_id)[:8]}  {path_rel}")
        full = BASE_DIR / path_rel

        # Extract frames (always needed for thumbnail)
        frames, duration = [], float(cfg.SEGMENT_DURATION_SEC)
        if full.exists():
            frames, duration = extract_frames(str(full))
            print(f"  frames={len(frames)}, duration={duration:.1f}s")
        else:
            print(f"  File not on disk")


        # Description
        if not desc or desc == PLACEHOLDER:
            if full.exists():
                desc = await describe_with_gemini(str(full), duration)
                print(f"  desc: {desc[:80] if desc else 'FAILED'}")
            else:
                print(f"  desc: skipped (no file)")


        if not desc:
            continue


        # Embedding
        embedding = None
        if needs_embed:
            embedding = await embed_text(desc)

            print(f"  embed: {'OK' if embedding else 'FAILED'}")

        # Thumbnail
        
        thumbnail_url = None
        if needs_thumb and frames:
            mid = frames[len(frames) // 2]
            thumb_dir = Path(cfg.LOCAL_RECORDINGS_DIR) / cam_id / "thumbnails"
            thumb_dir.mkdir(parents=True, exist_ok=True)
            thumb_file = thumb_dir / f"{seg_id}.jpg"
            thumb_file.write_bytes(mid)
            thumbnail_url = f"/recordings/{cam_id}/thumbnails/{seg_id}.jpg"
            print(f"  thumb: {thumbnail_url}")


        # Save to DB — use ORM so pgvector handles the vector column correctly
        
        with Session(engine) as db:
            seg_row = db.get(_Seg, seg_id)
            if seg_row:
                seg_row.description = desc
                if embedding:
                    seg_row.description_embedding = embedding
                if thumbnail_url:
                    seg_row.thumbnail_url = thumbnail_url
                db.commit()

        print(f"  saved.")
        
        await asyncio.sleep(0.5)

asyncio.run(main())
