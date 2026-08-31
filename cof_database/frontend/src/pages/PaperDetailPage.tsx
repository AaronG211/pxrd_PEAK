import { useEffect, useMemo, useRef, useState } from "react";
import {
  Activity,
  ArrowLeft,
  Download,
  ExternalLink,
  FileImage,
  Link2,
  Maximize2,
  ScanLine,
  TriangleAlert,
} from "lucide-react";
import { Link, useParams } from "react-router-dom";
import { CurvePlot } from "../components/CurvePlot";
import type { CurvePlotSeries } from "../components/CurvePlot";
import { PublicationStatusBadge, PublicationStatusBanner } from "../components/PublicationStatusBadge";
import { fetchPaper, getMaterialLabelIndex } from "../lib/api";
import { describeCurveDataError, fetchFigureCurves, isAbortError } from "../lib/curveData";
import { EMPTY_MATERIAL_LABEL_INDEX, groupForLabel } from "../lib/materialGroups";
import type { MaterialLabelGroup, MaterialLabelIndex } from "../lib/materialGroups";
import { WAVELENGTH_ASSUMPTION_LONG } from "../lib/peakSearch";
import type {
  CurveRole,
  FigureVerificationStatus,
  FirstPeakStatus,
  PaperDetail,
  PxrdCurve,
  PxrdFigure,
  StackingHumpStatus,
} from "../types";

const roleStyles: Record<CurveRole, string> = {
  experimental: "border-emerald-200 bg-emerald-50 text-emerald-700",
  simulated: "border-sky-200 bg-sky-50 text-sky-700",
  refined: "border-violet-200 bg-violet-50 text-violet-700",
  difference: "border-amber-200 bg-amber-50 text-amber-700",
  reference: "border-slate-200 bg-slate-100 text-slate-700",
  unclassified: "border-slate-200 bg-slate-50 text-slate-500",
};

/**
 * `axis_arbitrated` and `axis_unverified` both mean the 2theta scale is not
 * trustworthy, for different reasons. Everything that prints a position or a
 * residual on such a figure has to say so.
 */
function axisIsDisputed(status: FigureVerificationStatus | null): boolean {
  return status === "axis_arbitrated" || status === "axis_unverified";
}

const firstPeakStatusStyles: Record<FirstPeakStatus, string> = {
  ok: "border-emerald-200 bg-emerald-50 text-emerald-700",
  low_confidence: "border-amber-200 bg-amber-50 text-amber-800",
  truncated_at_window_start: "border-slate-200 bg-slate-100 text-slate-600",
  no_bragg_peak: "border-slate-200 bg-slate-100 text-slate-600",
};

const firstPeakStatusLabels: Record<FirstPeakStatus, string> = {
  ok: "ok",
  low_confidence: "low confidence",
  truncated_at_window_start: "cut off at window start",
  no_bragg_peak: "no Bragg peak",
};

const firstPeakStatusTitles: Record<FirstPeakStatus, string> = {
  ok: "Cleared the detector's signal-to-noise, persistence, width and edge gates.",
  low_confidence:
    "A real peak, but either weak (3.5-5 sigma) or with evidence that a lower-angle peak lies outside the plotted window.",
  truncated_at_window_start:
    "The pattern starts on a rising edge, so the first peak lies below the plotted range and has no position here.",
  no_bragg_peak: "No peak cleared the detector's gates anywhere in this trace.",
};

const humpStatusStyles: Record<StackingHumpStatus, string> = {
  hump_detected: "border-amber-200 bg-amber-50 text-amber-800",
  no_hump_detected: "border-emerald-200 bg-emerald-50 text-emerald-700",
  window_not_covered: "border-dashed border-slate-300 bg-white text-slate-500",
  not_computed: "border-dashed border-slate-300 bg-white text-slate-400",
};

const humpStatusLabels: Record<StackingHumpStatus, string> = {
  hump_detected: "hump detected",
  no_hump_detected: "no hump (window plotted)",
  window_not_covered: "not determinable",
  not_computed: "no descriptor computed",
};

const humpStatusTitles: Record<StackingHumpStatus, string> = {
  hump_detected: "A broad stacking hump was found inside the 15-35 deg search window.",
  no_hump_detected:
    "A MEASURED absence: the 15-35 deg window was plotted and carried no stacking hump, which is what a well-ordered sample looks like.",
  window_not_covered:
    "NOT a measurement: this figure never plotted enough of the 15-35 deg window to look. Nothing is known about a stacking hump here.",
  not_computed:
    "No descriptor was computed for this curve at all. An absence of data, not a low value.",
};

function Meta({ label, value }: { label: string; value: string | number | null }) {
  if (value === null || value === "") return null;
  return (
    <div>
      <p className="text-xs font-semibold uppercase tracking-wider text-slate-500">
        {label}
      </p>
      <p className="mt-1 text-sm font-medium text-slate-700">{value}</p>
    </div>
  );
}

function FigureImage({
  url,
  alt,
  label,
  icon,
}: {
  url: string;
  alt: string;
  label: string;
  icon: React.ReactNode;
}) {
  return (
    <figure className="bg-slate-50 p-4 md:p-6">
      <figcaption className="mb-3 flex items-center justify-between gap-3 text-xs font-semibold uppercase tracking-wider text-slate-500">
        <span className="flex items-center gap-2">{icon}{label}</span>
        <a
          href={url}
          target="_blank"
          rel="noreferrer"
          className="inline-flex items-center gap-1 text-slate-500 transition hover:text-slate-900"
        >
          <Maximize2 className="h-3.5 w-3.5" /> Full size
        </a>
      </figcaption>
      <a
        href={url}
        target="_blank"
        rel="noreferrer"
        className="flex min-h-[320px] items-center justify-center overflow-hidden rounded-xl border border-slate-200 bg-white p-2 transition hover:border-slate-300 md:min-h-[420px]"
      >
        <img src={url} alt={alt} className="max-h-[430px] w-full object-contain" loading="lazy" />
      </a>
    </figure>
  );
}

