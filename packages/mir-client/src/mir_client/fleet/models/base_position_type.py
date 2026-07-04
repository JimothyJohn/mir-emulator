from enum import Enum


class BasePositionType(str, Enum):
    EVACUATION = "Evacuation"
    ROBOT = "Robot"
    STAGING = "Staging"

    def __str__(self) -> str:
        return str(self.value)
