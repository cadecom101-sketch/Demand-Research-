"""Configuration for demand research workflow."""

from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field


class Settings(BaseSettings):
    """Configuration settings from environment."""

    # API Configuration
    anthropic_api_key: str = Field(default="", alias="ANTHROPIC_API_KEY")
    anthropic_model: str = Field(default="claude-opus-4-8", alias="ANTHROPIC_MODEL")
    anthropic_effort: str = Field(default="high", alias="ANTHROPIC_EFFORT")

    # Notion Configuration
    notion_api_key: str = Field(default="", alias="NOTION_API_KEY")
    notion_database_id: str = Field(default="", alias="NOTION_DATABASE_ID")

    # Research Configuration
    max_sources_per_phase: int = Field(default=10, alias="MAX_SOURCES_PER_PHASE")
    min_sources_required: int = Field(default=3, alias="MIN_SOURCES_REQUIRED")
    research_timeout_seconds: int = Field(default=300, alias="RESEARCH_TIMEOUT_SECONDS")
    enable_screenshots: bool = Field(default=False, alias="ENABLE_SCREENSHOTS")

    # Logging
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")

    model_config = SettingsConfigDict(
        env_file=".env",
        case_sensitive=False,
        populate_by_name=True,
        extra="ignore",
    )

    @property
    def project_root(self) -> Path:
        """Get project root directory."""
        return Path(__file__).parent.parent.parent

    @property
    def briefs_dir(self) -> Path:
        """Get briefs directory."""
        return self.project_root / "briefs"

    @property
    def data_dir(self) -> Path:
        """Get data directory."""
        return self.project_root / "data"

    @property
    def screenshots_dir(self) -> Path:
        """Get screenshots directory."""
        return self.data_dir / "screenshots"

    @property
    def cache_dir(self) -> Path:
        """Get cache directory."""
        return self.data_dir / "cache"

    def __init__(self, **data):
        super().__init__(**data)
        # Create directories if they don't exist
        self.briefs_dir.mkdir(parents=True, exist_ok=True)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.screenshots_dir.mkdir(parents=True, exist_ok=True)
        self.cache_dir.mkdir(parents=True, exist_ok=True)


settings = Settings()
