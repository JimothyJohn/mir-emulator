from enum import Enum


class ZoneEventAddress(str, Enum):
    INTEGRATION = "Integration"
    TOPMODULE = "TopModule"

    def __str__(self) -> str:
        return str(self.value)
