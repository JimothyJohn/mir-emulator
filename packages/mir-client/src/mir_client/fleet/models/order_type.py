from enum import Enum


class OrderType(str, Enum):
    CHARGING = "Charging"
    EVACUATION = "Evacuation"
    GOTO = "GoTo"
    STAGING = "Staging"
    UNKNOWN = "Unknown"
    USER = "User"

    def __str__(self) -> str:
        return str(self.value)
