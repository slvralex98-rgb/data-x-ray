"""Exact behavior of user-selected numeric rules, independent of heuristics."""
from decimal import Decimal

import polars as pl
import pytest

from data_xray.loaders import Dataset
from data_xray.rules import RuleSpec, evaluate_rules, load_rule_set, save_rule_set


def evaluate(data, rule):
    dataset = Dataset(pl.DataFrame(data), 'memory', 'parquet', False, [])
    return evaluate_rules(dataset, [rule]).evaluations[0]


@pytest.mark.parametrize('representation', [int, str, Decimal])
@pytest.mark.parametrize('operator,expected', [('=', [0, 1]), ('<=', [0]), ('>=', [1])])
def test_comparison_distinguishes_adjacent_large_numbers(representation, operator, expected):
    low, high = 2 ** 53, 2 ** 53 + 1
    result = evaluate({'a': [representation(high), representation(low)],
                       'b': [representation(low), representation(high)]},
                      RuleSpec('comparison', ('a', 'b'), {'operator': operator, 'type': 'number'}))
    assert result.row_indices == expected
    assert result.evidence['invalid_rows'] == 0


@pytest.mark.parametrize('inclusive,expected', [(False, [0]), (True, [])])
def test_numeric_constraint_preserves_integer_boundary_after_json(tmp_path, inclusive, expected):
    low = 2 ** 53
    rule = RuleSpec('numeric_constraint', ('x',), {'lower': low, 'upper': None,
                    'lower_inclusive': inclusive, 'upper_inclusive': False}, 'accepted')
    loaded = load_rule_set(save_rule_set([rule], tmp_path / 'rules.json'))[0]
    assert loaded.params['lower'] == low
    assert evaluate({'x': [low, low + 1]}, loaded).row_indices == expected


def test_exact_decimal_comparison_and_constraint_keep_decimal_comma():
    rule = RuleSpec('comparison', ('a', 'b'), {'operator': '=', 'type': 'number'})
    result = evaluate({'a': ['9007199254740992.1', '1,25', '1e3', 'invalid', 'Infinity'],
                       'b': ['9007199254740992.2', '1.25', '1000', '0', '0']}, rule)
    assert result.row_indices == [0, 3, 4]
    assert result.evidence['invalid_rows'] == 2
    bound = RuleSpec('numeric_constraint', ('x',), {'lower': 0.1, 'upper': None,
                     'lower_inclusive': True, 'upper_inclusive': False})
    assert evaluate({'x': ['0.1', '0.099999999999999999']}, bound).row_indices == [1]


@pytest.mark.parametrize('inclusive,expected', [(False, [1, 2]), (True, [2])])
def test_large_integer_upper_boundary(inclusive, expected):
    high = 2 ** 53 + 1
    rule = RuleSpec('numeric_constraint', ('x',), {'lower': None, 'upper': high,
                    'lower_inclusive': False, 'upper_inclusive': inclusive})
    assert evaluate({'x': [high - 1, high, high + 1]}, rule).row_indices == expected


def test_exact_numeric_rules_keep_missing_and_invalid_counts():
    rule = RuleSpec('comparison', ('a', 'b'), {'operator': '=', 'type': 'number'})
    result = evaluate({'a': ['NULL', '', None, 'bad', '1e999999999999999999999999'],
                       'b': ['0'] * 5}, rule)
    assert (result.evaluated, result.skipped, result.violations) == (2, 3, 2)
    assert result.evidence['invalid_rows'] == 2

