import pytest
from pydantic import ValidationError

from palisade_api.settings import Settings


def test_blank_credentials_stop_startup(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PAYPAL_CLIENT_ID", "")
    monkeypatch.setenv("PAYPAL_CLIENT_SECRET", "")

    with pytest.raises(ValidationError, match="paypal_client_id"):
        Settings()  # pyright: ignore[reportCallIssue]
