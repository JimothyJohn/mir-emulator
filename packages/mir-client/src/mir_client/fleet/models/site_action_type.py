from enum import Enum


class SiteActionType(str, Enum):
    CREATE = "Create"
    DELETE = "Delete"
    LOCKSTATECHANGE = "LockStateChange"
    NONE = "None"
    UPDATE = "Update"

    def __str__(self) -> str:
        return str(self.value)
