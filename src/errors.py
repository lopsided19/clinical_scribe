class ScribeError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


class ConfigError(ScribeError):
    pass


class InputError(ScribeError):
    pass


class CaseError(ScribeError):
    pass
