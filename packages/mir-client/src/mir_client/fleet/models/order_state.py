from enum import Enum


class OrderState(str, Enum):
    ABORTED = "Aborted"
    ABORTING = "Aborting"
    CREATED = "Created"
    EXECUTING = "Executing"
    FINISHED = "Finished"
    OUTBOUND = "Outbound"
    PAUSED = "Paused"
    PENDING = "Pending"
    UPDATED = "Updated"
    WAITINGFORINPUT = "WaitingForInput"

    def __str__(self) -> str:
        return str(self.value)
