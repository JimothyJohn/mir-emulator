from enum import Enum


class Alert(str, Enum):
    DEADLOCKDETECTED = "DeadlockDetected"
    INCOMPATIBLEROBOT = "IncompatibleRobot"
    INTERNALALERTREPORTED = "InternalAlertReported"
    INVALIDSITEENTITY = "InvalidSiteEntity"
    PATHCONFLICT = "PathConflict"
    ROBOTERROR = "RobotError"
    ROBOTESTOP = "RobotEstop"

    def __str__(self) -> str:
        return str(self.value)
