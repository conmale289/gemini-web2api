"""Prompt compression inspired by rtk-ai/rtk strategies.

Four strategies applied to incoming prompts:
1. Smart Filtering - strip HTML comments, boilerplate, noise
2. Deduplication - collapse repeated tool results, deduplicate lines
3. Truncation - smart keep-recent, summarize-old approach
4. Compact encoding - minimal JSON, shorter role markers
"""
import json
import re
import hashlib

from .logging import log

# ─── Strategy 1: Smart Filtering ──────────────────────────────────────────────

_HTML_COMMENT_RE = re.compile(r'<!--.*?-->', re.DOTALL)
_MULTI_BLANK_RE = re.compile(r'\n{3,}')
_TRAILING_SPACE_RE = re.compile(r'[ \t]+\n')


def strip_noise(text: str) -> str:
    """Remove HTML comments, excessive whitespace, and boilerplate noise."""
    text = _HTML_COMMENT_RE.sub('', text)
    text = _TRAILING_SPACE_RE.sub('\n', text)
    text = _MULTI_BLANK_RE.sub('\n\n', text)
    return text.strip()


# ─── Strategy 2: Deduplication ─────────────────────────────────────────────────

def _hash_block(text: str) -> str:
    return hashlib.md5(text.encode()).hexdigest()[:8]


def dedup_tool_results(messages: list) -> list:
    """Collapse consecutive identical tool results with counts."""
    if not messages:
        return messages
    result = []
    prev_hash = None
    dup_count = 0
    for msg in messages:
        if msg.get("role") == "tool":
            h = _hash_block(msg.get("content", ""))
            if h == prev_hash:
                dup_count += 1
                continue
            if dup_count > 0:
                result[-1] = dict(result[-1])
                result[-1]["content"] = result[-1].get("content", "") + f"\n[...repeated {dup_count}x]"
            prev_hash = h
            dup_count = 0
        else:
            if dup_count > 0 and result:
                result[-1] = dict(result[-1])
                result[-1]["content"] = result[-1].get("content", "") + f"\n[...repeated {dup_count}x]"
            prev_hash = None
            dup_count = 0
        result.append(msg)
    if dup_count > 0 and result:
        result[-1] = dict(result[-1])
        result[-1]["content"] = result[-1].get("content", "") + f"\n[...repeated {dup_count}x]"
    return result


# ─── Strategy 3: Truncation (smart) ───────────────────────────────────────────

MAX_TOOL_RESULT_CHARS = 8000
MAX_SYSTEM_CHARS = 6000


def truncate_tool_result(content: str, max_chars: int = MAX_TOOL_RESULT_CHARS) -> str:
    """Truncate verbose tool output, keeping head and tail."""
    if len(content) <= max_chars:
        return content
    keep = max_chars // 2 - 30
    return content[:keep] + f"\n[...truncated {len(content) - max_chars} chars...]\n" + content[-keep:]


def truncate_system_prompt(content: str, max_chars: int = MAX_SYSTEM_CHARS) -> str:
    """Truncate system prompts, keeping the core instructions."""
    if len(content) <= max_chars:
        return content
    # Keep the first section (identity/role) and last section (most recent instructions)
    keep_start = max_chars * 2 // 3
    keep_end = max_chars - keep_start - 50
    return content[:keep_start] + "\n[...system prompt truncated...]\n" + content[-keep_end:]


# ─── Strategy 4: Compact encoding ─────────────────────────────────────────────

def compact_tool_json(tool_defs: list) -> str:
    """Encode tool definitions as compact JSON (no indent, short keys)."""
    compact = []
    for t in tool_defs:
        entry = {"name": t["name"]}
        if t.get("description"):
            entry["desc"] = t["description"]
        if t.get("parameters"):
            params = t["parameters"]
            # Strip verbose JSON schema boilerplate if present
            if isinstance(params, dict):
                props = params.get("properties", {})
                if props:
                    entry["params"] = {k: v.get("type", "any") for k, v in props.items()}
                    req = params.get("required", [])
                    if req:
                        entry["required"] = req
                else:
                    entry["params"] = params
        compact.append(entry)
    return json.dumps(compact, separators=(',', ':'), ensure_ascii=False)


# ─── Main compression pipeline ────────────────────────────────────────────────

def compress_messages(messages: list, tools: list = None, max_total: int = 70000) -> tuple:
    """Apply all compression strategies to messages.

    Returns (compressed_messages, compressed_tools) ready for prompt building.
    Targets staying under max_total characters in final prompt.
    """
    # Deduplicate tool results
    messages = dedup_tool_results(messages)

    # Estimate current size
    total = sum(len(str(m.get("content", ""))) for m in messages)
    if tools:
        total += len(json.dumps(tools))

    if total <= max_total:
        # Just filter noise from system prompts
        return [_filter_message(m) for m in messages], tools

    # Apply progressive compression
    compressed = []
    for i, msg in enumerate(messages):
        m = _filter_message(msg)
        role = m.get("role", "user")

        # Compress older messages more aggressively
        age_ratio = i / max(len(messages), 1)  # 0.0 = oldest, 1.0 = newest
        is_old = age_ratio < 0.5

        if role == "system":
            content = m.get("content", "")
            if len(content) > MAX_SYSTEM_CHARS:
                m = dict(m)
                m["content"] = truncate_system_prompt(content)
        elif role == "tool":
            content = m.get("content", "")
            limit = MAX_TOOL_RESULT_CHARS // 2 if is_old else MAX_TOOL_RESULT_CHARS
            if len(content) > limit:
                m = dict(m)
                m["content"] = truncate_tool_result(content, limit)
        elif role == "assistant" and is_old:
            content = m.get("content", "")
            if content and len(content) > 2000:
                m = dict(m)
                m["content"] = content[:1500] + "\n[...truncated...]"

        compressed.append(m)

    # If still too large, drop oldest non-system messages from the middle
    est_size = sum(len(str(m.get("content", ""))) for m in compressed)
    if est_size > max_total and len(compressed) > 4:
        # Keep first (system), last 60% of messages
        keep_recent = max(3, int(len(compressed) * 0.6))
        system_msgs = [m for m in compressed if m.get("role") == "system"]
        recent = compressed[-keep_recent:]
        compressed = system_msgs + [{"role": "user", "content": f"[...{len(compressed) - keep_recent - len(system_msgs)} earlier messages omitted...]"}] + recent

    return compressed, tools


def _filter_message(msg: dict) -> dict:
    """Apply noise filtering to a single message."""
    content = msg.get("content", "")
    if not content or not isinstance(content, str):
        return msg
    filtered = strip_noise(content)
    if filtered != content:
        msg = dict(msg)
        msg["content"] = filtered
    return msg
