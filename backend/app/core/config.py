from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    log_level: str = "INFO"
    database_url: str = "sqlite+aiosqlite:///./astra-dev.db"
    model_provider: str = "openai"
    model_name: str = "gpt-5"
    model_api_key: str = ""
    model_base_url: str = "https://api.openai.com/v1"
    tool_web_search_enabled: bool = True
    tool_web_fetch_enabled: bool = True
    tool_chart_render_enabled: bool = True
    web_search_provider: str = "auto"
    web_search_api_key: str = ""
    google_search_api_key: str = ""
    google_search_engine_id: str = ""
    google_search_result_count: int = 5
    google_search_language: str = "lang_zh-CN"
    google_search_region: str = ""
    google_search_safe: str = "active"
    crawler_max_content_chars: int = 12000
    crawler_max_response_bytes: int = 2 * 1024 * 1024
    crawler_min_quality_chars: int = 240
    crawler_allow_proxy_fake_ip: bool = False
    agent_max_turns: int = 20
    agent_max_tool_calls: int = 16
    agent_max_reflections: int = 6
    agent_max_replans: int = 4
    agent_per_tool_retry_limit: int = 2
    agent_memory_write_enabled: bool = True
    agent_use_loop: bool = True
    agent_reasoning_shadow_mode: bool = False
    agent_use_general_runtime: bool = True
    allow_network_read: bool = True
    cors_origins: str = "http://localhost:5173"
    artifact_store_path: str = "./astra-artifacts"
    artifact_max_files: int = 16
    artifact_max_bytes: int = 20 * 1024 * 1024
    artifact_retention_days: int = 30
    sandbox_enabled: bool = True
    sandbox_skip_availability_check: bool = False
    sandbox_provider: str = "docker"
    docker_binary: str = "docker"
    sandbox_runtime_image: str = "astra-data-viz:0.1.0"
    sandbox_web_runtime_image: str = "astra-web-tools:0.1.0"
    sandbox_runtime_lock_digest: str = ""
    sandbox_wall_time_seconds: int = 30
    sandbox_memory_mb: int = 1024
    sandbox_cpus: float = 1.0
    sandbox_pids: int = 128
    runtime_profile_path: str = "./runtime-profile.json"
    runtime_build_timeout_seconds: int = 600
    runtime_image_keep_recent: int = 3
    runtime_image_retention_days: int = 30

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    @property
    def cors_origin_list(self) -> list[str]:
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
