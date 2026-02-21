"""Application configuration module.

This module handles all configuration settings for the Cotizador Proex microservice,
including environment variables, validation, and type safety.
"""

from functools import lru_cache
from typing import List, Optional
import os
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Application settings with validation and environment variable support.

    All settings can be overridden via environment variables.
    Required settings will raise ValidationError if not provided.
    """

    # HubSpot Configuration
    proex_access_token: str = Field(
        ...,
        description="HubSpot private app access token (required)",
        min_length=10
    )

    # CORS Configuration
    cors_origins: str = Field(
        default="http://localhost:3000,https://app.hubspot.com,https://app-eu1.hubspot.com",
        description="Comma-separated list of allowed CORS origins"
    )

    # App Configuration
    debug: bool = Field(
        default=False,
        description="Enable debug mode (not recommended for production)"
    )
    app_name: str = Field(
        default="Cotizador Proex Microservice",
        description="Application name for documentation and logging"
    )
    version: str = Field(
        default="1.0.0",
        description="Application version"
    )

    # Server Configuration
    host: str = Field(
        default="0.0.0.0",
        description="Server host address"
    )
    port: int = Field(
        default=8000,
        ge=1,
        le=65535,
        description="Server port number"
    )

    # AWS Configuration
    aws_region: Optional[str] = Field(
        default=None,
        description="AWS region for Lambda deployment"
    )
    lambda_function_name: Optional[str] = Field(
        default=None,
        description="AWS Lambda function name"
    )
    aws_account_id: Optional[str] = Field(
        default=None,
        description="AWS account ID for ECR"
    )
    ecr_repo_name: Optional[str] = Field(
        default=None,
        description="ECR repository name"
    )
    function_name: Optional[str] = Field(
        default=None,
        description="Lambda function name (used by deploy script)"
    )

    # Logging Configuration
    log_level: str = Field(
        default="INFO",
        description="Logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL)"
    )
    log_format: str = Field(
        default="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        description="Log message format"
    )

    # Performance Configuration
    request_timeout: int = Field(
        default=30,
        ge=1,
        le=300,
        description="Request timeout in seconds"
    )
    max_request_size: int = Field(
        default=16 * 1024 * 1024,  # 16MB
        ge=1024,
        description="Maximum request size in bytes"
    )

    @field_validator('proex_access_token')
    @classmethod
    def validate_hubspot_token(cls, v: str) -> str:
        """Validate HubSpot access token format."""
        if not v:
            raise ValueError("HubSpot access token is required")
        if not (v.startswith('pat-') or v.startswith('HUBSPOT_')):
            raise ValueError("Invalid HubSpot token format")
        return v

    @field_validator('log_level')
    @classmethod
    def validate_log_level(cls, v: str) -> str:
        """Validate logging level."""
        valid_levels = {'DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL'}
        if v.upper() not in valid_levels:
            raise ValueError(f"Invalid log level. Must be one of: {valid_levels}")
        return v.upper()

    @property
    def cors_origins_list(self) -> List[str]:
        """Convert comma-separated CORS origins to list.

        Returns:
            List of cleaned origin URLs.
        """
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @property
    def is_production(self) -> bool:
        """Check if running in production environment.

        Returns:
            True if not in debug mode.
        """
        return not self.debug

    class Config:
        """Pydantic configuration."""
        env_file = ".env"
        env_file_encoding = "utf-8"
        case_sensitive = False
        validate_assignment = True
        extra = "ignore"

@lru_cache()
def get_settings() -> Settings:
    """Get cached settings instance.

    This function uses LRU cache to ensure settings are loaded only once
    and reused throughout the application lifecycle.

    Returns:
        Configured Settings instance.
    """
    return Settings()


# Global settings instance for backward compatibility
settings = get_settings()
