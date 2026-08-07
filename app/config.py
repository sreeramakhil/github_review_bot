"""
Central configuration, loaded from environment variables (.env).
"""
import os
from dataclasses import dataclass, field
from dotenv import load_dotenv

load_dotenv()


def _get_bool(name: str, default: bool) -> bool:
    val = os.getenv(name)
    if val is None:
        return default
    return val.strip().lower() in ("1", "true", "yes", "on")


def _get_list(name: str, default: list[str]) -> list[str]:
    val = os.getenv(name)
    if not val:
        return default
    return [x.strip() for x in val.split(",") if x.strip()]


@dataclass
class Settings:
    # --- GitHub ---
    github_token: str = os.getenv("GITHUB_TOKEN", "")
    webhook_secret: str = os.getenv("GITHUB_WEBHOOK_SECRET", "")
    github_api_base: str = os.getenv("GITHUB_API_BASE", "https://api.github.com")

    # --- Ollama ---
    ollama_host: str = os.getenv("OLLAMA_HOST", "http://localhost:11434")
    ollama_model: str = os.getenv("OLLAMA_MODEL", "qwen2.5-coder:7b")
    ollama_timeout_s: int = int(os.getenv("OLLAMA_TIMEOUT_S", "120"))
    ollama_temperature: float = float(os.getenv("OLLAMA_TEMPERATURE", "0.1"))

    # --- Review behavior ---
    max_files_per_pr: int = int(os.getenv("MAX_FILES_PER_PR", "25"))
    max_patch_lines_per_file: int = int(os.getenv("MAX_PATCH_LINES_PER_FILE", "400"))
    max_comments_per_pr: int = int(os.getenv("MAX_COMMENTS_PER_PR", "30"))
    post_summary_even_if_clean: bool = _get_bool("POST_SUMMARY_EVEN_IF_CLEAN", True)
    review_event: str = os.getenv("REVIEW_EVENT", "COMMENT")  # COMMENT | REQUEST_CHANGES | APPROVE

    ignored_path_patterns: list[str] = field(default_factory=lambda: _get_list(
        "IGNORED_PATH_PATTERNS",
        [
            "package-lock.json", "yarn.lock", "pnpm-lock.yaml", "poetry.lock",
            "*.min.js", "*.min.css", "*.svg", "*.png", "*.jpg", "*.jpeg",
            "*.lock", "dist/*", "build/*", "vendor/*", "node_modules/*",
        ],
    ))

    server_port: int = int(os.getenv("PORT", "8000"))


settings = Settings()
