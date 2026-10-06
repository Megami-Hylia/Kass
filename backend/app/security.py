import hashlib
import hmac
import secrets
from datetime import timedelta, timezone

from fastapi import Cookie, Depends, HTTPException, Response
from sqlalchemy import delete, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from . import config
from .config import COOKIE_SECURE, SESSION_DAYS
from .db import LoginSession, User, get_db, now


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=16384, r=8, p=1, maxmem=64 * 1024 * 1024)
    return f"scrypt${salt.hex()}${digest.hex()}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        _, salt, expected = encoded.split("$")
        digest = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=16384, r=8, p=1, maxmem=64 * 1024 * 1024)
        return hmac.compare_digest(digest.hex(), expected)
    except (ValueError, TypeError):
        return False


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def create_session(db: Session, user: User, response: Response):
    token = secrets.token_urlsafe(32)
    db.execute(delete(LoginSession).where(LoginSession.expires_at < now()))
    db.add(LoginSession(token_hash=token_hash(token), user_id=user.id, expires_at=now() + timedelta(days=SESSION_DAYS)))
    db.commit()
    response.set_cookie("cass_session", token, httponly=True, secure=COOKIE_SECURE, samesite="lax", max_age=SESSION_DAYS * 86400, path="/")


def local_storage_owner(db: Session) -> User:
    """Resolve the local library without registering an account or opening a session.

    Existing installations keep their original owner and every associated record.
    The fixed fallback ID also makes initialization idempotent on SQLite in tests;
    PostgreSQL serializes first-run creation across API processes.
    """
    statement = select(User).order_by(User.created_at, User.id).limit(1)
    owner = db.scalar(statement)
    if owner is not None:
        return owner
    if db.bind.dialect.name == "postgresql":
        db.execute(text("SELECT pg_advisory_xact_lock(14817206)"))
        owner = db.scalar(statement)
        if owner is not None:
            return owner
    owner = User(
        id="00000000-0000-4000-8000-000000000001",
        name="La mia libreria",
        email="local-library@cass.invalid",
        password_hash="!local-storage-no-password",
    )
    db.add(owner)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        owner = db.scalar(statement)
        if owner is None:
            raise
    return owner


def current_user(cass_session: str | None = Cookie(default=None), db: Session = Depends(get_db)) -> User:
    if config.LOCAL_DESKTOP_MODE:
        return local_storage_owner(db)
    if not cass_session:
        raise HTTPException(401, "Accedi per continuare.")
    session = db.get(LoginSession, token_hash(cass_session))
    # SQLite returns naive UTC values; PostgreSQL preserves its session timezone.
    # Only attach UTC to naive values so an aware offset is never discarded.
    expiry = session.expires_at if session else None
    if expiry is not None and expiry.tzinfo is None:
        expiry = expiry.replace(tzinfo=timezone.utc)
    if expiry is None or expiry <= now():
        raise HTTPException(401, "La sessione è scaduta. Accedi di nuovo.")
    user = db.get(User, session.user_id)
    if user is None:
        raise HTTPException(401, "Account non disponibile.")
    return user
