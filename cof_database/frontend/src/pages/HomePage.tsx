import { useEffect, useMemo, useState } from "react";
import {
  Activity,
  ArrowUpRight,
  Database,
  FileText,
  Search,
} from "lucide-react";
import { Link } from "react-router-dom";
import { Hero } from "../components/Hero";
import { fetchPapers, isSupabaseConfigured } from "../lib/api";
import type { PaperSummary } from "../types";

function Stat({ label, value }: { label: string; value: number }) {
  return (
    <div className="rounded-xl border border-slate-200 bg-white px-5 py-4 shadow-sm">
      <p className="text-2xl font-semibold tracking-tight text-slate-900">{value}</p>
      <p className="mt-1 text-xs font-semibold uppercase tracking-wider text-slate-500">
        {label}
      </p>
    </div>
  );
}

export function HomePage() {
  const [papers, setPapers] = useState<PaperSummary[]>([]);
  const [query, setQuery] = useState("");
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    let active = true;
    fetchPapers()
      .then((data) => {
        if (active) setPapers(data);
      })
      .catch(() => {
        if (active) setError("The paper index could not be loaded.");
      })
      .finally(() => {
        if (active) setIsLoading(false);
      });
    return () => {
      active = false;
    };
  }, []);

  const filteredPapers = useMemo(() => {
    const needle = query.trim().toLowerCase();
    if (!needle) return papers;
    return papers.filter((paper) =>
      [paper.paperNumber, paper.title, paper.doi, paper.authors, paper.journal]
        .filter(Boolean)
        .some((value) => value?.toLowerCase().includes(needle)),
    );
  }, [papers, query]);

  const figureCount = papers.reduce((sum, paper) => sum + paper.figureCount, 0);
  const curveCount = papers.reduce((sum, paper) => sum + paper.curveCount, 0);

  return (
    <>
      <Hero />

      <section className="section-container py-16 md:py-24">
        <div className="mb-8 flex flex-col gap-6 lg:flex-row lg:items-end lg:justify-between">
          <div>
            <div className="mb-3 flex items-center gap-3">
              <h2 className="text-3xl font-semibold tracking-tight text-slate-900">
                Paper index
              </h2>
              {!isSupabaseConfigured && (
                <span className="badge border-sky-200 bg-sky-50 text-sky-700">
                  Demo snapshot
                </span>
              )}
            </div>
            <p className="max-w-2xl text-slate-500">
              Search by database number, title, DOI, author, or journal.
            </p>
          </div>
          <div className="grid grid-cols-3 gap-3">
            <Stat label="Papers" value={papers.length} />
            <Stat label="Figures" value={figureCount} />
            <Stat label="Curves" value={curveCount} />
          </div>
        </div>

        <div className="inventory-filter-card mb-6 rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
          <label className="relative block">
            <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-400" />
            <input
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="Search PXRD-00001, a paper title, or DOI..."
              className="w-full rounded-lg border border-slate-200 bg-slate-50 py-3 pl-10 pr-4 text-sm text-slate-900 outline-none transition focus:border-slate-400 focus:bg-white focus:ring-4 focus:ring-slate-100"
            />
          </label>
        </div>

        {isLoading ? (
          <div className="flex items-center justify-center py-20">
            <div className="flex flex-col items-center">
              <div className="strategy-loader-wrap">
                <div className="strategy-loader-ring" />
                <div className="strategy-loader-ring strategy-loader-ring-delay" />
              </div>
              <p className="mt-6 font-medium text-slate-400">Loading papers...</p>
            </div>
          </div>
        ) : error ? (
          <div className="rounded-xl border border-rose-200 bg-rose-50 p-6 text-rose-700">
            {error}
          </div>
        ) : (
          <div className="overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-sm">
            <div className="flex items-center justify-between border-b border-slate-200 px-5 py-4">
              <p className="text-sm font-medium text-slate-600">
                {filteredPapers.length} of {papers.length} papers
              </p>
              <p className="hidden text-xs text-slate-400 sm:block">
                Select a paper to inspect its PXRD figures
              </p>
            </div>
            {filteredPapers.length === 0 ? (
              <div className="py-16 text-center text-slate-500">
                No papers match “{query}”.
              </div>
            ) : (
              <div className="overflow-x-auto">
                <table className="min-w-full text-left text-sm">
                  <thead className="bg-slate-50 text-xs font-semibold uppercase tracking-wider text-slate-500">
                    <tr>
                      <th className="px-5 py-3">Paper ID</th>
                      <th className="px-5 py-3">Publication</th>
                      <th className="px-5 py-3 text-center">PXRD</th>
                      <th className="px-5 py-3 text-center">Curves</th>
                      <th className="px-5 py-3"><span className="sr-only">Open</span></th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-slate-100">
                    {filteredPapers.map((paper, index) => (
                      <tr
                        key={paper.id}
                        className="inventory-row group transition-colors hover:bg-slate-50/80"
                        style={{ "--i": index } as React.CSSProperties}
                      >
                        <td className="whitespace-nowrap px-5 py-5 align-top font-mono text-xs font-semibold text-slate-600">
                          {paper.paperNumber}
                        </td>
                        <td className="min-w-[420px] px-5 py-5">
                          <Link
                            to={`/paper/${encodeURIComponent(paper.id)}`}
                            className="inventory-title-link font-semibold leading-snug text-slate-900 hover:text-blue-700"
                          >
                            {paper.title}
                          </Link>
                          <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-slate-500">
                            {paper.journal && <span>{paper.journal}</span>}
                            {paper.year && <span>{paper.year}</span>}
                            {paper.doi && <span className="font-mono">{paper.doi}</span>}
                          </div>
                        </td>
                        <td className="px-5 py-5 text-center">
                          <span className="inline-flex items-center gap-1.5 font-medium text-slate-700">
                            <FileText className="h-4 w-4 text-slate-400" />
                            {paper.figureCount}
                          </span>
                        </td>
                        <td className="px-5 py-5 text-center">
                          <span className="inline-flex items-center gap-1.5 font-medium text-slate-700">
                            <Activity className="h-4 w-4 text-slate-400" />
                            {paper.curveCount}
                          </span>
                        </td>
                        <td className="px-5 py-5 text-right">
                          <Link
                            to={`/paper/${encodeURIComponent(paper.id)}`}
                            aria-label={`Open ${paper.title}`}
                            className="inline-flex h-9 w-9 items-center justify-center rounded-full border border-slate-200 bg-white text-slate-500 transition group-hover:border-slate-300 group-hover:text-slate-900"
                          >
                            <ArrowUpRight className="inventory-link-arrow h-4 w-4" />
                          </Link>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        )}
      </section>

      <section id="about" className="border-t border-slate-200 bg-white py-16">
        <div className="section-container grid gap-8 md:grid-cols-[1fr_1.4fr] md:items-start">
          <div className="flex items-center gap-3">
            <div className="rounded-xl bg-slate-900 p-3 text-white">
              <Database className="h-5 w-5" />
            </div>
            <h2 className="text-2xl font-semibold tracking-tight text-slate-900">
              Built for verification
            </h2>
          </div>
          <p className="leading-relaxed text-slate-600">
            This database publishes digitized PXRD as a traceable research asset,
            not as an opaque replacement for the source. Original figure crops,
            curve labels, provenance, and downloadable data stay together so
            researchers can inspect quality before reusing a trace.
          </p>
        </div>
      </section>
    </>
  );
}
