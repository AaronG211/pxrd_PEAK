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
import { Link, useSearchParams } from "react-router-dom";
import { Hero } from "../components/Hero";
import { PublicationStatusBadge } from "../components/PublicationStatusBadge";
import {
  fetchCurveSearchCapabilities,
  fetchPapers,
  getMaterialLabelIndex,
  isSupabaseConfigured,
  searchCurves,
  NO_CURVE_SEARCH_CAPABILITIES,} from "../lib/api";
import type { CurveSearchCapabilities, CurveSearchResult } from "../lib/api";
import { EMPTY_MATERIAL_LABEL_INDEX } from "../lib/materialGroups";
import type { MaterialLabelIndex } from "../lib/materialGroups";
import {
  DEFAULT_TOLERANCE_DEG,
  TOLERANCE_CHOICES,
  WAVELENGTH_ASSUMPTION_LONG,
  describeCurveSearch,
  isCurveSearchActive,
  parsePeakTarget,
} from "../lib/peakSearch";
import type {
  CurveSearchCriteria,
  HumpFilter,
  PeakUnit,
  RatioBand,
} from "../lib/peakSearch";
import { isPublisherNotice, publicationStatusLabel } from "../lib/publicationStatus";
import type { CurveRole, PaperSummary } from "../types";
import { useEffect } from "react";
import { useDocumentTitle, SITE_TITLE } from "../hooks/useDocumentTitle";

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

/**
 * `unique` deliberately requires the paper to have named materials at all. A
 * paper with no material labels is not "the only paper with this material" — it
 * is a paper we cannot say anything about, and it must not be swept into an
 * answer about uniqueness.
 */
type LabelScope = "all" | "shared" | "unique";
const LABEL_SCOPE_OPTIONS: Array<{ value: LabelScope; label: string }> = [
  { value: "all", label: "Any material label" },
  { value: "shared", label: "Label also used elsewhere" },
  { value: "unique", label: "Named, label not used elsewhere" },
];

const PEAK_UNIT_OPTIONS: Array<{ value: PeakUnit; label: string }> = [
  { value: "two-theta", label: "2θ (deg)" },
  { value: "d-spacing", label: "d (Å)" },
];

/** Shared empty result, so a constrained-but-matching-nothing state is a stable ref. */
const NO_CURVE_MATCHES: ReadonlyMap<string, number> = new Map<string, number>();

/**
 * A settled answer, tagged with the criteria object it answers.
 *
 * "In flight" is DERIVED from `outcome.criteria !== criteria` rather than stored,
 * so the effect never has to write state synchronously just to mark itself busy.
 * `criteria` is memoized on its primitive inputs, so identity is a sound test for
 * "is this the answer to the question currently being asked".
 */
type CurveSearchOutcome =
  | { criteria: CurveSearchCriteria; status: "ready"; result: CurveSearchResult }
  | { criteria: CurveSearchCriteria; status: "error"; message: string };

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
  // A leading =, +, - or @ makes a spreadsheet treat the cell as a formula. Only
  // free-text columns can carry one, but the guard is cheaper than the audit.
  const guarded = /^[=+\-@]/.test(text) ? `'${text}` : text;
  return `"${guarded.replaceAll('"', '""')}"`;
}

function exportManifest(
  papers: PaperSummary[],
  labelIndex: MaterialLabelIndex,
  matches: ReadonlyMap<string, number> | null,
  searchDescription: string,
) {
  const header = [
    "paper_number",
    "title",
    "doi",
    "journal",
    "year",
    "materials",
    "shared_material_labels",
    "figures",
    "curves",
  ];
  if (matches) {
    header.push("matching_curves", "curve_search");
  }
  const rows = [
    header,
    ...papers.map((paper) => {
      const row: Array<string | number | null> = [
        paper.paperNumber,
        paper.hasResolvedTitle ? paper.title : "",
        paper.doi,
        paper.journal,
        paper.year,
        paper.materialNames.join("; "),
        paper.sharedLabelKeys
          .map((key) => labelIndex.byKey.get(key)?.displayName ?? key)
          .join("; "),
        paper.figureCount,
        paper.curveCount,
      ];
      if (matches) {
        // The criteria travel INTO the file. A downloaded manifest outlives this
        // page, and a d-spacing search whose assumed wavelength stayed behind on
        // the screen is exactly how an assumption becomes a measurement.
        row.push(matches.get(paper.id) ?? 0, searchDescription);
      }
      return row;
    }),
  ];
  const blob = new Blob(
    // BOM plus CRLF: live material names carry Fe₃O₄ and UiO-66-NH₂, which Excel
    // on Windows mangles without them.
    ["﻿", rows.map((row) => row.map(csvCell).join(",")).join("\r\n")],
    { type: "text/csv;charset=utf-8" },
  );
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = "open-pxrd-paper-manifest.csv";
  anchor.click();
  // Revoking synchronously after click() cancels the download in Safari.
  window.setTimeout(() => URL.revokeObjectURL(url), 10_000);
}