/**
 * Per-figure automated checks.
 *
 * These badges carry only what VARIES between figures. The fact that nothing
 * here was reviewed by a human is true of every figure equally, so it is stated
 * once in the page-level "How is this verified?" panel rather than repeated as
 * an identical chip on all 1,866 of them — a label that never changes is not
 * information, and it crowds out the badges that are.
 *
 * All of these stay silent until the verification columns exist.
 */
function FigureQualityBadges({ figure }: { figure: PxrdFigure }) {
  const agreement = figure.axisAgreementDeg;
  const detected = figure.seriesDetected;
  const digitized = figure.seriesDigitized;
  const omitted = figure.seriesOmittedComputed ?? 0;

  return (
    <>
      {figure.verificationStatus === "axis_cross_validated" && (
        <span className="badge">2θ axis cross-checked</span>
      )}
      {figure.verificationStatus === "axis_single_method" && (
        <span className="badge border-amber-200 bg-amber-50 text-amber-700">
          2θ axis from one method
        </span>
      )}
      {figure.verificationStatus === "axis_arbitrated" && (
        <span className="badge border-rose-200 bg-rose-50 text-rose-700">
          <TriangleAlert className="mr-1 h-3 w-3" aria-hidden="true" />
          {agreement === null
            ? "2θ axis disputed"
            : `2θ axis disputed — methods differed by ${agreement.toFixed(agreement < 1 ? 2 : 1)}°`}
        </span>
      )}
      {figure.verificationStatus === "axis_unverified" && (
        <span className="badge border-amber-200 bg-amber-50 text-amber-700">
          2θ axis not verified
        </span>
      )}

      {detected !== null && digitized !== null && digitized < detected && (
        <span className="badge border-rose-200 bg-rose-50 text-rose-700">
          Incomplete · {digitized} of {detected} series digitized
        </span>
      )}
      {/*
        The column behind this is `count(*) where in_clean_set = 0` with NO filter
        on why the curve was dropped. It happens to be all computed traces on
        today's published figures, but database-wide 1,362 of the 3,732 excluded
        curves carry digitization quality flags and 65 are labelled
        "Experimental" — one re-import at a different limit and the old copy would
        be presenting a failed extraction as a deliberate scope decision. So the
        badge now claims only what the count knows.
      */}
      {omitted > 0 && (
        <span className="badge" title="Detected in the figure but not published: either a computed trace that is out of scope, or a trace whose digitization did not pass quality checks.">
          {omitted} curve{omitted === 1 ? "" : "s"} not digitized
        </span>
      )}
      {figure.qualityStatus === "flagged" && (
        <span className="badge border-rose-200 bg-rose-50 text-rose-700">Flagged</span>
      )}
    </>
  );
}

function VerificationNumber({
  label,
  value,
  note,
}: {
  label: string;
  value: string | null;
  note: string;
}) {
  if (value === null) return null;
  return (
    <div>
      <p className="text-xs font-semibold uppercase tracking-wider text-slate-500">{label}</p>
      <p className="mt-1 font-mono text-sm font-semibold text-slate-800">{value}</p>
      <p className="mt-0.5 text-xs text-slate-500">{note}</p>
    </div>
  );
}

/**
 * WHAT `two_theta_uncertainty_deg` IS, and why this component no longer calls it
 * an uncertainty.
 *
 * It is `fit.rmse_deg + abs(fit.slope)` (src/pxrd_fetcher/v2/extract.py): the
 * residual of whichever axis fit won, plus one pixel of quantisation. It measures
 * how tightly the CHOSEN calibration line sits on its own anchors. It contains no
 * information about whether the chosen line is the right one — which is exactly
 * the failure `axis_arbitrated` records. Corpus-wide it runs 0.0061-0.2923 deg:
 * three decimals of apparent precision, always small, always reassuring, whether
 * or not the axis is in dispute. On the worst arbitrated figure the tick fit and
 * the OCR fit disagree by 50.921 deg across the plot while its single curve
 * reports 0.1097.
 *
 * So: it is labelled "Axis-fit residual", the disagreement between the two
 * methods gets its own tile beside it, and a disputed axis gets a banner ABOVE
 * the numbers rather than a footnote below them. The old layout led with this
 * value in the first and largest slot, described as "2θ uncertainty ... worst
 * curve on this figure", which reads as a conservative position error bar.
 */
