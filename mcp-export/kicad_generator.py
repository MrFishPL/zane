"""KiCad library generator.

Generates a ZIP archive containing a .kicad_sym symbol library and a .pretty
directory with .kicad_mod footprint files.
"""

from __future__ import annotations

import io
import zipfile

import structlog

log = structlog.get_logger()


def _sanitize_name(name: str) -> str:
    """Sanitize a name for use in KiCad identifiers."""
    return name.replace(" ", "_").replace("/", "_").replace("\\", "_")


def _generate_symbol(comp: dict) -> str:
    """Generate a single KiCad symbol entry for a component."""
    mpn = _sanitize_name(comp.get("mpn", "UNKNOWN"))
    datasheet_url = comp.get("datasheet_url", "")
    description = comp.get("description", "")

    return f"""  (symbol "{mpn}"
    (pin_names (offset 1.016))
    (in_bom yes)
    (on_board yes)
    (property "Reference" "REF" (at 0 1.27 0) (effects (font (size 1.27 1.27))))
    (property "Value" "{mpn}" (at 0 -1.27 0) (effects (font (size 1.27 1.27))))
    (property "Footprint" "" (at 0 0 0) (effects (font (size 1.27 1.27)) hide))
    (property "Datasheet" "{datasheet_url}" (at 0 0 0) (effects (font (size 1.27 1.27)) hide))
    (property "Description" "{description}" (at 0 0 0) (effects (font (size 1.27 1.27)) hide))
    (symbol "{mpn}_0_1"
      (rectangle (start -3.81 2.54) (end 3.81 -2.54)
        (stroke (width 0.254) (type default))
        (fill (type background))
      )
    )
  )"""


def _generate_footprint(comp: dict) -> str:
    """Generate a KiCad footprint (.kicad_mod) for a component."""
    mpn = _sanitize_name(comp.get("mpn", "UNKNOWN"))
    description = comp.get("description", "")

    return f"""(footprint "{mpn}"
  (version 20231120)
  (generator "zane_export")
  (layer "F.Cu")
  (property "Reference" "REF" (at 0 -2.54) (layer "F.SilkS") (effects (font (size 1 1) (thickness 0.15))))
  (property "Value" "{mpn}" (at 0 2.54) (layer "F.Fab") (effects (font (size 1 1) (thickness 0.15))))
  (property "Footprint" "" (at 0 0) (layer "F.Fab") (effects (font (size 1.27 1.27)) hide))
  (property "Datasheet" "" (at 0 0) (layer "F.Fab") (effects (font (size 1.27 1.27)) hide))
  (property "Description" "{description}" (at 0 0) (layer "F.Fab") (effects (font (size 1.27 1.27)) hide))
  (pad "1" smd rect (at -1.27 0) (size 1 1) (layers "F.Cu" "F.Paste" "F.Mask"))
  (pad "2" smd rect (at 1.27 0) (size 1 1) (layers "F.Cu" "F.Paste" "F.Mask"))
)
"""


def generate_library(components: list[dict]) -> bytes:
    """Generate a KiCad library ZIP archive.

    The ZIP contains:
      - ``library.kicad_sym`` -- symbol library
      - ``library.pretty/<MPN>.kicad_mod`` -- one footprint per component

    Args:
        components: List of component dicts.

    Returns:
        Bytes of the ZIP archive.
    """
    if not components:
        log.warning("kicad_generate.empty_components")

    # Build symbol library content
    symbol_entries = "\n".join(_generate_symbol(c) for c in components)
    kicad_sym = f"""(kicad_symbol_lib
  (version 20231120)
  (generator "zane_export")
{symbol_entries}
)
"""

    # Build ZIP
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("library.kicad_sym", kicad_sym)
        for comp in components:
            mpn = _sanitize_name(comp.get("mpn", "UNKNOWN"))
            footprint_content = _generate_footprint(comp)
            zf.writestr(f"library.pretty/{mpn}.kicad_mod", footprint_content)

    log.info("kicad_library_generated", component_count=len(components))
    return buf.getvalue()
