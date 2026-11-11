import hashlib
import secrets
from datetime import datetime, timedelta
from uuid import UUID

import jwt

from app.application.exceptions import AuthenticationError
from app.application.ports.auth import NewRefreshToken

_ALGORITHM = "HS256"


class JwtTokenService:
    def __init__(self, secret: str, access_ttl: timedelta, refresh_ttl: timedelta) -> None:
        self._secret = secret
        self._access_ttl = access_ttl
        self._refresh_ttl = refresh_ttl

    @property
    def access_token_ttl_seconds(self) -> int:
        return int(self._access_ttl.total_seconds())

    def issue_access_token(self, user_id: UUID, now: datetime) -> str:
        payload = {"sub": str(user_id), "iat": now, "exp": now + self._access_ttl}
        return jwt.encode(payload, self._secret, algorithm=_ALGORITHM)

    def read_access_token(self, token: str) -> UUID:
        try:
            payload = jwt.decode(
                token,
                self._secret,
                algorithms=[_ALGORITHM],
                options={"require": ["sub", "exp"]},
            )
            return UUID(payload["sub"])
        except (jwt.PyJWTError, ValueError) as exc:
            raise AuthenticationError("Invalid access token") from exc

    def new_refresh_token(self, now: datetime) -> NewRefreshToken:
        value = secrets.token_urlsafe(48)
        return NewRefreshToken(
            value=value,
            token_hash=self.hash_refresh_token(value),
            expires_at=now + self._refresh_ttl,
        )

    def hash_refresh_token(self, value: str) -> str:
        return hashlib.sha256(value.encode()).hexdigest()