function FigureVerification({ figure }: { figure: PxrdFigure }) {
  const rmse = figure.axisRmseDeg;
  const ticks = figure.axisTickCount;
  const agreement = figure.axisAgreementDeg;
  const digitized = figure.seriesDigitized;
  const detected = figure.seriesDetected;
  // Worst case across the figure's curves: the number a reader needs before reuse.
  const uncertainties = figure.curves.flatMap((curve) =>
    curve.twoThetaUncertaintyDeg === null ? [] : [curve.twoThetaUncertaintyDeg],
  );
  const confidences = figure.curves.flatMap((curve) =>
    curve.traceConfidence === null ? [] : [curve.traceConfidence],
  );
  const worstUncertainty = uncertainties.length > 0 ? Math.max(...uncertainties) : null;
  const lowestConfidence = confidences.length > 0 ? Math.min(...confidences) : null;
  const disputed = axisIsDisputed(figure.verificationStatus);
  const hasNumbers =
    rmse !== null
    || ticks !== null
    || agreement !== null
    || digitized !== null
    || worstUncertainty !== null
    || lowestConfidence !== null;

  return (
    <div className="rounded-xl border border-slate-200 bg-slate-50/70 p-4">
      {disputed && (
        <div className="mb-4 rounded-lg border border-rose-200 bg-rose-50 p-3 text-sm text-rose-900">
          <p className="flex items-start gap-2 font-semibold">
            <TriangleAlert className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
            {figure.verificationStatus === "axis_arbitrated"
              ? agreement === null
                ? "The 2θ axis of this figure is disputed."
                : `The 2θ axis of this figure is disputed: the two calibration methods differed by ${agreement.toFixed(agreement < 1 ? 3 : 1)}° across the plot.`
              : "The 2θ axis of this figure could not be verified."}
          </p>
          <p className="mt-1.5 leading-relaxed">
            The axis was fit twice, once from tick marks and once from OCR of the axis
            labels, and the two fits did not agree; a further read chose between them.
            The residual below measures how tightly the <em>chosen</em> fit sits on its
            own anchors. It does not measure whether the right fit was chosen, and it
            stays small on exactly these figures. Treat every peak position on this
            figure as provisional and check it against the source crop.
          </p>
        </div>
      )}
      {hasNumbers ? (
        <div className="grid grid-cols-2 gap-x-6 gap-y-4 sm:grid-cols-3 lg:grid-cols-5">
          <VerificationNumber
            label="Axis-fit residual"
            value={
              worstUncertainty !== null
                ? `±${worstUncertainty.toFixed(3)}°`
                : rmse === null
                  ? null
                  : `±${rmse.toFixed(3)}°`
            }
            note={
              worstUncertainty !== null
                ? "worst trace; the chosen fit's own residual, not a position error bar"
                : "figure level; the chosen fit's own residual, not a position error bar"
            }
          />
          <VerificationNumber
            label="Method disagreement"
            value={agreement === null ? null : `${agreement.toFixed(3)}°`}
            note="tick fit vs. label OCR, across the plot — the number the residual cannot see"
          />
          <VerificationNumber
            label="Trace fidelity"
            value={lowestConfidence === null ? null : lowestConfidence.toFixed(3)}
            note="lowest of the curves, 0-1, automated"
          />
          <VerificationNumber
            label="Axis anchors"
            value={ticks === null ? null : `${ticks} ticks`}
            note="tick marks the axis was fit to"
          />
          <VerificationNumber
            label="Series digitized"
            value={
              digitized === null
                ? null
                : detected === null
                  ? String(digitized)
                  : `${digitized} of ${detected}`
            }
            note="traces accepted from those detected"
          />
        </div>
      ) : (
        <p className="text-sm text-slate-600">
          Per-figure calibration numbers are not published for this record yet. The
          axis-fit residual the digitizer reported for each trace is shown in the plot
          readout above; it is the residual of the fit that was chosen, and it cannot
          tell you whether that fit was the right one.
        </p>
      )}
      <details className="mt-3">
        <summary className="cursor-pointer text-xs font-semibold text-blue-700 hover:underline">
          How is this verified?
        </summary>
        <p className="mt-2 max-w-3xl text-xs leading-relaxed text-slate-600">
          No human has reviewed this extraction. Every number here is produced automatically.
          The 2θ axis is fit twice — once from detected tick marks, once from OCR of the axis
          labels. When the two fits agree within 0.5° across the plot, the axis is marked
          cross-checked. When they disagree, a second read breaks the tie and the figure is
          flagged. The axis-fit residual is computed from the winning fit alone, so it says
          nothing about that fit being correct — read it next to the method disagreement, not
          instead of it. Compare against the original crop before reuse.
        </p>
      </details>
    </div>
  );
}

/**
 * Interactive plot section.
 *
 * The bundle is only fetched once the section nears the viewport: a paper can
 * carry many figures and each `curves.csv.gz` is tens to hundreds of kilobytes,
 * so fetching them all on mount would be far worse than the images already are.
 */
