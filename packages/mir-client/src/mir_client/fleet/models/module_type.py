from enum import Enum


class ModuleType(str, Enum):
    INTERNAL4PORT = "Internal4Port"
    WISE = "Wise"

    def __str__(self) -> str:
        return str(self.value)
