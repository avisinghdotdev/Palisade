from typing import ClassVar

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Values the API reads from its environment at startup; a missing or blank one stops the start."""

    model_config: ClassVar[SettingsConfigDict] = SettingsConfigDict(
        env_ignore_empty=True
    )

    paypal_client_id: str
    paypal_client_secret: SecretStr
    paypal_webhook_id: str
