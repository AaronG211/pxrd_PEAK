/**
 * Pure geometry helpers for the hand-rolled PXRD plot.
 *
 * These live outside CurvePlot.tsx for two reasons: `react-refresh/only-export-components`
 * forbids exporting non-component values beside a component, and keeping the maths
 * dependency-free makes it verifiable without a DOM.
 */

export interface PlotPoint {
  x: number;
  y: number;
}

export type Domain = [number, number];

/**
 * Snap a rough interval to the nearest 1, 2 or 5 times a power of ten.
 *
 * The thresholds round to the *nearest* member of the family rather than always
 * upward: a 5-90 deg axis asking for 8 intervals wants a step of 10 (9 ticks),
 * not the 20 (4 ticks) that ceiling rounding produces.
 */
export function niceStep(rough: number): number {
  if (!Number.isFinite(rough) || rough <= 0) return 1;
  const exponent = Math.floor(Math.log10(rough));
  const magnitude = 10 ** exponent;
  const fraction = rough / magnitude;
  const nice = fraction < 1.5 ? 1 : fraction < 3 ? 2 : fraction < 7 ? 5 : 10;
  return nice * magnitude;
}

/** Decimal places needed to print a value at this step without float noise. */
export function stepPrecision(step: number): number {
  if (!Number.isFinite(step) || step <= 0) return 0;
  return Math.max(0, Math.min(10, -Math.floor(Math.log10(step))));
}

/**
 * Guarantee a strictly increasing domain. A flat series, a single point and an
 * all-zero series all collapse to min === max, which would divide by zero in
 * every scale; pad them into a usable window instead.
 */
export function padDomain(min: number, max: number): Domain {
  if (!Number.isFinite(min) || !Number.isFinite(max)) return [0, 1];
  if (max > min) return [min, max];
  const pad = (Math.abs(min) || 1) * 0.05;
  return [min - pad, max + pad];
}

/** Nice-number tick values inside [min, max], at roughly `target` intervals. */
export function niceTicks(min: number, max: number, target = 6): number[] {
  if (!Number.isFinite(min) || !Number.isFinite(max) || max <= min) return [];
  const step = niceStep((max - min) / Math.max(1, target));
  const precision = stepPrecision(step);
  const first = Math.ceil(min / step) * step;
  const ticks: number[] = [];
  // Accumulating `value += step` drifts; index off `first` instead.
  for (let i = 0; i < 512; i += 1) {
    const value = first + i * step;
    if (value > max + step * 1e-9) break;
    const rounded = Number(value.toFixed(precision));
    if (rounded >= min - step * 1e-9) ticks.push(rounded);
  }
  return ticks;
}

/** Index of the first point with x >= target, in a list sorted ascending by x. */
export function lowerBound(points: PlotPoint[], target: number): number {
  let low = 0;
  let high = points.length;
  while (low < high) {
    const mid = (low + high) >>> 1;
    if (points[mid].x < target) low = mid + 1;
    else high = mid;
  }
  return low;
}

/** Index of the first point with x > target, in a list sorted ascending by x. */
export function upperBound(points: PlotPoint[], target: number): number {
  let low = 0;
  let high = points.length;
  while (low < high) {
    const mid = (low + high) >>> 1;
    if (points[mid].x <= target) low = mid + 1;
    else high = mid;
  }
  return low;
}

/** Index of the point closest to `target` along x. Returns -1 for an empty list. */
export function nearestIndex(points: PlotPoint[], target: number): number {
  if (points.length === 0) return -1;
  const right = lowerBound(points, target);
  if (right === 0) return 0;
  if (right >= points.length) return points.length - 1;
  const left = right - 1;
  return target - points[left].x <= points[right].x - target ? left : right;
}

export function extent(points: PlotPoint[], key: "x" | "y"): Domain | null {
  if (points.length === 0) return null;
  let min = Infinity;
  let max = -Infinity;
  for (const point of points) {
    const value = point[key];
    if (value < min) min = value;
    if (value > max) max = value;
  }
  return Number.isFinite(min) && Number.isFinite(max) ? [min, max] : null;
}

/**
 * Reduce a trace to at most two points per rendered pixel column by keeping the
 * minimum and maximum sample of each column.
 *
 * The emitted objects are the ORIGINAL samples, not synthesised midpoints, so a
 * peak keeps both its exact height and its exact 2θ position. One sample either
 * side of the window is carried along so the polyline still reaches the plot
 * edges after a zoom.
 *
 * `points` must be sorted ascending by x.
 */
