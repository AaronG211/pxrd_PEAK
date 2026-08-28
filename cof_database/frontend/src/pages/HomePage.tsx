import { useMemo, useState } from "react";
import {
  Activity,
  ArrowUpRight,
  ChevronLeft,
  ChevronRight,
  Database,
  Download,
  FileText,
  FlaskConical,
  Search,
  SlidersHorizontal,
  X,
} from "lucide-react";
import { Link } from "react-router-dom";
import { Hero } from "../components/Hero";
import { PublicationStatusBadge } from "../components/PublicationStatusBadge";
import { fetchPapers, isSupabaseConfigured } from "../lib/api";
import { publicationStatusLabel } from "../lib/publicationStatus";
import type { CurveRole, PaperSummary } from "../types";
import { useEffect } from "react";

type SortOption = "paper-number" | "title" | "curves" | "figures";
type MaterialScope = "all" | "named" | "multiple";

const PAGE_SIZES = [25, 50, 100];
const ROLE_OPTIONS: Array<{ value: "all" | CurveRole | "exp-model"; label: string }> = [
  { value: "all", label: "Any curve type" },
  { value: "experimental", label: "Experimental" },
  { value: "simulated", label: "Simulated" },
  { value: "refined", label: "Refined" },
  { value: "reference", label: "Reference" },
  { value: "unclassified", label: "Unclassified" },
  { value: "exp-model", label: "Experimental + model" },
];

function Stat({ label, value }: { label: string; value: number }) {
  return (
    <div className="rounded-xl border border-slate-200 bg-white px-5 py-4 shadow-sm">
      <p className="text-2xl font-semibold tracking-tight text-slate-900">
        {value.toLocaleString()}
      </p>
      <p className="mt-1 text-xs font-semibold uppercase tracking-wider text-slate-500">
        {label}
      </p>
    </div>
  );
}

function csvCell(value: string | number | null): string {
  const text = value === null ? "" : String(value);
  return `"${text.replaceAll('"', '""')}"`;
}

function exportManifest(papers: PaperSummary[]) {
  const rows = [
    ["paper_number", "title", "doi", "journal", "year", "materials", "figures", "curves"],
    ...papers.map((paper) => [
      paper.paperNumber,
      paper.hasResolvedTitle ? paper.title : "",
      paper.doi,
      paper.journal,
      paper.year,
      paper.materialNames.join("; "),
      paper.figureCount,
      paper.curveCount,
    ]),
  ];
  const blob = new Blob(
    [rows.map((row) => row.map(csvCell).join(",")).join("\n")],
    { type: "text/csv;charset=utf-8" },
  );
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = "open-pxrd-paper-manifest.csv";
  anchor.click();
  URL.revokeObjectURL(url);
}

function MaterialChips({ paper }: { paper: PaperSummary }) {
  if (paper.materialNames.length === 0) {
    return <span className="text-xs text-slate-400">Not labeled</span>;
  }
  const visible = paper.materialNames.slice(0, 2);
  return (
    <div className="flex max-w-sm flex-wrap gap-1.5">
      {visible.map((material) => (
        <span
          key={material}
          title={material}
          className="max-w-[180px] truncate rounded-full border border-sky-100 bg-sky-50 px-2 py-1 text-xs font-medium text-sky-800"
        >
          {material}
        </span>
      ))}
      {paper.materialNames.length > visible.length && (
        <span className="rounded-full border border-slate-200 bg-slate-50 px-2 py-1 text-xs font-medium text-slate-600">
          +{paper.materialNames.length - visible.length}
        </span>
      )}
    </div>
  );
}

function PaperIdentity({ paper }: { paper: PaperSummary }) {
  return (
    <div>
      <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1">
        <Link
          to={`/paper/${encodeURIComponent(paper.id)}`}
          className="inventory-title-link font-semibold leading-snug text-slate-900 hover:text-blue-700"
        >
          {paper.hasResolvedTitle ? paper.title : "Title unavailable"}
        </Link>
        <PublicationStatusBadge status={paper.publicationStatus} />
      </div>
      <div className="mt-2 flex flex-wrap items-center gap-x-2 gap-y-1.5 text-xs text-slate-500">
        <span className="rounded bg-slate-100 px-1.5 py-0.5 font-mono font-semibold text-slate-600">
          {paper.paperNumber}
        </span>
        {paper.journal && <span>{paper.journal}</span>}
        {paper.year && <span>{paper.year}</span>}
        {paper.doi && (
          <a
            href={`https://doi.org/${paper.doi}`}
            target="_blank"
            rel="noreferrer"
            className="font-mono text-blue-700 hover:underline"
            onClick={(event) => event.stopPropagation()}
          >
            {paper.doi}
          </a>
        )}
      </div>
    </div>
  );
}

