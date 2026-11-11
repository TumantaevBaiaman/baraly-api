from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy.exc import IntegrityError

from app.domain.exceptions import ConflictError


@contextmanager
def conflict_on_integrity_error(message: str) -> Iterator[None]:
    try:
        yield
    except IntegrityError as exc:
        raise ConflictError(message) from exc
