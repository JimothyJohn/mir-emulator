from enum import Enum


class TopModuleEventType(str, Enum):
    ERROR = "Error"
    EVENT = "Event"

    def __str__(self) -> str:
        return str(self.value)
