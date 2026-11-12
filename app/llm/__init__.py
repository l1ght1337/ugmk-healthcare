"""DeepSeek client, prompts and masking of personal data."""

from __future__ import annotations

import logging

from app.config import PROJECT_ROOT, Settings
from app.flow.loader import KnowledgeBase
from app.llm.assistant import Assistant
from app.llm.client import DeepSeekClient, LLMUnavailable
from app.llm.usage import JsonlUsageLog, UsageRecorder, log_usage

__all__ = ["Assistant", "DeepSeekClient", "LLMUnavailable", "make_assistant"]

log = logging.getLogger(__name__)


def make_assistant(settings: Settings, kb: KnowledgeBase) -> Assistant | None:
    """The LLM, or None without DEEPSEEK_API_KEY — then the bot works on buttons only."""
    if settings.deepseek_api_key is None:
        log.warning("DEEPSEEK_API_KEY is not set: free text is not understood, buttons only")
        return None
    recorders: list[UsageRecorder] = [log_usage]
    if settings.llm_usage_log is not None:
        path = settings.llm_usage_log
        recorders.append(JsonlUsageLog(path if path.is_absolute() else PROJECT_ROOT / path))
    client = DeepSeekClient(
        api_key=settings.deepseek_api_key.get_secret_value(),
        base_url=settings.deepseek_base_url,
        model=settings.deepseek_model,
        timeout=settings.llm_timeout_seconds,
        recorders=recorders,
    )
    return Assistant(client, kb)
