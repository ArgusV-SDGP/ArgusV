"""Helpers to upload frames to the argus-frames bucket via MinIO."""

import io
import os
from typing import Optional
from urllib.parse import quote

from minio import Minio
from minio.error import S3Error

MINIO_ENDPOINT = os.getenv("MINIO_ENDPOINT", "127.0.0.1:9000")
MINIO_ACCESS_KEY = os.getenv("MINIO_ACCESS_KEY", "minioadmin")
MINIO_SECRET_KEY = os.getenv("MINIO_SECRET_KEY", "minioadmin")
MINIO_SECURE = os.getenv("MINIO_SECURE", "false").lower() in ("1", "true", "yes")
MINIO_BUCKET = os.getenv("MINIO_BUCKET", "argus-frames")
MINIO_REGION = os.getenv("MINIO_REGION")
MINIO_PUBLIC_URL = os.getenv("MINIO_PUBLIC_URL", f"http{'s' if MINIO_SECURE else ''}://{MINIO_ENDPOINT}")

_client = Minio(
    MINIO_ENDPOINT,
    access_key=MINIO_ACCESS_KEY,
    secret_key=MINIO_SECRET_KEY,
    secure=MINIO_SECURE,
    region=MINIO_REGION,
)
_bucket_ready = False


def _ensure_bucket():
    """Create the bucket if it does not already exist."""
    global _bucket_ready
    if _bucket_ready:
        return
    try:
        if not _client.bucket_exists(MINIO_BUCKET):
            _client.make_bucket(MINIO_BUCKET)
        _bucket_ready = True
    except S3Error as exc:
        print(f"[storage] Failed to prepare bucket {MINIO_BUCKET}: {exc}")


def _build_public_url(object_name: str) -> str:
    base = MINIO_PUBLIC_URL.rstrip("/")
    object_path = object_name.lstrip("/")
    safe_object_path = quote(object_path, safe="/")
    return f"{base}/{MINIO_BUCKET}/{safe_object_path}"


def upload_frame(frame_bytes: bytes, object_name: str) -> Optional[str]:
    """Upload frame bytes to MinIO and return a URL for the stored object."""
    if not frame_bytes:
        print("[storage] upload skipped because frame_bytes is empty")
        return None

    _ensure_bucket()
    try:
        data = io.BytesIO(frame_bytes)
        data.seek(0)
        _client.put_object(
            MINIO_BUCKET,
            object_name,
            data,
            len(frame_bytes),
        )
        return _build_public_url(object_name)
    except S3Error as exc:
        print(f"[storage] Error uploading {object_name}: {exc}")
    except Exception as exc:  # pragma: no cover - best-effort guard
        print(f"[storage] Unexpected error uploading {object_name}: {exc}")
    return None
