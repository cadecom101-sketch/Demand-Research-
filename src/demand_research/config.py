"""Configuration for demand research workflow."""

import os
from pathlib import Path
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Configuration settings from environment."""

    # API Configuration
    anthropic_api_key: str = os.getenv("ANTHROPIC_API_KEY", "")
    anthropic_model: str = os.getenv("ANTHROPIC_MODEL", "claude-opus-4-8")

    # Notion Configuration
    notion_api_key: str = os.getenv("NOTION_API_KEY", "")
    notion_database_id: str = os.getenv("NOTION_DATABASE_ID", "")

    # Paths
    project_root: Path = Path(__file__).parent.parent.parent
    briefs_dir: Path = project_root / "briefs"
    data_dir: Path = project_root / "data"
    screenshots_dir: Path = data_dir / "screenshots"
    cache_dir: Path = data_dir / "cache"

    # Research Configuration
    max_sources_per_phase: int = 10
    min_sources_required: int = 3
    research_timeout_seconds: int = 300
    enable_screenshots: bool = os.getenv("ENABLE_SCREENSHOTS", "false").lower() == "true"

    # Logging
    log_level: str = os.getenv("LOG_LEVEL", "INFO")

    class Config:
        env_file = ".env"
        case_sensitive = False

    def __init__(self, **data):
        super().__init__(**data)
        # Create directories if they don't exist
        self.briefs_dir.mkdir(parents=True, exist_ok=True)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.screenshots_dir.mkdir(parents=True, exist_ok=True)
        self.cache_dir.mkdir(parents=True, exist_ok=True)


settings = Settings()
