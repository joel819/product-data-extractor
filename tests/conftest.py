"""Tests run offline: no keys, no network. DNS is faked, the HTTP layer is an in-memory transport,
and the real environment cannot leak settings into a test."""
import os
from pathlib import Path

import pytest

from product_extractor.config import Settings

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "pages"
PUBLIC_IP = "93.184.216.34"


@pytest.fixture(autouse=True)
def _clean_environment(monkeypatch):
    for name in Settings.model_fields:
        monkeypatch.delenv(name.upper(), raising=False)
    monkeypatch.chdir(Path(__file__).resolve().parent.parent)
    assert not os.environ.get("LLM_API_KEY")


@pytest.fixture
def settings():
    """Fast, offline defaults: no rate-limit delay, no LLM, no rendering."""
    return Settings(_env_file=None, per_domain_min_interval_seconds=0.0, llm_api_key="", api_key="",
                    cache_ttl_seconds=3600, render_js=False)


@pytest.fixture
def public_resolver():
    """Every hostname resolves to one public address, except the names mapped explicitly."""
    mapping: dict[str, list[str]] = {}

    async def resolve(host: str, port: int) -> list[str]:
        return mapping.get(host, [PUBLIC_IP])

    resolve.mapping = mapping
    return resolve


def fixture_html(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")
