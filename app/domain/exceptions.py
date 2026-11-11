class DomainError(Exception):
    """Нарушение бизнес-правила."""


class NotFoundError(DomainError):
    """Запрошенная сущность не существует."""


class ConflictError(DomainError):
    """Операция противоречит существующим данным."""
