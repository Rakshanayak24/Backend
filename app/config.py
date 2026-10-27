from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "Amrutam Care API"
    environment: str = "development"
    database_url: str = "sqlite:///./amrutam.db"
    jwt_secret: str = "development-only-change-this-secret-key"
    mfa_encryption_key: str = ""
    jwt_issuer: str = "amrutam-care"
    access_token_minutes: int = 15
    refresh_token_days: int = 7
    redis_url: str = "redis://localhost:6379/0"
    cors_origins: str = "http://localhost:3000"
    rate_limit_per_minute: int = 120
    log_level: str = "INFO"
    otel_exporter_otlp_endpoint: str = ""
    bootstrap_admin_email: str = ""
    bootstrap_admin_password: str = ""
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


settings = Settings()
