"""CSV BOM generator.

Generates a simple two-column CSV with Manufacturer Part Number and Quantity.
"""

from __future__ import annotations

import csv
import io

import structlog

log = structlog.get_logger()


def generate(components: list[dict], volume: int) -> str:
    """Generate a CSV BOM string from a list of components.

    Args:
        components: List of component dicts, each with at least ``mpn`` and
            ``qty_per_unit`` fields.
        volume: Production volume multiplier.

    Returns:
        CSV string with columns ``Manufacturer Part Number`` and ``Quantity``.
    """
    if not components:
        log.warning("csv_generate.empty_components")
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["Manufacturer Part Number", "Quantity"])
        return output.getvalue()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Manufacturer Part Number", "Quantity"])

    for comp in components:
        mpn = comp.get("mpn", "UNKNOWN")
        qty_per_unit = comp.get("qty_per_unit", 1)
        quantity = qty_per_unit * volume
        writer.writerow([mpn, quantity])

    log.info("csv_generated", component_count=len(components), volume=volume)
    return output.getvalue()
