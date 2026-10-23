from typing import ClassVar

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from palisade_api.logging import LogFormat, LogLevel


class DatabaseSettings(BaseSettings):
    """The database connection alone, which migrations need without the rest of the app's settings."""

    model_config: ClassVar[SettingsConfigDict] = SettingsConfigDict(
        env_ignore_empty=True
    )

    database_url: SecretStr


class Settings(DatabaseSettings):
    """Values the API reads from its environment at startup; a missing or blank one stops the start."""

    paypal_client_id: str
    paypal_client_secret: SecretStr
    paypal_webhook_id: str
    # The site's origin as the buyer's browser reaches it; PayPal sends the buyer back to URLs under it.
    public_origin: str
    # The PayPal account whose linked wallet is the shop's default buyer.
    demo_buyer_email: str
    log_level: LogLevel
    log_format: LogFormat
    # Startup applies pending migrations only where this is true, so one process owns the schema.
    run_migrations: bool
    # The job worker runs inside this process only where this is true, so a laptop never takes
    # the deployed app's jobs.
    run_worker: bool
