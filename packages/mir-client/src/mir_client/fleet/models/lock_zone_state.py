from enum import Enum


class LockZoneState(str, Enum):
    LOCKED = "Locked"
    LOCKING = "Locking"
    UNLOCKED = "Unlocked"

    def __str__(self) -> str:
        return str(self.value)
