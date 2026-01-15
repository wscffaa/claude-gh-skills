#!/usr/bin/env python3
"""Unified JSON stream parsing for multiple backends."""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Iterable, List, Optional


@dataclass
class StreamParseResult:
    message: str = ""
    session_id: str = ""


def _normalize_text(value: object) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts: List[str] = []
        for item in value:
            if isinstance(item, str):
                parts.append(item)
        return "".join(parts)
    return ""


def _load_event(line: str) -> Optional[dict]:
    try:
        return json.loads(line)
    except json.JSONDecodeError:
        return None


def parse_codex_stream(lines: Iterable[str]) -> StreamParseResult:
    message = ""
    session_id = ""

    for line in lines:
        line = line.strip()
        if not line:
            continue
        event = _load_event(line)
        if not isinstance(event, dict):
            continue

        thread_id = event.get("thread_id")
        if isinstance(thread_id, str) and thread_id:
            session_id = session_id or thread_id

        if event.get("type") != "item.completed":
            continue
        item = event.get("item")
        if not isinstance(item, dict):
            continue
        if item.get("type") != "agent_message":
            continue
        text = _normalize_text(item.get("text"))
        if text:
            message = text

    return StreamParseResult(message=message, session_id=session_id)


def parse_claude_stream(lines: Iterable[str]) -> StreamParseResult:
    message = ""
    session_id = ""

    for line in lines:
        line = line.strip()
        if not line:
            continue
        event = _load_event(line)
        if not isinstance(event, dict):
            continue

        sid = event.get("session_id")
        if isinstance(sid, str) and sid:
            session_id = session_id or sid

        result = event.get("result")
        if isinstance(result, str) and result:
            message = result

    return StreamParseResult(message=message, session_id=session_id)


def parse_gemini_stream(lines: Iterable[str]) -> StreamParseResult:
    buffer: List[str] = []
    session_id = ""

    for line in lines:
        line = line.strip()
        if not line:
            continue
        event = _load_event(line)
        if not isinstance(event, dict):
            continue

        sid = event.get("session_id")
        if isinstance(sid, str) and sid:
            session_id = session_id or sid

        content = event.get("content")
        if isinstance(content, str) and content:
            buffer.append(content)

    return StreamParseResult(message="".join(buffer), session_id=session_id)


def parse_stream_json(backend: str, output: str) -> StreamParseResult:
    lines = output.splitlines()
    backend = (backend or "").strip().lower()

    if backend == "codex":
        result = parse_codex_stream(lines)
    elif backend == "claude":
        result = parse_claude_stream(lines)
    elif backend == "gemini":
        result = parse_gemini_stream(lines)
    else:
        result = _parse_unknown(lines)

    if not result.message:
        stripped = output.strip()
        if stripped and not stripped.lstrip().startswith("{"):
            result.message = stripped

    return result


def _parse_unknown(lines: Iterable[str]) -> StreamParseResult:
    codex = parse_codex_stream(lines)
    if codex.message or codex.session_id:
        return codex
    claude = parse_claude_stream(lines)
    if claude.message or claude.session_id:
        return claude
    return parse_gemini_stream(lines)
