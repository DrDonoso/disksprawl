class AppError(Exception):
    """An error answered as JSON `{"code": ..., "fields"?: {...}}` with an HTTP status.

    The frontend translates `code`; `fields` maps request fields to their own error codes.
    """

    def __init__(
        self,
        code: str,
        status: int = 422,
        *,
        fields: dict[str, str] | None = None,
        headers: dict[str, str] | None = None,
    ):
        super().__init__(code)
        self.code = code
        self.status = status
        self.fields = fields
        self.headers = headers
