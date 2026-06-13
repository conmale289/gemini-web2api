"""Account pool with anti-ban rotation algorithm.

Strategies to avoid detection:
1. Token bucket rate limiting per account (burst-tolerant)
2. Minimum cooldown between consecutive uses of same account
3. Weighted random selection based on health score (not predictable round-robin)
4. Graduated warmup after backoff recovery
5. Exponential backoff with jitter on failures
6. Soft health scoring from sliding window success rate
"""
import json
import os
import random
import threading
import time
from collections import deque
from typing import Dict, List, Optional, Tuple

from .logging import log


# ─── Configuration ─────────────────────────────────────────────────────────────

MIN_COOLDOWN_SEC = 3.0        # Minimum seconds between uses of same account
TOKEN_BUCKET_MAX = 5          # Max burst tokens per account
TOKEN_REFILL_RATE = 1 / 60.0  # Tokens per second (1 per minute)
HEALTH_WINDOW_SEC = 300       # Sliding window for health tracking (5 min)
HEALTH_WINDOW_MAX = 20        # Max events to track per window
BACKOFF_BASE = 2.0            # Base for exponential backoff
BACKOFF_MAX = 300             # Max backoff seconds (5 min)
BACKOFF_JITTER = 0.3          # Jitter factor (±30%)
WARMUP_REQUESTS = 3           # Requests before account is at full capacity after recovery


def _extract_sapisid(cookie_str: str) -> Optional[str]:
    pairs = dict(p.split("=", 1) for p in cookie_str.split("; ") if "=" in p)
    return pairs.get("SAPISID") or None


def mask_account_id(account_id: str) -> str:
    if not account_id:
        return "unknown"
    s = str(account_id)
    if len(s) <= 4:
        return "*" * len(s)
    return f"{s[:2]}***{s[-2:]}"


class _AccountState:
    """Per-account state tracking."""
    __slots__ = (
        "failures", "backoff_until", "last_used", "tokens",
        "last_refill", "events", "warmup_remaining",
    )

    def __init__(self):
        self.failures = 0
        self.backoff_until = 0.0
        self.last_used = 0.0
        self.tokens = float(TOKEN_BUCKET_MAX)
        self.last_refill = time.time()
        self.events: deque = deque(maxlen=HEALTH_WINDOW_MAX)  # (timestamp, success:bool)
        self.warmup_remaining = 0

    def refill_tokens(self, now: float):
        """Refill tokens based on elapsed time."""
        elapsed = now - self.last_refill
        self.tokens = min(TOKEN_BUCKET_MAX, self.tokens + elapsed * TOKEN_REFILL_RATE)
        self.last_refill = now

    def consume_token(self) -> bool:
        """Try to consume a token. Returns False if empty."""
        if self.tokens >= 1.0:
            self.tokens -= 1.0
            return True
        return False

    def health_score(self, now: float) -> float:
        """Calculate health score 0.0-1.0 based on recent success rate and rest time."""
        if not self.events:
            return 1.0
        # Filter events within window
        cutoff = now - HEALTH_WINDOW_SEC
        recent = [(t, s) for t, s in self.events if t > cutoff]
        if not recent:
            return 1.0
        success_count = sum(1 for _, s in recent if s)
        success_rate = success_count / len(recent)
        # Boost score for accounts that have been resting
        rest_bonus = min(0.2, (now - self.last_used) / 600.0)  # Up to 0.2 bonus for 10min rest
        return min(1.0, success_rate * 0.8 + 0.2 + rest_bonus)

    def cooldown_remaining(self, now: float) -> float:
        """Seconds remaining in cooldown."""
        return max(0.0, (self.last_used + MIN_COOLDOWN_SEC) - now)