function FigurePlotSection({
  figure,
  csvUrl,
}: {
  figure: PxrdFigure;
  csvUrl: string | null | undefined;
}) {
  const sectionRef = useRef<HTMLDivElement | null>(null);
  // No IntersectionObserver (very old browser, some test runners) means load eagerly
  // rather than never.
  const [inView, setInView] = useState(() => typeof IntersectionObserver === "undefined");
  const [series, setSeries] = useState<CurvePlotSeries[] | null>(null);
  const [error, setError] = useState("");

  const curvesBySeriesId = useMemo(
    () => new Map(figure.curves.map((curve) => [curve.seriesId, curve])),
    [figure.curves],
  );

  useEffect(() => {
    if (inView) return;
    const element = sectionRef.current;
    if (!element) return;
    const observer = new IntersectionObserver(
      (entries) => {
        if (entries.some((entry) => entry.isIntersecting)) {
          setInView(true);
          observer.disconnect();
        }
      },
      { rootMargin: "300px 0px" },
    );
    observer.observe(element);
    return () => observer.disconnect();
  }, [inView]);

  useEffect(() => {
    if (!inView || !csvUrl) return;
    let active = true;
    const controller = new AbortController();
    fetchFigureCurves(csvUrl, controller.signal)
      .then((loaded) => {
        if (!active) return;
        setSeries(
          loaded.map((item) => ({
            seriesId: item.seriesId,
            label: curvesBySeriesId.get(item.seriesId)?.label || item.label,
            role: curvesBySeriesId.get(item.seriesId)?.role ?? "unclassified",
            materialName: item.materialName,
            sampleState: item.sampleState,
            uncertainty: item.uncertainty,
            points: item.points,
          })),
        );
      })
      .catch((cause: unknown) => {
        if (!active || isAbortError(cause)) return;
        setError(describeCurveDataError(cause));
      });
    return () => {
      active = false;
      controller.abort();
    };
  }, [csvUrl, curvesBySeriesId, inView]);

  const isLoading = Boolean(csvUrl) && inView && series === null && error === "";

  return (
    <div ref={sectionRef} className="border-b border-slate-200 p-5 md:p-7">
      {!csvUrl ? (
        <p className="rounded-xl border border-slate-200 bg-slate-50 p-4 text-sm text-slate-600">
          No curve data file is attached to this figure, so there is nothing to plot. The
          digitized trace image above is still available.
        </p>
      ) : error ? (
        <div
          role="alert"
          className="flex items-start gap-3 rounded-xl border border-amber-200 bg-amber-50 p-4 text-sm text-amber-900"
        >
          <TriangleAlert className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
          <span>{error}</span>
        </div>
      ) : isLoading ? (
        <div
          className="rounded-xl border border-slate-200 bg-white py-10"
          role="status"
          aria-live="polite"
        >
          <div className="flex flex-col items-center">
            <div className="strategy-loader-wrap" aria-hidden="true">
              <div className="strategy-loader-ring" />
              <div className="strategy-loader-ring strategy-loader-ring-delay" />
            </div>
            <p className="mt-6 font-medium text-slate-500">Loading digitized curve data...</p>
          </div>
        </div>
      ) : series ? (
        <CurvePlot
          series={series}
          figureLabel={figure.figureLabel || "this PXRD figure"}
          verificationStatus={figure.verificationStatus}
        />
      ) : (
        <div className="strategy-skeleton h-[260px] w-full" aria-hidden="true" />
      )}
    </div>
  );
}

/**
 * The cross-paper material label affordance.
 *
 * The claim is about the LABEL, never the material, and the wording says so at
 * every size. A group only reaches this component when it passed the specificity
 * gate in lib/materialGroups AND spans more than one paper, so the absence of a
 * chip means "nothing linkable" — it never means "this material is unique", and
 * nothing in the UI says otherwise.
 */
function MaterialLabelLink({ group }: { group: MaterialLabelGroup }) {
  const others = group.paperCount - 1;
  const basis = group.matchBasis === "label_identical"
    ? "Identical printed labels."
    : `Labels differing only in formatting: ${group.variants.join(", ")}.`;
  return (
    <Link
      to={`/?label=${encodeURIComponent(group.key)}`}
      title={`${others} other paper${others === 1 ? "" : "s"} print a label that normalises to "${group.key}". ${basis} This is label agreement, not verified material identity — check each paper before treating these as the same material.`}
      className="ml-2 inline-flex items-center whitespace-nowrap rounded-full border border-sky-200 bg-sky-50 px-2 py-0.5 text-[11px] font-semibold text-sky-800 transition hover:border-sky-300 hover:bg-sky-100"
    >
      same label in {others} other paper{others === 1 ? "" : "s"}
    </Link>
  );
}

/** A number the reader may copy, or an em-dash. Never a blank cell, never a zero. */
function NumericCell({ value, suffix = "" }: { value: number | null; suffix?: string }) {
  return (
    <td className="whitespace-nowrap px-4 py-3 text-right font-mono text-xs text-slate-600">
      {value === null ? "—" : `${value}${suffix}`}
    </td>
  );
}

/**
 * Per-curve crystallinity descriptors, in their own table rather than as six more
 * columns on the inventory.
 *
 * They need more caveat per number than any other field on the page — one of them
 * is deliberately NOT offered as a filter anywhere on the site — and burying that
 * in a fourteen-column scroll would be the same mistake as the old uncertainty
 * tile.
 */
