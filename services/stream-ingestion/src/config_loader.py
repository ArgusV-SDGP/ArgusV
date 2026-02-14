import os
import redis
import json
import asyncio

REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")

class StreamConfigLoader:
    """
    Reads configuration from Redis.
    Does NOT connect to Postgres.
    """
    
    def __init__(self):
        self.redis = redis.from_url(REDIS_URL, decode_responses=True)
        self.local_cache = {}

    def get_zone_config(self, zone_id: str):
        """
        Tries local cache -> Redis -> Returns Default.
        """
        # 1. Check in-memory cache
        if zone_id in self.local_cache:
            return self.local_cache[zone_id]

        # 2. Check Redis
        redis_key = f"config:zone:{zone_id}"
        data = self.redis.get(redis_key)
        
        if data:
            config = json.loads(data)
            self.local_cache[zone_id] = config
            print(f"[ConfigLoader] Loaded config for Zone {zone_id} from Redis.")
            return config
            
        print(f"[ConfigLoader] Warning: No config found for {zone_id} in Redis.")
        return None

    def listen_for_updates(self):
        """
        Subscribes to Redis Pub/Sub for live updates.
        Run this in a background task.
        """
        pubsub = self.redis.pubsub()
        pubsub.subscribe("config-updates")
        
        print("[ConfigLoader] Listening for config updates...")
        for message in pubsub.listen():
            if message["type"] == "message":
                data = json.loads(message["data"])
                if data.get("type") == "ZONE_UPDATE":
                    zone_id = data.get("id")
                    # Invalidate local cache to force re-fetch next time
                    if zone_id in self.local_cache:
                        del self.local_cache[zone_id]
                        print(f"[ConfigLoader] Invalidated cache for Zone {zone_id}")

# Usage Example
loader = StreamConfigLoader()
# asyncio.create_task(loader.listen_for_updates())
