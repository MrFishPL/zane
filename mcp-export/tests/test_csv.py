"""Tests for the CSV BOM generator."""

import csv
import io
import json
import os

import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import csv_generator

FIXTURES_DIR = os.path.join(os.path.dirname(__file__), "fixtures")


def _load_fixture(name: str) -> list[dict]:
    with open(os.path.join(FIXTURES_DIR, name)) as f:
        return json.load(f)


def test_csv_has_exactly_two_columns():
    """CSV output must have exactly two columns: MPN and Quantity."""
    components = _load_fixture("sample_components.json")
    result = csv_generator.generate(components, volume=1)

    reader = csv.reader(io.StringIO(result))
    rows = list(reader)

    # Header row
    assert rows[0] == ["Manufacturer Part Number", "Quantity"]

    # Every row must have exactly 2 columns
    for i, row in enumerate(rows):
        assert len(row) == 2, f"Row {i} has {len(row)} columns, expected 2"


def test_quantity_multiplication():
    """Quantity should be qty_per_unit * volume for each component."""
    components = _load_fixture("sample_components.json")
    volume = 100
    result = csv_generator.generate(components, volume=volume)

    reader = csv.reader(io.StringIO(result))
    rows = list(reader)

    # Skip header
    data_rows = rows[1:]

    assert len(data_rows) == len(components)

    for row, comp in zip(data_rows, components):
        expected_qty = comp["qty_per_unit"] * volume
        assert row[0] == comp["mpn"]
        assert int(row[1]) == expected_qty


def test_empty_components_list():
    """Empty component list should produce CSV with header only."""
    result = csv_generator.generate([], volume=10)

    reader = csv.reader(io.StringIO(result))
    rows = list(reader)

    assert len(rows) == 1  # Header only
    assert rows[0] == ["Manufacturer Part Number", "Quantity"]


def test_single_component():
    """Test with a single component."""
    components = _load_fixture("single_component.json")
    result = csv_generator.generate(components, volume=50)

    reader = csv.reader(io.StringIO(result))
    rows = list(reader)

    assert len(rows) == 2  # Header + 1 data row
    assert rows[1][0] == "STM32F103C8T6"
    assert int(rows[1][1]) == 50  # qty_per_unit=1 * volume=50


def test_various_quantities():
    """Components with different qty_per_unit values multiply correctly."""
    components = [
        {"mpn": "PART_A", "qty_per_unit": 1},
        {"mpn": "PART_B", "qty_per_unit": 5},
        {"mpn": "PART_C", "qty_per_unit": 10},
        {"mpn": "PART_D", "qty_per_unit": 100},
    ]
    volume = 25
    result = csv_generator.generate(components, volume=volume)

    reader = csv.reader(io.StringIO(result))
    rows = list(reader)
    data_rows = rows[1:]

    expected = [25, 125, 250, 2500]
    for row, exp in zip(data_rows, expected):
        assert int(row[1]) == exp


def test_volume_of_one():
    """Volume of 1 should give raw qty_per_unit values."""
    components = _load_fixture("sample_components.json")
    result = csv_generator.generate(components, volume=1)

    reader = csv.reader(io.StringIO(result))
    rows = list(reader)
    data_rows = rows[1:]

    for row, comp in zip(data_rows, components):
        assert int(row[1]) == comp["qty_per_unit"]
