"""Account pool for multi-cookie round-robin routing."""
import json
import logging
import os
import threading
import time
from typing import Dict, List, Optional, Tuple


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


class AccountPool:
    """Thread-safe account selector with round-robin and backoff."""

    def __init__(self, config: dict):
        self._config = config
        self._lock = threading.Lock()
        self._rr_index = 0
        self._cookie_cache: Dict[str, dict] = {}
        self._state: Dict[str, dict] = {}

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

    def enabled_account_count(self) -> int:
        with self._lock:
            return len(self._accounts())

    def next_account(self) -> dict:
        with self._lock:
            now = time.time()
            accounts = self._accounts()
            eligible = []
            fallback = []
            for acct in accounts:
                state = self._state.setdefault(acct["id"], {"failures": 0, "backoff_until": 0})
                for _ in range(acct["weight"]):
                    fallback.append(acct)
                    if state["backoff_until"] <= now:
                        eligible.append(acct)
            candidates = eligible or fallback
            if not candidates:
                logging.warning("Account pool has no eligible accounts; falling back to anonymous mode")
                return {"id": "anonymous", "cookie": "", "sapisid": None, "auth_user": None, "xsrf_token": None}
            account = candidates[self._rr_index % len(candidates)]
            self._rr_index += 1
            return dict(account)

    def report_success(self, account: dict):
        if not account:
            return
        with self._lock:
            state = self._state.setdefault(account["id"], {"failures": 0, "backoff_until": 0})
            state["failures"] = 0
            state["backoff_until"] = 0

    def report_failure(self, account: dict) -> int:
        if not account:
            return 0
        with self._lock:
            state = self._state.setdefault(account["id"], {"failures": 0, "backoff_until": 0})
            state["failures"] += 1
            backoff = min(60, 2 ** (state["failures"] - 1))
            state["backoff_until"] = time.time() + backoff
            return backoff