export function HomePage() {
  const [papers, setPapers] = useState<PaperSummary[]>([]);
  const [query, setQuery] = useState("");
  const [materialQuery, setMaterialQuery] = useState("");
  const [roleFilter, setRoleFilter] = useState<(typeof ROLE_OPTIONS)[number]["value"]>("all");
  const [materialScope, setMaterialScope] = useState<MaterialScope>("all");
  const [minimumCurves, setMinimumCurves] = useState(0);
  const [sortBy, setSortBy] = useState<SortOption>("paper-number");
  const [pageSize, setPageSize] = useState(25);
  const [page, setPage] = useState(1);
  const [filtersOpen, setFiltersOpen] = useState(false);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    let active = true;
    fetchPapers()
      .then((data) => {
        if (active) setPapers(data);
      })
      .catch(() => {
        if (active) setError("The paper index could not be loaded. Please try again.");
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
    const materialNeedle = materialQuery.trim().toLowerCase();
    const matched = papers.filter((paper) => {
      const generalMatch = !needle || [
        paper.paperNumber,
        paper.title,
        paper.doi,
        paper.authors,
        paper.journal,
        publicationStatusLabel(paper.publicationStatus),
        ...paper.materialNames,
        ...paper.sampleStates,
      ]
        .filter(Boolean)
        .some((value) => value?.toLowerCase().includes(needle));
      const materialMatch = !materialNeedle || paper.materialNames.some((material) =>
        material.toLowerCase().includes(materialNeedle),
      );
      const roleMatch = roleFilter === "all"
        || (roleFilter === "exp-model"
          ? paper.curveRoles.includes("experimental")
            && paper.curveRoles.some((role) => role === "simulated" || role === "refined")
          : paper.curveRoles.includes(roleFilter));
      const scopeMatch = materialScope === "all"
        || (materialScope === "named" && paper.materialNames.length > 0)
        || (materialScope === "multiple" && paper.materialNames.length > 1);
      return generalMatch
        && materialMatch
        && roleMatch
        && scopeMatch
        && paper.curveCount >= minimumCurves;
    });

    return [...matched].sort((a, b) => {
      if (sortBy === "title") {
        if (a.hasResolvedTitle !== b.hasResolvedTitle) return a.hasResolvedTitle ? -1 : 1;
        return a.title.localeCompare(b.title);
      }
      if (sortBy === "curves") return b.curveCount - a.curveCount || a.paperNumber.localeCompare(b.paperNumber);
      if (sortBy === "figures") return b.figureCount - a.figureCount || a.paperNumber.localeCompare(b.paperNumber);
      return a.paperNumber.localeCompare(b.paperNumber);
    });
  }, [materialQuery, materialScope, minimumCurves, papers, query, roleFilter, sortBy]);

  const figureCount = papers.reduce((sum, paper) => sum + paper.figureCount, 0);
  const curveCount = papers.reduce((sum, paper) => sum + paper.curveCount, 0);
  const visibleRoleOptions = useMemo(() => {
    const roles = new Set(papers.flatMap((paper) => paper.curveRoles));
    const hasExpModel = papers.some((paper) =>
      paper.curveRoles.includes("experimental")
      && paper.curveRoles.some((role) => role === "simulated" || role === "refined"),
    );
    return ROLE_OPTIONS.filter((option) => option.value === "all"
      || (option.value === "exp-model" ? hasExpModel : roles.has(option.value)));
  }, [papers]);
  const totalPages = Math.max(1, Math.ceil(filteredPapers.length / pageSize));
  const currentPage = Math.min(page, totalPages);
  const pageStart = (currentPage - 1) * pageSize;
  const visiblePapers = filteredPapers.slice(pageStart, pageStart + pageSize);
  const activeFilterCount = Number(Boolean(materialQuery.trim()))
    + Number(roleFilter !== "all")
    + Number(materialScope !== "all")
    + Number(minimumCurves > 0);

  const resetPage = () => setPage(1);
  const clearFilters = () => {
    setQuery("");
    setMaterialQuery("");
    setRoleFilter("all");
    setMaterialScope("all");
    setMinimumCurves(0);
    setSortBy("paper-number");
    setPage(1);
  };

  return (
    <>
      <Hero />

      <section id="browse" className="section-container scroll-mt-20 py-12 md:py-16">
        <div className="mb-8 flex flex-col gap-6 lg:flex-row lg:items-end lg:justify-between">
          <div>
            <div className="mb-3 flex flex-wrap items-center gap-3">
              <h2 className="text-3xl font-semibold tracking-tight text-slate-900">
                Browse the collection
              </h2>
              {!isSupabaseConfigured && (
                <span className="badge border-sky-200 bg-sky-50 text-sky-700">
                  Demo snapshot
                </span>
              )}
            </div>
            <p className="max-w-2xl text-slate-600">
              Search publications, DOI records, material names, and experimental states.
            </p>
          </div>
          <div className="grid grid-cols-3 gap-3">
            <Stat label="Papers" value={papers.length} />
            <Stat label="Figures" value={figureCount} />
            <Stat label="Curves" value={curveCount} />
          </div>
        </div>

        <div className="inventory-filter-card mb-6 rounded-2xl border border-slate-200 bg-white p-4 shadow-sm md:p-5">
          <div className="flex flex-col gap-3 lg:flex-row">
            <label className="relative block flex-1">
              <span className="sr-only">Search papers and materials</span>
              <Search className="pointer-events-none absolute left-3.5 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-500" />
              <input
                type="search"
                value={query}
                onChange={(event) => {
                  setQuery(event.target.value);
                  resetPage();
                }}
                placeholder="Search title, DOI, paper number, author, journal, or material..."
                className="w-full rounded-xl border border-slate-200 bg-slate-50 py-3 pl-10 pr-10 text-sm text-slate-900 outline-none transition placeholder:text-slate-500 focus:border-slate-400 focus:bg-white focus:ring-4 focus:ring-slate-100"
              />
              {query && (
                <button
                  type="button"
                  onClick={() => {
                    setQuery("");
                    resetPage();
                  }}
                  aria-label="Clear search"
                  className="absolute right-2.5 top-1/2 -translate-y-1/2 rounded-md p-1.5 text-slate-500 hover:bg-slate-200 hover:text-slate-900"
                >
                  <X className="h-4 w-4" />
                </button>
              )}
            </label>
            <button
              type="button"
              onClick={() => setFiltersOpen((open) => !open)}
              aria-expanded={filtersOpen}
              className="inline-flex items-center justify-center gap-2 rounded-xl border border-slate-200 bg-white px-4 py-3 text-sm font-semibold text-slate-700 transition hover:border-slate-300 hover:bg-slate-50"
            >
              <SlidersHorizontal className="h-4 w-4" />
              Filters
              {activeFilterCount > 0 && (
                <span className="rounded-full bg-slate-900 px-2 py-0.5 text-xs text-white">
                  {activeFilterCount}
                </span>
              )}
            </button>
          </div>

          {filtersOpen && (
            <div className="mt-4 grid gap-4 border-t border-slate-200 pt-4 sm:grid-cols-2 xl:grid-cols-5">
              <label className="block xl:col-span-2">
                <span className="mb-1.5 block text-xs font-semibold uppercase tracking-wider text-slate-500">
                  Material / series name
                </span>
                <input
                  value={materialQuery}
                  onChange={(event) => {
                    setMaterialQuery(event.target.value);
                    resetPage();
                  }}
                  placeholder="e.g. COF-5 or TpPa-1"
                  className="inventory-select w-full rounded-lg border border-slate-200 bg-white px-3 py-2.5 text-sm outline-none focus:ring-4 focus:ring-slate-100"
                />
              </label>
              <label className="block">
                <span className="mb-1.5 block text-xs font-semibold uppercase tracking-wider text-slate-500">
                  Curve content
                </span>
                <select
                  value={roleFilter}
                  onChange={(event) => {
                    setRoleFilter(event.target.value as (typeof ROLE_OPTIONS)[number]["value"]);
                    resetPage();
                  }}
                  className="inventory-select w-full rounded-lg border border-slate-200 bg-white px-3 py-2.5 text-sm outline-none focus:ring-4 focus:ring-slate-100"
                >
                  {visibleRoleOptions.map((option) => (
                    <option key={option.value} value={option.value}>{option.label}</option>
                  ))}
                </select>
              </label>
              <label className="block">
                <span className="mb-1.5 block text-xs font-semibold uppercase tracking-wider text-slate-500">
                  Material coverage
                </span>
                <select
                  value={materialScope}
                  onChange={(event) => {
                    setMaterialScope(event.target.value as MaterialScope);
                    resetPage();
                  }}
                  className="inventory-select w-full rounded-lg border border-slate-200 bg-white px-3 py-2.5 text-sm outline-none focus:ring-4 focus:ring-slate-100"
                >
                  <option value="all">All records</option>
                  <option value="named">Named material</option>
                  <option value="multiple">Multiple materials</option>
                </select>
              </label>
              <label className="block">
                <span className="mb-1.5 block text-xs font-semibold uppercase tracking-wider text-slate-500">
                  Minimum curves
                </span>
                <select
                  value={minimumCurves}
                  onChange={(event) => {
                    setMinimumCurves(Number(event.target.value));
                    resetPage();
                  }}
                  className="inventory-select w-full rounded-lg border border-slate-200 bg-white px-3 py-2.5 text-sm outline-none focus:ring-4 focus:ring-slate-100"
                >
                  <option value={0}>Any number</option>
                  <option value={2}>2+</option>
                  <option value={5}>5+</option>
                  <option value={10}>10+</option>
                </select>
              </label>
              {activeFilterCount > 0 && (
                <div className="flex items-end sm:col-span-2 xl:col-span-5">
                  <button
                    type="button"
                    onClick={clearFilters}
                    className="inline-flex items-center gap-2 text-sm font-semibold text-slate-600 hover:text-slate-900"
                  >
                    <X className="h-4 w-4" /> Clear all filters
                  </button>
                </div>
              )}
            </div>
          )}
        </div>

        {isLoading ? (
          <div className="rounded-2xl border border-slate-200 bg-white py-20" role="status" aria-live="polite">
            <div className="flex flex-col items-center">
              <div className="strategy-loader-wrap" aria-hidden="true">
                <div className="strategy-loader-ring" />
                <div className="strategy-loader-ring strategy-loader-ring-delay" />
              </div>
              <p className="mt-6 font-medium text-slate-500">Loading papers and material index...</p>
            </div>
          </div>
        ) : error ? (
          <div role="alert" className="rounded-xl border border-rose-200 bg-rose-50 p-6 text-rose-700">
            {error}
          </div>
        ) : (
          <div className="overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-sm">
            <div className="flex flex-col gap-3 border-b border-slate-200 px-5 py-4 sm:flex-row sm:items-center sm:justify-between">
              <p className="text-sm font-medium text-slate-700" role="status" aria-live="polite">
                {filteredPapers.length.toLocaleString()} of {papers.length.toLocaleString()} papers
                {filteredPapers.length > 0 && (
                  <span className="font-normal text-slate-500">
                    {" "}· showing {pageStart + 1}–{Math.min(pageStart + pageSize, filteredPapers.length)}
                  </span>
                )}
              </p>
              <div className="flex flex-wrap items-center gap-2">
                <label className="flex items-center gap-2 text-xs font-medium text-slate-500">
                  Sort
                  <select
                    value={sortBy}
                    onChange={(event) => {
                      setSortBy(event.target.value as SortOption);
                      resetPage();
                    }}
                    className="rounded-lg border border-slate-200 bg-white px-2.5 py-2 text-xs font-semibold text-slate-700 outline-none focus:ring-4 focus:ring-slate-100"
                  >
                    <option value="paper-number">Paper number</option>
                    <option value="title">Title A–Z</option>
                    <option value="curves">Most curves</option>
                    <option value="figures">Most figures</option>
                  </select>
                </label>
                <button
                  type="button"
                  disabled={filteredPapers.length === 0}
                  onClick={() => exportManifest(filteredPapers)}
                  className="inline-flex items-center gap-2 rounded-lg border border-slate-200 px-3 py-2 text-xs font-semibold text-slate-700 transition hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-50"
                >
                  <Download className="h-3.5 w-3.5" />
                  Export results
                </button>
              </div>
            </div>

            {filteredPapers.length === 0 ? (
              <div className="px-6 py-16 text-center">
                <FlaskConical className="mx-auto h-8 w-8 text-slate-300" />
                <p className="mt-4 font-medium text-slate-700">No matching PXRD records</p>
                <p className="mt-1 text-sm text-slate-500">Try a broader material name or clear the active filters.</p>
                <button type="button" onClick={clearFilters} className="mt-5 text-sm font-semibold text-blue-700 hover:underline">
                  Clear search and filters
                </button>
              </div>
            ) : (
              <>
                <div className="hidden overflow-x-auto md:block">
                  <table className="min-w-full text-left text-sm">
                    <thead className="bg-slate-50 text-xs font-semibold uppercase tracking-wider text-slate-500">
                      <tr>
                        <th scope="col" className="px-5 py-3">Paper</th>
                        <th scope="col" className="px-5 py-3">Materials</th>
                        <th scope="col" className="px-5 py-3 text-center">PXRD</th>
                        <th scope="col" className="px-5 py-3 text-center">Curves</th>
                        <th scope="col" className="px-5 py-3"><span className="sr-only">Open</span></th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-slate-100">
                      {visiblePapers.map((paper, index) => (
                        <tr
                          key={paper.id}
                          className="inventory-row group transition-colors hover:bg-slate-50/80"
                          style={{ "--i": Math.min(index, 10) } as React.CSSProperties}
                        >
                          <td className="min-w-[420px] px-5 py-5 align-top"><PaperIdentity paper={paper} /></td>
                          <td className="min-w-[220px] px-5 py-5 align-top"><MaterialChips paper={paper} /></td>
                          <td className="px-5 py-5 text-center">
                            <span className="inline-flex items-center gap-1.5 font-medium text-slate-700">
                              <FileText className="h-4 w-4 text-slate-500" />
                              {paper.figureCount.toLocaleString()}
                            </span>
                          </td>
                          <td className="px-5 py-5 text-center">
                            <span className="inline-flex items-center gap-1.5 font-medium text-slate-700">
                              <Activity className="h-4 w-4 text-slate-500" />
                              {paper.curveCount.toLocaleString()}
                            </span>
                          </td>
                          <td className="px-5 py-5 text-right">
                            <Link
                              to={`/paper/${encodeURIComponent(paper.id)}`}
                              aria-label={`Open ${paper.hasResolvedTitle ? paper.title : paper.paperNumber}${paper.publicationStatus === "active" ? "" : ` — ${publicationStatusLabel(paper.publicationStatus)}`}`}
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

                <div className="divide-y divide-slate-100 md:hidden">
                  {visiblePapers.map((paper) => (
                    <article key={paper.id} className="p-5">
                      <PaperIdentity paper={paper} />
                      <div className="mt-4"><MaterialChips paper={paper} /></div>
                      <div className="mt-4 flex items-center justify-between">
                        <div className="flex items-center gap-4 text-xs font-medium text-slate-600">
                          <span className="inline-flex items-center gap-1"><FileText className="h-4 w-4" />{paper.figureCount} figures</span>
                          <span className="inline-flex items-center gap-1"><Activity className="h-4 w-4" />{paper.curveCount} curves</span>
                        </div>
                        <Link
                          to={`/paper/${encodeURIComponent(paper.id)}`}
                          aria-label={`Open ${paper.hasResolvedTitle ? paper.title : paper.paperNumber}${paper.publicationStatus === "active" ? "" : ` — ${publicationStatusLabel(paper.publicationStatus)}`}`}
                          className="inline-flex h-9 w-9 items-center justify-center rounded-full border border-slate-200 text-slate-600"
                        >
                          <ArrowUpRight className="h-4 w-4" />
                        </Link>
                      </div>
                    </article>
                  ))}
                </div>

                <div className="flex flex-col gap-4 border-t border-slate-200 bg-slate-50/70 px-5 py-4 sm:flex-row sm:items-center sm:justify-between">
                  <label className="flex items-center gap-2 text-xs font-medium text-slate-600">
                    Rows per page
                    <select
                      value={pageSize}
                      onChange={(event) => {
                        setPageSize(Number(event.target.value));
                        resetPage();
                      }}
                      className="rounded-lg border border-slate-200 bg-white px-2.5 py-2 font-semibold text-slate-700 outline-none"
                    >
                      {PAGE_SIZES.map((size) => <option key={size} value={size}>{size}</option>)}
                    </select>
                  </label>
                  <nav className="flex items-center gap-2" aria-label="Paper index pages">
                    <button
                      type="button"
                      disabled={currentPage === 1}
                      onClick={() => setPage((value) => Math.max(1, value - 1))}
                      className="inline-flex h-9 items-center gap-1 rounded-lg border border-slate-200 bg-white px-3 text-xs font-semibold text-slate-700 disabled:cursor-not-allowed disabled:opacity-40"
                    >
                      <ChevronLeft className="h-4 w-4" /> Previous
                    </button>
                    <span className="min-w-24 text-center text-xs font-medium text-slate-600">
                      Page {currentPage} of {totalPages}
                    </span>
                    <button
                      type="button"
                      disabled={currentPage === totalPages}
                      onClick={() => setPage((value) => Math.min(totalPages, value + 1))}
                      className="inline-flex h-9 items-center gap-1 rounded-lg border border-slate-200 bg-white px-3 text-xs font-semibold text-slate-700 disabled:cursor-not-allowed disabled:opacity-40"
                    >
                      Next <ChevronRight className="h-4 w-4" />
                    </button>
                  </nav>
                </div>
              </>
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
