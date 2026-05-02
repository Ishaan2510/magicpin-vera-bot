"""
Thread-safe in-memory context store for all four context scopes.
"""

import threading
import time
from typing import Any, Dict, Optional


class ContextStore:
    def __init__(self):
        self._lock = threading.Lock()
        self._store: Dict[str, Dict[str, Any]] = {
            "category": {},
            "merchant": {},
            "customer": {},
            "trigger": {},
        }
        # suppression_keys: merchant_id -> suppression_key -> expiry timestamp
        self._suppression: Dict[str, Dict[str, float]] = {}
        # conversation_history: conv_id -> list of turns
        self._conversations: Dict[str, list] = {}

    def upsert(self, scope: str, context_id: str, version: int, payload: Any) -> bool:
        """
        Store context. Idempotent: same version is a no-op.
        Higher version replaces atomically.
        Returns True if stored, False if skipped (same or lower version).
        """
        with self._lock:
            existing = self._store[scope].get(context_id)
            if existing and existing.get("version", 0) >= version:
                return False  # no-op
            self._store[scope][context_id] = {
                "version": version,
                "payload": payload,
            }
            return True

    def get(self, scope: str, context_id: str) -> Optional[Dict]:
        with self._lock:
            return self._store[scope].get(context_id)

    def get_all(self, scope: str) -> Dict[str, Any]:
        with self._lock:
            return dict(self._store[scope])

    def context_counts(self) -> Dict[str, int]:
        with self._lock:
            return {scope: len(items) for scope, items in self._store.items()}

    def is_suppressed(self, merchant_id: str, suppression_key: str) -> bool:
        with self._lock:
            now = time.time()
            keys = self._suppression.get(merchant_id, {})
            expiry = keys.get(suppression_key, 0)
            return expiry > now

    def add_suppression(self, merchant_id: str, suppression_key: str, ttl_seconds: int = 7200):
        with self._lock:
            if merchant_id not in self._suppression:
                self._suppression[merchant_id] = {}
            self._suppression[merchant_id][suppression_key] = time.time() + ttl_seconds

    def get_conversation(self, conversation_id: str) -> list:
        with self._lock:
            return list(self._conversations.get(conversation_id, []))

    def set_conversation(self, conversation_id: str, history: list):
        with self._lock:
            self._conversations[conversation_id] = history
