import pytest
from typing import Union
import math
# 待测函数
from calculate_square_area import calculate_square_area
class TestCalculateSquareArea:
    # 正常场景
    def test_positive_integer(self):
        assert calculate_square_area(5) == 25
    def test_positive_float(self):
        assert calculate_square_area(0.5) == 0.25
    def test_large_positive(self):
        result = calculate_square_area(1e6)
        assert result == pytest.approx(1e12, rel=1e-9)
    def test_string_number(self):
        # 可转为float的字符串视为正常输入
        assert calculate_square_area("3") == 9
    def test_boolean_true(self):
        # boolean True 处理为 1.0
        assert calculate_square_area(True) == 1
    # 边界值
    def test_zero(self):
        assert calculate_square_area(0) == "Error: side length must be a positive finite number."
    def test_negative(self):
        assert calculate_square_area(-2) == "Error: side length must be a positive finite number."
    def test_very_small_positive(self):
        result = calculate_square_area(1e-10)
        assert result == pytest.approx(1e-20, rel=1e-9)
    def test_positive_infinity(self):
        assert calculate_square_area(float('inf')) == "Error: side length must be a positive finite number."
    def test_negative_infinity(self):
        assert calculate_square_area(float('-inf')) == "Error: side length must be a positive finite number."
    def test_nan(self):
        assert calculate_square_area(float('nan')) == "Error: side length must be a positive finite number."
    # 异常输入
    def test_none(self):
        assert calculate_square_area(None) == "Error: invalid input type."
    def test_non_numeric_string(self):
        assert calculate_square_area("abc") == "Error: invalid input type."
    def test_list(self):
        assert calculate_square_area([1,2]) == "Error: invalid input type."
    def test_dict(self):
        assert calculate_square_area({"side": 3}) == "Error: invalid input type."
    def test_complex_number(self):
        assert calculate_square_area(1+2j) == "Error: invalid input type."