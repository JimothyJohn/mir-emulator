from enum import Enum


class ZoneEventType(str, Enum):
    ENTRY = "Entry"
    EXIT = "Exit"

    def __str__(self) -> str:
        return str(self.value)
