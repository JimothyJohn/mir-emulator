from enum import Enum


class RequestedRobotEndState(str, Enum):
    OPERATIONAL = "Operational"
    PAUSED = "Paused"

    def __str__(self) -> str:
        return str(self.value)
