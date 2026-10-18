import logging

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from common.auth import Role
from identity_service.models import User
from identity_service.passwords import DUMMY_HASH, hash_password, verify_password
from identity_service.schemas import UserOut

log = logging.getLogger(__name__)


class EmailAlreadyRegistered(Exception):
    pass


def register(session_factory: sessionmaker, email: str, password: str, role: str = Role.CUSTOMER) -> UserOut:
    try:
        with session_factory.begin() as session:
            user = User(email=email, password_hash=hash_password(password), role=role)
            session.add(user)
            session.flush()
            return UserOut.model_validate(user)
    except IntegrityError as exc:  # unique email; also covers two registrations racing each other
        raise EmailAlreadyRegistered(email) from exc


def authenticate(session_factory: sessionmaker, email: str, password: str) -> UserOut | None:
    with session_factory() as session:
        user = session.scalar(select(User).where(User.email == email))
        if user is None:
            verify_password(password, DUMMY_HASH)  # equalise timing with the wrong-password path
            return None
        if not verify_password(password, user.password_hash):
            return None
        return UserOut.model_validate(user)


def get_user(session_factory: sessionmaker, user_id: str) -> UserOut | None:
    with session_factory() as session:
        user = session.get(User, user_id)
        return UserOut.model_validate(user) if user else None


def ensure_admin(session_factory: sessionmaker, email: str, password: str) -> None:
    with session_factory() as session:
        if session.scalar(select(User).where(User.email == email)) is not None:
            return
    try:
        register(session_factory, email, password, role=Role.ADMIN)
        log.info("bootstrap admin created")
    except EmailAlreadyRegistered:
        pass  # another replica created it first