function MaterialChips({ paper }: { paper: PaperSummary }) {
  if (paper.materialNames.length === 0) {
    return <span className="text-xs text-slate-500">Not labeled</span>;
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

const FILTER_LABEL_CLASS =
  "mb-1.5 block text-xs font-semibold uppercase tracking-wider text-slate-500";
const FILTER_FIELD_CLASS =
  "inventory-select w-full rounded-lg border border-slate-200 bg-white px-3 py-2.5 text-sm focus:ring-4 focus:ring-slate-100";

export function HomePage() {
  useDocumentTitle(SITE_TITLE);
  const [papers, setPapers] = useState<PaperSummary[]>([]);
  const [labelIndex, setLabelIndex] = useState<MaterialLabelIndex>(
    EMPTY_MATERIAL_LABEL_INDEX,
  );
  const [query, setQuery] = useState("");
  const [materialQuery, setMaterialQuery] = useState("");
  const [roleFilter, setRoleFilter] = useState<(typeof ROLE_OPTIONS)[number]["value"]>("all");
  const [materialScope, setMaterialScope] = useState<MaterialScope>("all");
  const [labelScope, setLabelScope] = useState<LabelScope>("all");
  const [minimumCurves, setMinimumCurves] = useState(0);
  const [peakValue, setPeakValue] = useState("");
  const [peakUnit, setPeakUnit] = useState<PeakUnit>("two-theta");
  const [peakTolerance, setPeakTolerance] = useState<number>(DEFAULT_TOLERANCE_DEG);
  const [includeLowConfidence, setIncludeLowConfidence] = useState(false);
  const [humpFilter, setHumpFilter] = useState<HumpFilter>("all");
  const [ratioBand, setRatioBand] = useState<RatioBand>("all");
  const [capabilities, setCapabilities] = useState<CurveSearchCapabilities | null>(null);
  const [curveSearch, setCurveSearch] = useState<CurveSearchOutcome | null>(null);
  const [sortBy, setSortBy] = useState<SortOption>("paper-number");
  const [pageSize, setPageSize] = useState(25);
  const [page, setPage] = useState(1);
  const [filtersOpen, setFiltersOpen] = useState(false);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState("");

  /**
   * The one filter with a URL. A cross-paper label link has to be shareable to be
   * worth anything, and unlike the free-text material box it addresses an exact
   * normalised group rather than a substring. It is kept two-way and is also
   * shown as a removable chip above the results, so the page never displays a
   * filter that only the address bar knows about.
   */
  const [searchParams, setSearchParams] = useSearchParams();
  const labelKey = searchParams.get("label") ?? "";

  const setLabelKey = (next: string) => {
    const params = new URLSearchParams(searchParams);
    if (next) params.set("label", next);
    else params.delete("label");
    setSearchParams(params, { replace: true });
  };

  useEffect(() => {
    let active = true;
    fetchPapers()
      .then((data) => {
        if (!active) return;
        setPapers(data);
        // Already primed by fetchPapers out of the facet rows it downloaded, so
        // this resolves without a further request.
        return getMaterialLabelIndex().then((index) => {
          if (active) setLabelIndex(index);
        });
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

  useEffect(() => {
    let active = true;
    fetchCurveSearchCapabilities()
      .then((next) => {
        if (active) setCapabilities(next);
      })
      .catch(() => {
        // Treated as "no curve columns published", which hides the controls.
        if (active) {
          setCapabilities(NO_CURVE_SEARCH_CAPABILITIES);
        }
      });
    return () => {
      active = false;
    };
  }, []);

  const canSearchPeaks = capabilities?.firstPeak ?? false;
  const canSearchDescriptors = capabilities?.descriptors ?? false;
  const curveSearchAvailable = canSearchPeaks || canSearchDescriptors;
  // intensity_ratio_100_001 is NULL unless a hump was detected, and SQL
  // comparison drops NULL, so any band combined with these hump filters is an
  // unconditional zero-result state rather than a query.
  const ratioRequiresHump =
    humpFilter === "no_hump_detected"
    || humpFilter === "window_not_covered"
    || humpFilter === "no_descriptor";

  const peakParse = useMemo(
    () => (canSearchPeaks ? parsePeakTarget(peakValue, peakUnit) : { target: null, error: null }),
    [canSearchPeaks, peakUnit, peakValue],
  );

  const criteria = useMemo<CurveSearchCriteria>(
    () => ({
      targetTwoThetaDeg: peakParse.target?.twoThetaDeg ?? null,
      toleranceDeg: peakTolerance,
      includeLowConfidence,
      humpFilter: canSearchDescriptors ? humpFilter : "all",
      // Neutralised, not just disabled: leaving a stale band in the criteria
      // after the hump filter makes it unsatisfiable would silently return zero
      // rows through a control the reader can no longer see or change.
      ratioBand: canSearchDescriptors && !ratioRequiresHump ? ratioBand : "all",
    }),
    [
      canSearchDescriptors,
      humpFilter,
      includeLowConfidence,
      peakParse.target,
      peakTolerance,
      ratioBand,
      ratioRequiresHump,
    ],
  );
  const curveSearchActive = isCurveSearchActive(criteria);
  const searchDescription = useMemo(
    () => describeCurveSearch(criteria, peakParse.target),
    [criteria, peakParse.target],
  );

  useEffect(() => {
    if (!curveSearchActive || !capabilities) return;
    let active = true;
    const controller = new AbortController();
    // Debounced: the peak box filters on every keystroke and each run is a real
    // round trip.
    const timer = window.setTimeout(() => {
      searchCurves(criteria, capabilities, controller.signal)
        .then((result) => {
          if (active) setCurveSearch({ criteria, status: "ready", result });
        })
        .catch(() => {
          if (!active || controller.signal.aborted) return;
          setCurveSearch({
            criteria,
            status: "error",
            message:
              "The curve search could not be run. Results are not filtered by peak position or crystallinity right now — clear those filters to browse the rest of the collection.",
          });
        });
    }, 250);
    return () => {
      active = false;
      window.clearTimeout(timer);
      controller.abort();
    };
  }, [capabilities, criteria, curveSearchActive]);

  /** The settled answer to the question currently on screen, if there is one. */
  const settledSearch = curveSearchActive && curveSearch?.criteria === criteria
    ? curveSearch
    : null;
  /** The last answer of any age, so the table does not flash between keystrokes. */
  const staleResult = curveSearchActive && curveSearch?.status === "ready"
    ? curveSearch.result
    : null;
  const isSearching = curveSearchActive && settledSearch === null;
  const searchingFirstTime = isSearching && staleResult === null;

  /**
   * Null means "this filter is not constraining anything". An empty map means
   * "constrained, and nothing matched" — including the error case, where dropping
   * the constraint and showing every paper would misreport a failed search as a
   * wide result.
   */
  const curveMatches = useMemo<ReadonlyMap<string, number> | null>(() => {
    if (!curveSearchActive) return null;
    if (settledSearch?.status === "ready") return settledSearch.result.papers;
    if (settledSearch?.status === "error") return NO_CURVE_MATCHES;
    return staleResult?.papers ?? NO_CURVE_MATCHES;
  }, [curveSearchActive, settledSearch, staleResult]);

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
      const labelScopeMatch = labelScope === "all"
        || (labelScope === "shared" && paper.sharedLabelKeys.length > 0)
        || (labelScope === "unique"
          && paper.sharedLabelKeys.length === 0
          && paper.materialNames.length > 0);
      const labelKeyMatch = !labelKey || paper.sharedLabelKeys.includes(labelKey);
      const curveMatch = curveMatches === null || curveMatches.has(paper.id);
      return generalMatch
        && materialMatch
        && roleMatch
        && scopeMatch
        && labelScopeMatch
        && labelKeyMatch
        && curveMatch
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
  }, [curveMatches, labelKey, labelScope, materialQuery, materialScope, minimumCurves, papers, query, roleFilter, sortBy]);

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

  /** Same rule as the role dropdown: never offer an option that matches nothing. */
  const visibleLabelScopeOptions = useMemo(() => {
    const shared = papers.some((paper) => paper.sharedLabelKeys.length > 0);
    const unique = papers.some(
      (paper) => paper.sharedLabelKeys.length === 0 && paper.materialNames.length > 0,
    );
    return LABEL_SCOPE_OPTIONS.filter((option) => option.value === "all"
      || (option.value === "shared" ? shared : unique));
  }, [papers]);

  const humpOptions = useMemo(() => {
    const counts = capabilities?.humpCounts ?? {};
    const entries: Array<{ value: HumpFilter; label: string; count: number | null }> = [
      { value: "all", label: "Any crystallinity state", count: null },
      {
        value: "hump_detected",
        label: "Stacking hump detected",
        count: counts.hump_detected ?? 0,
      },
      {
        value: "no_hump_detected",
        label: "No hump — window was plotted",
        count: counts.no_hump_detected ?? 0,
      },
      {
        value: "window_not_covered",
        label: "Not determinable — window not plotted",
        count: counts.window_not_covered ?? 0,
      },
      {
        value: "no_descriptor",
        label: "No descriptor computed",
        count: counts.no_descriptor ?? 0,
      },
    ];
    return entries.filter((entry) => entry.count === null || entry.count > 0);
  }, [capabilities]);

  const totalPages = Math.max(1, Math.ceil(filteredPapers.length / pageSize));
  const currentPage = Math.min(page, totalPages);
  const pageStart = (currentPage - 1) * pageSize;
  const visiblePapers = filteredPapers.slice(pageStart, pageStart + pageSize);

  const activeFilterCount = Number(Boolean(materialQuery.trim()))
    + Number(roleFilter !== "all")
    + Number(materialScope !== "all")
    + Number(labelScope !== "all")
    + Number(Boolean(labelKey))
    + Number(minimumCurves > 0)
    // The low-confidence checkbox is a modifier that WIDENS the peak search, so
    // it is not counted as a filter of its own.
    + Number(criteria.targetTwoThetaDeg !== null)
    + Number(criteria.humpFilter !== "all")
    + Number(criteria.ratioBand !== "all");

  const resetPage = () => setPage(1);
  const clearFilters = () => {
    setQuery("");
    setMaterialQuery("");
    setRoleFilter("all");
    setMaterialScope("all");
    setLabelScope("all");
    setMinimumCurves(0);
    setPeakValue("");
    setPeakUnit("two-theta");
    setPeakTolerance(DEFAULT_TOLERANCE_DEG);
    setIncludeLowConfidence(false);
    setHumpFilter("all");
    setRatioBand("all");
    setLabelKey("");
    setSortBy("paper-number");
    setPage(1);
  };

  const activeLabelGroup = labelKey ? labelIndex.byKey.get(labelKey) : undefined;

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
                className="w-full rounded-xl border border-slate-200 bg-slate-50 py-3 pl-10 pr-10 text-sm text-slate-900 transition placeholder:text-slate-500 focus:border-slate-400 focus:bg-white focus:ring-4 focus:ring-slate-100"
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
            <>
              <div className="mt-4 grid gap-4 border-t border-slate-200 pt-4 sm:grid-cols-2 xl:grid-cols-6">
                <label className="block xl:col-span-2">
                  <span className={FILTER_LABEL_CLASS}>Material / series name</span>
                  <input
                    value={materialQuery}
                    onChange={(event) => {
                      setMaterialQuery(event.target.value);
                      resetPage();
                    }}
                    placeholder="e.g. COF-5 or TpPa-1"
                    className={FILTER_FIELD_CLASS}
                  />
                </label>
                <label className="block">
                  <span className={FILTER_LABEL_CLASS}>Curve content</span>
                  <select
                    value={roleFilter}
                    onChange={(event) => {
                      setRoleFilter(event.target.value as (typeof ROLE_OPTIONS)[number]["value"]);
                      resetPage();
                    }}
                    className={FILTER_FIELD_CLASS}
                  >
                    {visibleRoleOptions.map((option) => (
                      <option key={option.value} value={option.value}>{option.label}</option>
                    ))}
                  </select>
                </label>
                <label className="block">
                  <span className={FILTER_LABEL_CLASS}>Material coverage</span>
                  <select
                    value={materialScope}
                    onChange={(event) => {
                      setMaterialScope(event.target.value as MaterialScope);
                      resetPage();
                    }}
                    className={FILTER_FIELD_CLASS}
                  >
                    <option value="all">All records</option>
                    <option value="named">Named material</option>
                    <option value="multiple">Multiple materials</option>
                  </select>
                </label>
                <label className="block">
                  <span className={FILTER_LABEL_CLASS}>Cross-paper labels</span>
                  <select
                    value={labelScope}
                    onChange={(event) => {
                      setLabelScope(event.target.value as LabelScope);
                      resetPage();
                    }}
                    className={FILTER_FIELD_CLASS}
                  >
                    {visibleLabelScopeOptions.map((option) => (
                      <option key={option.value} value={option.value}>{option.label}</option>
                    ))}
                  </select>
                </label>
                <label className="block">
                  <span className={FILTER_LABEL_CLASS}>Minimum curves</span>
                  <select
                    value={minimumCurves}
                    onChange={(event) => {
                      setMinimumCurves(Number(event.target.value));
                      resetPage();
                    }}
                    className={FILTER_FIELD_CLASS}
                  >
                    <option value={0}>Any number</option>
                    <option value={2}>2+</option>
                    <option value={5}>5+</option>
                    <option value={10}>10+</option>
                  </select>
                </label>
                {labelScope !== "all" && (
                  <p className="text-xs leading-relaxed text-slate-500 sm:col-span-2 xl:col-span-6">
                    {labelIndex.sharedGroupCount.toLocaleString()} material labels in this
                    collection appear in more than one paper, across{" "}
                    {labelIndex.sharedPaperCount.toLocaleString()} papers. Grouping is by
                    the <em>printed label</em>, normalised for case, formatting and
                    subscripts — not by verified material identity. Generic words, papers&rsquo;
                    own serial numbering and one-letter variants are excluded, because
                    agreement on those carries no information.
                  </p>
                )}
                {activeFilterCount > 0 && (
                  <div className="flex items-end sm:col-span-2 xl:col-span-6">
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

              {capabilities && !curveSearchAvailable && isSupabaseConfigured && (
                <p className="mt-4 rounded-xl border border-slate-200 bg-slate-50 p-3 text-xs leading-relaxed text-slate-600">
                  Peak-position and crystallinity search are not offered for this
                  collection: the per-curve first-peak and descriptor columns are not
                  published in it. Rather than show controls that would silently match
                  nothing, they are hidden until that data ships.
                </p>
              )}

              {curveSearchAvailable && (
                <fieldset className="mt-4 rounded-xl border border-slate-200 bg-slate-50/70 p-4">
                  <legend className="px-1 text-xs font-semibold uppercase tracking-wider text-slate-500">
                    Curve-level search
                  </legend>
                  <p className="mb-3 max-w-4xl text-xs leading-relaxed text-slate-600">
                    These match individual digitized traces on the server, then list the
                    papers holding them: a paper appears when at least one of its curves
                    matches, and the result count below tells you how many curves that was.
                  </p>
                  <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
                    {canSearchPeaks && (
                      <>
                        <div className="sm:col-span-2">
                          <span className={FILTER_LABEL_CLASS}>First peak position</span>
                          <div className="flex gap-2">
                            <label className="flex-1">
                              <span className="sr-only">
                                First peak position value
                              </span>
                              <input
                                inputMode="decimal"
                                value={peakValue}
                                onChange={(event) => {
                                  setPeakValue(event.target.value);
                                  resetPage();
                                }}
                                placeholder={peakUnit === "two-theta" ? "e.g. 3.4" : "e.g. 26.1"}
                                aria-invalid={peakParse.error !== null}
                                className={FILTER_FIELD_CLASS}
                              />
                            </label>
                            <label className="w-32 shrink-0">
                              <span className="sr-only">Units for the first peak search</span>
                              <select
                                value={peakUnit}
                                onChange={(event) => {
                                  setPeakUnit(event.target.value as PeakUnit);
                                  resetPage();
                                }}
                                className={FILTER_FIELD_CLASS}
                              >
                                {PEAK_UNIT_OPTIONS.map((option) => (
                                  <option key={option.value} value={option.value}>
                                    {option.label}
                                  </option>
                                ))}
                              </select>
                            </label>
                          </div>
                          {peakParse.error && (
                            <p className="mt-1.5 text-xs font-medium text-rose-700" role="alert">
                              {peakParse.error}
                            </p>
                          )}
                          {!peakParse.error && peakUnit === "d-spacing" && peakParse.target && (
                            <p className="mt-1.5 text-xs text-slate-600">
                              d = {peakParse.target.enteredValue} Å is{" "}
                              <span className="font-mono">
                                {peakParse.target.twoThetaDeg.toFixed(3)}° 2θ
                              </span>{" "}
                              under the assumed wavelength. The search runs on the angle.
                            </p>
                          )}
                        </div>
                        <label className="block">
                          <span className={FILTER_LABEL_CLASS}>Tolerance</span>
                          <select
                            value={peakTolerance}
                            onChange={(event) => {
                              setPeakTolerance(Number(event.target.value));
                              resetPage();
                            }}
                            className={FILTER_FIELD_CLASS}
                          >
                            {TOLERANCE_CHOICES.map((choice) => (
                              <option key={choice} value={choice}>±{choice}° 2θ</option>
                            ))}
                          </select>
                        </label>
                      </>
                    )}
                    {canSearchDescriptors && humpOptions.length > 1 && (
                      <label className="block">
                        <span className={FILTER_LABEL_CLASS}>Stacking hump</span>
                        <select
                          value={humpFilter}
                          onChange={(event) => {
                            setHumpFilter(event.target.value as HumpFilter);
                            resetPage();
                          }}
                          className={FILTER_FIELD_CLASS}
                        >
                          {humpOptions.map((option) => (
                            <option key={option.value} value={option.value}>
                              {option.count === null
                                ? option.label
                                : `${option.label} (${option.count.toLocaleString()})`}
                            </option>
                          ))}
                        </select>
                      </label>
                    )}
                    {canSearchDescriptors && (capabilities?.ratioCount ?? 0) > 0 && (
                      <label className="block">
                        <span className={FILTER_LABEL_CLASS}>(100)/(001) ratio</span>
                        <select
                          value={ratioBand}
                          disabled={ratioRequiresHump}
                          onChange={(event) => {
                            setRatioBand(event.target.value as RatioBand);
                            resetPage();
                          }}
                          className={`${FILTER_FIELD_CLASS} disabled:cursor-not-allowed disabled:bg-slate-100 disabled:text-slate-400`}
                        >
                          <option value="all">Any ratio</option>
                          <option value="hump-dominant">≤ 1 — hump taller than the peak</option>
                          <option value="balanced">1 to 10</option>
                          <option value="peak-dominant">&gt; 10 — peak dominates</option>
                        </select>
                        {/*
                          The ratio is computed only where a hump was detected, so
                          every band silently drops the hump-free curves - which
                          include the BEST-ordered samples. Saying so beside the
                          control, and disabling it where it could only ever
                          return nothing, keeps "no ratio" from reading as
                          "no order".
                        */}
                        <span className="mt-1 block text-xs leading-relaxed text-slate-500">
                          {ratioRequiresHump
                            ? "Not available: the ratio exists only where a stacking hump was detected."
                            : "Computed only where a stacking hump was detected. Curves with no hump — including well-ordered samples — have no ratio and are returned by no band."}
                        </span>
                      </label>
                    )}
                  </div>

                  {canSearchPeaks && (
                    <label className="mt-3 flex items-start gap-2 text-xs text-slate-700">
                      <input
                        type="checkbox"
                        checked={includeLowConfidence}
                        onChange={(event) => {
                          setIncludeLowConfidence(event.target.checked);
                          resetPage();
                        }}
                        className="mt-0.5 h-4 w-4 rounded border-slate-300 accent-blue-700"
                      />
                      <span>
                        Include low-confidence first peaks
                        {capabilities?.statusCounts.low_confidence
                          ? ` (+${capabilities.statusCounts.low_confidence.toLocaleString()} curves)`
                          : ""}
                        .{" "}
                        <span className="text-slate-500">
                          Off by default, the search covers only the{" "}
                          {capabilities?.statusCounts.ok?.toLocaleString() ?? "gated"} curves
                          whose first peak cleared the signal-to-noise, persistence, width
                          and edge checks. Low confidence means a real peak that is weak, or
                          that has evidence of a lower-angle peak outside the plotted window.
                          Curves whose first peak was cut off by the plotted range, or which
                          have no Bragg peak at all, carry no position and can never be
                          returned by this search.
                        </span>
                      </span>
                    </label>
                  )}

                  {canSearchPeaks && (
                    <p className="mt-3 max-w-4xl text-xs leading-relaxed text-slate-600">
                      <strong className="font-semibold text-slate-800">
                        On d values:
                      </strong>{" "}
                      {WAVELENGTH_ASSUMPTION_LONG}
                    </p>
                  )}
                  {canSearchPeaks && (
                    <p className="mt-2 max-w-4xl text-xs leading-relaxed text-slate-600">
                      The tolerance floor is ±0.1° because the per-curve axis-fit
                      residual has a median of 0.046° and a 95th percentile of 0.102°; a
                      finer window would be a precision the axis cannot support. Each curve
                      is matched against the wider of your tolerance and its own residual.
                      That residual measures how well the chosen calibration fit its own
                      tick marks — it does not bound positional error, and says nothing
                      about the fit being wrong. Where the two calibration methods
                      disagreed the count above says so separately.
                    </p>
                  )}
                </fieldset>
              )}
            </>
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
            {settledSearch?.status === "error" && (
              <div role="alert" className="border-b border-amber-200 bg-amber-50 px-5 py-3 text-sm text-amber-900">
                {settledSearch.message}
              </div>
            )}
            {labelKey && (
              <div className="flex flex-wrap items-center gap-2 border-b border-slate-200 bg-sky-50/60 px-5 py-3 text-sm text-slate-700">
                <span>
                  Papers printing a label that normalises to{" "}
                  <span className="font-semibold">
                    {activeLabelGroup?.displayName ?? labelKey}
                  </span>
                  {activeLabelGroup && activeLabelGroup.variants.length > 1 && (
                    <span className="text-slate-500">
                      {" "}(as {activeLabelGroup.variants.join(", ")})
                    </span>
                  )}
                  . This is label agreement, not verified material identity.
                </span>
                <button
                  type="button"
                  onClick={() => {
                    setLabelKey("");
                    resetPage();
                  }}
                  className="inline-flex items-center gap-1 rounded-lg border border-slate-300 bg-white px-2 py-1 text-xs font-semibold text-slate-700 hover:bg-slate-50"
                >
                  <X className="h-3.5 w-3.5" /> Remove
                </button>
              </div>
            )}
            <div className="flex flex-col gap-3 border-b border-slate-200 px-5 py-4 sm:flex-row sm:items-center sm:justify-between">
              <p className="text-sm font-medium text-slate-700" role="status" aria-live="polite">
                {searchingFirstTime ? (
                  "Searching curve records..."
                ) : (
                  <>
                    {filteredPapers.length.toLocaleString()} of {papers.length.toLocaleString()} papers
                    {settledSearch?.status === "ready" && (
                      <span className="font-normal text-slate-500">
                        {" "}· {settledSearch.result.curveCount.toLocaleString()} matching curve
                        {settledSearch.result.curveCount === 1 ? "" : "s"}
                        {/*
                          A match on an arbitrated axis is a match against a
                          position the pipeline itself disputes. Disclosed here,
                          beside the count, rather than left for the reader to
                          discover one paper at a time.
                        */}
                        {settledSearch.result.disputedAxisCurveCount > 0 && (
                          <span className="text-rose-700">
                            {" "}({settledSearch.result.disputedAxisCurveCount.toLocaleString()} on a
                            disputed or unverified 2θ axis)
                          </span>
                        )}
                      </span>
                    )}
                    {filteredPapers.length > 0 && (
                      <span className="font-normal text-slate-500">
                        {" "}· showing {pageStart + 1}–{Math.min(pageStart + pageSize, filteredPapers.length)}
                      </span>
                    )}
                  </>
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
                    className="rounded-lg border border-slate-200 bg-white px-2.5 py-2 text-xs font-semibold text-slate-700 focus:ring-4 focus:ring-slate-100"
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
                  onClick={() =>
                    exportManifest(filteredPapers, labelIndex, curveMatches, searchDescription)
                  }
                  className="inline-flex items-center gap-2 rounded-lg border border-slate-200 px-3 py-2 text-xs font-semibold text-slate-700 transition hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-50"
                >
                  <Download className="h-3.5 w-3.5" />
                  Export results
                </button>
              </div>
            </div>

            {searchingFirstTime ? (
              <div className="px-6 py-16 text-center" role="status" aria-live="polite">
                <div className="strategy-loader-wrap mx-auto" aria-hidden="true">
                  <div className="strategy-loader-ring" />
                  <div className="strategy-loader-ring strategy-loader-ring-delay" />
                </div>
                <p className="mt-6 font-medium text-slate-500">
                  Matching curves against {searchDescription || "the current criteria"}...
                </p>
              </div>
            ) : filteredPapers.length === 0 ? (
              <div className="px-6 py-16 text-center">
                <FlaskConical className="mx-auto h-8 w-8 text-slate-300" />
                <p className="mt-4 font-medium text-slate-700">No matching PXRD records</p>
                <p className="mt-1 text-sm text-slate-500">
                  {curveSearchActive
                    ? "No published curve matches those criteria. Widen the tolerance, or allow low-confidence first peaks, before concluding the collection has nothing."
                    : "Try a broader material name or clear the active filters."}
                </p>
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
                        {curveMatches !== null && (
                          <th scope="col" className="px-5 py-3 text-center">Matched</th>
                        )}
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
                          {curveMatches !== null && (
                            <td className="px-5 py-5 text-center font-semibold tabular-nums text-slate-700">
                              {(curveMatches.get(paper.id) ?? 0).toLocaleString()}
                            </td>
                          )}
                          <td className="px-5 py-5 text-right">
                            <Link
                              to={`/paper/${encodeURIComponent(paper.id)}`}
                              aria-label={`Open ${paper.hasResolvedTitle ? paper.title : paper.paperNumber}${isPublisherNotice(paper.publicationStatus) ? ` — ${publicationStatusLabel(paper.publicationStatus)}` : ""}`}
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
                          {curveMatches !== null && (
                            <span className="font-semibold text-slate-700">
                              {(curveMatches.get(paper.id) ?? 0).toLocaleString()} matched
                            </span>
                          )}
                        </div>
                        <Link
                          to={`/paper/${encodeURIComponent(paper.id)}`}
                          aria-label={`Open ${paper.hasResolvedTitle ? paper.title : paper.paperNumber}${isPublisherNotice(paper.publicationStatus) ? ` — ${publicationStatusLabel(paper.publicationStatus)}` : ""}`}
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
                      className="rounded-lg border border-slate-200 bg-white px-2.5 py-2 font-semibold text-slate-700"
                    >
                      {PAGE_SIZES.map((size) => <option key={size} value={size}>{size}</option>)}
                    </select>
                  </label>
                  <nav className="flex items-center gap-2" aria-label="Paper index pages">
                    <button
                      type="button"
                      disabled={currentPage === 1}
                      // Stepped from the CLAMPED page, not the raw one. `page` can
                      // otherwise sit above `totalPages` after a filter narrows the
                      // result — every control calls resetPage, but the curve search
                      // narrows asynchronously with no control to hang that on — and
                      // Previous would then decrement a number nobody can see while
                      // the visible page stayed put.
                      onClick={() => setPage(Math.max(1, currentPage - 1))}
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
                      onClick={() => setPage(Math.min(totalPages, currentPage + 1))}
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
