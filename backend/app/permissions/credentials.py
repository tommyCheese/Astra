from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Iterable

from app.repositories.permissions import PermissionRepository


@dataclass(frozen=True)
class BrokeredCredential:
    grant_id: str
    service: str
    token: str
    expires_at: datetime
    scopes: tuple[str, ...]
    on_behalf_of: str


class CredentialBroker:
    def __init__(self, repository: PermissionRepository):
        self.repository = repository
        self._issued: dict[str, BrokeredCredential] = {}

    async def issue(
        self,
        *,
        run_id: str,
        agent_identity_id: str,
        service: str,
        scopes: Iterable[str],
        allowed_scopes: Iterable[str],
        on_behalf_of: str,
        ttl_seconds: int = 300,
    ) -> BrokeredCredential:
        requested = tuple(sorted(set(scopes)))
        if not set(requested) <= set(allowed_scopes):
            raise ValueError("Credential scope exceeds the broker policy")
        ttl_seconds = max(1, min(ttl_seconds, 900))
        expires_at = datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds)
        grant = await self.repository.create_credential_grant(
            run_id=run_id,
            agent_identity_id=agent_identity_id,
            service=service,
            scopes=list(requested),
            actions=["use"],
            expires_at=expires_at,
            metadata={"on_behalf_of": on_behalf_of, "secret_persisted": False},
        )
        credential = BrokeredCredential(
            grant_id=grant.id,
            service=service,
            token=secrets.token_urlsafe(32),
            expires_at=expires_at,
            scopes=requested,
            on_behalf_of=on_behalf_of,
        )
        self._issued[grant.id] = credential
        return credential

    async def revoke(self, grant_id: str) -> None:
        self._issued.pop(grant_id, None)
        await self.repository.revoke_credential_grant(grant_id)

    def redact(self, value: str) -> str:
        redacted = value
        for credential in self._issued.values():
            redacted = redacted.replace(credential.token, "[REDACTED_CREDENTIAL]")
        return redacted
