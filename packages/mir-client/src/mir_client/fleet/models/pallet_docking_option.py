from enum import Enum


class PalletDockingOption(str, Enum):
    MARKER = "Marker"
    MARKERWITHML = "MarkerWithMl"
    ML = "Ml"

    def __str__(self) -> str:
        return str(self.value)
