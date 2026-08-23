import {
  AlertCircle,
  AlertTriangle,
  Calculator,
  CheckCircle2,
  Clock3,
  FileUp,
  KeyRound,
  LoaderCircle,
  LockKeyhole,
  RefreshCw,
  ShieldCheck,
  Trash2,
  UserRoundCheck,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useAuth } from "../auth/context";
import {
  createDigitizationJob,
  deleteDigitizationJob,
  describeWorkspaceError,
  fetchDigitizationJobs,
} from "../lib/digitization";
import type { DigitizationJob, DigitizationJobStatus } from "../lib/digitization";

const MAX_FILE_BYTES = 20 * 1024 * 1024;
const MAX_FILES = 10;

const STATUS_LABELS: Record<DigitizationJobStatus, { label: string; className: string }> = {
  draft: { label: "Draft", className: "border-slate-200 bg-slate-50 text-slate-700" },
  saved: { label: "Saved · not queued", className: "border-amber-200 bg-amber-50 text-amber-800" },
  awaiting_quote: { label: "Awaiting quote", className: "border-cyan-200 bg-cyan-50 text-cyan-800" },
  awaiting_approval: { label: "Awaiting approval", className: "border-orange-200 bg-orange-50 text-orange-800" },
  queued: { label: "Queued", className: "border-sky-200 bg-sky-50 text-sky-800" },
  running: { label: "Running", className: "border-violet-200 bg-violet-50 text-violet-800" },
  review: { label: "Ready for review", className: "border-indigo-200 bg-indigo-50 text-indigo-800" },
  completed: { label: "Completed", className: "border-emerald-200 bg-emerald-50 text-emerald-800" },
  failed: { label: "Failed", className: "border-rose-200 bg-rose-50 text-rose-800" },
  cancelled: { label: "Cancelled", className: "border-slate-200 bg-slate-50 text-slate-500" },
};

function numericValue(value: string, fallback = 0): number {
  const parsed = Number(value);
  return Number.isFinite(parsed) && parsed >= 0 ? parsed : fallback;
}

function money(value: number): string {
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
    minimumFractionDigits: value < 0.1 ? 4 : 2,
    maximumFractionDigits: value < 0.1 ? 4 : 2,
  }).format(value);
}

function fileSize(value: number): string {
  if (value < 1024 * 1024) return `${Math.max(1, Math.round(value / 1024))} KB`;
  return `${(value / 1048576).toFixed(1)} MB`;
}

