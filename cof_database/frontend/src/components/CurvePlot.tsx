import { useCallback, useEffect, useId, useMemo, useRef, useState } from "react";
import {
  ChevronLeft,
  ChevronRight,
  Layers,
  LineChart,
  RotateCcw,
  ZoomIn,
  ZoomOut,
} from "lucide-react";
import {
  buildPath,
  decimateMinMax,
  extent,
  formatDegrees,
  nearestIndex,
  niceTicks,
  padDomain,
  stepPrecision,
} from "../lib/plotGeometry";
import type { Domain, PlotPoint } from "../lib/plotGeometry";
import type { CurveRole } from "../types";

export interface CurvePlotSeries {
  seriesId: string;
  label: string;
  role: CurveRole;
  materialName: string | null;
  sampleState: string | null;
  /** two_theta_uncertainty_deg reported by the digitizer for this trace. */
  uncertainty: number | null;
  points: PlotPoint[];
}

/**
 * Stroke ramps stay inside the hue of the matching role badge in the inventory
 * table (see `roleStyles` in PaperDetailPage), so a plotted line is traceable to
 * its row. Series sharing a role step through the ramp, then through the dash
 * patterns, so identity never rests on colour alone — the legend also prints the
 * label, the role and the series number.
 *
 * Every value is a compiled Tailwind v4 token at 3:1 or better against white.
 */
const ROLE_RAMP: Record<CurveRole, string[]> = {
  experimental: ["#009966", "#006045", "#00BC7D", "#007A55"],
  simulated: ["#0084D1", "#00598A", "#00A6F4", "#0069A8"],
  refined: ["#7F22FE", "#4D179A", "#8E51FF", "#7008E7"],
  difference: ["#E17100", "#7B3306", "#FE9A00", "#BB4D00"],
  reference: ["#314158", "#0F172B", "#45556C", "#1D293D"],
  unclassified: ["#62748E", "#0F172B", "#314158", "#1D293D", "#45556C"],
};

const DASH_PATTERNS: Array<string | undefined> = [undefined, "7 3", "2 3", "10 3 2 3"];

const GRID_COLOR = "#E2E8F0";
const AXIS_COLOR = "#94A3B8";
const LABEL_COLOR = "#62748E";
const TITLE_COLOR = "#314158";

const MIN_DRAG_PX = 8;

type SeriesStyle = { color: string; dash: string | undefined };

function styleSeries(series: CurvePlotSeries[]): Map<string, SeriesStyle> {
  const seenPerRole = new Map<CurveRole, number>();
  const styles = new Map<string, SeriesStyle>();
  for (const item of series) {
    const ordinal = seenPerRole.get(item.role) ?? 0;
    seenPerRole.set(item.role, ordinal + 1);
    const ramp = ROLE_RAMP[item.role];
    styles.set(item.seriesId, {
      color: ramp[ordinal % ramp.length],
      dash: DASH_PATTERNS[Math.floor(ordinal / ramp.length) % DASH_PATTERNS.length],
    });
  }
  return styles;
}

function seriesNumber(seriesId: string): string {
  const tail = seriesId.split("-").at(-1);
  return tail && /^s\d+$/i.test(tail) ? tail : seriesId.slice(-3);
}

function formatIntensity(value: number): string {
  if (!Number.isFinite(value)) return "—";
  const magnitude = Math.abs(value);
  if (magnitude >= 1000) return value.toFixed(0);
  if (magnitude >= 10) return value.toFixed(1);
  if (magnitude >= 0.1) return value.toFixed(2);
  return value.toPrecision(2);
}

const CONTROL_CLASS =
  "inline-flex items-center gap-1.5 rounded-lg border border-slate-200 bg-white px-2.5 py-1.5 text-xs font-semibold text-slate-700 transition hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-40";

