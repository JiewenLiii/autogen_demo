def calculate_square_area(side_length: float) -> float:
    if not isinstance(side_length, (int, float)):
        raise ValueError("边长必须是数值")
    if side_length <= 0:
        raise ValueError("边长必须大于0")
    return side_length ** 2