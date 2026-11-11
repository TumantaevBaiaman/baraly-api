class AuthenticationError(Exception):
    """Не удалось подтвердить личность пользователя."""


class AccessDeniedError(Exception):
    """Пользователь опознан, но у него нет прав на операцию."""


class ExternalServiceError(Exception):
    """Внешний сервис временно недоступен."""
