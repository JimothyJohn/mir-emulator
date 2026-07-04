from enum import Enum


class ActionStatus(str, Enum):
    ABORTED = "Aborted"
    EXECUTING = "Executing"
    FAILED = "Failed"
    INVALID = "Invalid"
    SUCCEEDED = "Succeeded"
    UNKNOWN = "Unknown"

    def __str__(self) -> str:
        return str(self.value)
