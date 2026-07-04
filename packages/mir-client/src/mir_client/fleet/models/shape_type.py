from enum import Enum


class ShapeType(str, Enum):
    LINE = "Line"
    POLYGON = "Polygon"

    def __str__(self) -> str:
        return str(self.value)
