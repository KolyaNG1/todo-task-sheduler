class ApplicationError(Exception):
    code = "APPLICATION_ERROR"
    status_code = 400

    def __init__(self, message: str, *, details: dict | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details or {}


class NotFoundError(ApplicationError):
    code = "NOT_FOUND"
    status_code = 404


class ConflictError(ApplicationError):
    code = "CONFLICT"
    status_code = 409


class ValidationError(ApplicationError):
    code = "DOMAIN_VALIDATION"
    status_code = 422
