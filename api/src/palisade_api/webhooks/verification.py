"""Signature check for PayPal webhook deliveries, by postback to PayPal."""

import json
from dataclasses import dataclass
from typing import Literal, final
from urllib.parse import urlsplit

from pydantic import BaseModel

from palisade_api.paypal.client import PayPalClient

VERIFY_PATH = "/v1/notifications/verify-webhook-signature"
# The only algorithm PayPal signs deliveries with.
AUTH_ALGO = "SHA256withRSA"


@dataclass(frozen=True)
class Transmission:
    """The signature headers PayPal sends with every delivery."""

    id: str
    time: str
    signature: str
    cert_url: str
    auth_algo: str

    def plausible(self) -> bool:
        """Whether the headers could be PayPal's at all, checked before PayPal is asked.

        Each check by postback costs a PayPal call, so a delivery that names a certificate off
        PayPal's hosts or another algorithm is refused without one.
        """
        cert = urlsplit(self.cert_url)
        return (
            self.auth_algo == AUTH_ALGO
            and cert.scheme == "https"
            and (cert.hostname or "").endswith(".paypal.com")
        )


class VerificationResponse(BaseModel):
    verification_status: Literal["SUCCESS", "FAILURE"]


@final
class WebhookVerifier:
    def __init__(self, paypal: PayPalClient, webhook_id: str) -> None:
        self._paypal = paypal
        self._webhook_id = webhook_id

    async def verify(self, transmission: Transmission, raw_event: bytes) -> bool:
        """True when PayPal confirms the signature over `raw_event` for this webhook.

        PayPal must see the event exactly as it was delivered, so the raw bytes are spliced into
        the request instead of being parsed and serialized again.
        """
        fields = json.dumps(
            {
                "auth_algo": transmission.auth_algo,
                "cert_url": transmission.cert_url,
                "transmission_id": transmission.id,
                "transmission_sig": transmission.signature,
                "transmission_time": transmission.time,
                "webhook_id": self._webhook_id,
            }
        ).encode()
        body = fields[:-1] + b',"webhook_event":' + raw_event + b"}"
        response = await self._paypal.post_json(VERIFY_PATH, body)
        response.raise_for_status()
        result = VerificationResponse.model_validate_json(response.content)
        return result.verification_status == "SUCCESS"
