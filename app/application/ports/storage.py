from typing import Protocol


class ImageStorage(Protocol):
    async def save(self, key: str, data: bytes, content_type: str) -> str:
        """Сохраняет файл и возвращает его публичный URL."""
        ...

    def public_url(self, key: str) -> str:
        """Публичный URL файла или префикса ключей в хранилище."""
        ...
