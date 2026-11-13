"""DeepSeek chat client (OpenAI-compatible API) returning validated JSON.

* JSON mode and low temperature for every call;
* one hard time budget per call (10 s by default) that also covers the single retry,
  so a slow or broken API never blocks the patient — the caller shows buttons instead;
* the retry after an invalid answer appends to the same messages, keeping the cached
  prompt prefix (system prompt first, then the request) unchanged;
* token usage, including DeepSeek cache hits, is recorded for every attempt;
* ``on_text`` streams the first attempt (the raw JSON so far) for a live draft in Telegram;
  the answer is still validated as a whole before the patient gets the message.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import replace
from types import SimpleNamespace
from typing import Any, TypeVar

import httpx2
import openai
from openai import AsyncOpenAI
from pydantic import ValidationError

from app.llm.usage import Usage, UsageRecorder, log_usage

T = TypeVar("T")
log = logging.getLogger(__name__)
TextSink = Callable[[str], Awaitable[None]]

RETRY_NOTE = (
    "Ответ не прошёл проверку: {error}. Верни только JSON-объект в формате, описанном для этой "
    "задачи."
)
MAX_ERROR_CHARS = 300


class LLMUnavailable(RuntimeError):
    """No usable answer: timeout, API error or invalid JSON twice. ``reason`` as in Usage."""

    def __init__(self, message: str, reason: str = "api_error") -> None:
        super().__init__(message)
        self.reason = reason


def _api_reason(exc: openai.APIError) -> str:
    if isinstance(exc, openai.APITimeoutError):
        return "timeout"
    if isinstance(exc, openai.APIConnectionError):
        return "network"
    if isinstance(exc, openai.APIStatusError):
        return f"http_{exc.status_code}"
    return "api_error"


def _describe(exc: ValueError) -> str:
    """Where the answer broke the format — field names and error types, never the values."""
    if isinstance(exc, json.JSONDecodeError):
        return f"{exc.msg} at char {exc.pos}"
    if isinstance(exc, ValidationError):
        parts = [f"{'.'.join(map(str, e['loc'])) or 'answer'}: {e['type']}" for e in exc.errors()]
        return "; ".join(parts)[:MAX_ERROR_CHARS]
    return str(exc)[:MAX_ERROR_CHARS]


class DeepSeekClient:
    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        model: str,
        timeout: float = 10.0,
        recorders: Sequence[UsageRecorder] = (log_usage,),
        http_client: httpx2.AsyncClient | None = None,
    ) -> None:
        self.model = model
        self.timeout = timeout
        self.recorders = tuple(recorders)
        self._client = AsyncOpenAI(
            api_key=api_key,
            base_url=base_url,
            timeout=timeout,
            max_retries=0,  # the time budget is ours; a retry happens only on invalid JSON
            http_client=http_client,
        )

    async def complete_json(
        self,
        *,
        task: str,
        system: str,
        user: str,
        parse: Callable[[dict[str, Any]], T],
        temperature: float = 0.0,
        max_tokens: int = 400,
        on_text: TextSink | None = None,
    ) -> T:
        """Ask for a JSON object and turn it into ``T`` with ``parse`` (may raise ValueError).

        Every attempt is recorded once its answer is checked: ok, or the reason it failed.
        """
        messages: list[dict[str, str]] = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
        attempt = 0
        started = time.monotonic()
        try:
            async with asyncio.timeout(self.timeout):
                while True:
                    attempt += 1
                    stream = on_text if attempt == 1 else None
                    content, usage = await self._call(
                        task, messages, attempt, temperature, max_tokens, stream
                    )
                    try:
                        data = json.loads(content)
                        if not isinstance(data, dict):
                            raise json.JSONDecodeError("expected a JSON object", content, 0)
                        result = parse(data)
                    except ValueError as exc:  # JSONDecodeError and ValidationError included
                        reason = (
                            "invalid_json" if isinstance(exc, json.JSONDecodeError)
                            else "invalid_answer"
                        )  # fmt: skip
                        self._record(replace(usage, ok=False, reason=reason, error=_describe(exc)))
                        if attempt >= 2:
                            raise LLMUnavailable(f"{task}: {reason} twice", reason) from exc
                        error = str(exc)[:MAX_ERROR_CHARS]
                        messages += [
                            {"role": "assistant", "content": content},
                            {"role": "user", "content": RETRY_NOTE.format(error=error)},
                        ]
                    else:
                        self._record(usage)
                        return result
        except TimeoutError as exc:
            self._record(
                Usage(task, self.model, attempt, ok=False, reason="timeout",
                      error=f"no answer within {self.timeout:g} s", latency_ms=_ms(started))
            )  # fmt: skip
            raise LLMUnavailable(f"{task}: no answer within {self.timeout:g} s", "timeout") from exc

    async def _call(
        self,
        task: str,
        messages: list[dict[str, str]],
        attempt: int,
        temperature: float,
        max_tokens: int,
        on_text: TextSink | None = None,
    ) -> tuple[str, Usage]:
        started = time.monotonic()
        try:
            if on_text is not None:
                return await self._stream(task, messages, attempt, temperature, max_tokens, on_text)
            response = await self._client.chat.completions.create(
                model=self.model,
                messages=messages,  # type: ignore[arg-type]
                response_format={"type": "json_object"},
                temperature=temperature,
                max_tokens=max_tokens,
            )
        except openai.APIError as exc:
            reason = _api_reason(exc)
            self._record(
                Usage(task, self.model, attempt, ok=False, reason=reason,
                      error=type(exc).__name__, latency_ms=_ms(started))
            )  # fmt: skip
            raise LLMUnavailable(f"{task}: {type(exc).__name__}", reason) from exc
        usage = _usage(task, self.model, attempt, response, _ms(started))
        if not response.choices:
            return "", usage
        return response.choices[0].message.content or "", usage

    async def _stream(
        self,
        task: str,
        messages: list[dict[str, str]],
        attempt: int,
        temperature: float,
        max_tokens: int,
        on_text: TextSink,
    ) -> tuple[str, Usage]:
        started = time.monotonic()
        stream = await self._client.chat.completions.create(
            model=self.model,
            messages=messages,  # type: ignore[arg-type]
            response_format={"type": "json_object"},
            temperature=temperature,
            max_tokens=max_tokens,
            stream=True,
            stream_options={"include_usage": True},
        )
        parts: list[str] = []
        last: Any = SimpleNamespace(usage=None)
        async for chunk in stream:
            if getattr(chunk, "usage", None) is not None:
                last = chunk
            delta = chunk.choices[0].delta.content if chunk.choices else None
            if delta:
                parts.append(delta)
                try:
                    await on_text("".join(parts))
                except Exception:  # a failed draft must not break the answer
                    log.warning("streaming a draft failed", exc_info=True)
        return "".join(parts), _usage(task, self.model, attempt, last, _ms(started))

    def _record(self, usage: Usage) -> None:
        for recorder in self.recorders:
            recorder(usage)


def _ms(started: float) -> int:
    return int((time.monotonic() - started) * 1000)


def _usage(task: str, model: str, attempt: int, response: Any, latency_ms: int) -> Usage:
    u = response.usage
    if u is None:
        return Usage(task, model, attempt, ok=True, latency_ms=latency_ms)
    extra = u.model_extra or {}
    details = getattr(u, "prompt_tokens_details", None)
    cached = getattr(details, "cached_tokens", None) or 0
    hit = int(extra.get("prompt_cache_hit_tokens", cached) or 0)
    miss = int(extra.get("prompt_cache_miss_tokens", u.prompt_tokens - hit) or 0)
    return Usage(
        task,
        model,
        attempt,
        ok=True,
        prompt_tokens=u.prompt_tokens,
        completion_tokens=u.completion_tokens,
        cache_hit_tokens=hit,
        cache_miss_tokens=miss,
        latency_ms=latency_ms,
    )
