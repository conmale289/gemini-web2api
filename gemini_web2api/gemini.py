"""Gemini StreamGenerate protocol implementation with httpx streaming."""
import json
import time
import uuid
import re
import urllib.request
import urllib.parse
import urllib.error
import ssl
import hashlib

try:
    import httpx
    HAS_HTTPX = True
except ImportError:
    HAS_HTTPX = False

from .config import CONFIG
from .account_pool import AccountPool, mask_account_id

_ssl_ctx = None
_httpx_client = None
ACCOUNT_POOL = AccountPool(CONFIG)


def log(msg: str):
    if CONFIG["log_requests"]:
        import sys
        sys.stderr.write(f"[{time.strftime('%H:%M:%S')}] {msg}\n")
        sys.stderr.flush()


def _get_ssl_ctx():
    global _ssl_ctx
    if _ssl_ctx is None:
        _ssl_ctx = ssl.create_default_context()
    return _ssl_ctx


def _get_httpx_client():
    global _httpx_client
    if _httpx_client is None and HAS_HTTPX:
        proxy = CONFIG.get("proxy")
        transport = httpx.HTTPTransport(proxy=proxy) if proxy else None
        _httpx_client = httpx.Client(transport=transport, timeout=CONFIG["request_timeout_sec"], verify=True)
    return _httpx_client


def next_account() -> dict:
    return ACCOUNT_POOL.next_account()


def load_cookie(account: dict = None) -> tuple:
    account = account or ACCOUNT_POOL.next_account()
    return account.get("cookie", ""), account.get("sapisid")


def make_sapisidhash(sapisid: str) -> str:
    ts = int(time.time())
    h = hashlib.sha1(f"{ts} {sapisid} https://gemini.google.com".encode()).hexdigest()
    return f"SAPISIDHASH {ts}_{h}"


def _account_prefix(account: dict = None) -> str:
    """Return the Gemini account path prefix for non-default Google accounts."""
    auth_user = (account or {}).get("auth_user")
    if auth_user is None:
        auth_user = CONFIG.get("auth_user")
    if auth_user is None or auth_user == "":
        return ""
    return f"/u/{auth_user}"


def _build_headers(account: dict = None) -> dict:
    account_prefix = _account_prefix(account)
    headers = {
        "Content-Type": "application/x-www-form-urlencoded",
        "Origin": "https://gemini.google.com",
        "Referer": f"https://gemini.google.com{account_prefix}/app",
        "X-Same-Domain": "1",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    }
    if account_prefix:
        auth_user = (account or {}).get("auth_user")
        if auth_user is None:
            auth_user = CONFIG.get("auth_user")
        headers["X-Goog-AuthUser"] = str(auth_user)
    cookie_str, sapisid = load_cookie(account)
    if cookie_str:
        headers["Cookie"] = cookie_str
    if sapisid:
        headers["Authorization"] = make_sapisidhash(sapisid)
    return headers


def _build_payload(prompt: str, model_id: int, think_mode: int, file_refs: list = None, extra_fields: dict = None,
                   account: dict = None) -> str:
    inner = [None] * 102
    if file_refs:
        refs = [[None, None, ref] for ref in file_refs]
        inner[0] = [prompt, 0, None, refs, None, None, 0]
    else:
        inner[0] = [prompt, 0, None, None, None, None, 0]
    inner[1] = ["en"]
    inner[2] = ["", "", "", None, None, None, None, None, None, ""]
    inner[6] = [0]
    inner[7] = 1
    inner[10] = 1
    inner[11] = 0
    inner[17] = [[think_mode]]
    inner[18] = 0
    inner[27] = 1
    inner[30] = [4]
    inner[41] = [2]
    inner[53] = 0
    inner[59] = str(uuid.uuid4())
    inner[61] = []
    inner[68] = 1
    inner[79] = model_id
    if extra_fields:
        for k, v in extra_fields.items():
            inner[k] = v
    outer = [None, json.dumps(inner)]
    params = {"f.req": json.dumps(outer)}
    xsrf_token = (account or {}).get("xsrf_token")
    if xsrf_token is None:
        xsrf_token = CONFIG.get("xsrf_token")
    if xsrf_token:
        params["at"] = xsrf_token
    return urllib.parse.urlencode(params)


def _get_url(account: dict = None) -> str:
    reqid = int(time.time()) % 1000000
    account_prefix = _account_prefix(account)
    return (
        f"https://gemini.google.com{account_prefix}/_/BardChatUi/data/"
        "assistant.lamda.BardFrontendService/StreamGenerate"
        f"?bl={CONFIG['gemini_bl']}&hl=en&_reqid={reqid}&rt=c"
    )


def _is_account_error(err: Exception) -> bool:
    if isinstance(err, urllib.error.HTTPError):
        if err.code in (400, 401, 403, 429):
            return True
        try:
            body = err.read().decode("utf-8", errors="ignore").lower()
            return any(k in body for k in ("xsrf", "auth", "unauthorized", "forbidden", "rate", "quota"))
        except Exception:
            return False
    if HAS_HTTPX and isinstance(err, httpx.HTTPStatusError):
        return err.response.status_code in (400, 401, 403, 429)
    msg = str(err).lower()
    return any(k in msg for k in ("xsrf", "auth", "unauthorized", "forbidden", "rate limit", "quota", "429"))