export function CurvePlot({
  series,
  figureLabel,
}: {
  series: CurvePlotSeries[];
  figureLabel: string;
}) {
  // useId output can contain punctuation; strip it so the value is safe both as
  // an id attribute and inside an SVG `url(#...)` reference.
  const baseId = useId().replace(/[^a-zA-Z0-9]/g, "");
  const clipId = `${baseId}-clip`;
  const summaryId = `${baseId}-summary`;
  const helpId = `${baseId}-help`;

  const containerRef = useRef<HTMLDivElement | null>(null);
  const [width, setWidth] = useState(0);
  const [hidden, setHidden] = useState<ReadonlySet<string>>(() => new Set<string>());
  const [stacked, setStacked] = useState(false);
  const [zoom, setZoom] = useState<Domain | null>(null);
  const [cursorX, setCursorX] = useState<number | null>(null);
  const [drag, setDrag] = useState<{ from: number; to: number } | null>(null);

  useEffect(() => {
    const element = containerRef.current;
    if (!element) return;
    const observer = new ResizeObserver((entries) => {
      const entry = entries[0];
      if (!entry) return;
      const next = entry.contentRect.width;
      setWidth((current) => (Math.abs(current - next) < 0.5 ? current : next));
    });
    observer.observe(element);
    return () => observer.disconnect();
  }, []);

  /* ---------------------------------------------------------------------- */
  /* Derived data                                                            */
  /* ---------------------------------------------------------------------- */

  const sorted = useMemo(
    () =>
      series.map((item) => ({
        ...item,
        // A pattern is a function of 2θ, so sorting is safe and makes the
        // binary searches and the per-column reduction well defined.
        points: [...item.points].sort((a, b) => a.x - b.x),
      })),
    [series],
  );

  const styles = useMemo(() => styleSeries(sorted), [sorted]);

  const fullX = useMemo<Domain>(() => {
    let min = Infinity;
    let max = -Infinity;
    for (const item of sorted) {
      const span = extent(item.points, "x");
      if (!span) continue;
      if (span[0] < min) min = span[0];
      if (span[1] > max) max = span[1];
    }
    return padDomain(min, max);
  }, [sorted]);

  const xDomain = zoom ?? fullX;
  const [x0, x1] = xDomain;
  const xSpan = x1 - x0;

  const visible = useMemo(
    () => sorted.filter((item) => !hidden.has(item.seriesId)),
    [hidden, sorted],
  );

  const isNarrow = width > 0 && width < 520;
  const height = isNarrow ? 250 : 340;
  const margin = useMemo(
    () => ({
      top: 16,
      right: 16,
      bottom: 46,
      left: stacked ? (isNarrow ? 22 : 28) : isNarrow ? 46 : 58,
    }),
    [isNarrow, stacked],
  );
  const innerW = Math.max(1, width - margin.left - margin.right);
  const innerH = Math.max(1, height - margin.top - margin.bottom);
  const columns = Math.max(1, Math.round(innerW));

  /** Per-series samples inside the window, reduced to <= 2 per pixel column. */
  const drawn = useMemo(
    () =>
      visible.map((item) => ({
        item,
        points: decimateMinMax(item.points, x0, x1, columns),
      })),
    [columns, visible, x0, x1],
  );

  /** Overlaid y-domain, measured over the visible window only. */
  const yDomain = useMemo<Domain>(() => {
    let min = Infinity;
    let max = -Infinity;
    for (const { points } of drawn) {
      for (const point of points) {
        if (point.x < x0 || point.x > x1) continue;
        if (point.y < min) min = point.y;
        if (point.y > max) max = point.y;
      }
    }
    if (!Number.isFinite(min) || !Number.isFinite(max)) return [0, 1];
    const [low, high] = padDomain(min, max);
    const headroom = (high - low) * 0.06;
    return [low - headroom, high + headroom];
  }, [drawn, x0, x1]);

  /** Per-lane y-domain for the offset view. */
  const laneDomains = useMemo(
    () =>
      drawn.map(({ points }) => {
        let min = Infinity;
        let max = -Infinity;
        for (const point of points) {
          if (point.x < x0 || point.x > x1) continue;
          if (point.y < min) min = point.y;
          if (point.y > max) max = point.y;
        }
        return padDomain(min, max);
      }),
    [drawn, x0, x1],
  );

  const totalSourcePoints = series.reduce((sum, item) => sum + item.points.length, 0);
  const totalDrawnPoints = drawn.reduce((sum, entry) => sum + entry.points.length, 0);

  const strongest = useMemo(() => {
    let best: { label: string; point: PlotPoint } | null = null;
    for (const item of visible) {
      for (const point of item.points) {
        if (point.x < x0 || point.x > x1) continue;
        if (!best || point.y > best.point.y) best = { label: item.label, point };
      }
    }
    return best;
  }, [visible, x0, x1]);

  /* ---------------------------------------------------------------------- */
  /* Scales                                                                  */
  /* ---------------------------------------------------------------------- */

  const scaleX = useCallback(
    (value: number) => margin.left + ((value - x0) / xSpan) * innerW,
    [innerW, margin.left, x0, xSpan],
  );
  const scaleY = useCallback(
    (value: number) =>
      margin.top + innerH - ((value - yDomain[0]) / (yDomain[1] - yDomain[0])) * innerH,
    [innerH, margin.top, yDomain],
  );

  const laneHeight = innerH / Math.max(1, drawn.length);
  const laneScale = useCallback(
    (laneIndex: number, value: number) => {
      const [low, high] = laneDomains[laneIndex] ?? [0, 1];
      const baseline = margin.top + (laneIndex + 1) * laneHeight - laneHeight * 0.1;
      const usable = laneHeight * 0.78;
      return baseline - ((value - low) / (high - low)) * usable;
    },
    [laneDomains, laneHeight, margin.top],
  );

  const xTicks = useMemo(() => niceTicks(x0, x1, isNarrow ? 4 : 8), [isNarrow, x0, x1]);
  const yTicks = useMemo(
    () => (stacked ? [] : niceTicks(yDomain[0], yDomain[1], isNarrow ? 4 : 6)),
    [isNarrow, stacked, yDomain],
  );
  const xTickPrecision = stepPrecision(
    xTicks.length > 1 ? xTicks[1] - xTicks[0] : xSpan / 8,
  );

  /* ---------------------------------------------------------------------- */
  /* Interaction                                                             */
  /* ---------------------------------------------------------------------- */

  const clampWindow = useCallback(
    (from: number, to: number): Domain => {
      const low = Math.max(fullX[0], Math.min(from, to));
      const high = Math.min(fullX[1], Math.max(from, to));
      const minimumSpan = (fullX[1] - fullX[0]) / 2000;
      if (high - low < minimumSpan) {
        const centre = (low + high) / 2;
        return [
          Math.max(fullX[0], centre - minimumSpan / 2),
          Math.min(fullX[1], centre + minimumSpan / 2),
        ];
      }
      return [low, high];
    },
    [fullX],
  );

  const isZoomed = zoom !== null;

  const applyZoom = useCallback(
    (factor: number) => {
      const centre = cursorX ?? (x0 + x1) / 2;
      const nextSpan = xSpan * factor;
      if (nextSpan >= fullX[1] - fullX[0]) {
        setZoom(null);
        return;
      }
      setZoom(clampWindow(centre - nextSpan / 2, centre + nextSpan / 2));
    },
    [clampWindow, cursorX, fullX, x0, x1, xSpan],
  );

  const panBy = useCallback(
    (fraction: number) => {
      if (!isZoomed) return;
      const shift = xSpan * fraction;
      const low = Math.max(fullX[0], x0 + shift);
      const high = Math.min(fullX[1], x1 + shift);
      // Preserve the span when a pan runs into either end of the axis.
      if (high - low < xSpan) {
        if (x0 + shift < fullX[0]) setZoom([fullX[0], fullX[0] + xSpan]);
        else setZoom([fullX[1] - xSpan, fullX[1]]);
        return;
      }
      setZoom([low, high]);
    },
    [fullX, isZoomed, x0, x1, xSpan],
  );

  const pointerToPixel = (event: React.PointerEvent<SVGSVGElement>): number => {
    const rect = event.currentTarget.getBoundingClientRect();
    const scale = rect.width > 0 ? width / rect.width : 1;
    return (event.clientX - rect.left) * scale;
  };

  const pixelToData = useCallback(
    (pixel: number) => {
      const clamped = Math.max(margin.left, Math.min(margin.left + innerW, pixel));
      return x0 + ((clamped - margin.left) / innerW) * xSpan;
    },
    [innerW, margin.left, x0, xSpan],
  );

  const handlePointerDown = (event: React.PointerEvent<SVGSVGElement>) => {
    if (event.button !== 0 && event.pointerType === "mouse") return;
    const pixel = pointerToPixel(event);
    event.currentTarget.setPointerCapture(event.pointerId);
    setDrag({ from: pixel, to: pixel });
    setCursorX(pixelToData(pixel));
  };

  const handlePointerMove = (event: React.PointerEvent<SVGSVGElement>) => {
    const pixel = pointerToPixel(event);
    setCursorX(pixelToData(pixel));
    setDrag((current) => (current ? { ...current, to: pixel } : null));
  };

  const endDrag = (event: React.PointerEvent<SVGSVGElement>) => {
    if (event.currentTarget.hasPointerCapture(event.pointerId)) {
      event.currentTarget.releasePointerCapture(event.pointerId);
    }
    // A drag shorter than the threshold is a click, not a range selection.
    if (drag && Math.abs(drag.to - drag.from) >= MIN_DRAG_PX) {
      setZoom(clampWindow(pixelToData(drag.from), pixelToData(drag.to)));
    }
    setDrag(null);
  };

  const handleKeyDown = (event: React.KeyboardEvent<SVGSVGElement>) => {
    const stepSize = (xSpan / innerW) * (event.shiftKey ? 20 : 1);
    if (event.key === "ArrowLeft" || event.key === "ArrowRight") {
      event.preventDefault();
      const direction = event.key === "ArrowLeft" ? -1 : 1;
      setCursorX((current) => {
        const base = current ?? (x0 + x1) / 2;
        return Math.max(x0, Math.min(x1, base + direction * stepSize));
      });
      return;
    }
    if (event.key === "Home" || event.key === "End") {
      event.preventDefault();
      setCursorX(event.key === "Home" ? x0 : x1);
      return;
    }
    if (event.key === "+" || event.key === "=") {
      event.preventDefault();
      applyZoom(0.6);
      return;
    }
    if (event.key === "-" || event.key === "_") {
      event.preventDefault();
      applyZoom(1 / 0.6);
      return;
    }
    if (event.key === "0") {
      event.preventDefault();
      setZoom(null);
      return;
    }
    if (event.key === "Escape") {
      setCursorX(null);
    }
  };

  const toggleSeries = (seriesId: string) => {
    setHidden((current) => {
      const next = new Set(current);
      if (next.has(seriesId)) next.delete(seriesId);
      else next.add(seriesId);
      // Never let the reader hide every trace; that leaves an empty frame.
      return next.size === sorted.length ? current : next;
    });
  };

  /* ---------------------------------------------------------------------- */
  /* Readout                                                                 */
  /* ---------------------------------------------------------------------- */

  const readout = useMemo(() => {
    if (cursorX === null) return [];
    return visible.flatMap((item) => {
      const index = nearestIndex(item.points, cursorX);
      if (index < 0) return [];
      const point = item.points[index];
      if (point.x < x0 || point.x > x1) return [];
      return [{ item, point }];
    });
  }, [cursorX, visible, x0, x1]);

  const accessibleName = `Digitized PXRD pattern for ${figureLabel}: ${visible.length} of ${sorted.length} curves, 2θ from ${formatDegrees(x0, xSpan)} to ${formatDegrees(x1, xSpan)} degrees.`;

  /* ---------------------------------------------------------------------- */
  /* Render                                                                  */
  /* ---------------------------------------------------------------------- */

  const dragRect =
    drag && Math.abs(drag.to - drag.from) >= MIN_DRAG_PX
      ? {
          x: Math.max(margin.left, Math.min(drag.from, drag.to)),
          width: Math.min(
            Math.abs(drag.to - drag.from),
            margin.left + innerW - Math.max(margin.left, Math.min(drag.from, drag.to)),
          ),
        }
      : null;

  return (
    <div className="curve-plot">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <h3 className="flex items-center gap-2 font-semibold text-slate-900">
          <LineChart className="h-4 w-4 text-slate-500" />
          Interactive pattern
        </h3>
        <div className="flex flex-wrap items-center gap-1.5">
          <button
            type="button"
            onClick={() => setStacked((current) => !current)}
            aria-pressed={stacked}
            className={`${CONTROL_CLASS} ${stacked ? "border-slate-400 bg-slate-100" : ""}`}
          >
            <Layers className="h-3.5 w-3.5" />
            {stacked ? "Offset" : "Overlaid"}
          </button>
          <button
            type="button"
            onClick={() => panBy(-0.25)}
            disabled={!isZoomed}
            aria-label="Pan the 2 theta axis left"
            className={CONTROL_CLASS}
          >
            <ChevronLeft className="h-3.5 w-3.5" />
          </button>
          <button
            type="button"
            onClick={() => panBy(0.25)}
            disabled={!isZoomed}
            aria-label="Pan the 2 theta axis right"
            className={CONTROL_CLASS}
          >
            <ChevronRight className="h-3.5 w-3.5" />
          </button>
          <button
            type="button"
            onClick={() => applyZoom(0.6)}
            aria-label="Zoom in on the 2 theta axis"
            className={CONTROL_CLASS}
          >
            <ZoomIn className="h-3.5 w-3.5" />
          </button>
          <button
            type="button"
            onClick={() => applyZoom(1 / 0.6)}
            disabled={!isZoomed}
            aria-label="Zoom out on the 2 theta axis"
            className={CONTROL_CLASS}
          >
            <ZoomOut className="h-3.5 w-3.5" />
          </button>
          <button
            type="button"
            onClick={() => {
              setZoom(null);
              setCursorX(null);
            }}
            disabled={!isZoomed}
            className={CONTROL_CLASS}
          >
            <RotateCcw className="h-3.5 w-3.5" />
            Reset
          </button>
        </div>
      </div>

      <div
        ref={containerRef}
        className="w-full overflow-hidden rounded-xl border border-slate-200 bg-white"
      >
        {width === 0 ? (
          <div className="strategy-skeleton m-4 h-[220px]" aria-hidden="true" />
        ) : (
          <svg
            width={width}
            height={height}
            viewBox={`0 0 ${width} ${height}`}
            className="block w-full select-none"
            style={{ touchAction: "pan-y" }}
            role="img"
            tabIndex={0}
            aria-label={accessibleName}
            aria-describedby={`${summaryId} ${helpId}`}
            onPointerDown={handlePointerDown}
            onPointerMove={handlePointerMove}
            onPointerUp={endDrag}
            onPointerCancel={endDrag}
            onPointerLeave={() => {
              // A captured drag can cross the boundary; keep it alive until pointerup.
              if (drag) return;
              setCursorX(null);
            }}
            onKeyDown={handleKeyDown}
          >
            <defs>
              <clipPath id={clipId}>
                <rect x={margin.left} y={margin.top} width={innerW} height={innerH} />
              </clipPath>
            </defs>

            <g aria-hidden="true">
              {xTicks.map((tick) => (
                <line
                  key={`gx-${tick}`}
                  x1={scaleX(tick)}
                  x2={scaleX(tick)}
                  y1={margin.top}
                  y2={margin.top + innerH}
                  stroke={GRID_COLOR}
                  strokeWidth={1}
                />
              ))}
              {yTicks.map((tick) => (
                <line
                  key={`gy-${tick}`}
                  x1={margin.left}
                  x2={margin.left + innerW}
                  y1={scaleY(tick)}
                  y2={scaleY(tick)}
                  stroke={GRID_COLOR}
                  strokeWidth={1}
                />
              ))}
            </g>

            <g clipPath={`url(#${clipId})`}>
              {drawn.map(({ item, points }, laneIndex) => {
                const style = styles.get(item.seriesId);
                return (
                  <path
                    key={item.seriesId}
                    d={buildPath(
                      points,
                      scaleX,
                      stacked ? (value) => laneScale(laneIndex, value) : scaleY,
                    )}
                    fill="none"
                    stroke={style?.color ?? LABEL_COLOR}
                    strokeDasharray={style?.dash}
                    strokeWidth={1.75}
                    strokeLinejoin="round"
                    strokeLinecap="round"
                  />
                );
              })}
              {stacked
                && drawn.map(({ item }, laneIndex) => (
                  <text
                    key={`lane-${item.seriesId}`}
                    x={margin.left + 6}
                    y={margin.top + laneIndex * laneHeight + 12}
                    fontSize={10}
                    fontWeight={600}
                    fill={styles.get(item.seriesId)?.color ?? LABEL_COLOR}
                  >
                    {seriesNumber(item.seriesId)} {item.label}
                  </text>
                ))}
              {dragRect && (
                <rect
                  x={dragRect.x}
                  y={margin.top}
                  width={Math.max(0, dragRect.width)}
                  height={innerH}
                  fill="#0084D1"
                  fillOpacity={0.12}
                  stroke="#0084D1"
                  strokeOpacity={0.45}
                />
              )}
              {cursorX !== null && !drag && (
                <g aria-hidden="true">
                  <line
                    x1={scaleX(cursorX)}
                    x2={scaleX(cursorX)}
                    y1={margin.top}
                    y2={margin.top + innerH}
                    stroke={AXIS_COLOR}
                    strokeWidth={1}
                    strokeDasharray="3 3"
                  />
                  {readout.map(({ item, point }) => {
                    const laneIndex = drawn.findIndex(
                      (entry) => entry.item.seriesId === item.seriesId,
                    );
                    const cy = stacked && laneIndex >= 0
                      ? laneScale(laneIndex, point.y)
                      : scaleY(point.y);
                    return (
                      <circle
                        key={`dot-${item.seriesId}`}
                        cx={scaleX(point.x)}
                        cy={cy}
                        r={3}
                        fill="#FFFFFF"
                        stroke={styles.get(item.seriesId)?.color ?? LABEL_COLOR}
                        strokeWidth={2}
                      />
                    );
                  })}
                </g>
              )}
            </g>

            <g aria-hidden="true">
              <line
                x1={margin.left}
                x2={margin.left + innerW}
                y1={margin.top + innerH}
                y2={margin.top + innerH}
                stroke={AXIS_COLOR}
                strokeWidth={1}
              />
              <line
                x1={margin.left}
                x2={margin.left}
                y1={margin.top}
                y2={margin.top + innerH}
                stroke={AXIS_COLOR}
                strokeWidth={1}
              />
              {xTicks.map((tick) => (
                <text
                  key={`tx-${tick}`}
                  x={scaleX(tick)}
                  y={margin.top + innerH + 16}
                  textAnchor="middle"
                  fontSize={11}
                  fill={LABEL_COLOR}
                >
                  {tick.toFixed(xTickPrecision)}
                </text>
              ))}
              {yTicks.map((tick) => (
                <text
                  key={`ty-${tick}`}
                  x={margin.left - 7}
                  y={scaleY(tick) + 4}
                  textAnchor="end"
                  fontSize={11}
                  fill={LABEL_COLOR}
                >
                  {formatIntensity(tick)}
                </text>
              ))}
              <text
                x={margin.left + innerW / 2}
                y={height - 8}
                textAnchor="middle"
                fontSize={12}
                fontWeight={600}
                fill={TITLE_COLOR}
              >
                2θ (deg)
              </text>
              <text
                x={12}
                y={margin.top + innerH / 2}
                textAnchor="middle"
                transform={`rotate(-90 12 ${margin.top + innerH / 2})`}
                fontSize={12}
                fontWeight={600}
                fill={TITLE_COLOR}
              >
                {stacked ? "Relative intensity (offset)" : "Relative intensity"}
              </text>
            </g>
          </svg>
        )}
      </div>

      <p id={helpId} className="sr-only">
        Drag across the plot to zoom into a 2θ range. With the plot focused, use the left
        and right arrow keys to move the readout, Home and End to jump to either end, plus
        and minus to zoom, and 0 to reset.
      </p>

      <div
        role="status"
        aria-live="polite"
        className="mt-3 min-h-[2.5rem] rounded-xl border border-slate-200 bg-slate-50 px-3 py-2 text-xs"
      >
        {readout.length === 0 ? (
          <span className="text-slate-500">
            Hover the plot, or focus it and use the arrow keys, to read 2θ, intensity and the
            digitizer&rsquo;s 2θ uncertainty for each curve.
          </span>
        ) : (
          <ul className="flex flex-wrap gap-x-5 gap-y-1">
            {readout.map(({ item, point }) => (
              <li key={`read-${item.seriesId}`} className="flex items-center gap-1.5">
                <span
                  aria-hidden="true"
                  className="inline-block h-2.5 w-2.5 rounded-full"
                  style={{ backgroundColor: styles.get(item.seriesId)?.color }}
                />
                <span className="font-semibold text-slate-700">{item.label}</span>
                <span className="font-mono text-slate-600">
                  {formatDegrees(point.x, xSpan)}° · {formatIntensity(point.y)}
                </span>
                <span className="text-slate-500">
                  {item.uncertainty === null
                    ? "uncertainty not reported"
                    : `±${item.uncertainty.toFixed(3)}° 2θ`}
                </span>
              </li>
            ))}
          </ul>
        )}
      </div>

      <ul className="mt-3 flex flex-wrap gap-2">
        {sorted.map((item) => {
          const style = styles.get(item.seriesId);
          const shown = !hidden.has(item.seriesId);
          return (
            <li key={`legend-${item.seriesId}`}>
              <button
                type="button"
                onClick={() => toggleSeries(item.seriesId)}
                aria-pressed={shown}
                title={item.materialName ?? undefined}
                className={`flex items-center gap-2 rounded-lg border px-2.5 py-1.5 text-xs transition ${
                  shown
                    ? "border-slate-300 bg-white text-slate-700"
                    : "border-slate-200 bg-slate-50 text-slate-400"
                }`}
              >
                <svg width="22" height="8" aria-hidden="true" className="shrink-0">
                  <line
                    x1="1"
                    y1="4"
                    x2="21"
                    y2="4"
                    stroke={shown ? style?.color : "#CAD5E2"}
                    strokeDasharray={style?.dash}
                    strokeWidth={2.5}
                    strokeLinecap="round"
                  />
                </svg>
                <span className="font-mono text-[10px] text-slate-400">
                  {seriesNumber(item.seriesId)}
                </span>
                <span className="max-w-[13rem] truncate font-semibold">{item.label}</span>
                <span className="capitalize text-slate-400">{item.role}</span>
                <span className="sr-only">
                  {shown ? " — shown, activate to hide" : " — hidden, activate to show"}
                </span>
              </button>
            </li>
          );
        })}
      </ul>

      <p id={summaryId} className="mt-3 text-xs leading-relaxed text-slate-500">
        {visible.length} of {sorted.length} curve{sorted.length === 1 ? "" : "s"} plotted,{" "}
        {stacked ? "each normalised and vertically offset" : "overlaid on a shared axis"}. 2θ{" "}
        {formatDegrees(x0, xSpan)}°–{formatDegrees(x1, xSpan)}°
        {isZoomed ? " (zoomed)" : ""}. {totalSourcePoints.toLocaleString()} digitized points;{" "}
        {totalDrawnPoints.toLocaleString()} drawn after per-pixel minimum/maximum reduction,
        which keeps every peak at its measured height and position.
        {strongest
          ? ` Tallest point in view: ${strongest.label} at ${formatDegrees(strongest.point.x, xSpan)}° 2θ, intensity ${formatIntensity(strongest.point.y)}.`
          : ""}
      </p>
    </div>
  );
}
