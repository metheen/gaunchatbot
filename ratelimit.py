#!/usr/bin/env python3
"""Bağımlılıksız, thread-safe IP-başına sliding-window rate limiter.

Kurumsal serving için (api.py): tek bir istemcinin dakikada N'den fazla istek
atmasını engeller (spam/DoS koruması). Harici bağımlılık YOK (slowapi vb.
gerektirmez) — deploy'da ekstra kurulum istemez. FastAPI threadpool ile uyumlu
(kilitli). Bellek: eski anahtarlar periyodik temizlenir.
"""

from __future__ import annotations

import threading
import time
from collections import deque


class RateLimiter:
    """max_hits / window_sec penceresinde anahtar (ör. IP) başına sınır."""

    def __init__(self, max_hits: int, window_sec: float = 60.0,
                 gc_threshold: int = 10_000) -> None:
        self.max_hits = max(1, int(max_hits))
        self.window = float(window_sec)
        self._gc_threshold = gc_threshold
        self._hits: "dict[str, deque]" = {}
        self._lock = threading.Lock()

    def allow(self, key: str, now: "float | None" = None) -> bool:
        """key için istek serbest mi? Serbestse True (ve sayar), doluysa False.
        `now` test için enjekte edilebilir (varsayılan monotonic saat)."""
        t = time.monotonic() if now is None else now
        with self._lock:
            dq = self._hits.setdefault(key, deque())
            cutoff = t - self.window
            while dq and dq[0] <= cutoff:
                dq.popleft()
            if len(dq) >= self.max_hits:
                return False
            dq.append(t)
            if len(self._hits) > self._gc_threshold:
                self._gc(t)
            return True

    def _gc(self, now: float) -> None:
        """Penceresi boşalmış/eskimiş anahtarları at (bellek sızıntısı önlemi)."""
        cutoff = now - self.window
        dead = [k for k, d in self._hits.items() if not d or d[-1] <= cutoff]
        for k in dead:
            self._hits.pop(k, None)
