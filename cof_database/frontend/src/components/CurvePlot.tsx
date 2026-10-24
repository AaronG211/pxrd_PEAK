import { useCallback, useId, useMemo, useState } from "react";
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
  spreadLabels,
  stepPrecision,
} from "../lib/plotGeometry";
import type { Domain, PlotPoint } from "../lib/plotGeometry";
import { isExplicitRole } from "../lib/curveRole";
import { styleSeries } from "../lib/seriesStyle";
import type { SeriesStyle } from "../lib/seriesStyle";
import type { CurveRole, FigureVerificationStatus } from "../types";

export interface CurvePlotSeries {
  seriesId: string;
  label: string;
  role: CurveRole;
  materialName: string | null;
  sampleState: string | null;
  /**
   * `two_theta_uncertainty_deg` for this trace.
   *
   * NOT a positional uncertainty. It is `rmse_deg + |slope|` of whichever axis
   * fit won (src/pxrd_fetcher/v2/extract.py) — how tightly the chosen calibration
   * line sits on its own anchors, plus one pixel of quantisation. It carries no
   * information about whether the chosen fit is the right one, which is the
   * failure `axis_arbitrated` exists to record. Hence `verificationStatus`.
   */
  uncertainty: number | null;
  points: PlotPoint[];
}

/*
 * Stroke colours and dashes come from lib/seriesStyle, one hue per series in
 * series order. They used to be a two-shade ramp per ROLE, matched to the role
 * badges in the inventory; since nearly nine in ten multi-curve figures carry a
 * single role across all their curves, that drew most of them in two shades of
 * one hue told apart only by dash pattern.
 */

const GRID_COLOR = "#E2E8F0";
const AXIS_COLOR = "#94A3B8";
const LABEL_COLOR = "#62748E";
const TITLE_COLOR = "#314158";

const MIN_DRAG_PX = 8;

/** Vertical room one end-of-trace series number needs, in pixels. */
const END_LABEL_GAP = 12;

/** Discrete positions the readout slider offers across the visible window. */
const READOUT_STEPS = 400;

/**
 * Vertical room at the top of each offset-view lane that belongs to its label
 * and never to its trace.
 *
 * Each trace is normalised so its tallest point reaches the top of its drawing
 * area, and COF patterns put that point at low 2θ — the left end, exactly where
 * the label sits. With the label drawn inside the trace's own area, its white
 * halo cut 3,398 of the 9,357 curves in multi-curve figures off flat at their
 * strongest peak at desktop width, and 5,896 at phone width (geometry over
 * outputs/pxrd.db, 2026-10-24). The band holds the label's 10 px text and its
 * 1.5 px halo, baseline at +12, with a pixel to spare above the trace stroke.
 */
const LANE_LABEL_BAND_PX = 18;

/** Gap between a lane's baseline and the label band of the lane below it. */
const LANE_FOOT_PX = 3;

/**
 * Smallest lane the offset view draws before it grows the plot instead.
 *
 * The offset view is the default for any figure with more than one curve, and
 * the published collection has figures of up to 23. Dividing a fixed 340 px
 * among them left lanes of about 12 px — flat lines under overlapping labels.
 * Below this height per lane the plot gets taller rather than the lanes thinner.
 * Net of the label band and the foot, these leave a trace 33 px tall on a
 * desktop and 25 px on a phone.
 */
const MIN_LANE_PX = 54;
const MIN_LANE_PX_NARROW = 46;

/**
 * The line sample used wherever a series is named outside the plot — legend
 * chips, readout rows, offset-view lane labels — so every place a reader matches
 * a name to a line shows the SAME colour and the same dash as that line. A plain
 * colour dot dropped the dash, which is the only thing telling apart two traces
 * that share a hue in figures with more series than the palette has colours.
 */