class AccountPool:
    """Thread-safe account selector with anti-ban rotation."""

    def __init__(self, config: dict):
        self._config = config
        self._lock = threading.Lock()
        self._cookie_cache: Dict[str, dict] = {}
        self._state: Dict[str, _AccountState] = {}

    def _load_cookie_file(self, cookie_file: str) -> Tuple[str, Optional[str]]:
        if not cookie_file or not os.path.exists(cookie_file):
            return "", None
        try:
            mtime = os.path.getmtime(cookie_file)
            cache = self._cookie_cache.get(cookie_file)
            if cache and cache.get("mtime") == mtime:
                return cache["cookie"], cache["sapisid"]
            with open(cookie_file, "r") as f:
                content = f.read().strip()
            if content.startswith("{"):
                data = json.loads(content)
                cookie = data.get("cookie", "")
                sapisid = data.get("sapisid") or _extract_sapisid(cookie)
            else:
                cookie = content
                sapisid = _extract_sapisid(cookie)
            self._cookie_cache[cookie_file] = {"mtime": mtime, "cookie": cookie, "sapisid": sapisid}
            return cookie, sapisid
        except Exception:
            cache = self._cookie_cache.get(cookie_file, {})
            return cache.get("cookie", ""), cache.get("sapisid")

    @staticmethod
    def _weight(raw: dict) -> int:
        try:
            return max(1, int(raw.get("weight", 1)))
        except (TypeError, ValueError):
            return 1

    def _normalize_account(self, raw: dict, fallback_id: str) -> Optional[dict]:
        if not isinstance(raw, dict):
            return None
        if raw.get("enabled", True) is False:
            return None
        cookie = raw.get("cookie", "")
        sapisid = raw.get("sapisid")
        cookie_file = raw.get("cookie_file")
        if cookie_file:
            cookie, loaded_sapisid = self._load_cookie_file(cookie_file)
            sapisid = sapisid or loaded_sapisid
        elif cookie and not sapisid:
            sapisid = _extract_sapisid(cookie)
        account_id = str(raw.get("id") or fallback_id)
        return {
            "id": account_id,
            "cookie": cookie or "",
            "sapisid": sapisid,
            "auth_user": raw.get("auth_user"),
            "xsrf_token": raw.get("xsrf_token"),
            "weight": self._weight(raw),
        }

    def _legacy_account(self) -> dict:
        cookie = ""
        sapisid = None
        cookie_file = self._config.get("cookie_file")
        if cookie_file:
            cookie, sapisid = self._load_cookie_file(cookie_file)
        return {
            "id": "legacy",
            "cookie": cookie,
            "sapisid": sapisid,
            "auth_user": self._config.get("auth_user"),
            "xsrf_token": self._config.get("xsrf_token"),
            "weight": 1,
        }

    def _accounts(self) -> List[dict]:
        raw_accounts = self._config.get("accounts") or []
        accounts = []
        for i, raw in enumerate(raw_accounts):
            acct = self._normalize_account(raw, fallback_id=f"acct-{i}")
            if acct:
                accounts.append(acct)
        if not accounts:
            accounts = [self._legacy_account()]
        return accounts

    def _get_state(self, account_id: str) -> _AccountState:
        if account_id not in self._state:
            self._state[account_id] = _AccountState()
        return self._state[account_id]

    def enabled_account_count(self) -> int:
        with self._lock:
            return len(self._accounts())

    def next_account(self) -> dict:
        """Select best account using weighted random based on health score.

        Selection algorithm:
        1. Filter accounts not in hard backoff
        2. Filter accounts that have cooldown elapsed
        3. Filter accounts with available tokens
        4. Score remaining by health_score * weight
        5. Weighted random selection from scored candidates
        """
        with self._lock:
            now = time.time()
            accounts = self._accounts()

            # Score each account
            scored = []
            fallback = []
            for acct in accounts:
                state = self._get_state(acct["id"])
                state.refill_tokens(now)

                # Hard backoff — skip entirely
                if state.backoff_until > now:
                    fallback.append((acct, 0.01))  # tiny weight for fallback
                    continue

                # Cooldown — prefer others but don't hard-exclude
                cooldown = state.cooldown_remaining(now)
                cooldown_penalty = 0.3 if cooldown > 0 else 1.0

                # Token bucket — prefer accounts with tokens
                token_penalty = 1.0 if state.tokens >= 1.0 else 0.2

                # Warmup penalty — recently recovered accounts get fewer requests
                warmup_penalty = 1.0
                if state.warmup_remaining > 0:
                    warmup_penalty = 0.5

                # Compute final score
                health = state.health_score(now)
                score = health * acct["weight"] * cooldown_penalty * token_penalty * warmup_penalty
                scored.append((acct, score))

            candidates = scored if scored else fallback
            if not candidates:
                log("Account pool empty; falling back to anonymous", level="WARNING")
                return {"id": "anonymous", "cookie": "", "sapisid": None, "auth_user": None, "xsrf_token": None}

            # Weighted random selection
            total_score = sum(s for _, s in candidates)
            if total_score <= 0:
                selected = random.choice(candidates)[0]
            else:
                r = random.uniform(0, total_score)
                cumulative = 0
                selected = candidates[-1][0]
                for acct, score in candidates:
                    cumulative += score
                    if r <= cumulative:
                        selected = acct
                        break

            # Update state
            state = self._get_state(selected["id"])
            state.consume_token()
            state.last_used = now

            return dict(selected)

    def report_success(self, account: dict):
        """Record successful request. Gradually heals account."""
        if not account:
            return
        with self._lock:
            state = self._get_state(account["id"])
            state.events.append((time.time(), True))
            # Gradual recovery: don't reset failures instantly
            if state.failures > 0:
                state.failures = max(0, state.failures - 1)
            if state.warmup_remaining > 0:
                state.warmup_remaining -= 1
            # Only clear backoff after consistent success
            if state.failures == 0:
                state.backoff_until = 0

    def report_failure(self, account: dict) -> float:
        """Record failure with exponential backoff + jitter."""
        if not account:
            return 0
        with self._lock:
            state = self._get_state(account["id"])
            state.failures += 1
            state.events.append((time.time(), False))

            # Exponential backoff with jitter
            exponent = min(state.failures, 8)
            base_backoff = min(BACKOFF_MAX, BACKOFF_BASE ** exponent)
            jitter = base_backoff * BACKOFF_JITTER * (2 * random.random() - 1)
            backoff = base_backoff + jitter

            state.backoff_until = time.time() + backoff
            # After recovery, require warmup
            state.warmup_remaining = WARMUP_REQUESTS

            log(f"Account {mask_account_id(account['id'])}: failure #{state.failures}, "
                f"backoff={backoff:.1f}s, health={state.health_score(time.time()):.2f}",
                level="DEBUG")
            return backoff
