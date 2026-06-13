"""Metrics collector for monitoring dashboard."""
import threading
import time
from collections import deque

_lock = threading.Lock()
_start_time = time.time()

# Global counters
_total_requests = 0
_total_errors = 0
_total_truncations = 0

# Per-account stats: {account_id: {requests, successes, failures, latencies}}
_account_stats: dict = {}

# Per-model stats: {model_name: count}
_model_stats: dict = {}

# Error breakdown: {error_code: count}
_error_codes: dict = {}

# Recent requests for RPM calculation (timestamp list)
_recent_requests: deque = deque(maxlen=1000)

# Recent errors for display
_recent_errors: deque = deque(maxlen=20)

# Latencies for avg calculation
_latencies: deque = deque(maxlen=200)


def record_request(model: str, account_id: str, latency_ms: float, success: bool, error_code: int = None, prompt_len: int = 0, compressed_len: int = 0):
    """Record a completed request."""
    global _total_requests, _total_errors, _total_truncations
    now = time.time()
    with _lock:
        _total_requests += 1
        _recent_requests.append(now)
        _latencies.append(latency_ms)

        # Model stats
        _model_stats[model] = _model_stats.get(model, 0) + 1

        # Account stats
        if account_id not in _account_stats:
            _account_stats[account_id] = {"requests": 0, "successes": 0, "failures": 0, "latencies": deque(maxlen=50), "last_used": 0}
        s = _account_stats[account_id]
        s["requests"] += 1
        s["last_used"] = now
        s["latencies"].append(latency_ms)

        if success:
            s["successes"] += 1
        else:
            s["failures"] += 1
            _total_errors += 1
            if error_code:
                _error_codes[error_code] = _error_codes.get(error_code, 0) + 1
                _recent_errors.append({"time": now, "code": error_code, "account": account_id, "model": model})

        if compressed_len and compressed_len < prompt_len:
            _total_truncations += 1


def get_stats() -> dict:
    """Get all stats for dashboard/API."""
    now = time.time()
    with _lock:
        # RPM: count requests in last 60s
        cutoff = now - 60
        rpm = sum(1 for t in _recent_requests if t > cutoff)

        # Avg latency
        avg_latency = sum(_latencies) / len(_latencies) if _latencies else 0

        # Per-account
        accounts = []
        for aid, s in _account_stats.items():
            avg_lat = sum(s["latencies"]) / len(s["latencies"]) if s["latencies"] else 0
            total = s["requests"]
            accounts.append({
                "id": aid,
                "requests": total,
                "successes": s["successes"],
                "failures": s["failures"],
                "health": round(s["successes"] / total, 2) if total else 1.0,
                "avg_latency_ms": round(avg_lat),
                "last_used": round(now - s["last_used"], 1) if s["last_used"] else None,
            })

        return {
            "uptime_sec": round(now - _start_time),
            "total_requests": _total_requests,
            "total_errors": _total_errors,
            "total_truncations": _total_truncations,
            "rpm": rpm,
            "avg_latency_ms": round(avg_latency),
            "accounts": accounts,
            "models": dict(_model_stats),
            "errors": dict(_error_codes),
            "recent_errors": [{"time": round(e["time"] - _start_time), "code": e["code"], "account": e["account"], "model": e["model"]} for e in _recent_errors],
        }
