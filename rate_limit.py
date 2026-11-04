#!/usr/bin/env python3
"""
rate_limit.py — Retry, backoff, and rate limiting utilities.
Zero external dependencies. Pure stdlib.

Usage:
    from rate_limit import retry_with_backoff, RateLimiter, download_with_resume

    @retry_with_backoff(max_retries=5, base_delay=1.0)
    def fetch_data():
        ...

    limiter = RateLimiter(max_requests=30, window=60)
    if limiter.allow():
        make_request()

    download_with_resume(url, dest, chunk_size=8192)
"""

import time
import random
import urllib.request
import urllib.error
from pathlib import Path
from collections import deque
from functools import wraps


# ─── RETRY WITH EXPONENTIAL BACKOFF + JITTER ─────────────────────────────────


class RateLimitError(Exception):
    """Raised when a rate limit (HTTP 429) is hit."""

    pass


def retry_with_backoff(max_retries=5, base_delay=1.0, max_delay=60.0, jitter=True):
    """Decorator: retry a function with exponential backoff on failure.

    Handles HTTP 429 (rate limit) specially by reading Retry-After header.
    """

    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            last_exc = None
            for attempt in range(max_retries):
                try:
                    return func(*args, **kwargs)
                except urllib.error.HTTPError as e:
                    if e.code == 429:
                        retry_after = _parse_retry_after(e)
                        if retry_after is not None:
                            wait = min(retry_after, max_delay)
                        else:
                            wait = _calc_delay(attempt, base_delay, max_delay, jitter)
                        _log_retry(attempt + 1, wait, "rate limit (429)")
                        time.sleep(wait)
                        last_exc = e
                        continue
                    elif e.code >= 500:
                        wait = _calc_delay(attempt, base_delay, max_delay, jitter)
                        _log_retry(attempt + 1, wait, f"server error ({e.code})")
                        time.sleep(wait)
                        last_exc = e
                        continue
                    else:
                        raise
                except (ConnectionError, TimeoutError, OSError) as e:
                    wait = _calc_delay(attempt, base_delay, max_delay, jitter)
                    _log_retry(attempt + 1, wait, "connection error")
                    time.sleep(wait)
                    last_exc = e
                    continue
            raise last_exc

        return wrapper

    return decorator


def _calc_delay(attempt, base_delay, max_delay, jitter):
    exp = min(attempt, 10)
    delay = base_delay * (2**exp)
    if jitter:
        delay += random.uniform(0, base_delay)
    return min(delay, max_delay)


def _parse_retry_after(http_error):
    header = http_error.headers.get("Retry-After")
    if header is None:
        return None
    try:
        val = int(header)
        return max(val, 1)
    except ValueError:
        return None


def _log_retry(attempt, wait, reason):
    print(f"\n  ⏳ Reintento {attempt} — esperando {wait:.1f}s ({reason})")


# ─── RATE LIMITER (TOKEN BUCKET) ─────────────────────────────────────────────


class RateLimiter:
    """Simple token bucket rate limiter.

    Args:
        max_requests: Maximum number of requests allowed in the window.
        window: Time window in seconds.
    """

    def __init__(self, max_requests=30, window=60):
        self.max_requests = max_requests
        self.window = window
        self._timestamps = deque()

    def allow(self) -> bool:
        now = time.time()
        cutoff = now - self.window
        while self._timestamps and self._timestamps[0] < cutoff:
            self._timestamps.popleft()
        if len(self._timestamps) < self.max_requests:
            self._timestamps.append(now)
            return True
        return False

    def wait_and_allow(self) -> float:
        """Block until a request is allowed. Returns wait time (0 if immediate)."""
        wait = 0.0
        while not self.allow():
            now = time.time()
            oldest = self._timestamps[0] if self._timestamps else now
            wait = max(oldest + self.window - now, 0.1)
            time.sleep(wait + 0.05)
        return wait

    def remaining(self) -> int:
        now = time.time()
        cutoff = now - self.window
        while self._timestamps and self._timestamps[0] < cutoff:
            self._timestamps.popleft()
        return max(0, self.max_requests - len(self._timestamps))

    def reset(self):
        self._timestamps.clear()


# ─── DOWNLOAD WITH RESUME + RETRY ────────────────────────────────────────────


def download_with_resume(
    url: str, dest: Path, chunk_size: int = 8192, max_retries: int = 5, progress_cb=None
) -> bool:
    """Download a file with resume support, retry on failure, and progress callback.

    Returns True on success, False on permanent failure.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)

    for attempt in range(max_retries):
        try:
            _do_download(url, dest, chunk_size, progress_cb)
            return True
        except urllib.error.HTTPError as e:
            if e.code == 416:
                return True
            if e.code == 429:
                retry_after = _parse_retry_after(e)
                wait = (
                    retry_after
                    if retry_after
                    else _calc_delay(attempt, 2.0, 120.0, True)
                )
                _log_retry(attempt + 1, wait, "rate limit (429)")
                time.sleep(wait)
                continue
            if e.code >= 500 and attempt < max_retries - 1:
                wait = _calc_delay(attempt, 2.0, 120.0, True)
                _log_retry(attempt + 1, wait, f"server error ({e.code})")
                time.sleep(wait)
                continue
            print(f"\n  ✗ Error descargando {url}: HTTP {e.code}")
            return False
        except (ConnectionError, TimeoutError, OSError) as e:
            if attempt < max_retries - 1:
                wait = _calc_delay(attempt, 2.0, 120.0, True)
                _log_retry(attempt + 1, wait, "connection error")
                time.sleep(wait)
                continue
            print(f"\n  ✗ Error descargando {url}: {e}")
            return False
    return False


def _do_download(url: str, dest: Path, chunk_size: int, progress_cb=None):
    req = urllib.request.Request(url)
    existing = dest.stat().st_size if dest.exists() else 0

    if existing > 0:
        req.add_header("Range", f"bytes={existing}-")
        mode = "ab"
    else:
        mode = "wb"

    with urllib.request.urlopen(req, timeout=300) as resp:
        total = resp.headers.get("Content-Length")
        total = int(total) + existing if total else None

        with open(dest, mode) as f:
            downloaded = existing
            while True:
                chunk = resp.read(chunk_size)
                if not chunk:
                    break
                f.write(chunk)
                downloaded += len(chunk)
                if progress_cb:
                    progress_cb(downloaded, total)


# ─── SAFE HTTP REQUEST HELPER ────────────────────────────────────────────────


@retry_with_backoff(max_retries=3, base_delay=0.5, max_delay=30.0)
def safe_urlopen(url: str, data=None, headers=None, timeout=30):
    """Make an HTTP request with automatic retry and backoff."""
    req = urllib.request.Request(url, data=data, headers=headers or {})
    return urllib.request.urlopen(req, timeout=timeout)


# ─── MODULE-LEVEL DEFAULTS ───────────────────────────────────────────────────

_default_limiter = RateLimiter(max_requests=25, window=60)


def get_limiter() -> RateLimiter:
    return _default_limiter
