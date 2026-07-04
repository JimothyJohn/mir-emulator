from enum import Enum


class DockingType(str, Enum):
    BAR = "Bar"
    LEG = "Leg"

    def __str__(self) -> str:
        return str(self.value)
