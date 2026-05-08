import pytest
from math import isclose
def calculate_square_area(side_length: float) -> float:
    if not isinstance(side_length, (int, float)):
        raise ValueError("边长必须是数值")
    if side_length <= 0:
        raise ValueError("边长必须大于0")
    return side_length ** 2
class TestCalculateSquareArea:
    # 正常场景
    @pytest.mark.parametrize("side, expected", [
        (1, 1),
        (2, 4),
        (3.5, 12.25),
        (0.5, 0.25),
        (100, 10000),
    ])
    def test_normal(self, side, expected):
        assert calculate_square_area(side) == expected
    # 边界值：极小正数
    def test_small_positive(self):
        result = calculate_square_area(1e-10)
        assert result == 1e-20
    # 边界值：极大正数
    def test_large_positive(self):
        result = calculate_square_area(1e10)
        assert result == 1e20
    # 异常输入：边长等于0
    def test_zero(self):
        with pytest.raises(ValueError, match="边长必须大于0"):
            calculate_square_area(0)
    # 异常输入：负整数
    def test_negative(self):
        with pytest.raises(ValueError, match="边长必须大于0"):
            calculate_square_area(-5)
    # 异常输入：负浮点数
    def test_negative_float(self):
        with pytest.raises(ValueError, match="边长必须大于0"):
            calculate_square_area(-0.1)
    # 异常输入：字符串
    def test_string(self):
        with pytest.raises(ValueError, match="边长必须是数值"):
            calculate_square_area("abc")
    # 异常输入：列表
    def test_list(self):
        with pytest.raises(ValueError, match="边长必须是数值"):
            calculate_square_area([1, 2])
    # 异常输入：None
    def test_none(self):
        with pytest.raises(ValueError, match="边长必须是数值"):
            calculate_square_area(None)
    # 异常输入：布尔值（True当作1？但isinstance检查时bool是int的子类）
    def test_boolean(self):
        with pytest.raises(ValueError, match="边长必须大于0"):
            calculate_square_area(True)  # True转换为1，但大于0，实际上不算异常，可正常运行
        with pytest.raises(ValueError, match="边长必须大于0"):
            calculate_square_area(False) # False转换为0，会触发边长不大于0的异常