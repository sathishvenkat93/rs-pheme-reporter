from __future__ import annotations

from uuid import UUID

import pytest

from app.core.config import Settings
from app.core.principal import current_principal


def test_settings_read_prefixed_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PHEME_MAX_UPLOAD_BYTES", "1024")
    monkeypatch.setenv("PHEME_MODEL_FAST", "anthropic.claude-haiku-5-5")
    s = Settings()
    assert s.max_upload_bytes == 1024
    assert s.model_fast == "anthropic.claude-haiku-5-5"


def test_settings_defaults_for_models() -> None:
    s = Settings(_env_file=None)
    assert s.model_main == "anthropic.claude-sonnet-5-5"
    assert s.model_fast == "anthropic.claude-haiku-5-5"
    assert s.llm == "fake"
    assert s.live_llm is False


def test_dev_principal_comes_from_config() -> None:
    p = current_principal()
    assert isinstance(p.workspace_id, UUID)
    assert isinstance(p.user_id, UUID)


def test_env_example_lists_every_setting() -> None:
    from pathlib import Path

    example = Path(__file__).resolve().parents[2] / ".env.example"
    keys = {
        line.split("=", 1)[0].strip()
        for line in example.read_text().splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }
    expected = {f"PHEME_{name.upper()}" for name in Settings.model_fields}
    assert expected <= keys, f"missing from .env.example: {sorted(expected - keys)}"
