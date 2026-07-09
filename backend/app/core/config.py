from functools import lru_cache
from typing import List

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    database_url: str = "postgresql+asyncpg://astra:astra@localhost:5432/astra"
    model_provider: str = "mock"
    model_name: str = "mock-web-query"
    model_api_key: str = ""
    model_base_url: str = "https://api.openai.com/v1"
    web_search_provider: str = "mock"
    web_search_api_key: str = ""
    allow_network_read: bool = True
    cors_origins: str = "http://localhost:5173"

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    @property
    def cors_origin_list(self) -> List[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @property
    def model_policy(self) -> dict:
        return {
            "provider": self.model_provider,
            "model": self.model_name,
            "base_url": self.model_base_url,
        }


@lru_cache
def get_settings() -> Settings:
    return Settings()
