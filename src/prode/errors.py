"""Domain errors. Messages are written to be read by an LLM client."""


class ProdeError(Exception):
    """Base class for all expected, user-facing errors."""


class NotFoundError(ProdeError):
    def __init__(self, message: str, suggestions: list[dict] | None = None):
        self.suggestions = suggestions or []
        if self.suggestions:
            hint = ", ".join(f"{s['slug']} ({s['name']})" for s in self.suggestions)
            message = f"{message} Did you mean: {hint}?"
        super().__init__(message)


class AlreadyExistsError(ProdeError):
    pass


class ValidationError(ProdeError):
    pass
