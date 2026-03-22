"""Baseline schema — all ArgusV tables

Revision ID: 0001_baseline
Revises:
Create Date: 2026-03-20

Idempotent: skips CREATE TABLE for tables that already exist so it is
safe to run against a database that was bootstrapped via create_all().
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB

revision = "0001_baseline"
down_revision = None
branch_labels = None
depends_on = None


def _existing_tables(conn):
    return set(sa.inspect(conn).get_table_names())


def upgrade() -> None:
    conn = op.get_bind()
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    existing = _existing_tables(conn)

    # ── zones (no FK deps) ────────────────────────────────────────────────────
    if "zones" not in existing:
        op.create_table(
            "zones",
            sa.Column("zone_id", UUID(as_uuid=True), primary_key=True),
            sa.Column("name", sa.String(), nullable=False),
            sa.Column("polygon_coords", JSONB(), nullable=False),
            sa.Column("zone_type", sa.String(), nullable=True),
            sa.Column("dwell_threshold_sec", sa.Integer(), nullable=True),
            sa.Column("active", sa.Boolean(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.Column("updated_at", sa.DateTime(), nullable=True),
        )

    # ── cameras ───────────────────────────────────────────────────────────────
    if "cameras" not in existing:
        op.create_table(
            "cameras",
            sa.Column("camera_id", sa.String(), primary_key=True),
            sa.Column("name", sa.String(), nullable=False),
            sa.Column("rtsp_url", sa.String(), nullable=True),
            sa.Column("zone_id", UUID(as_uuid=True), sa.ForeignKey("zones.zone_id"), nullable=True),
            sa.Column("status", sa.String(), nullable=True),
            sa.Column("resolution", sa.String(), nullable=True),
            sa.Column("fps", sa.Integer(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.Column("last_seen", sa.DateTime(), nullable=True),
        )

    # ── segments ──────────────────────────────────────────────────────────────
    if "segments" not in existing:
        op.create_table(
            "segments",
            sa.Column("segment_id", UUID(as_uuid=True), primary_key=True),
            sa.Column("camera_id", sa.String(), sa.ForeignKey("cameras.camera_id"), nullable=False),
            sa.Column("start_time", sa.DateTime(), nullable=False),
            sa.Column("end_time", sa.DateTime(), nullable=False),
            sa.Column("duration_sec", sa.Float(), nullable=False),
            sa.Column("minio_path", sa.String(), nullable=False),
            sa.Column("size_bytes", sa.BigInteger(), nullable=True),
            sa.Column("has_motion", sa.Boolean(), nullable=True),
            sa.Column("has_detections", sa.Boolean(), nullable=True),
            sa.Column("detection_count", sa.Integer(), nullable=True),
            sa.Column("retain_until", sa.DateTime(), nullable=True),
            sa.Column("locked", sa.Boolean(), nullable=True),
        )
        op.create_index("ix_segments_camera_start", "segments", ["camera_id", "start_time"])
        op.create_index("ix_segments_start", "segments", ["start_time"])

    # ── rules ─────────────────────────────────────────────────────────────────
    if "rules" not in existing:
        op.create_table(
            "rules",
            sa.Column("rule_id", UUID(as_uuid=True), primary_key=True),
            sa.Column("zone_id", UUID(as_uuid=True), sa.ForeignKey("zones.zone_id"), nullable=True),
            sa.Column("trigger_type", sa.String(), nullable=True),
            sa.Column("severity", sa.String(), nullable=True),
            sa.Column("action_config", JSONB(), nullable=True),
            sa.Column("is_active", sa.Boolean(), nullable=True),
        )

    # ── incidents ─────────────────────────────────────────────────────────────
    if "incidents" not in existing:
        op.create_table(
            "incidents",
            sa.Column("incident_id", UUID(as_uuid=True), primary_key=True),
            sa.Column("camera_id", sa.String(), sa.ForeignKey("cameras.camera_id"), nullable=True),
            sa.Column("zone_id", UUID(as_uuid=True), sa.ForeignKey("zones.zone_id"), nullable=True),
            sa.Column("zone_name", sa.String(), nullable=True),
            sa.Column("object_class", sa.String(), nullable=True),
            sa.Column("threat_level", sa.String(), nullable=True),
            sa.Column("summary", sa.Text(), nullable=True),
            sa.Column("status", sa.String(), nullable=True),
            sa.Column("detected_at", sa.DateTime(), nullable=True),
            sa.Column("resolved_at", sa.DateTime(), nullable=True),
            sa.Column("metadata_json", JSONB(), nullable=True),
        )

    # ── detections ────────────────────────────────────────────────────────────
    if "detections" not in existing:
        op.execute("""
            CREATE TABLE detections (
                detection_id  UUID PRIMARY KEY,
                event_id      TEXT        NOT NULL,
                camera_id     TEXT        NOT NULL REFERENCES cameras(camera_id),
                segment_id    UUID                 REFERENCES segments(segment_id),
                incident_id   UUID                 REFERENCES incidents(incident_id),
                detected_at   TIMESTAMP   NOT NULL,
                object_class  TEXT        NOT NULL,
                confidence    FLOAT       NOT NULL,
                zone_id       TEXT,
                zone_name     TEXT,
                event_type    TEXT,
                track_id      INTEGER,
                dwell_sec     FLOAT,
                bbox_x1       FLOAT,
                bbox_y1       FLOAT,
                bbox_x2       FLOAT,
                bbox_y2       FLOAT,
                frame_url     TEXT,
                thumbnail_url TEXT,
                is_threat     BOOLEAN,
                threat_level  TEXT,
                vlm_summary   TEXT,
                vlm_embedding vector(1536)
            )
        """)
        op.create_index("ix_detections_camera_ts", "detections", ["camera_id", "detected_at"])
        op.create_index("ix_detections_event_id",  "detections", ["event_id"])
        op.create_index("ix_detections_segment",   "detections", ["segment_id"])
        op.create_index("ix_detections_incident",  "detections", ["incident_id"])

    # ── notification_rules ────────────────────────────────────────────────────
    if "notification_rules" not in existing:
        op.create_table(
            "notification_rules",
            sa.Column("id", UUID(as_uuid=True), primary_key=True),
            sa.Column("zone_id", sa.String(), nullable=True),
            sa.Column("severity", sa.String(), nullable=True),
            sa.Column("channels", JSONB(), nullable=True),
            sa.Column("config", JSONB(), nullable=True),
        )

    # ── rag_configs ───────────────────────────────────────────────────────────
    if "rag_configs" not in existing:
        op.create_table(
            "rag_configs",
            sa.Column("key", sa.String(), primary_key=True),
            sa.Column("value", sa.Text(), nullable=True),
            sa.Column("group", sa.String(), nullable=True),
        )

    # ── user_accounts ─────────────────────────────────────────────────────────
    if "user_accounts" not in existing:
        op.create_table(
            "user_accounts",
            sa.Column("user_id", UUID(as_uuid=True), primary_key=True),
            sa.Column("username", sa.String(), nullable=False),
            sa.Column("password_hash", sa.String(), nullable=False),
            sa.Column("role", sa.String(), nullable=False),
            sa.Column("is_active", sa.Boolean(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=True),
        )
        op.create_index("ix_user_accounts_username", "user_accounts", ["username"], unique=True)


def downgrade() -> None:
    op.drop_table("user_accounts")
    op.drop_table("rag_configs")
    op.drop_table("notification_rules")
    op.drop_table("detections")
    op.drop_table("incidents")
    op.drop_table("rules")
    op.drop_table("segments")
    op.drop_table("cameras")
    op.drop_table("zones")
