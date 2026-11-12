import importlib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


@pytest.mark.parametrize(
    "module",
    ["app", "app.config", "app.bot", "app.flow", "app.llm", "app.schedule", "app.notify", "app.db"],
)
def test_packages_import(module: str) -> None:
    importlib.import_module(module)


@pytest.mark.parametrize(
    "path", ["app/llm/prompts", "config", "kb/text", "kb/scenarios", "kb/info", "data"]
)
def test_layout(path: str) -> None:
    assert (ROOT / path).is_dir()


def test_secrets_and_clinic_pdfs_are_gitignored() -> None:
    ignored = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert ".env" in ignored
    assert "kb/source_pdf/" in ignored
