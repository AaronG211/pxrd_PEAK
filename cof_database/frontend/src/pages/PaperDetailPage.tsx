import { useEffect, useState } from "react";
import {
  Activity,
  ArrowLeft,
  CheckCircle2,
  Download,
  ExternalLink,
  FileImage,
  ScanLine,
} from "lucide-react";
import { Link, useParams } from "react-router-dom";
import { fetchPaper } from "../lib/api";
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
      <p className="text-xs font-semibold uppercase tracking-wider text-slate-400">
        {label}
      </p>
      <p className="mt-1 text-sm font-medium text-slate-700">{value}</p>
    </div>
  );
}

function FigureCard({ figure, index }: { figure: PxrdFigure; index: number }) {
  const csvUrl = figure.curves.find((curve) => curve.dataUrl)?.dataUrl;
  const totalPoints = figure.curves.reduce((sum, curve) => sum + curve.pointCount, 0);

  return (
    <article
      className="reveal-up overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-sm"
      style={{ animationDelay: `${index * 100}ms` }}
    >
      <div className="flex flex-col gap-4 border-b border-slate-200 px-5 py-5 sm:flex-row sm:items-start sm:justify-between md:px-7">
        <div>
          <div className="mb-2 flex flex-wrap items-center gap-2">
            <h2 className="text-xl font-semibold text-slate-900">
              {figure.figureLabel || `PXRD figure ${index + 1}`}
            </h2>
            <span className={`badge ${
              figure.qualityStatus === "reviewed"
                ? "border-emerald-200 bg-emerald-50 text-emerald-700"
                : figure.qualityStatus === "flagged"
                  ? "border-rose-200 bg-rose-50 text-rose-700"
                  : ""
            }`}>
              {figure.qualityStatus === "reviewed" && <CheckCircle2 className="mr-1 h-3 w-3" />}
              {figure.qualityStatus}
            </span>
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
        {csvUrl && (
          <a
            href={csvUrl}
            download
            className="inline-flex shrink-0 items-center justify-center gap-2 rounded-lg bg-slate-900 px-4 py-2.5 text-sm font-semibold text-white transition hover:bg-slate-700"
          >
            <Download className="h-4 w-4" />
            Download CSV
          </a>
        )}
      </div>

      <div className="grid gap-px bg-slate-200 lg:grid-cols-2">
        <figure className="bg-slate-50 p-4 md:p-6">
          <figcaption className="mb-3 flex items-center gap-2 text-xs font-semibold uppercase tracking-wider text-slate-500">
            <FileImage className="h-4 w-4" />
            Source figure crop
          </figcaption>
          <div className="flex min-h-[320px] items-center justify-center overflow-hidden rounded-xl border border-slate-200 bg-white p-2 md:min-h-[420px]">
            <img
              src={figure.sourceCropUrl}
              alt={`Published source crop for ${figure.figureLabel || "PXRD figure"}`}
              className="max-h-[430px] w-full object-contain"
              loading="lazy"
            />
          </div>
        </figure>
        <figure className="bg-slate-50 p-4 md:p-6">
          <figcaption className="mb-3 flex items-center gap-2 text-xs font-semibold uppercase tracking-wider text-slate-500">
            <ScanLine className="h-4 w-4" />
            Digitized traces
          </figcaption>
          <div className="flex min-h-[320px] items-center justify-center overflow-hidden rounded-xl border border-slate-200 bg-white p-2 md:min-h-[420px]">
            <img
              src={figure.digitizedPlotUrl}
              alt={`Digitized traces for ${figure.figureLabel || "PXRD figure"}`}
              className="max-h-[430px] w-full object-contain"
              loading="lazy"
            />
          </div>
        </figure>
      </div>

      <div className="p-5 md:p-7">
        <div className="mb-4 flex flex-wrap items-center justify-between gap-2">
          <h3 className="font-semibold text-slate-900">Curve inventory</h3>
          <span className="text-xs text-slate-400">
            {figure.curves.length} curves · {totalPoints.toLocaleString()} points
          </span>
        </div>
        <div className="overflow-x-auto rounded-xl border border-slate-200">
          <table className="min-w-full text-left text-sm">
            <thead className="bg-slate-50 text-xs font-semibold uppercase tracking-wider text-slate-500">
              <tr>
                <th className="px-4 py-3">Series</th>
                <th className="px-4 py-3">Label</th>
                <th className="px-4 py-3">Type</th>
                <th className="px-4 py-3">Material</th>
                <th className="px-4 py-3">2θ range</th>
                <th className="px-4 py-3 text-right">Points</th>
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
          {paper.doi && <span className="font-mono text-xs text-slate-500">DOI {paper.doi}</span>}
        </div>
        <h1 className="max-w-5xl text-3xl font-semibold leading-tight tracking-tight text-slate-900 md:text-4xl">
          {paper.title}
        </h1>
        {paper.authors && <p className="mt-4 text-slate-600">{paper.authors}</p>}

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
