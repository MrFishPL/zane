"""Altium library generator.

Generates a ZIP archive containing simplified text-based .SchLib and .PcbLib
files that Altium can import.  These are placeholder libraries -- real symbols
would come from SnapMagic.
"""

from __future__ import annotations

import io
import zipfile

import structlog

log = structlog.get_logger()


def _sanitize_name(name: str) -> str:
    """Sanitize a name for use in Altium identifiers."""
    return name.replace(" ", "_").replace("/", "_").replace("\\", "_")


def _generate_schlib(components: list[dict]) -> str:
    """Generate simplified Altium SchLib text content."""
    lines = ["|HEADER=Protel for Windows - Schematic Library Editor Binary File Version 5.0"]
    for comp in components:
        mpn = _sanitize_name(comp.get("mpn", "UNKNOWN"))
        desc = comp.get("description", "")
        lines.append(
            f"|RECORD=Component|LIBREFERENCE={mpn}"
            f"|COMPONENTDESCRIPTION={desc}|PARTCOUNT=1"
        )
        lines.append(
            "|RECORD=Pin|OWNERINDEX=0|NAME=1|DESIGNATOR=1"
            "|LOCATION.X=0|LOCATION.Y=0|PINLENGTH=200"
        )
    return "\n".join(lines) + "\n"


def _generate_pcblib(components: list[dict]) -> str:
    """Generate simplified Altium PcbLib text content."""
    lines = ["|HEADER=Protel for Windows - PCB Library Editor Binary File Version 5.0"]
    for comp in components:
        mpn = _sanitize_name(comp.get("mpn", "UNKNOWN"))
        desc = comp.get("description", "")
        lines.append(
            f"|RECORD=Component|PATTERN={mpn}|DESCRIPTION={desc}"
        )
        lines.append(
            "|RECORD=Pad|NAME=1|X=0|Y=0|XSIZE=60|YSIZE=60"
            "|SHAPE=Rectangle|LAYER=Top"
        )
    return "\n".join(lines) + "\n"


def generate_library(components: list[dict]) -> bytes:
    """Generate an Altium library ZIP archive.

    The ZIP contains:
      - ``library.SchLib`` -- schematic library (simplified text format)
      - ``library.PcbLib`` -- PCB library (simplified text format)

    Args:
        components: List of component dicts.

    Returns:
        Bytes of the ZIP archive.
    """
    if not components:
        log.warning("altium_generate.empty_components")

    schlib_content = _generate_schlib(components)
    pcblib_content = _generate_pcblib(components)

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("library.SchLib", schlib_content)
        zf.writestr("library.PcbLib", pcblib_content)

    log.info("altium_library_generated", component_count=len(components))
    return buf.getvalue()
