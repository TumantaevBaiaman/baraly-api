from uuid import UUID, uuid4

from app.application.ports.storage import ImageStorage
from app.domain.exceptions import DomainError
from app.domain.user import User

_IMAGE_SIGNATURES: tuple[tuple[bytes, int, str, str], ...] = (
    (b"\xff\xd8\xff", 0, "image/jpeg", "jpg"),
    (b"\x89PNG\r\n\x1a\n", 0, "image/png", "png"),
    (b"WEBP", 8, "image/webp", "webp"),
)


def detect_image_type(data: bytes) -> tuple[str, str] | None:
    """Определяет формат по содержимому файла, а не по заголовку, который задаёт клиент."""
    for signature, offset, content_type, extension in _IMAGE_SIGNATURES:
        if data[offset : offset + len(signature)] == signature:
            if extension == "webp" and not data.startswith(b"RIFF"):
                continue
            return content_type, extension
    return None


def user_images_prefix(user_id: UUID) -> str:
    return f"users/{user_id}/"


async def store_image(storage: ImageStorage, max_bytes: int, prefix: str, data: bytes) -> str:
    if not data:
        raise DomainError("File is empty")
    if len(data) > max_bytes:
        raise DomainError(f"Image must be at most {max_bytes // (1024 * 1024)} MB")
    detected = detect_image_type(data)
    if detected is None:
        raise DomainError("Only JPEG, PNG and WebP images are allowed")
    content_type, extension = detected
    return await storage.save(f"{prefix}{uuid4().hex}.{extension}", data, content_type)


class UploadUserImage:
    """Фото пользователя, например для отзыва."""

    def __init__(self, storage: ImageStorage, max_bytes: int) -> None:
        self._storage = storage
        self._max_bytes = max_bytes

    @property
    def max_bytes(self) -> int:
        return self._max_bytes

    async def execute(self, user: User, data: bytes) -> str:
        return await store_image(self._storage, self._max_bytes, user_images_prefix(user.id), data)
