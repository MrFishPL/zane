"""Tests for the Altium library generator."""

import io
import json
import os
import zipfile

import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import altium_generator

FIXTURES_DIR = os.path.join(os.path.dirname(__file__), "fixtures")


def _load_fixture(name: str) -> list[dict]:
    with open(os.path.join(FIXTURES_DIR, name)) as f:
        return json.load(f)


def test_zip_contains_schlib_and_pcblib():
    """ZIP archive must contain .SchLib and .PcbLib files."""
    components = _load_fixture("sample_components.json")
    zip_bytes = altium_generator.generate_library(components)

    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        names = zf.namelist()
        assert "library.SchLib" in names
        assert "library.PcbLib" in names


def test_schlib_basic_structure():
    """SchLib file should have the expected header and component records."""
    components = _load_fixture("sample_components.json")
    zip_bytes = altium_generator.generate_library(components)

    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        content = zf.read("library.SchLib").decode("utf-8")

    # Header
    assert "|HEADER=Protel for Windows - Schematic Library Editor Binary File Version 5.0" in content

    # Each component should have a record
    for comp in components:
        mpn = comp["mpn"].replace(" ", "_").replace("/", "_").replace("\\", "_")
        assert f"|LIBREFERENCE={mpn}" in content
        assert "|RECORD=Pin" in content


def test_pcblib_basic_structure():
    """PcbLib file should have the expected header and component records."""
    components = _load_fixture("sample_components.json")
    zip_bytes = altium_generator.generate_library(components)

    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        content = zf.read("library.PcbLib").decode("utf-8")

    # Header
    assert "|HEADER=Protel for Windows - PCB Library Editor Binary File Version 5.0" in content

    # Each component should have a pattern record
    for comp in components:
        mpn = comp["mpn"].replace(" ", "_").replace("/", "_").replace("\\", "_")
        assert f"|PATTERN={mpn}" in content
        assert "|RECORD=Pad" in content


def test_empty_components_produces_valid_zip():
    """An empty component list should still produce a valid ZIP."""
    zip_bytes = altium_generator.generate_library([])

    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        names = zf.namelist()
        assert "library.SchLib" in names
        assert "library.PcbLib" in names

        # Files should have headers even with no components
        schlib = zf.read("library.SchLib").decode("utf-8")
        assert "|HEADER=" in schlib

        pcblib = zf.read("library.PcbLib").decode("utf-8")
        assert "|HEADER=" in pcblib


def test_single_component():
    """Single-component library should produce correct records."""
    components = _load_fixture("single_component.json")
    zip_bytes = altium_generator.generate_library(components)

    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        schlib = zf.read("library.SchLib").decode("utf-8")
        pcblib = zf.read("library.PcbLib").decode("utf-8")

    assert "STM32F103C8T6" in schlib
    assert "STM32F103C8T6" in pcblib
    assert "ARM Cortex-M3 microcontroller 64KB Flash" in schlib


def test_description_in_records():
    """Component descriptions should appear in both SchLib and PcbLib."""
    components = _load_fixture("sample_components.json")
    zip_bytes = altium_generator.generate_library(components)

    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        schlib = zf.read("library.SchLib").decode("utf-8")
        pcblib = zf.read("library.PcbLib").decode("utf-8")

    for comp in components:
        desc = comp["description"]
        assert desc in schlib, f"Description '{desc}' not found in SchLib"
        assert desc in pcblib, f"Description '{desc}' not found in PcbLib"
