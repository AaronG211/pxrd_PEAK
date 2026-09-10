/**
 * Pure logic for the first-peak / d-spacing search.
 *
 * Kept out of the page component so the arithmetic can be exercised without a
 * DOM, and because `react-refresh/only-export-components` forbids exporting
 * non-components beside one.
 *
 * THE CENTRAL FACT THIS MODULE ENCODES: searching by d is searching by 2theta.
 * `first_peak_d_angstrom` is `lambda / (2 sin theta)` under a wavelength that is
 * ASSUMED for all but six of the published curves. Exactly one paper in the
 * 2,370-paper corpus reports a machine-readable wavelength; it had no clean-set
 * curve until curves.admission re-admitted six of them, and those six are the
 * only rows whose first_peak_wavelength_source reads 'paper_reported'
 * (1.54051 A, against the 1.5406 A assumed elsewhere). The other 9,379 are
 * still assumed, so the column carries no information the angle does not.
 *
 * The SEARCH is unaffected, and this is worth being precise about: a d entered
 * here is converted to 2theta once, under the assumed wavelength, and matched
 * against first_peak_two_theta_deg - which is measured, not derived. So those
 * six curves are still found at their true angle. What differs is the d shown
 * on their rows, computed from their own wavelength rather than the search's.
 * Both sides are labelled with their own provenance, which is why this is a
 * disclosure rather than a defect, and why every surface that shows or accepts
 * a d value still has to show {@link WAVELENGTH_ASSUMPTION_SHORT} beside it.
 */

import type { FirstPeakStatus } from "../types";

/** Cu K-alpha-1. The default chosen by tools/first_peak.py, not a measurement. */
export const ASSUMED_WAVELENGTH_ANGSTROM = 1.5406;

export const WAVELENGTH_ASSUMPTION_SHORT = "assumes Cu Kα 1.5406 Å";

export const WAVELENGTH_ASSUMPTION_LONG =
  "d is computed from an assumed Cu Kα wavelength of 1.5406 Å. No paper in this "
  + "collection reports its wavelength in machine-readable form, so this is a "
  + "default we chose, not a value read from the publication. A pattern collected "
  + "on another source is wrong by the ratio of the wavelengths — Mo Kα is off by "
  + "2.17×. Filtering by d is filtering by 2θ under one fixed constant.";

/**
 * Tolerance choices, set from the axis calibration rather than taste.
 *
 * Per-curve `two_theta_uncertainty_deg` runs to a median of 0.046°, p95 0.102°
 * and p99 0.128°, so the list deliberately FLOORS at ±0.1°: a ±0.05° window is
 * finer than the axis of half the corpus supports and would return confident
 * nonsense. The default sits one step above the floor, past the p95.
 */
export const TOLERANCE_CHOICES = [0.1, 0.15, 0.25, 0.5, 1] as const;
export const DEFAULT_TOLERANCE_DEG = 0.15;

/**
 * Ceiling on any single curve's axis uncertainty (corpus max 0.2923°). The
 * server-side window is widened by this much so the per-curve widening below can
 * still be applied client-side without a curve slipping past the query.
 */
export const MAX_AXIS_UNCERTAINTY_DEG = 0.3;

/** Bounds of the published first-peak column, used to reject impossible input. */
export const MIN_TWO_THETA_DEG = 0.1;
export const MAX_TWO_THETA_DEG = 90;

export type PeakUnit = "two-theta" | "d-spacing";

/**
 * Enumerated rather than derived from `StackingHumpStatus`, because the last
 * option covers TWO storage encodings of one fact: the importer may record "no
 * descriptor was computed" as the string `not_computed` or as a NULL, and a
 * reader asking for that case means both.
 */
export type HumpFilter =
  | "all"
  | "hump_detected"
  | "no_hump_detected"
  | "window_not_covered"
  | "no_descriptor";

export type RatioBand = "all" | "hump-dominant" | "balanced" | "peak-dominant";

export interface CurveSearchCriteria {
  /** Always in degrees 2theta, whatever unit the reader typed. */
  targetTwoThetaDeg: number | null;
  toleranceDeg: number;
  includeLowConfidence: boolean;
  humpFilter: HumpFilter;
  ratioBand: RatioBand;
}

const DEG = 180 / Math.PI;

/**
 * Bragg, n = 1. Returns null when no reflection exists at this d for this
 * wavelength (d < lambda/2 makes sin(theta) > 1), rather than producing NaN.
 */
export function dSpacingToTwoTheta(
  d: number,
  wavelength = ASSUMED_WAVELENGTH_ANGSTROM,
): number | null {
  if (!Number.isFinite(d) || d <= 0) return null;
  const sinTheta = wavelength / (2 * d);
  if (sinTheta > 1) return null;
  return 2 * Math.asin(sinTheta) * DEG;
}

export function twoThetaToDSpacing(
  twoTheta: number,
  wavelength = ASSUMED_WAVELENGTH_ANGSTROM,
): number | null {
  if (!Number.isFinite(twoTheta) || twoTheta <= 0 || twoTheta >= 180) return null;
  const sinTheta = Math.sin((twoTheta / 2) / DEG);
  if (sinTheta <= 0) return null;
  return wavelength / (2 * sinTheta);
}

export interface ParsedPeakTarget {
  twoThetaDeg: number;
  /** What the reader typed, echoed back for the results summary and the CSV. */
  enteredValue: number;
  unit: PeakUnit;
}

/**
 * Parse the search box. Returns null for empty input (not an error) and an
 * error string for input that cannot address a peak in this collection.
 */
