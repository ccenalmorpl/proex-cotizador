from functools import lru_cache
from typing import List, Optional
import os
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    proex_access_token: str = Field(
        ...,
        description="HubSpot private app access token (required)",
        min_length=10
    )

    cors_origins: str = Field(
        default="http://localhost:3000,https://app.hubspot.com,https://app-eu1.hubspot.com",
        description="Comma-separated list of allowed CORS origins"
    )

    debug: bool = Field(default=False, description="Enable debug mode")
    app_name: str = Field(default="Cotizador Proex Microservice")
    version: str = Field(default="1.0.0")

    host: str = Field(default="0.0.0.0")
    port: int = Field(default=8000, ge=1, le=65535)

    aws_region: Optional[str] = Field(default=None)
    lambda_function_name: Optional[str] = Field(default=None)
    aws_account_id: Optional[str] = Field(default=None)
    ecr_repo_name: Optional[str] = Field(default=None)
    function_name: Optional[str] = Field(default=None)

    log_level: str = Field(
        default="INFO",
        description="Logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL)"
    )
    log_format: str = Field(
        default="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )

    request_timeout: int = Field(default=30, ge=1, le=300)
    max_request_size: int = Field(default=16 * 1024 * 1024, ge=1024)

    @field_validator('proex_access_token')
    @classmethod
    def validate_hubspot_token(cls, v: str) -> str:
        if not v:
            raise ValueError("HubSpot access token is required")
        if not (v.startswith('pat-') or v.startswith('HUBSPOT_')):
            raise ValueError("Invalid HubSpot token format")
        return v

    @field_validator('log_level')
    @classmethod
    def validate_log_level(cls, v: str) -> str:
        valid_levels = {'DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL'}
        if v.upper() not in valid_levels:
            raise ValueError(f"Invalid log level. Must be one of: {valid_levels}")
        return v.upper()

    @property
    def cors_origins_list(self) -> List[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @property
    def is_production(self) -> bool:
        return not self.debug

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        case_sensitive = False
        validate_assignment = True
        extra = "ignore"

@lru_cache()
def get_settings() -> Settings:
    return Settings()

settings = get_settings()
