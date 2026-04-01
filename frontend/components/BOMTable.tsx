"use client";

import { useState } from "react";
import { getFileUrl } from "@/lib/api";

// ---------------------------------------------------------------------------
// Types matching the agent response schema
// ---------------------------------------------------------------------------

interface Alternative {
  mpn: string;
  manufacturer: string;
  unit_price: number;
  stock?: number;
  note: string;
  snapmagic_available?: boolean;
  snapmagic_url?: string;
}

interface Component {
  ref: string;
  mpn: string;
  manufacturer: string;
  description: string;
  package: string;
  qty_per_unit: number;
  qty_total: number;
  justification?: string;
  unit_price: number;
  price_break?: { qty: number; unit_price: number };
  stock: number;
  lifecycle: string;
  distributor: string;
  distributor_url: string;
  datasheet_url?: string;
  snapmagic_url?: string;
  snapmagic_available: boolean;
  snapmagic_formats?: string[];
  needs_cad_decision?: boolean;
  mpn_confidence?: string;
  verified?: boolean;
  warnings: string[];
  alternatives: Alternative[];
}

interface NotSourced {
  item: string;
  reason: string;
}

interface BOMSummary {
  unique_parts: number;
  total_components_per_unit: number;
  cost_per_unit: number;
  cost_total: number;
  volume: number;
  currency: string;
}

interface ExportFiles {
  csv?: string;
  kicad_library?: string;
  altium_library?: string;
}

interface BOMTableProps {
  components: Component[];
  notSourced?: NotSourced[];
  summary: BOMSummary;
  exportFiles?: ExportFiles;
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function formatPrice(value: number, currency: string): string {
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency,
    minimumFractionDigits: 2,
  }).format(value);
}

function formatStock(stock: number): string {
  if (stock >= 10000)
    return `${(stock / 1000).toFixed(0)}k`;
  return stock.toLocaleString();
}