export function parsePeakTarget(
  raw: string,
  unit: PeakUnit,
): { target: ParsedPeakTarget | null; error: string | null } {
  const trimmed = raw.trim();
  if (trimmed === "") return { target: null, error: null };
  const value = Number(trimmed);
  if (!Number.isFinite(value) || value <= 0) {
    return { target: null, error: "Enter a positive number." };
  }
  if (unit === "two-theta") {
    if (value < MIN_TWO_THETA_DEG || value > MAX_TWO_THETA_DEG) {
      return {
        target: null,
        error: `First peaks in this collection lie between ${MIN_TWO_THETA_DEG}° and ${MAX_TWO_THETA_DEG}° 2θ.`,
      };
    }
    return { target: { twoThetaDeg: value, enteredValue: value, unit }, error: null };
  }
  const twoTheta = dSpacingToTwoTheta(value);
  if (twoTheta === null) {
    return {
      target: null,
      error: `No reflection exists at d = ${value} Å for the assumed 1.5406 Å wavelength (d must exceed 0.7703 Å).`,
    };
  }
  if (twoTheta < MIN_TWO_THETA_DEG || twoTheta > MAX_TWO_THETA_DEG) {
    return {
      target: null,
      error: `d = ${value} Å is ${twoTheta.toFixed(2)}° 2θ, outside the ${MIN_TWO_THETA_DEG}–${MAX_TWO_THETA_DEG}° range this collection covers.`,
    };
  }
  return { target: { twoThetaDeg: twoTheta, enteredValue: value, unit }, error: null };
}

/** Statuses a search may return, per the gating rule. Never all four. */
export function searchableStatuses(includeLowConfidence: boolean): FirstPeakStatus[] {
  return includeLowConfidence ? ["ok", "low_confidence"] : ["ok"];
}

/**
 * Does this curve match, given its own axis calibration?
 *
 * The window is widened per curve to `max(tolerance, that curve's
 * two_theta_uncertainty_deg)`: a tolerance finer than the axis it is measured
 * against is a false precision, and it is the curve's own number that decides,
 * not a corpus average.
 */
export function curveMatchesTarget(
  peakTwoThetaDeg: number | null | undefined,
  axisUncertaintyDeg: number | null | undefined,
  targetTwoThetaDeg: number,
  toleranceDeg: number,
): boolean {
  if (typeof peakTwoThetaDeg !== "number" || !Number.isFinite(peakTwoThetaDeg)) return false;
  const widened = Math.max(
    toleranceDeg,
    typeof axisUncertaintyDeg === "number" && Number.isFinite(axisUncertaintyDeg)
      ? axisUncertaintyDeg
      : 0,
  );
  return Math.abs(peakTwoThetaDeg - targetTwoThetaDeg) <= widened;
}

/** Half-width of the window actually sent to the server, before per-curve widening. */
export function serverWindowHalfWidth(toleranceDeg: number): number {
  return toleranceDeg + MAX_AXIS_UNCERTAINTY_DEG;
}

export const RATIO_BAND_BOUNDS: Record<
  Exclude<RatioBand, "all">,
  { min: number | null; max: number | null }
> = {
  "hump-dominant": { min: null, max: 1 },
  balanced: { min: 1, max: 10 },
  "peak-dominant": { min: 10, max: null },
};

export function isCurveSearchActive(criteria: CurveSearchCriteria): boolean {
  return criteria.targetTwoThetaDeg !== null
    || criteria.humpFilter !== "all"
    || criteria.ratioBand !== "all";
}

const HUMP_FILTER_LABELS: Record<Exclude<HumpFilter, "all">, string> = {
  hump_detected: "stacking hump detected",
  no_hump_detected: "no stacking hump in the plotted 15–35° window",
  window_not_covered: "stacking-hump window not plotted (not determinable)",
  no_descriptor: "no crystallinity descriptor computed",
};

const RATIO_BAND_LABELS: Record<Exclude<RatioBand, "all">, string> = {
  "hump-dominant": "(100)/(001) ≤ 1",
  balanced: "(100)/(001) 1–10",
  "peak-dominant": "(100)/(001) > 10",
};

/**
 * One human-readable line describing an active search, reused by the results
 * counter and by the CSV export. When the reader searched in d, the assumed
 * wavelength travels with the sentence — including into the downloaded file,
 * which is the copy most likely to outlive this page.
 */
export function describeCurveSearch(
  criteria: CurveSearchCriteria,
  target: ParsedPeakTarget | null,
): string {
  const parts: string[] = [];
  if (target) {
    // Trim the echo: a pasted or computed value can carry seventeen digits, and
    // this string is read by a person and written into a CSV cell.
    const entered = Number(target.enteredValue.toPrecision(6));
    const window = `±${criteria.toleranceDeg}° 2θ (widened per curve to that curve's axis uncertainty)`;
    if (target.unit === "d-spacing") {
      parts.push(
        `first peak at d = ${entered} Å = ${target.twoThetaDeg.toFixed(3)}° 2θ `
        + `[${WAVELENGTH_ASSUMPTION_SHORT}], ${window}`,
      );
    } else {
      parts.push(`first peak at ${entered}° 2θ, ${window}`);
    }
    parts.push(
      criteria.includeLowConfidence
        ? "first-peak status ok or low_confidence"
        : "first-peak status ok only",
    );
  }
  if (criteria.humpFilter !== "all") parts.push(HUMP_FILTER_LABELS[criteria.humpFilter]);
  if (criteria.ratioBand !== "all") parts.push(RATIO_BAND_LABELS[criteria.ratioBand]);
  return parts.join("; ");
}
