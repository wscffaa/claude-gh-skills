#!/usr/bin/env python3
"""Content compressor for parallel-agent using Gemini Flash."""
from __future__ import annotations

import hashlib
import subprocess
import sys
from typing import Dict

DEFAULT_COMPRESS_MODEL = "flash"
DEFAULT_COMPRESS_RATIO = 0.3
_MIN_RATIO = 0.05
_MAX_RATIO = 1.0
_CACHE: Dict[str, str] = {}


def _cache_key(text: str, ratio: float, model: str) -> str:
    """Generate cache key from content hash, ratio, and model."""
    digest = hashlib.sha256(text.encode("utf-8", errors="ignore")).hexdigest()[:16]
    return f"{model}:{ratio:.3f}:{digest}"


def _clamp_ratio(ratio: float) -> float:
    """Clamp ratio to valid range."""
    if ratio <= 0:
        return DEFAULT_COMPRESS_RATIO
    return max(_MIN_RATIO, min(ratio, _MAX_RATIO))


def _model_to_gemini_name(model: str) -> str:
    """Convert short model name to full Gemini model name."""
    model = (model or DEFAULT_COMPRESS_MODEL).strip().lower()
    if model == "flash":
        return "gemini-3-flash-preview"
    elif model == "pro":
        return "gemini-3-pro-preview"
    return model


def compress_text(
    text: str,
    ratio: float = DEFAULT_COMPRESS_RATIO,
    model: str = DEFAULT_COMPRESS_MODEL,
) -> str:
    """Compress text using Gemini Flash.

    Args:
        text: Original text to compress
        ratio: Target compression ratio (0.1-1.0)
        model: Model to use (flash/pro)

    Returns:
        Compressed text, or original if compression fails
    """
    if not text.strip():
        return text

    # Skip short content
    lines = text.strip().split("\n")
    if len(lines) < 50:
        return text

    ratio = _clamp_ratio(ratio)
    model = (model or DEFAULT_COMPRESS_MODEL).strip() or DEFAULT_COMPRESS_MODEL
    key = _cache_key(text, ratio, model)

    # Check cache
    if key in _CACHE:
        return _CACHE[key]

    # Build compression prompt
    target_lines = max(20, int(len(lines) * ratio))
    prompt = (
        f"将以下内容压缩到原长度的 {ratio:.0%} 左右（约 {target_lines} 行），保留关键结论、数据和指令。"
        "直接输出压缩后的文本，不要补充额外说明。\n\n"
        f"[原文]\n{text}"
    )

    # Call Gemini CLI
    gemini_model = _model_to_gemini_name(model)
    args = ["gemini", "-o", "text", "-y", "-m", gemini_model, "-p", "-"]

    try:
        proc = subprocess.run(
            args,
            input=prompt,
            text=True,
            capture_output=True,
            timeout=300,
            check=False,
        )
    except FileNotFoundError:
        sys.stderr.write("WARN: gemini CLI not found, using original text\n")
        _CACHE[key] = text
        return text
    except subprocess.TimeoutExpired:
        sys.stderr.write("WARN: compression timeout, using original text\n")
        _CACHE[key] = text
        return text

    if proc.returncode != 0:
        sys.stderr.write(f"WARN: compression failed: {proc.stderr}\n")
        _CACHE[key] = text
        return text

    compressed = proc.stdout.strip() or text

    # Log compression stats
    original_lines = len(lines)
    compressed_lines = len(compressed.split("\n"))
    actual_ratio = compressed_lines / original_lines if original_lines > 0 else 1.0
    sys.stderr.write(
        f"INFO: compressed {original_lines} -> {compressed_lines} lines "
        f"(ratio: {actual_ratio:.1%}, target: {ratio:.0%})\n"
    )

    _CACHE[key] = compressed
    return compressed


def log_info(message: str) -> None:
    """Log info message to stderr."""
    sys.stderr.write(f"INFO: {message}\n")