function SeriesSwatch({ style, muted = false }: { style: SeriesStyle | undefined; muted?: boolean }) {
  return (
    <svg width="22" height="8" aria-hidden="true" className="shrink-0">
      <line
        x1="1"
        y1="4"
        x2="21"
        y2="4"
        stroke={muted ? "#CAD5E2" : (style?.color ?? LABEL_COLOR)}
        strokeDasharray={style?.dash}
        strokeWidth={2.5}
        strokeLinecap="round"
      />
    </svg>
  );
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

/**
 * Whether this figure's 2θ axis is in dispute.
 *
 * When it is, the per-trace residual must not be printed: it is small and
 * three-decimal on exactly the figures where the axis is least trustworthy, so a
 * reader comparing peak positions would take the reassuring number and never see
 * the disagreement. On the worst arbitrated figure in the collection the two
 * calibration methods differ by 50.9° across the plot while the trace reports a
 * residual of ±0.110°.
 */
function axisIsDisputed(status: FigureVerificationStatus | null | undefined): boolean {
  return status === "axis_arbitrated" || status === "axis_unverified";
}

const CONTROL_CLASS =
  "inline-flex items-center gap-1.5 rounded-lg border border-slate-200 bg-white px-2.5 py-2 text-xs font-semibold text-slate-700 transition hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-40";
/** Icon-only buttons match the 36px pagination controls the rest of the site uses. */
const ICON_CONTROL_CLASS = `${CONTROL_CLASS} h-9 w-9 justify-center px-0`;

export function CurvePlot({
  series,
  figureLabel,
  verificationStatus = null,
}: {
  series: CurvePlotSeries[];
  figureLabel: string;
  /**
   * Calibration outcome for the figure these traces came from. Without it the
   * plot cannot qualify its own readout even in principle, which is how a
   * disputed axis used to reach the reader as a tidy ±0.110°.
   */
  verificationStatus?: FigureVerificationStatus | null;
}) {
  // useId output can contain punctuation; strip it so the value is safe both as
  // an id attribute and inside an SVG `url(#...)` reference.
  const baseId = useId().replace(/[^a-zA-Z0-9]/g, "");
  const clipId = `${baseId}-clip`;
  const summaryId = `${baseId}-summary`;
  const helpId = `${baseId}-help`;

  const [width, setWidth] = useState(0);
  const [hidden, setHidden] = useState<ReadonlySet<string>>(() => new Set<string>());
  /**
   * Offset by default whenever there is more than one trace. Published PXRD
   * figures almost always stack their patterns, so the overlaid view was the one
   * that did NOT look like the source crop above it, and with every trace on a
   * shared axis the tallest one flattens the rest. A single trace has nothing to
   * offset from and opens overlaid, with its intensity axis. Read once at mount:
   * the section only mounts this component after the figure's data has loaded.
   */
  const [stacked, setStacked] = useState(() => series.length > 1);
  const [zoom, setZoom] = useState<Domain | null>(null);
  const [cursorX, setCursorX] = useState<number | null>(null);
  const [drag, setDrag] = useState<{ from: number; to: number } | null>(null);

  const disputed = axisIsDisputed(verificationStatus);

  /**
   * Measure at commit rather than in an effect: a ResizeObserver alone never
   * delivers its first callback while the document is hidden (background tab,
   * prerender), which would leave the plot on its skeleton indefinitely. The
   * observer still handles every later change, so decimation always runs at the
   * true rendered pixel width.
   */
  const measureRef = useCallback((element: HTMLDivElement | null) => {
    if (!element) return;
    const apply = (next: number) =>
      setWidth((current) => (Math.abs(current - next) < 0.5 ? current : next));
    apply(element.getBoundingClientRect().width);
    const observer = new ResizeObserver((entries) => {
      const entry = entries[0];
      if (entry) apply(entry.contentRect.width);
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

  const styles = useMemo(() => styleSeries(sorted.map((item) => item.seriesId)), [sorted]);

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
  const margin = useMemo(
    () => ({
      top: 16,
      right: 16,
      bottom: 46,
      left: stacked ? (isNarrow ? 22 : 28) : isNarrow ? 46 : 58,
    }),
    [isNarrow, stacked],
  );
  const baseHeight = isNarrow ? 250 : 340;
  const height = stacked
    ? Math.max(
        baseHeight,
        margin.top
          + margin.bottom
          + visible.length * (isNarrow ? MIN_LANE_PX_NARROW : MIN_LANE_PX),
      )
    : baseHeight;
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
      // Fixed pixel offsets, not fractions of the lane: the label is a fixed
      // size, so a fractional margin either wasted room on tall lanes or let the
      // trace run up under the label on short ones.
      const baseline = margin.top + (laneIndex + 1) * laneHeight - LANE_FOOT_PX;
      const usable = Math.max(1, laneHeight - LANE_LABEL_BAND_PX - LANE_FOOT_PX);
      return baseline - ((value - low) / (high - low)) * usable;
    },
    [laneDomains, laneHeight, margin.top],
  );

  /**
   * Path strings, memoized.
   *
   * Every pointer move calls `setCursorX`, which re-renders. Building these
   * inline in the render map regenerated all of them on every frame even though
   * the geometry had not moved: 11 series x 3,500 points at 900px is 278 kB of
   * path data and about 5.6 ms per render on a desktop, four to six times that on
   * a mid-range phone. `cursorX` now only drives the crosshair, the dots and the
   * readout, which are cheap.
   */
  const paths = useMemo(
    () =>
      drawn.map(({ item, points }, laneIndex) => ({
        seriesId: item.seriesId,
        d: buildPath(
          points,
          scaleX,
          stacked ? (value) => laneScale(laneIndex, value) : scaleY,
        ),
      })),
    [drawn, laneScale, scaleX, scaleY, stacked],
  );

  /**
   * A series number printed at the right-hand end of each overlaid trace.
   *
   * This is the part of the identity fix that does not depend on vision at all:
   * the reader can follow a line to its label instead of matching a stroke colour
   * against a legend swatch. Skipped when the labels cannot fit without
   * overlapping, and unnecessary in offset mode, which already prints a label per
   * lane.
   */
  const endLabels = useMemo(() => {
    if (stacked) return [];
    if (drawn.length * END_LABEL_GAP > innerH) return [];
    const anchors = drawn.flatMap(({ item, points }) => {
      for (let i = points.length - 1; i >= 0; i -= 1) {
        const point = points[i];
        if (point.x >= x0 && point.x <= x1) {
          return [{ seriesId: item.seriesId, y: scaleY(point.y) }];
        }
      }
      return [];
    });
    const placed = spreadLabels(
      anchors.map((anchor) => anchor.y),
      END_LABEL_GAP,
      margin.top + 8,
      margin.top + innerH - 2,
    );
    return anchors.map((anchor, index) => ({ seriesId: anchor.seriesId, y: placed[index] }));
  }, [drawn, innerH, margin.top, scaleY, stacked, x0, x1]);

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

  /**
   * Zoom shortcuts that the range input has no native meaning for. Arrow, Home,
   * End, PageUp and PageDown are deliberately left alone so the browser's own
   * slider semantics handle them — that is the whole reason the readout is a
   * range input.
   */
  const handleReadoutKeyDown = (event: React.KeyboardEvent<HTMLInputElement>) => {
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
      event.preventDefault();
      setCursorX(null);
    }
  };

  const toggleSeries = (seriesId: string) => {
    setHidden((current) => {
      const next = new Set(current);
      if (next.has(seriesId)) next.delete(seriesId);
      else next.add(seriesId);
      // Never let the reader hide every trace; that leaves an empty frame. The
      // button that would do it is marked aria-disabled and says why, so this is
      // defence in depth rather than the user-visible mechanism.
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

  /**
   * What the browser announces when the slider moves.
   *
   * A native range control reports its own value changes, so the visible readout
   * below is NOT a live region any more — it used to be re-announced in full on
   * every keypress and on every pointer move.
   */
  const readoutValueText = useMemo(() => {
    if (cursorX === null) return "Readout cleared.";
    const head = `${formatDegrees(cursorX, xSpan)} degrees 2 theta.`;
    if (readout.length === 0) return `${head} No curve is sampled here.`;
    const spoken = readout.slice(0, 4).map(({ item, point }) => {
      const qualifier = disputed
        ? ", axis disputed, position uncertain"
        : item.uncertainty === null
          ? ""
          : `, axis-fit residual ${item.uncertainty.toFixed(3)} degrees`;
      return `${item.label}, intensity ${formatIntensity(point.y)}${qualifier}`;
    });
    const rest = readout.length > 4 ? ` And ${readout.length - 4} more curves.` : "";
    return `${head} ${spoken.join(". ")}.${rest}`;
  }, [cursorX, disputed, readout, xSpan]);

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

  const sliderValue = cursorX === null
    ? (x0 + x1) / 2
    : Math.min(x1, Math.max(x0, cursorX));

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
            // The visible word flips to name the CURRENT view, which is the right
            // sighted affordance. The accessible name must not flip with it, or a
            // screen reader hears "Overlaid, not pressed" and the name and the
            // state describe two different modes. Pressed means offset, so on a
            // multi-curve figure the button now opens pressed, because that is
            // the default view.
            aria-label="Offset the traces vertically"
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
            className={ICON_CONTROL_CLASS}
          >
            <ChevronLeft className="h-3.5 w-3.5" />
          </button>
          <button
            type="button"
            onClick={() => panBy(0.25)}
            disabled={!isZoomed}
            aria-label="Pan the 2 theta axis right"
            className={ICON_CONTROL_CLASS}
          >
            <ChevronRight className="h-3.5 w-3.5" />
          </button>
          <button
            type="button"
            onClick={() => applyZoom(0.6)}
            aria-label="Zoom in on the 2 theta axis"
            className={ICON_CONTROL_CLASS}
          >
            <ZoomIn className="h-3.5 w-3.5" />
          </button>
          <button
            type="button"
            onClick={() => applyZoom(1 / 0.6)}
            disabled={!isZoomed}
            aria-label="Zoom out on the 2 theta axis"
            className={ICON_CONTROL_CLASS}
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
        ref={measureRef}
        className="w-full overflow-hidden rounded-xl border border-slate-200 bg-white"
      >
        {width === 0 ? (
          <div className="strategy-skeleton m-4 h-[220px]" aria-hidden="true" />
        ) : (
          /*
           * `role="img"` with no tabIndex and no key handler.
           *
           * It used to carry tabIndex={0} and onKeyDown, which is an ARIA
           * contradiction: `img` is not a widget role, so NVDA and JAWS stay in
           * browse mode when it takes focus and consume Arrow, Home, End and +/-
           * with their own virtual cursor — the keys never reached the handler.
           * `role="img"` also prunes the SVG's children from the accessibility
           * tree, so there was no fallback either. The readout is a real
           * <input type="range"> below instead, which works in both screen-reader
           * modes, and it lives OUTSIDE this overflow-hidden wrapper, so its focus
           * ring is no longer clipped away by the wrapper's padding box.
           */
          <svg
            width={width}
            height={height}
            viewBox={`0 0 ${width} ${height}`}
            className="block w-full select-none"
            style={{ touchAction: "pan-y" }}
            role="img"
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
              {paths.map(({ seriesId, d }) => {
                const style = styles.get(seriesId);
                return (
                  <path
                    key={seriesId}
                    d={d}
                    fill="none"
                    stroke={style?.color ?? LABEL_COLOR}
                    strokeDasharray={style?.dash}
                    strokeWidth={1.75}
                    strokeLinejoin="round"
                    strokeLinecap="round"
                  />
                );
              })}
              {/*
                End labels, like the lane labels below, carry the series colour
                on a short line sample and print the number in dark text. Three
                of the five palette hues sit between 3:1 and 4.5:1 against white:
                enough for a 1.75 px stroke, short of what 10 px text needs. The
                sample sits at the right edge, where the trace it names ends.
              */}
              {endLabels.map(({ seriesId, y }) => {
                const style = styles.get(seriesId);
                const right = margin.left + innerW - 2;
                return (
                  <g key={`end-${seriesId}`}>
                    <line
                      x1={right - 12}
                      x2={right}
                      y1={y - 3.5}
                      y2={y - 3.5}
                      stroke={style?.color ?? LABEL_COLOR}
                      strokeDasharray={style?.dash}
                      strokeWidth={2.5}
                      strokeLinecap="round"
                    />
                    <text
                      x={right - 16}
                      y={y}
                      textAnchor="end"
                      fontSize={10}
                      fontWeight={700}
                      fill={TITLE_COLOR}
                      stroke="#FFFFFF"
                      strokeWidth={3}
                      style={{ paintOrder: "stroke" }}
                    >
                      {seriesNumber(seriesId)}
                    </text>
                  </g>
                );
              })}
              {/*
                Lane labels sit in a band of their own at the top of each lane
                (LANE_LABEL_BAND_PX), which no trace enters, so a label never
                hides a peak. The colour is on the line sample, not the text,
                for the contrast reason above; the halo is only for the
                vertical grid lines, which do cross the band.
              */}
              {stacked
                && drawn.map(({ item }, laneIndex) => {
                  const style = styles.get(item.seriesId);
                  const baseline = margin.top + laneIndex * laneHeight + 12;
                  return (
                    <g key={`lane-${item.seriesId}`}>
                      <line
                        x1={margin.left + 6}
                        x2={margin.left + 22}
                        y1={baseline - 3.5}
                        y2={baseline - 3.5}
                        stroke={style?.color ?? LABEL_COLOR}
                        strokeDasharray={style?.dash}
                        strokeWidth={2.5}
                        strokeLinecap="round"
                      />
                      <text
                        x={margin.left + 27}
                        y={baseline}
                        fontSize={10}
                        fontWeight={600}
                        fill={TITLE_COLOR}
                        stroke="#FFFFFF"
                        strokeWidth={3}
                        style={{ paintOrder: "stroke" }}
                      >
                        {seriesNumber(item.seriesId)} {item.label}
                      </text>
                    </g>
                  );
                })}
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
        Drag across the plot with a pointer to zoom into a 2θ range. The readout
        slider below moves the reading position: left and right arrow keys step it,
        Page Up and Page Down move it in larger steps, Home and End jump to either
        end of the visible window. With the slider focused, plus and minus zoom the
        axis, 0 resets the zoom, and Escape clears the readout.
      </p>

      <label className="mt-3 flex items-center gap-3">
        <span className="shrink-0 text-xs font-semibold uppercase tracking-wider text-slate-500">
          Readout 2θ
        </span>
        <input
          type="range"
          min={x0}
          max={x1}
          step={xSpan / READOUT_STEPS}
          value={sliderValue}
          onChange={(event) => setCursorX(Number(event.target.value))}
          onKeyDown={handleReadoutKeyDown}
          aria-valuetext={readoutValueText}
          aria-describedby={helpId}
          className="h-9 min-w-0 flex-1 cursor-pointer accent-blue-700"
        />
        <span className="w-16 shrink-0 text-right font-mono text-xs text-slate-600">
          {cursorX === null ? "—" : `${formatDegrees(cursorX, xSpan)}°`}
        </span>
      </label>

      <div className="mt-2 min-h-[2.5rem] rounded-xl border border-slate-200 bg-slate-50 px-3 py-2 text-xs">
        {readout.length === 0 ? (
          <span className="text-slate-500">
            Hover the plot, or move the readout slider, to read 2θ and intensity for
            each curve.
          </span>
        ) : (
          <ul className="flex flex-wrap gap-x-5 gap-y-1">
            {readout.map(({ item, point }) => (
              <li key={`read-${item.seriesId}`} className="flex items-center gap-1.5">
                <SeriesSwatch style={styles.get(item.seriesId)} />
                <span className="font-mono text-[10px] text-slate-600">
                  {seriesNumber(item.seriesId)}
                </span>{" "}
                <span className="font-semibold text-slate-700">{item.label}</span>{" "}
                <span className="font-mono text-slate-600">
                  {formatDegrees(point.x, xSpan)}° · {formatIntensity(point.y)}
                </span>{" "}
                {/*
                  The number that used to sit here was labelled "2θ uncertainty".
                  It is the residual of the chosen axis fit and says nothing about
                  that fit being the right one, so on a disputed axis it is
                  replaced outright rather than printed beside a caveat — a number
                  beside a caveat wins every time.
                */}
                {disputed ? (
                  <span className="font-semibold text-rose-700">
                    2θ axis disputed · position uncertain
                  </span>
                ) : (
                  <span className="text-slate-500">
                    {item.uncertainty === null
                      ? "fit residual not reported"
                      : `axis-fit residual ±${item.uncertainty.toFixed(3)}°`}
                  </span>
                )}
              </li>
            ))}
          </ul>
        )}
      </div>

      <ul className="mt-3 flex flex-wrap gap-2">
        {sorted.map((item) => {
          const style = styles.get(item.seriesId);
          const shown = !hidden.has(item.seriesId);
          // Hiding this one would empty the plot. Announce that instead of
          // silently doing nothing while `aria-pressed` and the screen-reader text
          // both promise the opposite.
          const isLastVisible = shown && visible.length === 1;
          return (
            <li key={`legend-${item.seriesId}`}>
              <button
                type="button"
                onClick={() => toggleSeries(item.seriesId)}
                aria-pressed={shown}
                aria-disabled={isLastVisible || undefined}
                title={
                  isLastVisible
                    ? "At least one curve must stay plotted"
                    : (item.materialName ?? undefined)
                }
                className={`flex items-center gap-2 rounded-lg border px-2.5 py-1.5 text-xs transition ${
                  shown
                    ? "border-slate-300 bg-white text-slate-700"
                    : "border-slate-200 bg-slate-50 text-slate-500"
                } ${isLastVisible ? "cursor-not-allowed opacity-70" : ""}`}
              >
                <SeriesSwatch style={style} muted={!shown} />
                <span className="font-mono text-[10px] text-slate-600">
                  {seriesNumber(item.seriesId)}
                </span>
                <span className="max-w-[13rem] truncate font-semibold">{item.label}</span>
                {/* Only a role that was recorded. `unclassified` means none
                    was, and printed on half the chips in the collection it read
                    as a classification failure. See lib/curveRole. */}
                {isExplicitRole(item.role) && (
                  <span className="capitalize text-slate-500">{item.role}</span>
                )}
                <span className="sr-only">
                  {isLastVisible
                    ? " — shown, and the only plotted curve, so it cannot be hidden"
                    : shown
                      ? " — shown, activate to hide"
                      : " — hidden, activate to show"}
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
        {disputed
          ? " The 2θ axis of this figure is disputed: the two calibration methods disagreed, so every position on this plot carries an unquantified axis error on top of the digitizer's own residual."
          : ""}
      </p>
    </div>
  );
}