function CurveDescriptors({ figure }: { figure: PxrdFigure }) {
  const rows = figure.curves.filter(
    (curve) => curve.crystallineFraction !== null || curve.stackingHumpStatus !== null,
  );
  // `not_computed` and a NULL are the same fact in two encodings; the table
  // renders both, and never as a value.
  if (rows.length === 0) return null;

  return (
    <details className="mt-4 rounded-xl border border-slate-200">
      <summary className="cursor-pointer px-4 py-3 text-sm font-semibold text-slate-800">
        Crystallinity descriptors
        <span className="ml-2 font-normal text-slate-500">
          {rows.length} of {figure.curves.length} curve{figure.curves.length === 1 ? "" : "s"}
        </span>
      </summary>
      <div className="overflow-x-auto border-t border-slate-200">
        <table className="min-w-full text-left text-sm">
          <thead className="bg-slate-50 text-xs font-semibold uppercase tracking-wider text-slate-500">
            <tr>
              <th scope="col" className="px-4 py-3">Series</th>
              <th scope="col" className="px-4 py-3">Stacking hump</th>
              <th scope="col" className="px-4 py-3 text-right">Hump centre</th>
              <th scope="col" className="px-4 py-3 text-right">Hump FWHM</th>
              <th scope="col" className="px-4 py-3 text-right">(100)/(001)</th>
              <th scope="col" className="px-4 py-3 text-right">Sharp-feature fraction</th>
              <th scope="col" className="px-4 py-3 text-right">2θ window</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-100">
            {rows.map((curve) => (
              <tr key={`desc-${curve.id}`} className="hover:bg-slate-50/80">
                <td className="whitespace-nowrap px-4 py-3 font-mono text-xs text-slate-500">
                  {curve.seriesId.split("-").at(-1)}
                </td>
                <td className="px-4 py-3">
                  {curve.stackingHumpStatus === null ? (
                    <span
                      className="text-xs italic text-slate-400"
                      title="No descriptor was computed for this curve. That is an absence of data, not a low value."
                    >
                      no descriptor computed
                    </span>
                  ) : (
                    <span
                      title={humpStatusTitles[curve.stackingHumpStatus]}
                      className={`inline-flex rounded-full border px-2 py-0.5 text-xs font-semibold ${humpStatusStyles[curve.stackingHumpStatus]}`}
                    >
                      {humpStatusLabels[curve.stackingHumpStatus]}
                    </span>
                  )}
                </td>
                <NumericCell
                  value={curve.stackingHumpCenterDeg === null ? null : Number(curve.stackingHumpCenterDeg.toFixed(2))}
                  suffix="°"
                />
                <NumericCell
                  value={curve.stackingHumpFwhmDeg === null ? null : Number(curve.stackingHumpFwhmDeg.toFixed(2))}
                  suffix="°"
                />
                <NumericCell
                  value={curve.intensityRatio100001 === null
                    ? null
                    : Number(curve.intensityRatio100001.toFixed(curve.intensityRatio100001 >= 10 ? 1 : 3))}
                />
                <NumericCell
                  value={curve.crystallineFraction === null ? null : Number(curve.crystallineFraction.toFixed(3))}
                />
                <td className="whitespace-nowrap px-4 py-3 text-right font-mono text-xs text-slate-600">
                  {curve.twoThetaMin !== null && curve.twoThetaMax !== null
                    ? `${curve.twoThetaMin.toFixed(1)}–${curve.twoThetaMax.toFixed(1)}°`
                    : "—"}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="space-y-2 border-t border-slate-200 px-4 py-3 text-xs leading-relaxed text-slate-600">
        <p>
          <strong className="font-semibold text-slate-800">
            &ldquo;No hump&rdquo; and &ldquo;not determinable&rdquo; are different facts.
          </strong>{" "}
          A curve marked <em>no hump (window plotted)</em> was measured across 15–35° 2θ
          and carried none — that is what a well-ordered sample looks like. A curve marked
          <em> not determinable</em> comes from a figure that never plotted enough of that
          window, so nothing was measured. Neither is a low crystallinity value, and a
          curve with no descriptor row at all is a third case again.
        </p>
        <p>
          <strong className="font-semibold text-slate-800">Sharp-feature fraction</strong>{" "}
          (<code className="font-mono">crystalline_fraction</code>, v0) is the share of
          integrated intensity in features narrower than about 4°. It is <em>not</em> a
          degree of crystallinity and has no relation to a crystalline weight fraction or
          to a Rietveld quantity. It is also confounded by how much of the pattern the
          authors chose to plot — across the collection the median runs 0.85 for windows
          under 20° against 0.56 for 80–100° windows — so read it beside the 2θ window in
          the last column and do not compare it between curves plotted over different
          ranges. That is why the site offers no crystallinity-fraction filter.
        </p>
        <p>
          <strong className="font-semibold text-slate-800">(100)/(001)</strong> is a
          height ratio taken inside one curve, so it is scale-free and is the one
          descriptor here that compares across curves. It needs both a first peak and a
          detected hump.
        </p>
      </div>
    </details>
  );
}

function FigureCard({
  figure,
  index,
  labelIndex,
}: {
  figure: PxrdFigure;
  index: number;
  labelIndex: MaterialLabelIndex;
}) {
  const csvUrl = figure.curves.find((curve) => curve.dataUrl)?.dataUrl;
  const totalPoints = figure.curves.reduce((sum, curve) => sum + curve.pointCount, 0);
  // Column-presence flags, computed once and gating <th> and <td> as matched pairs.
  const showResidual = figure.curves.some((curve) => curve.twoThetaUncertaintyDeg !== null);
  const showFirstPeak = figure.curves.some((curve) => curve.firstPeakStatus !== null);
  const disputed = axisIsDisputed(figure.verificationStatus);
  const assumedWavelength = figure.curves.some(
    (curve) => curve.firstPeakWavelengthSource === "assumed_cu_ka",
  );
  const [linkCopied, setLinkCopied] = useState(false);
  const [copyFailed, setCopyFailed] = useState(false);

  const copyFigureLink = () => {
    const link = `${window.location.origin}${window.location.pathname}#${figure.id}`;
    // Rejects on an insecure origin and in Firefox without permission. Left
    // unhandled it produced an unhandled rejection and a button stuck on "Copy
    // link" with no explanation.
    navigator.clipboard?.writeText(link).then(
      () => {
        setLinkCopied(true);
        window.setTimeout(() => setLinkCopied(false), 1600);
      },
      () => setCopyFailed(true),
    );
  };

  const firstPeakCell = (curve: PxrdCurve) => {
    if (curve.firstPeakTwoThetaDeg !== null) {
      // The position is READ OFF the calibrated axis, so a calibration the two
      // methods disagree about undermines this number and the d derived from
      // it — not just the residual. The qualifier is appended inline rather
      // than placed beside the cell so it survives a copy into a spreadsheet,
      // which is the same reason the residual cell states its own caveat.
      return (
        <span className="font-mono text-xs text-slate-700">
          {curve.firstPeakTwoThetaDeg.toFixed(3)}°
          {disputed && (
            <span className="font-sans font-semibold text-rose-700"> · axis disputed</span>
          )}
        </span>
      );
    }
    // truncated_at_window_start and no_bragg_peak carry no position, and saying
    // WHY is more useful than an em-dash that reads like missing data.
    return curve.firstPeakStatus === null ? (
      <span className="text-xs text-slate-400">—</span>
    ) : (
      <span className="text-xs text-slate-500">
        {firstPeakStatusLabels[curve.firstPeakStatus]}
      </span>
    );
  };

  return (
    <article
      id={figure.id}
      className="reveal-up scroll-mt-24 overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-sm"
      style={{ animationDelay: `${index * 100}ms` }}
    >
      <div className="flex flex-col gap-4 border-b border-slate-200 px-5 py-5 sm:flex-row sm:items-start sm:justify-between md:px-7">
        <div>
          <div className="mb-2 flex flex-wrap items-center gap-2">
            <h2 className="text-xl font-semibold text-slate-900">
              {figure.figureLabel || `PXRD figure ${index + 1}`}
            </h2>
            <FigureQualityBadges figure={figure} />
            {figure.pageNumber && (
              <span className="text-xs font-medium text-slate-400">
                page {figure.pageNumber}
              </span>
            )}
          </div>
          {figure.caption && (
            <p className="max-w-3xl text-sm leading-relaxed text-slate-600">
              {figure.caption}
            </p>
          )}
        </div>
        <div className="flex shrink-0 flex-wrap gap-2">
          <button
            type="button"
            onClick={copyFigureLink}
            className="inline-flex items-center justify-center gap-2 rounded-lg border border-slate-200 bg-white px-3 py-2.5 text-sm font-semibold text-slate-700 transition hover:bg-slate-50"
          >
            <Link2 className="h-4 w-4" />{" "}
            {linkCopied ? "Copied" : copyFailed ? "Copy blocked — use the address bar" : "Copy link"}
          </button>
          {csvUrl && (
            <a
              href={csvUrl}
              download
              className="inline-flex items-center justify-center gap-2 rounded-lg bg-slate-900 px-4 py-2.5 text-sm font-semibold text-white transition hover:bg-slate-700"
            >
              <Download className="h-4 w-4" />
              Download all curves (.csv.gz)
            </a>
          )}
        </div>
      </div>

      <div className={`grid gap-px bg-slate-200 ${figure.overlayUrl ? "lg:grid-cols-3" : "lg:grid-cols-2"}`}>
        <FigureImage
          url={figure.sourceCropUrl}
          alt={`Published source crop for ${figure.figureLabel || "PXRD figure"}`}
          label="Source figure crop"
          icon={<FileImage className="h-4 w-4" />}
        />
        <FigureImage
          url={figure.digitizedPlotUrl}
          alt={`Digitized traces for ${figure.figureLabel || "PXRD figure"}`}
          label="Digitized traces"
          icon={<ScanLine className="h-4 w-4" />}
        />
        {figure.overlayUrl && (
          <FigureImage
            url={figure.overlayUrl}
            alt={`Source and digitized overlay for ${figure.figureLabel || "PXRD figure"}`}
            label="Trace overlay"
            icon={<Activity className="h-4 w-4" />}
          />
        )}
      </div>

      <FigurePlotSection figure={figure} csvUrl={csvUrl} />

      <div className="p-5 md:p-7">
        <div className="mb-4">
          <FigureVerification figure={figure} />
        </div>
        <div className="mb-4 flex flex-wrap items-center justify-between gap-2">
          <h3 className="font-semibold text-slate-900">Curve inventory</h3>
          <span className="text-xs text-slate-500">
            {figure.curves.length} curves · {totalPoints.toLocaleString()} points
          </span>
        </div>
        <div className="overflow-x-auto rounded-xl border border-slate-200">
          <table className="min-w-full text-left text-sm">
            <thead className="bg-slate-50 text-xs font-semibold uppercase tracking-wider text-slate-500">
              <tr>
                <th scope="col" className="px-4 py-3">Series</th>
                <th scope="col" className="px-4 py-3">Label</th>
                <th scope="col" className="px-4 py-3">Type</th>
                <th scope="col" className="px-4 py-3">Material</th>
                <th scope="col" className="px-4 py-3">2θ range</th>
                {showFirstPeak && (
                  <>
                    <th scope="col" className="px-4 py-3">First peak 2θ</th>
                    <th scope="col" className="px-4 py-3 text-right">
                      d (Å)
                      <span aria-hidden="true" className="font-normal"> †</span>
                    </th>
                    <th scope="col" className="px-4 py-3 text-right">Peak FWHM</th>
                  </>
                )}
                {showResidual && (
                  <th scope="col" className="px-4 py-3 text-right">
                    Axis-fit residual
                  </th>
                )}
                <th scope="col" className="px-4 py-3 text-right">Points</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {figure.curves.map((curve) => {
                const group = groupForLabel(labelIndex, curve.materialName);
                return (
                  <tr key={curve.id} className="hover:bg-slate-50/80">
                    <td className="whitespace-nowrap px-4 py-3 font-mono text-xs text-slate-500">
                      {curve.seriesId.split("-").at(-1)}
                    </td>
                    <td className="whitespace-nowrap px-4 py-3 font-medium text-slate-900">
                      {curve.label}
                    </td>
                    <td className="px-4 py-3">
                      <span className={`inline-flex rounded-full border px-2 py-0.5 text-xs font-semibold capitalize ${roleStyles[curve.role]}`}>
                        {curve.role}
                      </span>
                    </td>
                    <td className="whitespace-nowrap px-4 py-3 text-slate-600">
                      {curve.materialName || "—"}
                      {group && <MaterialLabelLink group={group} />}
                    </td>
                    <td className="whitespace-nowrap px-4 py-3 font-mono text-xs text-slate-600">
                      {curve.twoThetaMin !== null && curve.twoThetaMax !== null
                        ? `${curve.twoThetaMin.toFixed(2)}–${curve.twoThetaMax.toFixed(2)}°`
                        : "—"}
                    </td>
                    {showFirstPeak && (
                      <>
                        <td className="whitespace-nowrap px-4 py-3">
                          <span className="flex items-center gap-1.5">
                            {firstPeakCell(curve)}
                            {/*
                              The chip only qualifies a POSITION. When there is no
                              position the cell already reads "no Bragg peak", and
                              adding the chip printed the status twice.
                            */}
                            {curve.firstPeakTwoThetaDeg !== null
                              && curve.firstPeakStatus !== null
                              && curve.firstPeakStatus !== "ok" && (
                              <span
                                title={firstPeakStatusTitles[curve.firstPeakStatus]}
                                className={`inline-flex rounded-full border px-1.5 py-0.5 text-[10px] font-semibold ${firstPeakStatusStyles[curve.firstPeakStatus]}`}
                              >
                                {firstPeakStatusLabels[curve.firstPeakStatus]}
                              </span>
                            )}
                          </span>
                        </td>
                        <td className="whitespace-nowrap px-4 py-3 text-right font-mono text-xs text-slate-600">
                          {curve.firstPeakDAngstrom === null ? (
                            "—"
                          ) : (
                            <span
                              title={
                                curve.firstPeakWavelengthSource === "assumed_cu_ka"
                                  ? `Derived from ${curve.firstPeakTwoThetaDeg?.toFixed(3)}° 2θ under an ASSUMED wavelength of ${curve.firstPeakWavelengthAngstrom} Å. Not a measured d-spacing.`
                                  : `Derived from ${curve.firstPeakTwoThetaDeg?.toFixed(3)}° 2θ at a reported wavelength of ${curve.firstPeakWavelengthAngstrom} Å.`
                              }
                            >
                              {curve.firstPeakDAngstrom.toFixed(2)}
                              {curve.firstPeakWavelengthSource === "assumed_cu_ka" && (
                                <span aria-hidden="true"> †</span>
                              )}
                              {disputed && (
                                <span className="font-sans font-semibold text-rose-700">
                                  {" "}· axis disputed
                                </span>
                              )}
                            </span>
                          )}
                        </td>
                        <td className="whitespace-nowrap px-4 py-3 text-right font-mono text-xs text-slate-600">
                          {curve.firstPeakFwhmDeg === null
                            ? "—"
                            : `${curve.firstPeakFwhmDeg.toFixed(3)}°`}
                        </td>
                      </>
                    )}
                    {showResidual && (
                      <td className="whitespace-nowrap px-4 py-3 text-right font-mono text-xs text-slate-600">
                        {/*
                          Every number in this row that depends on the axis fit
                          carries the same inline qualifier, so a copy into a
                          spreadsheet cannot separate the value from the doubt.
                          Withholding the residual while printing the position
                          unqualified was the wrong asymmetry: the residual is
                          what the dispute barely touches, the position is what
                          it undermines.
                        */}
                        {curve.twoThetaUncertaintyDeg === null ? (
                          disputed ? (
                            <span className="font-sans font-semibold text-rose-700">
                              axis disputed
                            </span>
                          ) : (
                            "—"
                          )
                        ) : (
                          <>
                            ±{curve.twoThetaUncertaintyDeg.toFixed(3)}°
                            {disputed && (
                              <span className="font-sans font-semibold text-rose-700">
                                {" "}· axis disputed
                              </span>
                            )}
                          </>
                        )}
                      </td>
                    )}
                    <td className="px-4 py-3 text-right tabular-nums text-slate-600">
                      {curve.pointCount.toLocaleString()}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>

        <div className="mt-3 space-y-2 text-xs leading-relaxed text-slate-600">
          {showFirstPeak && assumedWavelength && (
            <p>
              <span aria-hidden="true" className="font-semibold">†</span>{" "}
              <span className="sr-only">Note on the d column: </span>
              {WAVELENGTH_ASSUMPTION_LONG}
            </p>
          )}
          {showFirstPeak && (
            <p>
              &ldquo;First peak&rdquo; is the lowest-angle peak <em>in the published
              figure</em>, not necessarily the material&rsquo;s lowest-angle reflection —
              across the collection 29% of curves have it within 1° of where the plotted
              window starts. Peak FWHM is reported as measured; it is clipped by the
              detector&rsquo;s own 0.05–3.0° gate at the extremes and is not a crystallite
              size.
            </p>
          )}
          {showResidual && !disputed && (
            <p>
              The axis-fit residual is the residual of the 2θ calibration that was chosen
              for this figure, plus one pixel. It is not a position error bar and does not
              account for the calibration being wrong.
            </p>
          )}
        </div>

        <CurveDescriptors figure={figure} />
      </div>
    </article>
  );
}

export function PaperDetailPage() {
  const { paperId } = useParams();
  const [paper, setPaper] = useState<PaperDetail | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState("");
  /**
   * The label index is a property of the whole collection, so one paper's own
   * payload cannot supply it. It is session-cached in lib/api: a reader who came
   * from the index page already has it for free, and one who deep-linked straight
   * here pays a single dedicated fetch (2,890 rows, ~21 kB gzip). It loads after
   * the paper and independently of it, so a failure costs the link chips and
   * nothing else.
   */
  const [labelIndex, setLabelIndex] = useState<MaterialLabelIndex>(
    EMPTY_MATERIAL_LABEL_INDEX,
  );

  useEffect(() => {
    let active = true;
    getMaterialLabelIndex()
      .then((index) => {
        if (active) setLabelIndex(index);
      })
      .catch(() => {
        // No chips rather than a broken page; absence of a chip already means
        // "not linkable" everywhere else in this component.
      });
    return () => {
      active = false;
    };
  }, []);

  useEffect(() => {
    let active = true;
    if (!paperId) {
      return () => {
        active = false;
      };
    }

    fetchPaper(paperId)
      .then((data) => {
        if (active) setPaper(data);
      })
      .catch(() => {
        if (active) setError("This paper could not be found.");
      })
      .finally(() => {
        if (active) setIsLoading(false);
      });

    return () => {
      active = false;
    };
  }, [paperId]);

  if (!paperId) {
    return (
      <section className="section-container py-16">
        <div className="rounded-xl border border-rose-200 bg-white p-8 text-rose-700">
          Invalid paper ID.
        </div>
      </section>
    );
  }

  if (isLoading) {
    return (
      <section className="section-container py-16">
        <div className="rounded-xl border border-slate-200 bg-white p-10">
          <div className="strategy-loader-wrap">
            <div className="strategy-loader-ring" />
            <div className="strategy-loader-ring strategy-loader-ring-delay" />
          </div>
          <p className="mt-6 text-center font-medium text-slate-400">Loading PXRD data...</p>
        </div>
      </section>
    );
  }

  if (error || !paper) {
    return (
      <section className="section-container py-16">
        <div className="rounded-xl border border-rose-200 bg-white p-8 text-rose-700">
          <p>{error || "Paper not found."}</p>
          <Link to="/" className="mt-4 inline-flex items-center gap-2 font-semibold text-slate-700 hover:text-slate-900">
            <ArrowLeft className="h-4 w-4" /> Back to paper index
          </Link>
        </div>
      </section>
    );
  }

  return (
    <section className="section-container py-10 md:py-16">
      <Link
        to="/"
        className="mb-6 inline-flex items-center gap-2 text-sm font-semibold text-slate-500 transition hover:text-slate-900"
      >
        <ArrowLeft className="h-4 w-4" />
        Paper index
      </Link>

      <header className="reveal-up mb-8 rounded-2xl border border-slate-200 bg-white p-6 shadow-sm md:p-9">
        <div className="mb-4 flex flex-wrap items-center gap-3">
          <span className="rounded-full bg-slate-900 px-3 py-1 font-mono text-xs font-semibold text-white">
            {paper.paperNumber}
          </span>
          {paper.doi && (
            <a
              href={`https://doi.org/${paper.doi}`}
              target="_blank"
              rel="noreferrer"
              className="font-mono text-xs text-blue-700 hover:underline"
            >
              DOI {paper.doi}
            </a>
          )}
          <PublicationStatusBadge status={paper.publicationStatus} showUnchecked />
        </div>
        <PublicationStatusBanner
          status={paper.publicationStatus}
          noticeDoi={paper.publicationStatusNoticeDoi}
        />
        <h1 className="max-w-5xl text-3xl font-semibold leading-tight tracking-tight text-slate-900 md:text-4xl">
          {paper.hasResolvedTitle ? paper.title : "Title unavailable"}
        </h1>
        {paper.authors && <p className="mt-4 text-slate-600">{paper.authors}</p>}
        {paper.materialNames.length > 0 && (
          <>
            <div className="mt-5 flex flex-wrap gap-2" aria-label="Materials in this paper">
              {paper.materialNames.map((material) => {
                const group = groupForLabel(labelIndex, material);
                return (
                  <span
                    key={material}
                    className="inline-flex items-center rounded-full border border-sky-100 bg-sky-50 px-2.5 py-1 text-xs font-semibold text-sky-800"
                  >
                    {material}
                    {group && <MaterialLabelLink group={group} />}
                  </span>
                );
              })}
            </div>
            {paper.materialNames.some((material) => groupForLabel(labelIndex, material)) && (
              <p className="mt-2 max-w-3xl text-xs leading-relaxed text-slate-500">
                A &ldquo;same label&rdquo; link groups curves whose <em>printed labels</em>
                {" "}normalise to the same string. Nobody has verified that they are the same
                material: labels are reused and redefined between research groups, so check
                each paper before treating linked traces as replicates. Generic labels
                (&ldquo;COF&rdquo;), paper-local serial numbers (&ldquo;COF-1&rdquo;) and
                one-letter variants (&ldquo;M-COF&rdquo;) are never linked, because in those
                cases agreement between two papers means nothing.
              </p>
            )}
          </>
        )}

        <div className="mt-7 flex flex-col gap-6 border-t border-slate-200 pt-6 lg:flex-row lg:items-end lg:justify-between">
          <div className="grid grid-cols-2 gap-x-8 gap-y-5 sm:grid-cols-4">
            <Meta label="Journal" value={paper.journal} />
            <Meta label="Year" value={paper.year} />
            <Meta label="PXRD figures" value={paper.figureCount} />
            <Meta label="Digitized curves" value={paper.curveCount} />
          </div>
          {paper.sourceUrl && (
            <a
              href={paper.sourceUrl}
              target="_blank"
              rel="noreferrer"
              className="inline-flex items-center gap-2 text-sm font-semibold text-blue-700 hover:text-blue-900"
            >
              View publication <ExternalLink className="h-4 w-4" />
            </a>
          )}
        </div>
      </header>

      <div className="mb-5 flex items-center gap-2 text-sm text-slate-500">
        <Activity className="h-4 w-4" />
        Source imagery and digitized data are shown together for visual verification.
      </div>

      <div className="space-y-8">
        {paper.figures.map((figure, index) => (
          <FigureCard
            key={figure.id}
            figure={figure}
            index={index}
            labelIndex={labelIndex}
          />
        ))}
      </div>
    </section>
  );
}