function dateTime(value: string): string {
  return new Intl.DateTimeFormat("en-US", {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(new Date(value));
}

export function DigitizationPlannerPage() {
  const inputRef = useRef<HTMLInputElement>(null);
  const { user, loading: authLoading, isConfigured, signInWithGoogle } = useAuth();
  const [files, setFiles] = useState<File[]>([]);
  const [fileError, setFileError] = useState("");
  const [pages, setPages] = useState("10");
  const [figures, setFigures] = useState("3");
  const [inputPrice, setInputPrice] = useState("2.00");
  const [outputPrice, setOutputPrice] = useState("12.00");
  const [pageTokens, setPageTokens] = useState("1400");
  const [figureInputTokens, setFigureInputTokens] = useState("6000");
  const [figureOutputTokens, setFigureOutputTokens] = useState("1200");
  const [budgetCap, setBudgetCap] = useState("1.00");
  const [jobs, setJobs] = useState<DigitizationJob[]>([]);
  const [jobsLoading, setJobsLoading] = useState(false);
  const [jobsError, setJobsError] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [uploadProgress, setUploadProgress] = useState<{ completed: number; total: number } | null>(null);
  const [submissionMessage, setSubmissionMessage] = useState("");
  const [confirmDeleteId, setConfirmDeleteId] = useState<string | null>(null);
  const [deletingJobId, setDeletingJobId] = useState<string | null>(null);

  const estimate = useMemo(() => {
    const pageCount = numericValue(pages);
    const figureCount = numericValue(figures);
    const totalInputTokens = pageCount * numericValue(pageTokens)
      + figureCount * numericValue(figureInputTokens);
    const totalOutputTokens = figureCount * numericValue(figureOutputTokens);
    const expected = totalInputTokens * numericValue(inputPrice) / 1_000_000
      + totalOutputTokens * numericValue(outputPrice) / 1_000_000;
    const upper = expected * 1.25;
    return { totalInputTokens, totalOutputTokens, expected, upper };
  }, [figureInputTokens, figureOutputTokens, figures, inputPrice, outputPrice, pageTokens, pages]);

  const cap = numericValue(budgetCap);
  const pageCount = numericValue(pages);
  const figureCount = numericValue(figures);
  const withinCap = cap > 0 && estimate.upper <= cap;
  const totalFileSize = files.reduce((sum, file) => sum + file.size, 0);
  const canSubmit = Boolean(
    user
    && files.length > 0
    && pageCount > 0
    && withinCap
    && !submitting,
  );

  const loadJobs = useCallback(async () => {
    if (!user || !isConfigured) {
      setJobs([]);
      setJobsError("");
      return;
    }
    setJobsLoading(true);
    setJobsError("");
    try {
      setJobs(await fetchDigitizationJobs());
    } catch (error) {
      setJobsError(describeWorkspaceError(error));
    } finally {
      setJobsLoading(false);
    }
  }, [isConfigured, user]);

  useEffect(() => {
    void loadJobs();
  }, [loadJobs]);

  const addFiles = (incoming: FileList | null) => {
    if (!incoming) return;
    const selected = [...incoming];
    const invalid = selected.find((file) => {
      const looksLikePdf = file.name.toLowerCase().endsWith(".pdf")
        && (!file.type || file.type === "application/pdf");
      return !looksLikePdf || file.size === 0 || file.size > MAX_FILE_BYTES;
    });
    if (invalid) {
      setFileError("Use non-empty PDF files no larger than 20 MB each.");
      return;
    }

    const signatures = new Set(files.map((file) => `${file.name}:${file.size}:${file.lastModified}`));
    const uniqueSelected = selected.filter(
      (file) => !signatures.has(`${file.name}:${file.size}:${file.lastModified}`),
    );
    if (files.length + uniqueSelected.length > MAX_FILES) {
      setFileError(`A private job accepts up to ${MAX_FILES} PDF files.`);
      return;
    }
    setFiles((current) => [...current, ...uniqueSelected]);
    setFileError("");
    setSubmissionMessage("");
  };

  const submitJob = async () => {
    if (!canSubmit) return;
    setSubmitting(true);
    setUploadProgress({ completed: 0, total: files.length });
    setFileError("");
    setSubmissionMessage("");
    try {
      const job = await createDigitizationJob({
        files,
        pageCount,
        figureCount,
        budgetCapUsd: cap,
        clientEstimateUsd: estimate.expected,
        clientEstimateUpperUsd: estimate.upper,
        onProgress: (completed, total) => setUploadProgress({ completed, total }),
      });
      setJobs((current) => [job, ...current.filter((item) => item.id !== job.id)]);
      setFiles([]);
      if (inputRef.current) inputRef.current.value = "";
      setSubmissionMessage(
        `Private job ${job.id.slice(0, 8)} was saved. It is not queued and cannot run without a future quote and your approval.`,
      );
    } catch (error) {
      setFileError(describeWorkspaceError(error));
    } finally {
      setSubmitting(false);
      setUploadProgress(null);
    }
  };

  const removeJob = async (jobId: string) => {
    setDeletingJobId(jobId);
    setJobsError("");
    try {
      await deleteDigitizationJob(jobId);
      setJobs((current) => current.filter((job) => job.id !== jobId));
      setConfirmDeleteId(null);
    } catch (error) {
      setJobsError(describeWorkspaceError(error));
    } finally {
      setDeletingJobId(null);
    }
  };

  return (
    <section className="section-container py-10 md:py-16">
      <header className="mb-8 max-w-4xl">
        <span className="inline-flex items-center gap-2 rounded-full border border-amber-200 bg-amber-50 px-3 py-1 text-xs font-semibold text-amber-800">
          <ShieldCheck className="h-3.5 w-3.5" /> Private workspace · worker paused
        </span>
        <h1 className="mt-4 text-3xl font-semibold tracking-tight text-slate-900 md:text-5xl">
          Plan and save a PXRD digitization job
        </h1>
        <p className="mt-4 max-w-3xl text-lg leading-relaxed text-slate-600">
          Estimate the model budget locally, then sign in to store source PDFs in your private workspace.
          Saved jobs are not processed yet: no model request is sent and no API key is collected.
        </p>
      </header>

      <div className="mb-6 flex items-start gap-3 rounded-2xl border border-sky-200 bg-sky-50 p-5 text-sm text-sky-950">
        <AlertCircle className="mt-0.5 h-5 w-5 shrink-0 text-sky-700" />
        <div>
          <p className="font-semibold">Phase A is an upload and planning workspace.</p>
          <p className="mt-1 leading-relaxed text-sky-900/80">
            Your PDFs remain private to your Supabase user. The future worker will recalculate the quote,
            enforce the cap before every model call, and require separate approval before processing.
          </p>
        </div>
      </div>

      <div className="grid gap-6 xl:grid-cols-[1.05fr_0.95fr]">
        <div className="space-y-6">
          <article className="rounded-2xl border border-slate-200 bg-white p-6 shadow-sm">
            <div className="flex items-start gap-3">
              <div className="rounded-xl bg-sky-50 p-2.5 text-sky-700"><FileUp className="h-5 w-5" /></div>
              <div>
                <h2 className="text-xl font-semibold text-slate-900">1. Add private source PDFs</h2>
                <p className="mt-1 text-sm text-slate-600">PDF only · up to 20 MB each · maximum 10 files · 2 active beta jobs</p>
              </div>
            </div>

            {authLoading ? (
              <div className="mt-5 flex min-h-36 items-center justify-center rounded-xl border border-slate-200 bg-slate-50 text-sm text-slate-600">
                <LoaderCircle className="mr-2 h-4 w-4 animate-spin" /> Checking your private workspace
              </div>
            ) : !isConfigured ? (
              <div className="mt-5 rounded-xl border border-amber-200 bg-amber-50 p-5 text-sm text-amber-900">
                Supabase is not configured in this deployment. Add the public URL and anon key before enabling sign-in.
              </div>
            ) : !user ? (
              <div className="mt-5 rounded-xl border border-slate-200 bg-slate-50 p-6 text-center">
                <LockKeyhole className="mx-auto h-7 w-7 text-slate-600" />
                <p className="mt-3 font-semibold text-slate-900">Sign in before choosing private files</p>
                <p className="mx-auto mt-1 max-w-md text-sm leading-relaxed text-slate-600">
                  Authentication gives every upload an owner and lets database policies isolate your workspace.
                </p>
                <button
                  type="button"
                  onClick={() => void signInWithGoogle("/digitize")}
                  className="mt-4 inline-flex items-center gap-2 rounded-lg bg-slate-900 px-4 py-2.5 text-sm font-semibold text-white transition hover:bg-slate-700"
                >
                  <KeyRound className="h-4 w-4" /> Sign in with Google
                </button>
              </div>
            ) : (
              <>
                <div className="mt-5 flex items-center gap-3 rounded-xl border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm text-emerald-900">
                  <UserRoundCheck className="h-5 w-5 shrink-0 text-emerald-700" />
                  <p className="min-w-0 truncate">Private workspace for <strong>{user.email}</strong></p>
                </div>
                <input
                  ref={inputRef}
                  type="file"
                  multiple
                  accept=".pdf,application/pdf"
                  className="sr-only"
                  onChange={(event) => {
                    addFiles(event.target.files);
                    event.target.value = "";
                  }}
                />
                <button
                  type="button"
                  disabled={submitting}
                  onClick={() => inputRef.current?.click()}
                  className="mt-4 flex min-h-36 w-full flex-col items-center justify-center rounded-xl border border-dashed border-slate-300 bg-slate-50 px-5 text-center transition hover:border-sky-400 hover:bg-sky-50/50 disabled:cursor-not-allowed disabled:opacity-60"
                >
                  <FileUp className="h-7 w-7 text-slate-500" />
                  <span className="mt-3 text-sm font-semibold text-slate-800">Choose PDFs for this private job</span>
                  <span className="mt-1 text-xs text-slate-500">Files upload only after you confirm the budget below</span>
                </button>
              </>
            )}

            {fileError && <p role="alert" className="mt-3 text-sm font-medium text-rose-700">{fileError}</p>}
            {files.length > 0 && (
              <div className="mt-5 rounded-xl border border-slate-200">
                <div className="flex items-center justify-between border-b border-slate-200 px-4 py-3 text-xs font-semibold text-slate-600">
                  <span>{files.length} PDF{files.length === 1 ? "" : "s"}</span>
                  <span>{fileSize(totalFileSize)} total</span>
                </div>
                <ul className="divide-y divide-slate-100">
                  {files.map((file, index) => (
                    <li key={`${file.name}-${file.size}-${file.lastModified}`} className="flex items-center justify-between gap-3 px-4 py-3 text-sm">
                      <span className="min-w-0 truncate font-medium text-slate-700">{file.name}</span>
                      <span className="ml-auto shrink-0 text-xs text-slate-500">{fileSize(file.size)}</span>
                      <button
                        type="button"
                        disabled={submitting}
                        aria-label={`Remove ${file.name}`}
                        onClick={() => setFiles((current) => current.filter((_, itemIndex) => itemIndex !== index))}
                        className="rounded-md p-1.5 text-slate-500 hover:bg-slate-100 hover:text-rose-700 disabled:opacity-40"
                      >
                        <Trash2 className="h-4 w-4" />
                      </button>
                    </li>
                  ))}
                </ul>
              </div>
            )}
          </article>

          <article className="rounded-2xl border border-slate-200 bg-white p-6 shadow-sm">
            <div className="flex items-start gap-3">
              <div className="rounded-xl bg-emerald-50 p-2.5 text-emerald-700"><Calculator className="h-5 w-5" /></div>
              <div>
                <h2 className="text-xl font-semibold text-slate-900">2. Estimate model cost</h2>
                <p className="mt-1 text-sm text-slate-600">This arithmetic runs locally and does not contact a model provider.</p>
              </div>
            </div>

            <div className="mt-5 grid gap-4 sm:grid-cols-2">
              {[
                ["PDF pages to screen", pages, setPages],
                ["Expected PXRD figures", figures, setFigures],
                ["Input price / 1M tokens ($)", inputPrice, setInputPrice],
                ["Output price / 1M tokens ($)", outputPrice, setOutputPrice],
                ["Screening tokens / page", pageTokens, setPageTokens],
                ["Input tokens / figure", figureInputTokens, setFigureInputTokens],
                ["Output tokens / figure", figureOutputTokens, setFigureOutputTokens],
                ["Your hard budget cap ($)", budgetCap, setBudgetCap],
              ].map(([label, value, setter]) => (
                <label key={label as string} className="block">
                  <span className="mb-1.5 block text-xs font-semibold text-slate-600">{label as string}</span>
                  <input
                    type="number"
                    min="0"
                    step="any"
                    disabled={submitting}
                    value={value as string}
                    onChange={(event) => (setter as (next: string) => void)(event.target.value)}
                    className="w-full rounded-lg border border-slate-200 bg-white px-3 py-2.5 text-sm outline-none focus:border-slate-400 focus:ring-4 focus:ring-slate-100 disabled:bg-slate-50"
                  />
                </label>
              ))}
            </div>
          </article>
        </div>

        <div className="space-y-6">
          <article className="sticky top-24 rounded-2xl border border-slate-200 bg-slate-950 p-6 text-white shadow-xl shadow-slate-200/60">
            <p className="text-xs font-semibold uppercase tracking-[0.16em] text-slate-400">Planning estimate</p>
            <div className="mt-5 grid grid-cols-2 gap-3">
              <div className="rounded-xl border border-white/10 bg-white/5 p-4">
                <p className="text-xs text-slate-400">Expected</p>
                <p className="mt-1 text-2xl font-semibold">{money(estimate.expected)}</p>
              </div>
              <div className="rounded-xl border border-white/10 bg-white/5 p-4">
                <p className="text-xs text-slate-400">Upper estimate (+25%)</p>
                <p className="mt-1 text-2xl font-semibold">{money(estimate.upper)}</p>
              </div>
            </div>
            <dl className="mt-5 space-y-3 border-t border-white/10 pt-5 text-sm">
              <div className="flex justify-between gap-4"><dt className="text-slate-400">Estimated input</dt><dd>{Math.round(estimate.totalInputTokens).toLocaleString()} tokens</dd></div>
              <div className="flex justify-between gap-4"><dt className="text-slate-400">Estimated output</dt><dd>{Math.round(estimate.totalOutputTokens).toLocaleString()} tokens</dd></div>
              <div className="flex justify-between gap-4"><dt className="text-slate-400">Budget cap</dt><dd>{cap > 0 ? money(cap) : "Required"}</dd></div>
            </dl>
            <div className={`mt-5 flex items-start gap-2 rounded-xl border p-4 text-sm ${withinCap ? "border-emerald-400/30 bg-emerald-400/10 text-emerald-100" : "border-amber-400/30 bg-amber-400/10 text-amber-100"}`}>
              {withinCap ? <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0" /> : <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />}
              <p>{withinCap ? "The upper estimate is within your cap." : cap === 0 ? "Set a positive cap before saving a job." : "The upper estimate exceeds your cap."}</p>
            </div>

            {user ? (
              <button
                type="button"
                disabled={!canSubmit}
                onClick={() => void submitJob()}
                className="mt-5 inline-flex w-full items-center justify-center gap-2 rounded-xl bg-white px-4 py-3 text-sm font-semibold text-slate-950 transition hover:bg-sky-50 disabled:cursor-not-allowed disabled:bg-slate-700 disabled:text-slate-400"
              >
                {submitting ? <LoaderCircle className="h-4 w-4 animate-spin" /> : <LockKeyhole className="h-4 w-4" />}
                {submitting && uploadProgress
                  ? `Uploading ${uploadProgress.completed}/${uploadProgress.total}`
                  : "Save private job"}
              </button>
            ) : (
              <button
                type="button"
                disabled={authLoading || !isConfigured}
                onClick={() => void signInWithGoogle("/digitize")}
                className="mt-5 inline-flex w-full items-center justify-center gap-2 rounded-xl bg-white px-4 py-3 text-sm font-semibold text-slate-950 transition hover:bg-sky-50 disabled:cursor-not-allowed disabled:bg-slate-700 disabled:text-slate-400"
              >
                <KeyRound className="h-4 w-4" /> Sign in to open workspace
              </button>
            )}

            {submissionMessage && (
              <p role="status" className="mt-4 rounded-xl border border-emerald-400/30 bg-emerald-400/10 p-3 text-sm text-emerald-100">
                {submissionMessage}
              </p>
            )}
            <p className="mt-4 text-xs leading-relaxed text-slate-400">
              Saving uploads the selected PDFs to private Supabase Storage. It does not start digitization,
              contact a model provider, or incur model cost.
            </p>
          </article>

          <article className="rounded-2xl border border-amber-200 bg-amber-50 p-6">
            <div className="flex items-start gap-3">
              <LockKeyhole className="mt-0.5 h-5 w-5 shrink-0 text-amber-700" />
              <div>
                <h2 className="font-semibold text-amber-950">Why there is still no API-key box</h2>
                <p className="mt-2 text-sm leading-relaxed text-amber-900/80">
                  Phase A stores only source files and planning metadata. Credentials will not be collected until an
                  isolated worker, temporary encryption, deletion policy, and server-side budget guard are deployed.
                </p>
              </div>
            </div>
          </article>
        </div>
      </div>

      {user && (
        <article className="mt-8 overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-sm">
          <header className="flex flex-col gap-3 border-b border-slate-200 px-6 py-5 sm:flex-row sm:items-center sm:justify-between">
            <div>
              <h2 className="text-xl font-semibold text-slate-900">Your private jobs</h2>
              <p className="mt-1 text-sm text-slate-600">Only your authenticated account can read these records.</p>
            </div>
            <button
              type="button"
              disabled={jobsLoading}
              onClick={() => void loadJobs()}
              className="inline-flex items-center gap-2 self-start rounded-lg border border-slate-200 px-3 py-2 text-sm font-medium text-slate-700 transition hover:bg-slate-50 disabled:opacity-50"
            >
              <RefreshCw className={`h-4 w-4 ${jobsLoading ? "animate-spin" : ""}`} /> Refresh
            </button>
          </header>

          {jobsError ? (
            <div className="m-6 flex items-start gap-3 rounded-xl border border-amber-200 bg-amber-50 p-4 text-sm text-amber-900" role="alert">
              <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
              <p>{jobsError}</p>
            </div>
          ) : jobsLoading && jobs.length === 0 ? (
            <div className="flex items-center justify-center px-6 py-12 text-sm text-slate-600">
              <LoaderCircle className="mr-2 h-4 w-4 animate-spin" /> Loading private jobs
            </div>
          ) : jobs.length === 0 ? (
            <div className="px-6 py-12 text-center">
              <Clock3 className="mx-auto h-7 w-7 text-slate-400" />
              <p className="mt-3 font-medium text-slate-800">No private jobs yet</p>
              <p className="mt-1 text-sm text-slate-500">Choose a PDF and save your first planning job above.</p>
            </div>
          ) : (
            <ul className="divide-y divide-slate-100">
              {jobs.map((job) => {
                const status = STATUS_LABELS[job.status];
                return (
                  <li key={job.id} className="grid gap-4 px-6 py-5 md:grid-cols-[1fr_auto_auto_auto] md:items-center">
                    <div className="min-w-0">
                      <div className="flex flex-wrap items-center gap-2">
                        <span className="font-mono text-sm font-semibold text-slate-800">JOB-{job.id.slice(0, 8).toUpperCase()}</span>
                        <span className={`rounded-full border px-2.5 py-0.5 text-xs font-semibold ${status.className}`}>{status.label}</span>
                      </div>
                      <p className="mt-1 text-xs text-slate-500">Created {dateTime(job.createdAt)}</p>
                      {job.errorSummary && <p className="mt-2 text-sm text-rose-700">{job.errorSummary}</p>}
                    </div>
                    <div className="text-sm text-slate-600 md:text-right">
                      <p>{job.sourceFileCount} PDF{job.sourceFileCount === 1 ? "" : "s"} · {fileSize(job.sourceTotalBytes)}</p>
                      <p className="mt-1 text-xs text-slate-500">{job.pageCount} pages · {job.figureCount} expected figures</p>
                    </div>
                    <div className="text-sm md:min-w-36 md:text-right">
                      <p className="font-semibold text-slate-900">Estimate {money(job.clientEstimateUpperUsd)}</p>
                      <p className="mt-1 text-xs text-slate-500">Cap {money(job.budgetCapUsd)} · spent {money(job.actualCostUsd)}</p>
                    </div>
                    {(job.status === "draft" || job.status === "saved") && (
                      <button
                        type="button"
                        disabled={deletingJobId === job.id}
                        onClick={() => {
                          if (confirmDeleteId === job.id) void removeJob(job.id);
                          else setConfirmDeleteId(job.id);
                        }}
                        onBlur={() => {
                          if (deletingJobId !== job.id) setConfirmDeleteId(null);
                        }}
                        className={`inline-flex items-center justify-center gap-1.5 rounded-lg border px-3 py-2 text-xs font-semibold transition disabled:opacity-50 ${confirmDeleteId === job.id ? "border-rose-300 bg-rose-50 text-rose-800 hover:bg-rose-100" : "border-slate-200 text-slate-600 hover:border-rose-200 hover:bg-rose-50 hover:text-rose-700"}`}
                      >
                        {deletingJobId === job.id ? <LoaderCircle className="h-3.5 w-3.5 animate-spin" /> : <Trash2 className="h-3.5 w-3.5" />}
                        {deletingJobId === job.id ? "Deleting" : confirmDeleteId === job.id ? "Confirm delete" : "Delete"}
                      </button>
                    )}
                  </li>
                );
              })}
            </ul>
          )}
        </article>
      )}

      <article className="mt-8 rounded-2xl border border-slate-200 bg-white p-6 shadow-sm">
        <div className="flex items-start gap-3">
          <KeyRound className="mt-0.5 h-5 w-5 shrink-0 text-slate-700" />
          <div>
            <h2 className="font-semibold text-slate-900">The next infrastructure phase</h2>
            <ol className="mt-3 grid gap-3 text-sm leading-relaxed text-slate-600 md:grid-cols-3">
              <li className="rounded-xl bg-slate-50 p-4"><strong className="text-slate-800">1. Controlled worker</strong><br />Lease queued jobs and isolate every run.</li>
              <li className="rounded-xl bg-slate-50 p-4"><strong className="text-slate-800">2. Budget enforcement</strong><br />Requote server-side and stop before the next call exceeds the cap.</li>
              <li className="rounded-xl bg-slate-50 p-4"><strong className="text-slate-800">3. Private review</strong><br />Inspect outputs before explicitly contributing anything to the public database.</li>
            </ol>
          </div>
        </div>
      </article>
    </section>
  );
}
