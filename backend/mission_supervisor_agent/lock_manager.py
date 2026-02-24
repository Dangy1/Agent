import threading
from typing import Dict, List, Tuple


class InMemoryLockManager:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._owners: Dict[str, str] = {}

    def acquire_many(self, owner: str, keys: List[str]) -> Tuple[bool, List[str]]:
        with self._lock:
            busy = [k for k in keys if k in self._owners and self._owners[k] != owner]
            if busy:
                return False, busy
            for k in keys:
                self._owners[k] = owner
            return True, []

    def release_owner(self, owner: str) -> None:
        with self._lock:
            for k in [k for k, v in self._owners.items() if v == owner]:
                self._owners.pop(k, None)

    def snapshot(self) -> Dict[str, str]:
        with self._lock:
            return dict(self._owners)


LOCK_MANAGER = InMemoryLockManager()