function resolveMinioPath(path: string): string {
  // minio://exports/... -> exports/...
  const cleaned = path.replace(/^minio:\/\//, "");
  return getFileUrl(cleaned);
}

// ---------------------------------------------------------------------------
// Component row
// ---------------------------------------------------------------------------

function ComponentRow({ comp, currency }: { comp: Component; currency: string }) {
  const [expanded, setExpanded] = useState(false);
  const hasAlternatives = comp.alternatives && comp.alternatives.length > 0;

  return (
    <>
      <tr className="border-b border-border/50 hover:bg-bg-hover/50 transition-colors">
        {/* Ref */}
        <td className="px-3 py-2.5 text-sm font-mono text-text-primary whitespace-nowrap">
          {comp.ref}
        </td>

        {/* MPN */}
        <td className="px-3 py-2.5 text-sm">
          {comp.mpn ? (
            <a
              href={comp.distributor_url || `https://octopart.com/search?q=${encodeURIComponent(comp.mpn)}`}
              target="_blank"
              rel="noopener noreferrer"
              className="text-accent hover:text-accent-hover underline decoration-accent/30 hover:decoration-accent transition-colors font-mono"
            >
              {comp.mpn}
            </a>
          ) : (
            <span className="font-mono text-text-muted">—</span>
          )}
        </td>

        {/* Manufacturer */}
        <td className="px-3 py-2.5 text-sm text-text-secondary whitespace-nowrap">
          {comp.manufacturer || "—"}
        </td>

        {/* Description */}
        <td className="px-3 py-2.5 text-sm text-text-secondary max-w-[200px] truncate">
          {comp.description}
        </td>

        {/* Package */}
        <td className="px-3 py-2.5 text-sm text-text-secondary font-mono whitespace-nowrap">
          {comp.package}
        </td>

        {/* Qty */}
        <td className="px-3 py-2.5 text-sm text-text-primary text-right whitespace-nowrap">
          {comp.qty_per_unit}
        </td>

        {/* Unit Price */}
        <td className="px-3 py-2.5 text-sm text-text-primary text-right whitespace-nowrap">
          {formatPrice(comp.unit_price, currency)}
        </td>

        {/* Stock */}
        <td className="px-3 py-2.5 text-sm text-right whitespace-nowrap">
          <span
            className={
              comp.stock > 100
                ? "text-success"
                : comp.stock > 0
                ? "text-warning"
                : "text-error"
            }
          >
            {formatStock(comp.stock)}
          </span>
        </td>

        {/* Lifecycle */}
        <td className="px-3 py-2.5 text-sm whitespace-nowrap">
          {comp.lifecycle && comp.lifecycle !== "unknown" && comp.lifecycle !== "Unknown" ? (
            <span
              className={
                comp.lifecycle === "Active"
                  ? "text-success"
                  : comp.lifecycle === "NRND" || comp.lifecycle === "Last Time Buy"
                  ? "text-warning"
                  : "text-text-secondary"
              }
            >
              {comp.lifecycle}
            </span>
          ) : (
            <span className="text-text-muted">—</span>
          )}
        </td>

        {/* Distributor */}
        <td className="px-3 py-2.5 text-sm text-text-secondary whitespace-nowrap">
          {comp.distributor}
        </td>

        {/* SnapMagic */}
        <td className="px-3 py-2.5 text-sm text-center">
          {comp.snapmagic_available ? (
            <a
              href={comp.snapmagic_url || "#"}
              target="_blank"
              rel="noopener noreferrer"
              className="text-success hover:text-success/80 transition-colors"
              title={
                comp.snapmagic_formats
                  ? `Available: ${comp.snapmagic_formats.join(", ")}`
                  : "Available on SnapMagic"
              }
            >
              <svg className="w-4 h-4 inline-block" fill="currentColor" viewBox="0 0 20 20">
                <path
                  fillRule="evenodd"
                  d="M16.707 5.293a1 1 0 010 1.414l-8 8a1 1 0 01-1.414 0l-4-4a1 1 0 011.414-1.414L8 12.586l7.293-7.293a1 1 0 011.414 0z"
                  clipRule="evenodd"
                />
              </svg>
            </a>
          ) : comp.needs_cad_decision ? (
            <span
              className="text-warning cursor-help text-xs font-medium"
              title="No CAD model — check alternatives for CAD-available options"
            >
              !!
            </span>
          ) : (
            <span className="text-text-muted">—</span>
          )}
        </td>

        {/* Warnings */}
        <td className="px-3 py-2.5 text-sm max-w-[120px]">
          {comp.warnings && comp.warnings.length > 0 && (
            <span
              className="text-xs text-warning cursor-help"
              title={comp.warnings.join('\n')}
            >
              {comp.warnings.length} warning{comp.warnings.length > 1 ? 's' : ''}
            </span>
          )}
        </td>

        {/* Expand alternatives */}
        <td className="px-2 py-2.5">
          {hasAlternatives && (
            <button
              onClick={() => setExpanded(!expanded)}
              className="text-text-muted hover:text-text-secondary transition-colors"
              title="Show alternatives"
            >
              <svg
                className={`w-4 h-4 transition-transform ${expanded ? "rotate-180" : ""}`}
                fill="none"
                viewBox="0 0 24 24"
                stroke="currentColor"
                strokeWidth={2}
              >
                <path strokeLinecap="round" strokeLinejoin="round" d="M19 9l-7 7-7-7" />
              </svg>
            </button>
          )}
        </td>
      </tr>

      {/* Alternatives */}
      {expanded &&
        comp.alternatives.map((alt, i) => (
          <tr
            key={`${comp.ref}-alt-${i}`}
            className={`border-b border-border/30 ${alt.snapmagic_available ? "bg-success/5" : "bg-bg-tertiary/50"}`}
          >
            <td className="px-3 py-2 text-xs text-text-muted pl-8">Alt</td>
            <td className="px-3 py-2 text-xs font-mono text-text-secondary">
              {alt.mpn}
            </td>
            <td className="px-3 py-2 text-xs text-text-muted">
              {alt.manufacturer}
            </td>
            <td colSpan={3} className="px-3 py-2 text-xs text-text-muted italic">
              {alt.note}
              {alt.snapmagic_available && (
                <span className="ml-2 text-success font-medium not-italic">CAD</span>
              )}
            </td>
            <td className="px-3 py-2 text-xs text-text-secondary text-right">
              {formatPrice(alt.unit_price, currency)}
            </td>
            <td colSpan={6} />
          </tr>
        ))}
    </>
  );
}

// ---------------------------------------------------------------------------
// Main BOMTable
// ---------------------------------------------------------------------------

export default function BOMTable({
  components,
  notSourced,
  summary,
  exportFiles,
}: BOMTableProps) {
  return (
    <div className="space-y-4">
      {/* Table */}
      <div className="overflow-x-auto rounded-lg border border-border">
        <table className="w-full text-left table-auto" style={{ minWidth: '900px' }}>
          <thead>
            <tr className="bg-bg-tertiary border-b border-border">
              <th className="px-3 py-2.5 text-xs font-medium text-text-muted uppercase tracking-wider">Ref</th>
              <th className="px-3 py-2.5 text-xs font-medium text-text-muted uppercase tracking-wider">MPN</th>
              <th className="px-3 py-2.5 text-xs font-medium text-text-muted uppercase tracking-wider">Manufacturer</th>
              <th className="px-3 py-2.5 text-xs font-medium text-text-muted uppercase tracking-wider">Description</th>
              <th className="px-3 py-2.5 text-xs font-medium text-text-muted uppercase tracking-wider">Package</th>
              <th className="px-3 py-2.5 text-xs font-medium text-text-muted uppercase tracking-wider text-right">Qty</th>
              <th className="px-3 py-2.5 text-xs font-medium text-text-muted uppercase tracking-wider text-right">Price</th>
              <th className="px-3 py-2.5 text-xs font-medium text-text-muted uppercase tracking-wider text-right">Stock</th>
              <th className="px-3 py-2.5 text-xs font-medium text-text-muted uppercase tracking-wider">Lifecycle</th>
              <th className="px-3 py-2.5 text-xs font-medium text-text-muted uppercase tracking-wider">Distributor</th>
              <th className="px-3 py-2.5 text-xs font-medium text-text-muted uppercase tracking-wider text-center">SnapMagic</th>
              <th className="px-3 py-2.5 text-xs font-medium text-text-muted uppercase tracking-wider">Warnings</th>
              <th className="px-3 py-2.5 w-8" />
            </tr>
          </thead>
          <tbody>
            {components.map((comp) => (
              <ComponentRow
                key={comp.ref}
                comp={comp}
                currency={summary.currency}
              />
            ))}
          </tbody>
        </table>
      </div>

      {/* Not sourced */}
      {notSourced && notSourced.length > 0 && (
        <div className="rounded-lg border border-warning/30 bg-warning/5 p-4">
          <h4 className="text-sm font-medium text-warning mb-2">
            Not Sourced ({notSourced.length})
          </h4>
          <ul className="space-y-1">
            {notSourced.map((ns, i) => {
              const label = typeof ns === "string" ? ns : (ns.item || ns.reason || JSON.stringify(ns));
              const reason = typeof ns === "object" && ns.reason && ns.item ? ns.reason : null;
              return (
                <li key={i} className="text-sm text-text-secondary">
                  <span className="text-text-primary">{label}</span>
                  {reason && (
                    <>
                      {" — "}
                      <span className="text-text-muted">{reason}</span>
                    </>
                  )}
                </li>
              );
            })}
          </ul>
        </div>
      )}

      {/* BOM Summary */}
      <div className="rounded-lg border border-border bg-bg-secondary p-4">
        <h4 className="text-sm font-medium text-text-primary mb-3">
          BOM Summary
        </h4>
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-4">
          <div>
            <p className="text-xs text-text-muted">Unique Parts</p>
            <p className="text-lg font-semibold text-text-primary">
              {summary.unique_parts}
            </p>
          </div>
          <div>
            <p className="text-xs text-text-muted">Cost / Unit</p>
            <p className="text-lg font-semibold text-text-primary">
              {formatPrice(summary.cost_per_unit, summary.currency)}
            </p>
          </div>
          <div>
            <p className="text-xs text-text-muted">
              Total ({summary.volume} units)
            </p>
            <p className="text-lg font-semibold text-accent">
              {formatPrice(summary.cost_total, summary.currency)}
            </p>
          </div>
          <div>
            <p className="text-xs text-text-muted">Volume</p>
            <p className="text-lg font-semibold text-text-primary">
              {summary.volume.toLocaleString()}
            </p>
          </div>
        </div>
      </div>

      {/* Download buttons */}
      {exportFiles && (
        <div className="flex flex-wrap gap-3">
          {exportFiles.csv && (
            <a
              href={resolveMinioPath(exportFiles.csv)}
              download
              className="inline-flex items-center gap-2 px-4 py-2 rounded-lg bg-bg-secondary border border-border hover:border-accent hover:bg-bg-hover text-sm text-text-primary transition-colors"
            >
              <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M12 10v6m0 0l-3-3m3 3l3-3m2 8H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
              </svg>
              Download CSV
            </a>
          )}
          {exportFiles.kicad_library && (
            <a
              href={resolveMinioPath(exportFiles.kicad_library)}
              download
              className="inline-flex items-center gap-2 px-4 py-2 rounded-lg bg-bg-secondary border border-border hover:border-accent hover:bg-bg-hover text-sm text-text-primary transition-colors"
            >
              <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M12 10v6m0 0l-3-3m3 3l3-3m2 8H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
              </svg>
              KiCad Library
            </a>
          )}
          {exportFiles.altium_library && (
            <a
              href={resolveMinioPath(exportFiles.altium_library)}
              download
              className="inline-flex items-center gap-2 px-4 py-2 rounded-lg bg-bg-secondary border border-border hover:border-accent hover:bg-bg-hover text-sm text-text-primary transition-colors"
            >
              <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M12 10v6m0 0l-3-3m3 3l3-3m2 8H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
              </svg>
              Altium Library
            </a>
          )}
        </div>
      )}
    </div>
  );
}
