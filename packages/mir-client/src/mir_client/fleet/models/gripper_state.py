from enum import Enum


class GripperState(str, Enum):
    CLOSED = "Closed"
    CLOSING = "Closing"
    ERROR = "Error"
    HOMING = "Homing"
    OPEN = "Open"
    OPENING = "Opening"

    def __str__(self) -> str:
        return str(self.value)
