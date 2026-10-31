"""Public, credential-free errors and results shared by channel collectors."""
from dataclasses import dataclass, field


class MonitorError(RuntimeError):
    def __init__(self, code, message, status_code=502):
        super().__init__(message)
        self.code = code
        self.status_code = status_code


@dataclass
class ScanResult:
    videos: list[dict] = field(default_factory=list)
    complete: bool = True
    message: str = ''
    observed_ids: list[str] = field(default_factory=list)