def _account_attempts(forced_account: dict = None) -> int:
    base = max(1, int(CONFIG["retry_attempts"]))
    if forced_account:
        return base
    attempts = base * max(1, ACCOUNT_POOL.enabled_account_count())
    cap = CONFIG.get("max_account_retry_attempts")
    if isinstance(cap, int) and cap > 0:
        attempts = min(attempts, cap)
    return max(base, attempts)


def clean_text(text: str) -> str:
    text = re.sub(
        r'```(?:python|javascript|text)\?code_(?:reference|stdout)&code_event_index=\d+\n.*?```\n?',
        '', text, flags=re.DOTALL
    )
    text = re.sub(r'http://googleusercontent\.com/card_content/\d+\n?', '', text)
    return text.strip()


def _extract_texts_from_line(line: str) -> list:
    """Parse a single wrb.fr line and return list of text strings found."""
    if '"wrb.fr"' not in line or len(line) < 200:
        return []
    try:
        arr = json.loads(line)
        inner_str = arr[0][2]
        if not inner_str or len(inner_str) < 50:
            return []
        inner = json.loads(inner_str)
        if not (isinstance(inner, list) and len(inner) > 4 and inner[4]):
            return []
        texts = []
        for part in inner[4]:
            if isinstance(part, list) and len(part) > 1 and part[1] and isinstance(part[1], list):
                for t in part[1]:
                    if isinstance(t, str) and t:
                        texts.append(t)
        return texts
    except (json.JSONDecodeError, IndexError, TypeError):
        return []


def extract_response_text(raw: str) -> str:
    """Parse full response to get final text."""
    last_text = ""
    for line in raw.split("\n"):
        for t in _extract_texts_from_line(line):
            if len(t) > len(last_text):
                last_text = t
    return clean_text(last_text)


def generate(prompt: str, model_id: int, think_mode: int, file_refs: list = None, extra_fields: dict = None,
             account: dict = None) -> str:
    """Non-streaming generation with retry."""
    ctx = _get_ssl_ctx()
    proxy = CONFIG.get("proxy")

    last_err = None
    max_attempts = _account_attempts(account)
    for attempt in range(max_attempts):
        account_ctx = account or ACCOUNT_POOL.next_account()
        body = _build_payload(prompt, model_id, think_mode, file_refs, extra_fields, account_ctx).encode()
        url = _get_url(account_ctx)
        headers = _build_headers(account_ctx)
        try:
            req = urllib.request.Request(url, data=body, headers=headers, method="POST")
            if proxy:
                opener = urllib.request.build_opener(
                    urllib.request.ProxyHandler({"http": proxy, "https": proxy}),
                    urllib.request.HTTPSHandler(context=ctx)
                )
                resp = opener.open(req, timeout=CONFIG["request_timeout_sec"])
            else:
                resp = urllib.request.urlopen(req, context=ctx, timeout=CONFIG["request_timeout_sec"])
            raw = resp.read().decode("utf-8", errors="replace")
            ACCOUNT_POOL.report_success(account_ctx)
            return extract_response_text(raw)
        except Exception as e:
            last_err = e
            if _is_account_error(e):
                account_backoff_sec = ACCOUNT_POOL.report_failure(account_ctx)
                if attempt < max_attempts - 1:
                    log(f"Account rotate {attempt+1}/{max_attempts}: account={mask_account_id(account_ctx.get('id'))} "
                        f"backoff={account_backoff_sec}s reason={e}")
            elif attempt < max_attempts - 1:
                log(f"Retry {attempt+1}/{max_attempts}: {e}")
                time.sleep(CONFIG["retry_delay_sec"])
    raise last_err


def generate_stream(prompt: str, model_id: int, think_mode: int, file_refs: list = None, extra_fields: dict = None,
                    account: dict = None):
    """Streaming generation via httpx with retry on connection failure."""
    if not HAS_HTTPX:
        text = generate(prompt, model_id, think_mode, file_refs, extra_fields, account=account)
        if text:
            yield text
        return

    client = _get_httpx_client()

    last_err = None
    max_attempts = _account_attempts(account)
    for attempt in range(max_attempts):
        account_ctx = account or ACCOUNT_POOL.next_account()
        body = _build_payload(prompt, model_id, think_mode, file_refs, extra_fields, account_ctx)
        url = _get_url(account_ctx)
        headers = _build_headers(account_ctx)
        try:
            prev_text = ""
            with client.stream("POST", url, content=body, headers=headers) as resp:
                resp.raise_for_status()
                buf = ""
                for chunk in resp.iter_text():
                    buf += chunk
                    while "\n" in buf:
                        line, buf = buf.split("\n", 1)
                        for t in _extract_texts_from_line(line):
                            if len(t) > len(prev_text):
                                delta = clean_text(t[len(prev_text):])
                                if delta:
                                    yield delta
                                prev_text = t
            ACCOUNT_POOL.report_success(account_ctx)
            return
        except Exception as e:
            last_err = e
            if _is_account_error(e):
                account_backoff_sec = ACCOUNT_POOL.report_failure(account_ctx)
                if attempt < max_attempts - 1:
                    log(f"Stream rotate {attempt+1}/{max_attempts}: account={mask_account_id(account_ctx.get('id'))} "
                        f"backoff={account_backoff_sec}s reason={e}")
            elif attempt < max_attempts - 1:
                log(f"Stream retry {attempt+1}/{max_attempts}: {e}")
                time.sleep(CONFIG["retry_delay_sec"])
    raise last_err
