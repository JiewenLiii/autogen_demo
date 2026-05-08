from typing import Union
def calculate_square_area(side_length: float) -> Union[float, str]:
    try:
        side = float(side_length)
        if side <= 0 or side != side or side == float('inf') or side == float('-inf'):
            return "Error: side length must be a positive finite number."
        return side * side
    except (ValueError, TypeError):
        return "Error: invalid input type."