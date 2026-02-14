import os
import json
import redis
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from contextlib import contextmanager

# 1. Database Connection
POSTGRES_URL = os.getenv("POSTGRES_URL", "postgresql://argus:password@postgres:5432/argus_db")
engine = create_engine(POSTGRES_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

@contextmanager
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# 2. Redis Connection
REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")
redis_client = redis.from_url(REDIS_URL, decode_responses=True)

class ConfigManager:
    """Handles the synchronization between Postgres (Truth) and Redis (Cache)."""
    
    @staticmethod
    def sync_zone_to_redis(zone_id: str, zone_data: dict):
        """
        Writes zone config to Redis for fast access by other services.
        Key: `config:zone:{zone_id}`
        """
        redis_key = f"config:zone:{zone_id}"
        redis_client.set(redis_key, json.dumps(zone_data))
        
        # Publish update event
        redis_client.publish("config-updates", json.dumps({
            "type": "ZONE_UPDATE",
            "id": str(zone_id),
            "timestamp": "now" # In real app, use ISO string
        }))
        print(f"[ConfigManager] Synced Zone {zone_id} to Redis.")

    @staticmethod
    def sync_rule_to_redis(rule_id: str, rule_data: dict):
        """
        Writes rule config to Redis.
        Key: `config:rule:{rule_id}`
        """
        redis_key = f"config:rule:{rule_id}"
        redis_client.set(redis_key, json.dumps(rule_data))
        redis_client.publish("config-updates", json.dumps({
            "type": "RULE_UPDATE",
            "id": str(rule_id)
        }))
