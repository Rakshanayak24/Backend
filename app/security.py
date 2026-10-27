from datetime import UTC, datetime, timedelta

import jwt
from argon2 import PasswordHasher
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models import Role, User

password_hash = PasswordHasher()
bearer = HTTPBearer(auto_error=False)


def hash_password(password: str) -> str:
    return password_hash.hash(password)


def verify_password(password: str, hashed: str) -> bool:
    try:
        return password_hash.verify(hashed, password)
    except Exception:
        return False


def create_access_token(user: User) -> tuple[str, int]:
    expires = int(settings.access_token_minutes * 60)
    payload = {"sub": user.id, "role": user.role.value, "iss": settings.jwt_issuer,
               "iat": datetime.now(UTC), "exp": datetime.now(UTC) + timedelta(seconds=expires)}
    return jwt.encode(payload, settings.jwt_secret, algorithm="HS256"), expires


def current_user(credentials: HTTPAuthorizationCredentials | None = Depends(bearer), db: Session = Depends(get_db)) -> User:
    unauthorized = HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or missing access token",
                                 headers={"WWW-Authenticate": "Bearer"})
    if not credentials:
        raise unauthorized
    try:
        payload = jwt.decode(credentials.credentials, settings.jwt_secret, algorithms=["HS256"],
                             issuer=settings.jwt_issuer, options={"require": ["exp", "sub", "iss"]})
    except jwt.PyJWTError:
        raise unauthorized
    user = db.get(User, payload["sub"])
    if not user or not user.is_active:
        raise unauthorized
    return user


def require_roles(*roles: Role):
    def check(user: User = Depends(current_user)) -> User:
        if user.role not in roles:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Insufficient role")
        return user
    return check
