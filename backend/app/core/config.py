from functools import lru_cache

from pydantic import Field
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
    tool_bash_execute_enabled: bool = False
    trusted_tool_providers: str = (
        "astra.builtin=builtin,astra.web=builtin,astra.chart=builtin,astra.shell=builtin"
    )
    permission_bundle_signing_secret: str = ""
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
    agent_max_turns: int = 60
    agent_max_tool_calls: int = 50
    agent_max_reflections: int = 6
    agent_max_replans: int = 4
    agent_parallel_execution_enabled: bool = True
    agent_max_parallel_nodes: int = 3
    agent_provider_concurrency_limit: int = 8
    agent_capability_concurrency_limit: int = 4
    agent_execution_heartbeat_seconds: int = 10
    agent_execution_stale_seconds: int = 45
    agent_node_attempt_timeout_seconds: int = 120
    agent_node_max_safe_retries: int = 1
    agent_memory_write_enabled: bool = True
    agent_use_general_runtime: bool = True
    allow_network_read: bool = True
    cors_origins: str = "http://localhost:5173"
    api_allow_remote: bool = False
    artifact_store_path: str = "./astra-artifacts"
    task_workspace_store_path: str = "./astra-workspaces"
    conversation_retention_enabled: bool = False
    conversation_retention_days: int = Field(default=180, ge=1, le=36_500)
    conversation_retention_sweep_seconds: int = Field(default=86_400, ge=60, le=2_592_000)
    conversation_retention_batch_size: int = Field(default=100, ge=1, le=1_000)
    task_workspace_max_files: int = 10_000
    task_workspace_max_bytes: int = 1024 * 1024 * 1024
    task_workspace_max_file_bytes: int = 100 * 1024 * 1024
    artifact_max_files: int = 16
    artifact_max_bytes: int = 20 * 1024 * 1024
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
    bash_output_max_chars: int = 12000
    runtime_profile_path: str = "./runtime-profile.json"
    runtime_build_timeout_seconds: int = 600
    runtime_image_keep_recent: int = 3
    runtime_image_retention_days: int = 30
    skills_enabled: bool = True
    skills_custom_authoring_enabled: bool = True
    skills_max_files: int = 256
    skills_max_file_bytes: int = 2 * 1024 * 1024
    skills_max_package_bytes: int = 20 * 1024 * 1024
    skills_max_instruction_chars: int = 40_000
    skills_catalog_metadata_chars: int = 24_000
    skills_max_active: int = 8
    skills_max_resource_bytes_per_run: int = 8 * 1024 * 1024
    skills_max_draft_tests_per_hour: int = 30
    skills_max_script_bytes: int = 2 * 1024 * 1024
    skills_max_execution_seconds: int = 300
    skills_max_artifacts_per_run: int = 16
    skills_max_artifact_bytes_per_run: int = 20 * 1024 * 1024
    skills_safety_scanner_required: bool = True

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

    @property
    def trusted_tool_provider_map(self) -> dict[str, set[str]]:
        result: dict[str, set[str]] = {}
        for item in self.trusted_tool_providers.split(","):
            provider, separator, digest = item.strip().partition("=")
            if provider and separator and digest:
                result.setdefault(provider, set()).add(digest)
        return result


@lru_cache
def get_settings() -> Settings:
    return Settings()
