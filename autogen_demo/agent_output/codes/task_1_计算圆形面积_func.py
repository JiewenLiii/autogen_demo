import math
from typing import Union
def circle_area(radius: Union[int, float]) -> float:
    try:
        r = float(radius)
    except (TypeError, ValueError):
        raise TypeError("Radius must be a numeric value")
    if r <= 0:
        raise ValueError("Radius must be positive")
    return math.pi * r ** 2