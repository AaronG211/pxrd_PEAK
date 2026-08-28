import { useEffect, useMemo, useRef, useState } from "react";
import {
  Activity,
  ArrowLeft,
  Bot,
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
import { fetchPaper } from "../lib/api";
import { describeCurveDataError, fetchFigureCurves, isAbortError } from "../lib/curveData";
import type { CurveRole, PaperDetail, PxrdFigure } from "../types";

const roleStyles: Record<CurveRole, string> = {
  experimental: "border-emerald-200 bg-emerald-50 text-emerald-700",
  simulated: "border-sky-200 bg-sky-50 text-sky-700",
  refined: "border-violet-200 bg-violet-50 text-violet-700",
  difference: "border-amber-200 bg-amber-50 text-amber-700",
  reference: "border-slate-200 bg-slate-100 text-slate-700",
  unclassified: "border-slate-200 bg-slate-50 text-slate-500",
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
 * Honest replacement for the old "reviewed" badge.
 *
 * `pxrd_figures.quality_status` was written as `'reviewed'` for every row by the
 * importer, so a green check claimed a human review that never happened. The
 * provenance badge below is deliberately identical on every figure, because that
 * is the same truth on every figure; the varying badges carry the automated
 * checks, and all of them stay silent until the verification columns exist.
 */
function FigureQualityBadges({ figure }: { figure: PxrdFigure }) {
  const agreement = figure.axisAgreementDeg;
  const detected = figure.seriesDetected;
  const digitized = figure.seriesDigitized;
  const omitted = figure.seriesOmittedComputed ?? 0;

  return (
    <>
      <span className="badge" title="No human has reviewed this extraction.">
        <Bot className="mr-1 h-3 w-3" aria-hidden="true" />
        Automated extraction · not human-reviewed
      </span>

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
      {omitted > 0 && (
        <span className="badge">
          Experimental traces only · {omitted} computed curve{omitted === 1 ? "" : "s"} not
          included
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

function FigureVerification({ figure }: { figure: PxrdFigure }) {
  const rmse = figure.axisRmseDeg;
  const ticks = figure.axisTickCount;
  const agreement = figure.axisAgreementDeg;
  const digitized = figure.seriesDigitized;
  const detected = figure.seriesDetected;
  const hasNumbers = rmse !== null || ticks !== null || agreement !== null || digitized !== null;

  return (
    <div className="rounded-xl border border-slate-200 bg-slate-50/70 p-4">
      {hasNumbers ? (
        <div className="grid grid-cols-2 gap-x-6 gap-y-4 sm:grid-cols-4">
          <VerificationNumber
            label="Axis fit RMSE"
            value={rmse === null ? null : `${rmse.toFixed(3)}°`}
            note="residual of the 2θ calibration"
          />
          <VerificationNumber
            label="Axis anchors"
            value={ticks === null ? null : `${ticks} ticks`}
            note="tick marks the axis was fit to"
          />
          <VerificationNumber
            label="Method agreement"
            value={agreement === null ? null : `${agreement.toFixed(3)}°`}
            note="tick fit vs. label OCR, across the plot"
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
          Per-figure calibration numbers are not published for this record yet. The 2θ
          uncertainty the digitizer reported for each trace is shown in the plot readout above.
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
          flagged. Compare against the original crop before reuse.
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
        <CurvePlot series={series} figureLabel={figure.figureLabel || "this PXRD figure"} />
      ) : (
        <div className="strategy-skeleton h-[260px] w-full" aria-hidden="true" />
      )}
    </div>
  );
}

function FigureCard({ figure, index }: { figure: PxrdFigure; index: number }) {
  const csvUrl = figure.curves.find((curve) => curve.dataUrl)?.dataUrl;
  const totalPoints = figure.curves.reduce((sum, curve) => sum + curve.pointCount, 0);
  const [linkCopied, setLinkCopied] = useState(false);

  const copyFigureLink = async () => {
    const link = `${window.location.origin}${window.location.pathname}#${figure.id}`;
    await navigator.clipboard.writeText(link);
    setLinkCopied(true);
    window.setTimeout(() => setLinkCopied(false), 1600);
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
            <Link2 className="h-4 w-4" /> {linkCopied ? "Copied" : "Copy link"}
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
                <th scope="col" className="px-4 py-3 text-right">Points</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {figure.curves.map((curve) => (
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
                  </td>
                  <td className="whitespace-nowrap px-4 py-3 font-mono text-xs text-slate-600">
                    {curve.twoThetaMin !== null && curve.twoThetaMax !== null
                      ? `${curve.twoThetaMin.toFixed(2)}–${curve.twoThetaMax.toFixed(2)}°`
                      : "—"}
                  </td>
                  <td className="px-4 py-3 text-right tabular-nums text-slate-600">
                    {curve.pointCount.toLocaleString()}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </article>
  );
}

export function PaperDetailPage() {
  const { paperId } = useParams();
  const [paper, setPaper] = useState<PaperDetail | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState("");

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
          <PublicationStatusBadge status={paper.publicationStatus} />
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
          <div className="mt-5 flex flex-wrap gap-2" aria-label="Materials in this paper">
            {paper.materialNames.map((material) => (
              <span key={material} className="rounded-full border border-sky-100 bg-sky-50 px-2.5 py-1 text-xs font-semibold text-sky-800">
                {material}
              </span>
            ))}
          </div>
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
          <FigureCard key={figure.id} figure={figure} index={index} />
        ))}
      </div>
    </section>
  );
}
