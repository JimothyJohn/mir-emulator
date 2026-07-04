from enum import Enum


class OrderStatus(str, Enum):
    ABORTED = "Aborted"
    ABORTING = "Aborting"
    EXECUTING = "Executing"
    FINISHED = "Finished"
    INVALID = "Invalid"
    OUTBOUND = "Outbound"
    PENDING = "Pending"
    WAITING = "Waiting"

    def __str__(self) -> str:
        return str(self.value)
