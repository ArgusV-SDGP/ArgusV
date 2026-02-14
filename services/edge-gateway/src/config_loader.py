import os
import json
import redis
import threading
import time

# Edge Gateway might run on limited hardware, so keep it efficient.
REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")

class EdgeConfig:
    def __init__(self):
        try:
            self.redis = redis.from_url(REDIS_URL, decode_responses=True)
            self.active_rules = {}
            self.running = True
            
            # Start background updater
            self.updater_thread = threading.Thread(target=self._poll_updates, daemon=True)
            self.updater_thread.start()
        except Exception as e:
            print(f"[EdgeConfig] Failed to connect to Redis: {e}")
            self.redis = None

    def get_rules_for_zone(self, zone_id):
        return self.active_rules.get(zone_id, {})

    def _poll_updates(self):
        """
        Simple polling or Pub/Sub implementation.
        """
        if not self.redis:
            return

        pubsub = self.redis.pubsub()
        pubsub.subscribe("config-updates")
        
        print("[EdgeConfig] Listening for updates on 'config-updates'...")
        
        for message in pubsub.listen():
            if message["type"] == "message":
                print(f"[EdgeConfig] Received Update: {message['data']}")
                # Here you would re-fetch the specific rule from Redis
                # rule_id = json.loads(message['data'])['id']
                # self.active_rules[rule_id] = self.redis.get(f"config:rule:{rule_id}")

# Singleton instance
config = EdgeConfig()