export function decimateMinMax(
  points: PlotPoint[],
  x0: number,
  x1: number,
  columns: number,
): PlotPoint[] {
  if (points.length === 0) return [];
  const cols = Math.max(1, Math.floor(columns));
  const start = lowerBound(points, x0);
  const end = upperBound(points, x1);
  const before = start > 0 ? points[start - 1] : null;
  const after = end < points.length ? points[end] : null;

  const inWindow = end - start;
  if (inWindow <= 0) {
    // The window falls between two samples: keep the straddling segment.
    const straddle: PlotPoint[] = [];
    if (before) straddle.push(before);
    if (after) straddle.push(after);
    return straddle;
  }
  if (inWindow <= cols * 2) {
    const slice = points.slice(start, end);
    if (before) slice.unshift(before);
    if (after) slice.push(after);
    return slice;
  }

  const span = x1 - x0;
  const reduced: PlotPoint[] = [];
  let column = -1;
  let lowest: PlotPoint | null = null;
  let highest: PlotPoint | null = null;

  const flush = () => {
    if (!lowest || !highest) return;
    if (lowest === highest) {
      reduced.push(lowest);
    } else if (lowest.x <= highest.x) {
      reduced.push(lowest, highest);
    } else {
      reduced.push(highest, lowest);
    }
    lowest = null;
    highest = null;
  };

  for (let i = start; i < end; i += 1) {
    const point = points[i];
    const fraction = span > 0 ? (point.x - x0) / span : 0;
    const index = Math.min(cols - 1, Math.max(0, Math.floor(fraction * cols)));
    if (index !== column) {
      flush();
      column = index;
    }
    if (!lowest || point.y < lowest.y) lowest = point;
    if (!highest || point.y > highest.y) highest = point;
  }
  flush();

  if (before) reduced.unshift(before);
  if (after) reduced.push(after);
  return reduced;
}

/** Build an SVG `d` attribute, rounding to 0.01px to keep the string compact. */
export function buildPath(
  points: PlotPoint[],
  toScreenX: (x: number) => number,
  toScreenY: (y: number) => number,
): string {
  if (points.length === 0) return "";
  const parts: string[] = [];
  for (let i = 0; i < points.length; i += 1) {
    const screenX = Math.round(toScreenX(points[i].x) * 100) / 100;
    const screenY = Math.round(toScreenY(points[i].y) * 100) / 100;
    if (!Number.isFinite(screenX) || !Number.isFinite(screenY)) continue;
    parts.push(`${parts.length === 0 ? "M" : "L"}${screenX} ${screenY}`);
  }
  if (parts.length === 1) {
    // A lone sample has no segment to stroke; draw a 0.01px stub so it is visible.
    parts.push(parts[0].replace("M", "L"));
  }
  return parts.join(" ");
}

/** Format a 2θ value with enough precision for the current span. */
export function formatDegrees(value: number, span: number): string {
  const decimals = span < 1 ? 3 : span < 10 ? 2 : 2;
  return value.toFixed(decimals);
}

/**
 * Push a set of desired label positions apart so none overlap, staying inside
 * `[lo, hi]` and moving each label as little as possible.
 *
 * Used to print a series number at the right-hand end of every trace, which is
 * what stops curve identity from resting on stroke colour alone when several
 * curves of one role are overlaid.
 *
 * Returns positions in the SAME ORDER as the input; the sort is internal.
 */
export function spreadLabels(
  desired: number[],
  minGap: number,
  lo: number,
  hi: number,
): number[] {
  const count = desired.length;
  if (count === 0) return [];
  const order = desired.map((_, index) => index).sort((a, b) => desired[a] - desired[b]);
  const placed = new Array<number>(count);

  // Forward pass: never place a label above the previous one plus the gap.
  let cursor = lo;
  for (const index of order) {
    const value = Math.max(desired[index], cursor);
    placed[index] = value;
    cursor = value + minGap;
  }

  // If that overflowed the bottom, walk back up from `hi` so the whole stack
  // stays in frame rather than the last few labels leaving it.
  const lastIndex = order[order.length - 1];
  if (placed[lastIndex] > hi) {
    let ceiling = hi;
    for (let k = order.length - 1; k >= 0; k -= 1) {
      const index = order[k];
      placed[index] = Math.min(placed[index], ceiling);
      ceiling = placed[index] - minGap;
    }
  }
  return placed;
}
