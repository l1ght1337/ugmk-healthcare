"""A stand-in for the DeepSeek HTTP API: canned replies, captured request bodies."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx2

from app.flow.loader import KnowledgeBase
from app.llm.assistant import Assistant
from app.llm.client import DeepSeekClient
from app.llm.usage import Usage

USAGE = {
    "prompt_tokens": 5000,
    "completion_tokens": 40,
    "total_tokens": 5040,
    "prompt_cache_hit_tokens": 4864,
    "prompt_cache_miss_tokens": 136,
}


class Delay:
    def __init__(self, seconds: float) -> None:
        self.seconds = seconds


class FakeDeepSeek:
    """Replies in order: a dict/str is the message content, an int an HTTP error, Delay a hang."""

    def __init__(self, *replies: Any) -> None:
        self.replies = list(replies)
        self.requests: list[dict[str, Any]] = []
        self.usage: list[Usage] = []

    async def _handle(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(json.loads(request.content))
        reply = self.replies.pop(0) if self.replies else {}
        if isinstance(reply, Delay):
            await asyncio.sleep(reply.seconds)
            reply = {}
        if isinstance(reply, int):
            return httpx2.Response(reply, json={"error": {"message": "fake error"}})
        content = reply if isinstance(reply, str) else json.dumps(reply, ensure_ascii=False)
        return httpx2.Response(
            200,
            json={
                "id": "fake",
                "object": "chat.completion",
                "created": 0,
                "model": "deepseek-chat",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": content},
                        "finish_reason": "stop",
                    }
                ],
                "usage": USAGE,
            },
        )

    def client(self, timeout: float = 10.0) -> DeepSeekClient:
        return DeepSeekClient(
            api_key="test-key",
            base_url="https://api.deepseek.test",
            model="deepseek-chat",
            timeout=timeout,
            recorders=[self.usage.append],
            http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(self._handle)),
        )

    def assistant(self, kb: KnowledgeBase, timeout: float = 10.0) -> Assistant:
        return Assistant(self.client(timeout), kb)

    def sent(self) -> str:
        """Everything that left the process, as one string."""
        return json.dumps(self.requests, ensure_ascii=False)
