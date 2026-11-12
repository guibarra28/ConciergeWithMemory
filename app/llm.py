"""Thin wrapper around the OpenAI SDK with structured outputs, retry and usage logging."""

from __future__ import annotations

import csv
import logging
import os
import time
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import TypeVar

import openai
from dotenv import load_dotenv
from openai import OpenAI
from pydantic import BaseModel

load_dotenv()

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
USAGE_LOG_PATH = PROJECT_ROOT / "logs" / "llm_usage.csv"
USAGE_LOG_FIELDS = [
    "timestamp",
    "model",
    "schema",
    "attempt",
    "status",
    "input_tokens",
    "output_tokens",
    "total_tokens",
    "latency_ms",
    "error",
]

DEFAULT_TIMEOUT_SECONDS = 30.0
MAX_ATTEMPTS = 2  # first call + one retry

RETRYABLE_ERRORS = (
    openai.APITimeoutError,
    openai.APIConnectionError,
    openai.RateLimitError,
    openai.InternalServerError,
)


class LLMError(RuntimeError):
    """Raised when the LLM call fails after all attempts or returns no parsed output."""


def _get_model() -> str:
    model = os.getenv("OPENAI_MODEL")
    if not model:
        raise LLMError("OPENAI_MODEL is not set. Add it to your .env file.")
    return model


def _get_timeout() -> float:
    return float(os.getenv("OPENAI_TIMEOUT_SECONDS", DEFAULT_TIMEOUT_SECONDS))


@lru_cache(maxsize=1)
def _get_client() -> OpenAI:
    # Retries are handled here explicitly so every attempt is timed and logged.
    return OpenAI(timeout=_get_timeout(), max_retries=0)


def _log_usage(row: dict) -> None:
    try:
        USAGE_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        is_new = not USAGE_LOG_PATH.exists()
        with USAGE_LOG_PATH.open("a", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=USAGE_LOG_FIELDS)
            if is_new:
                writer.writeheader()
            writer.writerow({k: row.get(k, "") for k in USAGE_LOG_FIELDS})
    except OSError:
        # Logging must never break the actual request.
        logger.exception("Could not write LLM usage log")


def call_llm(system: str, messages: list[dict], schema: type[T]) -> T:
    """Call the model and return its answer parsed into ``schema``.

    Args:
        system: System instructions for the model.
        messages: Conversation as ``[{"role": "user" | "assistant", "content": str}, ...]``.
        schema: Pydantic model class describing the expected structured output.

    Returns:
        An instance of ``schema``.

    Raises:
        LLMError: if the call fails after one retry, or the model refuses / returns no output.
    """
    model = _get_model()
    client = _get_client()
    last_error: Exception | None = None

    for attempt in range(1, MAX_ATTEMPTS + 1):
        start = time.perf_counter()
        log_row = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "model": model,
            "schema": schema.__name__,
            "attempt": attempt,
        }
        try:
            response = client.responses.parse(
                model=model,
                instructions=system,
                input=messages,
                text_format=schema,
            )
        except RETRYABLE_ERRORS as exc:
            last_error = exc
            _log_usage({**log_row, "status": "error", "latency_ms": _elapsed_ms(start),
                        "error": type(exc).__name__})
            logger.warning("LLM call failed (attempt %s/%s): %s", attempt, MAX_ATTEMPTS, exc)
            if attempt < MAX_ATTEMPTS:
                time.sleep(1)
            continue
        except openai.OpenAIError as exc:
            _log_usage({**log_row, "status": "error", "latency_ms": _elapsed_ms(start),
                        "error": type(exc).__name__})
            raise LLMError(f"LLM call failed: {exc}") from exc

        usage = getattr(response, "usage", None)
        _log_usage({
            **log_row,
            "status": "ok",
            "input_tokens": getattr(usage, "input_tokens", ""),
            "output_tokens": getattr(usage, "output_tokens", ""),
            "total_tokens": getattr(usage, "total_tokens", ""),
            "latency_ms": _elapsed_ms(start),
        })

        parsed = response.output_parsed
        if parsed is None:
            raise LLMError("Model returned no structured output (possibly a refusal).")
        return parsed

    raise LLMError(f"LLM call failed after {MAX_ATTEMPTS} attempts: {last_error}") from last_error


def _elapsed_ms(start: float) -> int:
    return round((time.perf_counter() - start) * 1000)
