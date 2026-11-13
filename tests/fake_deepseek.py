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
        if self.requests[-1].get("stream"):
            return self._stream(content)
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

    @staticmethod
    def _stream(content: str, piece: int = 9) -> httpx2.Response:
        """Server-sent events as DeepSeek streams them; usage comes in the last chunk."""
        head = {"id": "fake", "object": "chat.completion.chunk", "created": 0,
                "model": "deepseek-chat"}  # fmt: skip
        events = []
        for i in range(0, len(content), piece):
            delta = {"content": content[i : i + piece]}
            choice = {"index": 0, "delta": delta, "finish_reason": None}
            events.append({**head, "choices": [choice]})
        events.append({**head, "choices": [], "usage": USAGE})
        body = "".join(f"data: {json.dumps(e, ensure_ascii=False)}\n\n" for e in events)
        body += "data: [DONE]\n\n"
        return httpx2.Response(
            200, content=body.encode(), headers={"content-type": "text/event-stream"}
        )

    def client(self, timeout: float = 10.0, first_attempt: float = 6.0) -> DeepSeekClient:
        return DeepSeekClient(
            api_key="test-key",
            base_url="https://api.deepseek.test",
            model="deepseek-chat",
            timeout=timeout,
            first_attempt=first_attempt,
            retry_pause=0,
            recorders=[self.usage.append],
            http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(self._handle)),
        )

    def assistant(
        self, kb: KnowledgeBase, timeout: float = 10.0, first_attempt: float = 6.0
    ) -> Assistant:
        return Assistant(self.client(timeout, first_attempt), kb)

    def sent(self) -> str:
        """Everything that left the process, as one string."""
        return json.dumps(self.requests, ensure_ascii=False)
