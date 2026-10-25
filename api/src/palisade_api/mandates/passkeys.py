"""Passkeys that buyers register in their browser, with no account, to sign mandates."""

import base64
import secrets
from typing import final
from urllib.parse import urlsplit
from uuid import UUID, uuid7

from pydantic import BaseModel, JsonValue, TypeAdapter
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from webauthn import (
    generate_authentication_options,
    generate_registration_options,
    options_to_json,
    verify_authentication_response,
    verify_registration_response,
)
from webauthn.helpers.exceptions import (
    InvalidAuthenticationResponse,
    InvalidRegistrationResponse,
)
from webauthn.helpers.structs import (
    AuthenticatorSelectionCriteria,
    PublicKeyCredentialDescriptor,
    ResidentKeyRequirement,
    UserVerificationRequirement,
)

from palisade_api.mandates.models import PasskeyChallengeRecord, PasskeyRecord

RP_NAME = "Palisade demo buyer"
OPTIONS = TypeAdapter(dict[str, JsonValue])


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def unb64url(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


class RegistrationStart(BaseModel):
    """What the browser's `navigator.credentials.create()` needs, and the challenge to return."""

    challenge: str
    options: dict[str, JsonValue]


class Registration(BaseModel):
    challenge: str
    credential: dict[str, JsonValue]


class Passkey(BaseModel):
    id: UUID


class UnknownPasskey(LookupError):
    def __init__(self, passkey_id: UUID) -> None:
        super().__init__(f"no passkey has the id {passkey_id}")


class PasskeyRefused(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(f"the passkey was refused: {reason}")


@final
class Passkeys:
    def __init__(
        self, sessions: async_sessionmaker[AsyncSession], public_origin: str
    ) -> None:
        self._sessions = sessions
        self._origin = public_origin
        self._rp_id = urlsplit(public_origin).hostname or public_origin

    async def start_registration(self) -> RegistrationStart:
        """A fresh challenge and the options for a discoverable passkey under a random handle."""
        options = generate_registration_options(
            rp_id=self._rp_id,
            rp_name=RP_NAME,
            user_name=RP_NAME,
            user_id=secrets.token_bytes(32),
            authenticator_selection=AuthenticatorSelectionCriteria(
                resident_key=ResidentKeyRequirement.PREFERRED,
                user_verification=UserVerificationRequirement.PREFERRED,
            ),
        )
        challenge = b64url(options.challenge)
        async with self._sessions.begin() as session:
            session.add(PasskeyChallengeRecord(challenge=challenge))
        return RegistrationStart(
            challenge=challenge, options=_json(options_to_json(options))
        )

    async def register(self, registration: Registration) -> Passkey:
        """Verify the browser's answer against its challenge and keep the public key.

        Raises:
            PasskeyRefused: the challenge is unknown or the answer does not verify.
        """
        async with self._sessions.begin() as session:
            consumed = await session.execute(
                delete(PasskeyChallengeRecord)
                .where(PasskeyChallengeRecord.challenge == registration.challenge)
                .returning(PasskeyChallengeRecord.challenge)
            )
            if consumed.scalar_one_or_none() is None:
                raise PasskeyRefused("the registration challenge is unknown or used")
            try:
                verified = verify_registration_response(
                    credential=registration.credential,
                    expected_challenge=unb64url(registration.challenge),
                    expected_rp_id=self._rp_id,
                    expected_origin=self._origin,
                )
            except InvalidRegistrationResponse as error:
                raise PasskeyRefused(str(error)) from error
            record = PasskeyRecord(
                id=uuid7(),
                credential_id=b64url(verified.credential_id),
                public_key=b64url(verified.credential_public_key),
                sign_count=verified.sign_count,
            )
            session.add(record)
            return Passkey(id=record.id)

    async def assertion_options(
        self, passkey_id: UUID, challenge: bytes
    ) -> dict[str, JsonValue]:
        """The options for `navigator.credentials.get()` that sign `challenge` with the passkey."""
        async with self._sessions() as session:
            record = await session.get(PasskeyRecord, passkey_id)
        if record is None:
            raise UnknownPasskey(passkey_id)
        options = generate_authentication_options(
            rp_id=self._rp_id,
            challenge=challenge,
            allow_credentials=[
                PublicKeyCredentialDescriptor(id=unb64url(record.credential_id))
            ],
            user_verification=UserVerificationRequirement.PREFERRED,
        )
        return _json(options_to_json(options))

    async def verify(
        self, passkey_id: UUID, challenge: bytes, credential: dict[str, JsonValue]
    ) -> None:
        """Check the browser's assertion over `challenge`; the sign count moves on.

        Raises:
            UnknownPasskey, PasskeyRefused: no such passkey, or the assertion does not verify.
        """
        async with self._sessions.begin() as session:
            record = await session.scalar(
                select(PasskeyRecord)
                .where(PasskeyRecord.id == passkey_id)
                .with_for_update()
            )
            if record is None:
                raise UnknownPasskey(passkey_id)
            try:
                result = verify_authentication_response(
                    credential=credential,
                    expected_challenge=challenge,
                    expected_rp_id=self._rp_id,
                    expected_origin=self._origin,
                    credential_public_key=unb64url(record.public_key),
                    credential_current_sign_count=record.sign_count,
                )
            except InvalidAuthenticationResponse as error:
                raise PasskeyRefused(str(error)) from error
            record.sign_count = result.new_sign_count


def _json(text: str) -> dict[str, JsonValue]:
    return OPTIONS.validate_json(text)
