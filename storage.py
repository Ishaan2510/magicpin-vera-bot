"""
Thread-safe in-memory context store for all four context scopes.
"""

import threading
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
        # suppression_keys: merchant_id -> set of suppression keys
        self._suppression: Dict[str, set] = {}
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
            return suppression_key in self._suppression.get(merchant_id, set())

    def add_suppression(self, merchant_id: str, suppression_key: str):
        with self._lock:
            if merchant_id not in self._suppression:
                self._suppression[merchant_id] = set()
            self._suppression[merchant_id].add(suppression_key)

    def get_conversation(self, conversation_id: str) -> list:
        with self._lock:
            return list(self._conversations.get(conversation_id, []))

    def set_conversation(self, conversation_id: str, history: list):
        with self._lock:
            self._conversations[conversation_id] = history
