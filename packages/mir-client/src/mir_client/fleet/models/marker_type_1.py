from enum import Enum


class MarkerType1(str, Enum):
    BAR = "Bar"
    L = "L"
    PALLETRACK = "PalletRack"
    STRIPE = "Stripe"
    V = "V"
    VL = "VL"

    def __str__(self) -> str:
        return str(self.value)
