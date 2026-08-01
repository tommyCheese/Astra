from functools import lru_cache

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    log_level: str = "INFO"
    database_url: str = "sqlite+aiosqlite:///./astra-dev.db"
    sqlite_pool_size: int = Field(default=5, ge=1, le=32)
    sqlite_max_overflow: int = Field(default=0, ge=0, le=64)
    model_provider: str = "openai"
    model_name: str = "gpt-5"
    model_api_key: str = ""
    model_base_url: str = "https://api.openai.com/v1"
    model_http2_enabled: bool = True
    model_http_max_connections: int = Field(default=64, ge=1, le=512)
    model_http_max_keepalive_connections: int = Field(default=32, ge=1, le=256)
    model_http_keepalive_expiry_seconds: float = Field(default=300.0, ge=5.0, le=3600.0)
    model_http_connect_timeout_seconds: float = Field(default=10.0, gt=0, le=120.0)
    model_http_read_timeout_seconds: float = Field(default=60.0, gt=0, le=600.0)
    model_http_write_timeout_seconds: float = Field(default=30.0, gt=0, le=600.0)
    model_http_pool_timeout_seconds: float = Field(default=10.0, gt=0, le=120.0)
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
    agent_memory_cross_session_enabled: bool = False
    agent_memory_retrieval_policy_version: str = "memory-retrieval-v1"
    agent_memory_retrieval_candidate_limit: int = Field(default=100, ge=1, le=1_000)
    agent_memory_retrieval_max_items: int = Field(default=8, ge=0, le=50)
    agent_memory_retrieval_max_characters: int = Field(default=8_000, ge=0, le=100_000)
    agent_memory_retrieval_max_tokens: int = Field(default=2_000, ge=0, le=32_000)
    agent_memory_retrieval_min_confidence: float = Field(default=0.2, ge=0.0, le=1.0)
    agent_memory_retrieval_min_score: float = Field(default=0.05, ge=0.0, le=1.0)
    agent_memory_autodream_enabled: bool = False
    agent_memory_autodream_scan_seconds: int = Field(default=3_600, ge=60, le=604_800)
    agent_memory_autodream_cooldown_seconds: int = Field(default=86_400, ge=0, le=2_592_000)
    agent_memory_autodream_min_candidates: int = Field(default=2, ge=2, le=100)
    agent_memory_autodream_max_records_per_job: int = Field(default=100, ge=2, le=100)
    agent_memory_autodream_max_model_calls: int = Field(default=0, ge=0, le=8)
    agent_memory_autodream_lease_seconds: int = Field(default=120, ge=30, le=3_600)
    agent_memory_autodream_batch_size: int = Field(default=4, ge=1, le=32)
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
    scheduler_enabled: bool = False
    scheduler_poll_seconds: float = Field(default=1.0, ge=0.1, le=60.0)
    scheduler_lease_seconds: int = Field(default=30, ge=5, le=3_600)
    scheduler_batch_size: int = Field(default=20, ge=1, le=500)
    scheduler_max_dispatch_concurrency: int = Field(default=4, ge=1, le=64)
    scheduler_history_retention_days: int = Field(default=90, ge=1, le=36_500)
    scheduler_default_misfire_grace_seconds: int = Field(default=300, ge=0, le=604_800)
    scheduler_heartbeat_min_interval_seconds: int = Field(default=300, ge=60, le=86_400)
    context_window_fallback_tokens: int = Field(default=131_072, ge=16_384)
    context_system_reserve_tokens: int = Field(default=4_096, ge=1_024)
    context_output_reserve_tokens: int = Field(default=8_192, ge=1_024)
    context_auto_compact_ratio: float = Field(default=0.8, ge=0.5, le=0.95)
    context_compact_retain_runs: int = Field(default=4, ge=1, le=20)
    context_summary_max_chars: int = Field(default=12_000, ge=2_000, le=100_000)
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

    @model_validator(mode="after")
    def validate_autodream_bounds(self) -> "Settings":
        if (
            self.agent_memory_autodream_min_candidates
            > self.agent_memory_autodream_max_records_per_job
        ):
            raise ValueError("AutoDream minimum candidates cannot exceed records per job")
        return self

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
