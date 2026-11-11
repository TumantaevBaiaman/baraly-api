import asyncio
import logging

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

logger = logging.getLogger(__name__)

PROBE_TIMEOUT_SECONDS = 3


class SqlAlchemyDatabaseProbe:
    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def is_available(self) -> bool:
        # Любой сбой подключения означает "не готов", а не ошибку 500.
        try:
            async with asyncio.timeout(PROBE_TIMEOUT_SECONDS), self._engine.connect() as connection:
                await connection.execute(text("SELECT 1"))
        except Exception:
            logger.warning("Database is unavailable", exc_info=True)
            return False
        return True
