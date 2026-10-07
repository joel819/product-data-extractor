"""Settings, read from environment variables and an optional .env file."""
from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from product_extractor import __version__


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Fetching
    user_agent: str = (
        f"product-data-extractor/{__version__} (+https://github.com/joel819/product-data-extractor)"
    )
    request_timeout_seconds: float = 15.0
    max_page_bytes: int = 2_000_000
    max_redirects: int = 5
    respect_robots_txt: bool = True
    per_domain_min_interval_seconds: float = 1.0
    # Only the local demo and the tests turn this on. Leave it off anywhere user input reaches the API.
    allow_private_networks: bool = False

    # Cache
    cache_ttl_seconds: int = 3600
    cache_max_entries: int = 1000

    # API
    api_key: str = ""

    # LLM fallback (OpenAI-compatible)
    llm_api_key: str = ""
    llm_base_url: str = "https://api.groq.com/openai/v1"
    llm_model: str = "openai/gpt-oss-120b"
    llm_timeout_seconds: float = 30.0
    llm_max_input_chars: int = Field(default=12_000, ge=500)

    # Optional JavaScript rendering
    render_js: bool = False
    render_min_text_chars: int = 200
    render_timeout_seconds: float = 20.0

    @property
    def llm_enabled(self) -> bool:
        return bool(self.llm_api_key)


@lru_cache
def get_settings() -> Settings:
    return Settings()
