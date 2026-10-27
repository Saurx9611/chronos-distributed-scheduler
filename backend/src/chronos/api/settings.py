from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class ApiSettings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore", hide_input_in_errors=True)
    database_url: SecretStr | None = Field(default=None, validation_alias="CHRONOS_DATABASE_URL")
    auth_pepper: SecretStr | None = Field(default=None, validation_alias="CHRONOS_API_AUTH_PEPPER")
    rate_limit: int = Field(default=60, ge=1, le=1000, validation_alias="CHRONOS_API_RATE_LIMIT")

    @model_validator(mode="after")
    def strong_pepper(self) -> ApiSettings:
        if self.database_url and not self.database_url.get_secret_value().startswith(
            ("postgresql://", "postgresql+psycopg://")
        ):
            raise ValueError("recovery API requires a PostgreSQL URL")
        if self.auth_pepper and len(self.auth_pepper.get_secret_value()) < 32:
            raise ValueError("API pepper must contain at least 32 characters")
        return self
