"""Tests for the KiCad library generator."""

import io
import json
import os
import zipfile

import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import kicad_generator

FIXTURES_DIR = os.path.join(os.path.dirname(__file__), "fixtures")


def _load_fixture(name: str) -> list[dict]:
    with open(os.path.join(FIXTURES_DIR, name)) as f:
        return json.load(f)


def test_zip_contains_kicad_sym():
    """ZIP archive must contain a .kicad_sym file."""
    components = _load_fixture("sample_components.json")
    zip_bytes = kicad_generator.generate_library(components)

    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        names = zf.namelist()
        sym_files = [n for n in names if n.endswith(".kicad_sym")]
        assert len(sym_files) == 1
        assert sym_files[0] == "library.kicad_sym"


def test_kicad_sym_has_valid_syntax():
    """The .kicad_sym file should contain key KiCad tokens."""
    components = _load_fixture("sample_components.json")
    zip_bytes = kicad_generator.generate_library(components)

    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        content = zf.read("library.kicad_sym").decode("utf-8")

    # Check key structural tokens
    assert "(kicad_symbol_lib" in content
    assert "(version 20231120)" in content
    assert '(generator "zane_export")' in content

    # Each component should have a symbol entry
    for comp in components:
        mpn = comp["mpn"].replace(" ", "_").replace("/", "_").replace("\\", "_")
        assert f'(symbol "{mpn}"' in content
        assert "(in_bom yes)" in content
        assert "(on_board yes)" in content


def test_footprint_directory_with_kicad_mod_files():
    """ZIP must have library.pretty/ directory with .kicad_mod files per component."""
    components = _load_fixture("sample_components.json")
    zip_bytes = kicad_generator.generate_library(components)

    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        names = zf.namelist()

        for comp in components:
            mpn = comp["mpn"].replace(" ", "_").replace("/", "_").replace("\\", "_")
            expected_path = f"library.pretty/{mpn}.kicad_mod"
            assert expected_path in names, f"Missing footprint: {expected_path}"

            # Verify footprint content
            content = zf.read(expected_path).decode("utf-8")
            assert f'(footprint "{mpn}"' in content
            assert '(generator "zane_export")' in content
            assert '(layer "F.Cu")' in content
            assert "(pad " in content


def test_empty_components_produces_valid_zip():
    """An empty component list should still produce a valid ZIP with a .kicad_sym."""
    zip_bytes = kicad_generator.generate_library([])

    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        names = zf.namelist()
        assert "library.kicad_sym" in names
        content = zf.read("library.kicad_sym").decode("utf-8")
        assert "(kicad_symbol_lib" in content


def test_single_component():
    """Single-component library should have one symbol and one footprint."""
    components = _load_fixture("single_component.json")
    zip_bytes = kicad_generator.generate_library(components)

    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        names = zf.namelist()
        assert "library.kicad_sym" in names
        assert "library.pretty/STM32F103C8T6.kicad_mod" in names
        assert len(names) == 2


def test_datasheet_url_in_symbol():
    """The datasheet URL from component data should appear in the symbol."""
    components = _load_fixture("single_component.json")
    zip_bytes = kicad_generator.generate_library(components)

    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        content = zf.read("library.kicad_sym").decode("utf-8")
        assert components[0]["datasheet_url"] in content
