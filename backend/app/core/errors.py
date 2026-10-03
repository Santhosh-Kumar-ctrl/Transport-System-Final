"""Domain errors. Services raise these; main.py maps them to HTTP responses.

Keeping services free of FastAPI types means they can be called from tests,
background tasks, scripts and (later) the Transport Operations Agent.
"""


class DomainError(Exception):
    status_code = 400
    code = "bad_request"

    def __init__(self, message: str, *, code: str | None = None, extra: dict | None = None):
        super().__init__(message)
        self.message = message
        if code:
            self.code = code
        self.extra = extra or {}


class NotFound(DomainError):
    status_code = 404
    code = "not_found"


class Conflict(DomainError):
    status_code = 409
    code = "conflict"


class Forbidden(DomainError):
    status_code = 403
    code = "forbidden"


class Unauthorized(DomainError):
    status_code = 401
    code = "unauthorized"


class InvalidState(DomainError):
    status_code = 422
    code = "invalid_state"


class TooManyRequests(DomainError):
    status_code = 429
    code = "too_many_attempts"
