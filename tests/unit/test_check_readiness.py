import pytest

from app.application.use_cases.check_readiness import CheckReadiness


class FakeDatabaseProbe:
    def __init__(self, available: bool) -> None:
        self._available = available

    async def is_available(self) -> bool:
        return self._available


@pytest.mark.parametrize("available", [True, False])
async def test_reports_database_availability(available: bool) -> None:
    use_case = CheckReadiness(FakeDatabaseProbe(available))

    assert await use_case.execute() is available
