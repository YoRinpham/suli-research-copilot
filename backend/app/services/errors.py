class ServiceError(Exception):
    def __init__(self, status_code: int, code: str, message: str, stage: str, details: dict[str, object] | None = None):
        self.status_code = status_code
        self.code = code
        self.message = message
        self.stage = stage
        self.details = details or {}
        super().__init__(message)
