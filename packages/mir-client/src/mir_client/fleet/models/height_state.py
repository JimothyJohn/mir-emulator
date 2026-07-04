from enum import Enum


class HeightState(str, Enum):
    CHANGING = "Changing"
    ERROR = "Error"
    HOMING = "Homing"
    IDLE = "Idle"

    def __str__(self) -> str:
        return str(self.value)
