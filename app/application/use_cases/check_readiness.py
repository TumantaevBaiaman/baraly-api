from app.application.ports.database_probe import DatabaseProbe


class CheckReadiness:
    def __init__(self, database: DatabaseProbe) -> None:
        self._database = database

    async def execute(self) -> bool:
        return await self._database.is_available()
