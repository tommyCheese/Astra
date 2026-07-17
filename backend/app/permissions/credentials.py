from __future__ import annotations

import secrets
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from app.permissions.engine import PermissionEngine
from app.repositories.permissions import PermissionRepository
from app.schemas.permissions import (
    PermissionConditions,
    PermissionDecisionKind,
    PermissionPolicySet,
    PermissionRequest,
    PermissionSubject,
)


@dataclass(frozen=True)
class BrokeredCredential:
    grant_id: str
    service: str
    token: str
    expires_at: datetime
    scopes: tuple[str, ...]
    on_behalf_of: str


class CredentialBroker:
    def __init__(
        self,
        repository: PermissionRepository,
        permission_engine: PermissionEngine | None = None,
    ):
        self.repository = repository
        self.permission_engine = permission_engine or PermissionEngine()
        self._issued: dict[str, BrokeredCredential] = {}
        self._redaction_tokens: dict[str, datetime] = {}

    async def issue(
        self,
        *,
        run_id: str,
        agent_identity_id: str,
        service: str,
        scopes: Iterable[str],
        allowed_scopes: Iterable[str],
        on_behalf_of: str,
        subject: PermissionSubject,
        policies: PermissionPolicySet,
        ttl_seconds: int = 300,
    ) -> BrokeredCredential:
        requested = tuple(sorted(set(scopes)))
        if not set(requested) <= set(allowed_scopes):
            raise ValueError("Credential scope exceeds the broker policy")
        decision = self.permission_engine.authorize_request(
            PermissionRequest(
                subject=subject,
                action="credential_use",
                resource=f"credential://{service}",
                conditions=PermissionConditions(
                    data_labels=["credential"],
                    constraints={"scopes": list(requested), "on_behalf_of": on_behalf_of},
                ),
                context={"credential_scopes": list(requested)},
            ),
            policies,
        )
        if decision.decision != PermissionDecisionKind.allow:
            raise PermissionError(
                f"Credential issuance is not authorized: {decision.explanation.reason_code}"
            )
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
        self._redaction_tokens[credential.token] = credential.expires_at
        return credential

    async def revoke(self, grant_id: str) -> None:
        self._issued.pop(grant_id, None)
        await self.repository.revoke_credential_grant(grant_id)

    def redact(self, value: str) -> str:
        now = datetime.now(timezone.utc)
        self._redaction_tokens = {
            token: expires_at
            for token, expires_at in self._redaction_tokens.items()
            if expires_at > now
        }
        redacted = value
        for token in self._redaction_tokens:
            redacted = redacted.replace(token, "[REDACTED_CREDENTIAL]")
        return redacted
