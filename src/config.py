from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    ollama_base_url: str = "http://localhost:11434"
    llm_model: str = "qwen2.5-coder:7b"
    llm_temperature: float = 0.0
    llm_request_timeout: float = 300.0
    llm_num_ctx: int = 16_384

    default_max_score: int = 100
    grade_max_retries: int = 2

    max_files: int = 100
    max_file_bytes: int = 512 * 1024
    max_total_bytes: int = 8 * 1024 * 1024
    max_archive_entries: int = 1_000
    max_context_chars: int = 60_000
    max_chunk_chars: int = 15_000
    max_analysis_chars: int = 12_000

    sandbox_enabled: bool = True
    sandbox_image: str = "python:3.12-slim"
    sandbox_timeout_sec: int = 15
    sandbox_memory_mb: int = 256
    sandbox_backend: str = "docker"
    # Локальный запуск не изолирует код: только явное согласие.
    sandbox_allow_local: bool = False
    sandbox_output_limit_bytes: int = 64 * 1024
    sandbox_cpus: float = 1.0
    sandbox_pids_limit: int = 128
    sandbox_tmpfs_mb: int = 16
    sandbox_user: str = "65534:65534"

    api_host: str = "127.0.0.1"
    api_port: int = 8000
    # Пустое значение отключает проверку API-ключа.
    api_key: str = ""
    # 0 отключает ограничение частоты запросов.
    rate_limit_per_minute: int = 0


@lru_cache
def get_settings() -> Settings:
    return Settings()
