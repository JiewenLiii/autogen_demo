import pytest
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
class TestCircleArea:
    # 正常场景
    @pytest.mark.parametrize("radius, expected", [
        (1, math.pi),
        (2, 4 * math.pi),
        (0.5, 0.25 * math.pi),
        (10, 100 * math.pi),
        (1000, 1000000 * math.pi),
    ])
    def test_normal(self, radius, expected):
        assert circle_area(radius) == pytest.approx(expected, rel=1e-9)
    # 边界值：零和负数
    @pytest.mark.parametrize("radius", [0, -1, -0.0001, -100.5])
    def test_non_positive(self, radius):
        with pytest.raises(ValueError, match="Radius must be positive"):
            circle_area(radius)
    # 异常输入：非数值
    @pytest.mark.parametrize("radius", ["abc", None, [1, 2], {"r": 1}])
    def test_non_numeric(self, radius):
        with pytest.raises(TypeError, match="Radius must be a numeric value"):
            circle_area(radius)
    # 额外边界：大半径（不溢出即可）
    def test_large_radius(self):
        r = 1e10
        area = circle_area(r)
        assert area == pytest.approx(math.pi * 1e20, rel=1e-9)
    # 额外边界：极小正浮点
    def test_tiny_positive(self):
        r = 1e-10
        area = circle_area(r)
        assert area == pytest.approx(math.pi * 1e-20, rel=1e-9)