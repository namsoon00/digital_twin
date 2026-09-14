"""A collection attempt postponed until provider access is available."""


class ExternalCallDeferred(RuntimeError):
    def __init__(self, message, retry_at="", reason="rate-limited"):
        super().__init__(message)
        self.retry_at = retry_at
        self.reason = reason
