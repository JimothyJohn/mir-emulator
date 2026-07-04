from enum import Enum


class PlcAction(str, Enum):
    ADD = "Add"
    NONE = "None"
    SET = "Set"
    SUBTRACT = "Subtract"

    def __str__(self) -> str:
        return str(self.value)
