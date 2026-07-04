from enum import Enum


class ChargerType(str, Enum):
    MIRCHARGE24V = "MiRCharge24V"
    MIRCHARGE48V = "MiRCharge48V"
    MIRCHARGE48VFAST = "MiRCharge48VFast"

    def __str__(self) -> str:
        return str(self.value)
