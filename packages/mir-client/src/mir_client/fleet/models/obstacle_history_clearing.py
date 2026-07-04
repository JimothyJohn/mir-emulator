from enum import Enum


class ObstacleHistoryClearing(str, Enum):
    CLEARALL = "ClearAll"
    CLEARINFRONTOFROBOT = "ClearInFrontOfRobot"
    NOCLEARING = "NoClearing"

    def __str__(self) -> str:
        return str(self.value)
