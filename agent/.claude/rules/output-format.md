# Output Format
Always return JSON with exactly one of three statuses.

## needs_clarification
{status, message (plain text), data: {questions: [{id, question, default}], annotated_image}}

## recommendation  
{status, message (plain text), data: {components: [{ref, mpn, manufacturer, description, package, qty_per_unit, qty_total, justification, unit_price, price_break, stock, lifecycle, distributor, distributor_url, datasheet_url, snapmagic_url, snapmagic_available, snapmagic_formats, mpn_confidence, verified, warnings, alternatives}], not_sourced, bom_summary: {unique_parts, total_components_per_unit, cost_per_unit, cost_total, volume, currency}, export_files: {csv, kicad_library, altium_library}, sources_queried}}

## analysis
{status, message (plain text), data: {blocks: [{name, components, page}], identified_components, unclear_areas: [{page, description, annotated_image}]}}

Rules:
- message: always plain text, NEVER markdown
- Null/empty: include field, use null or []
- Every component has identical set of fields
- Prices always in USD
