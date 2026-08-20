class PreflightError(Exception):
    """
    Базовая типизированная ошибка preflight-валидации с понятной инструкцией для пользователя.
    """
    def __init__(self, code: str, message: str, hint: str, step_name: str):
        self.code = code
        self.message = message
        self.hint = hint
        self.step_name = step_name
        super().__init__(f"[{step_name}] {code}: {message}")

    def to_dict(self) -> dict[str, str]:
        return {
            "code": self.code,
            "message": self.message,
            "hint": self.hint,
            "step": self.step_name,
        }
