import { useMemo, useRef, useState } from "react";
import {
  AlertTriangle,
  Calculator,
  CheckCircle2,
  FileUp,
  KeyRound,
  LockKeyhole,
  ShieldCheck,
  Trash2,
} from "lucide-react";

type LocalFile = {
  name: string;
  size: number;
  type: string;
};

const MAX_FILE_BYTES = 20 * 1024 * 1024;
const MAX_FILES = 10;
const ALLOWED_EXTENSIONS = [".pdf", ".png", ".jpg", ".jpeg"];

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

export function DigitizationPlannerPage() {
  const inputRef = useRef<HTMLInputElement>(null);
  const [files, setFiles] = useState<LocalFile[]>([]);
  const [fileError, setFileError] = useState("");
  const [pages, setPages] = useState("10");
  const [figures, setFigures] = useState("3");
  const [inputPrice, setInputPrice] = useState("2.00");
  const [outputPrice, setOutputPrice] = useState("12.00");
  const [pageTokens, setPageTokens] = useState("1400");
  const [figureInputTokens, setFigureInputTokens] = useState("6000");
  const [figureOutputTokens, setFigureOutputTokens] = useState("1200");
  const [budgetCap, setBudgetCap] = useState("1.00");

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
  const withinCap = cap === 0 || estimate.upper <= cap;
  const totalFileSize = files.reduce((sum, file) => sum + file.size, 0);

  const addFiles = (incoming: FileList | null) => {
    if (!incoming) return;
    const selected = [...incoming];
    const invalid = selected.find((file) => {
      const lowerName = file.name.toLowerCase();
      return !ALLOWED_EXTENSIONS.some((extension) => lowerName.endsWith(extension))
        || file.size > MAX_FILE_BYTES;
    });
    if (invalid) {
      setFileError("Use PDF, PNG, or JPEG files no larger than 20 MB each.");
      return;
    }
    if (files.length + selected.length > MAX_FILES) {
      setFileError(`This planner accepts up to ${MAX_FILES} files at a time.`);
      return;
    }
    setFiles((current) => [
      ...current,
      ...selected.map((file) => ({ name: file.name, size: file.size, type: file.type })),
    ]);
    setFileError("");
  };

  return (
    <section className="section-container py-10 md:py-16">
      <header className="mb-8 max-w-4xl">
        <span className="inline-flex items-center gap-2 rounded-full border border-amber-200 bg-amber-50 px-3 py-1 text-xs font-semibold text-amber-800">
          <ShieldCheck className="h-3.5 w-3.5" /> Private beta planner
        </span>
        <h1 className="mt-4 text-3xl font-semibold tracking-tight text-slate-900 md:text-5xl">
          Plan a PXRD digitization job
        </h1>
        <p className="mt-4 max-w-3xl text-lg leading-relaxed text-slate-600">
          Check files and estimate a budget before any model request is made. This page is local-only:
          files stay in your browser, no key is collected, and no API call is sent.
        </p>
      </header>

      <div className="grid gap-6 xl:grid-cols-[1.05fr_0.95fr]">
        <div className="space-y-6">
          <article className="rounded-2xl border border-slate-200 bg-white p-6 shadow-sm">
            <div className="flex items-start gap-3">
              <div className="rounded-xl bg-sky-50 p-2.5 text-sky-700"><FileUp className="h-5 w-5" /></div>
              <div>
                <h2 className="text-xl font-semibold text-slate-900">1. Prepare source files</h2>
                <p className="mt-1 text-sm text-slate-600">PDF, PNG, or JPEG · up to 20 MB each · maximum 10 files</p>
              </div>
            </div>

            <input
              ref={inputRef}
              type="file"
              multiple
              accept=".pdf,.png,.jpg,.jpeg,application/pdf,image/png,image/jpeg"
              className="sr-only"
              onChange={(event) => {
                addFiles(event.target.files);
                event.target.value = "";
              }}
            />
            <button
              type="button"
              onClick={() => inputRef.current?.click()}
              className="mt-5 flex min-h-36 w-full flex-col items-center justify-center rounded-xl border border-dashed border-slate-300 bg-slate-50 px-5 text-center transition hover:border-sky-400 hover:bg-sky-50/50"
            >
              <FileUp className="h-7 w-7 text-slate-500" />
              <span className="mt-3 text-sm font-semibold text-slate-800">Choose files for local validation</span>
              <span className="mt-1 text-xs text-slate-500">Nothing is uploaded from this page</span>
            </button>
            {fileError && <p role="alert" className="mt-3 text-sm font-medium text-rose-700">{fileError}</p>}
            {files.length > 0 && (
              <div className="mt-5 rounded-xl border border-slate-200">
                <div className="flex items-center justify-between border-b border-slate-200 px-4 py-3 text-xs font-semibold text-slate-600">
                  <span>{files.length} file{files.length === 1 ? "" : "s"}</span>
                  <span>{(totalFileSize / 1048576).toFixed(1)} MB total</span>
                </div>
                <ul className="divide-y divide-slate-100">
                  {files.map((file, index) => (
                    <li key={`${file.name}-${index}`} className="flex items-center justify-between gap-3 px-4 py-3 text-sm">
                      <span className="min-w-0 truncate font-medium text-slate-700">{file.name}</span>
                      <button
                        type="button"
                        aria-label={`Remove ${file.name}`}
                        onClick={() => setFiles((current) => current.filter((_, itemIndex) => itemIndex !== index))}
                        className="rounded-md p-1.5 text-slate-500 hover:bg-slate-100 hover:text-rose-700"
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
                <p className="mt-1 text-sm text-slate-600">Enter the current prices shown by your model provider.</p>
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
                ["Your budget cap ($)", budgetCap, setBudgetCap],
              ].map(([label, value, setter]) => (
                <label key={label as string} className="block">
                  <span className="mb-1.5 block text-xs font-semibold text-slate-600">{label as string}</span>
                  <input
                    type="number"
                    min="0"
                    step="any"
                    value={value as string}
                    onChange={(event) => (setter as (next: string) => void)(event.target.value)}
                    className="w-full rounded-lg border border-slate-200 bg-white px-3 py-2.5 text-sm outline-none focus:border-slate-400 focus:ring-4 focus:ring-slate-100"
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
              <div className="flex justify-between gap-4"><dt className="text-slate-400">Budget cap</dt><dd>{cap > 0 ? money(cap) : "Not set"}</dd></div>
            </dl>
            <div className={`mt-5 flex items-start gap-2 rounded-xl border p-4 text-sm ${withinCap ? "border-emerald-400/30 bg-emerald-400/10 text-emerald-100" : "border-amber-400/30 bg-amber-400/10 text-amber-100"}`}>
              {withinCap ? <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0" /> : <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />}
              <p>{withinCap ? "The upper estimate is within your planning cap." : "The upper estimate exceeds your planning cap. Reduce scope or increase the cap before processing."}</p>
            </div>
            <p className="mt-4 text-xs leading-relaxed text-slate-400">
              This is a planning estimate, not a billing guarantee. Image tokenization, retries, failed requests,
              and provider price changes can alter actual cost. The provider-side spending limit remains the financial backstop.
            </p>
          </article>

          <article className="rounded-2xl border border-amber-200 bg-amber-50 p-6">
            <div className="flex items-start gap-3">
              <LockKeyhole className="mt-0.5 h-5 w-5 shrink-0 text-amber-700" />
              <div>
                <h2 className="font-semibold text-amber-950">Why there is no API-key box yet</h2>
                <p className="mt-2 text-sm leading-relaxed text-amber-900/80">
                  A browser-only key field would expose credentials to frontend code and cannot safely run the Python/CV pipeline.
                  Hosted extraction will require sign-in, private storage, a controlled worker, temporary encrypted credentials,
                  and a per-call budget guard. Until those controls exist, this site will not collect keys.
                </p>
              </div>
            </div>
          </article>

          <article className="rounded-2xl border border-slate-200 bg-white p-6 shadow-sm">
            <div className="flex items-start gap-3">
              <KeyRound className="mt-0.5 h-5 w-5 shrink-0 text-slate-700" />
              <div>
                <h2 className="font-semibold text-slate-900">Planned secure workflow</h2>
                <ol className="mt-3 space-y-3 text-sm leading-relaxed text-slate-600">
                  <li><strong className="text-slate-800">1.</strong> Private upload and zero-API preflight.</li>
                  <li><strong className="text-slate-800">2.</strong> Review cost range and set a job cap.</li>
                  <li><strong className="text-slate-800">3.</strong> Run with a temporary user-owned credential in an isolated worker.</li>
                  <li><strong className="text-slate-800">4.</strong> Review results privately before optionally contributing them.</li>
                </ol>
              </div>
            </div>
          </article>
        </div>
      </div>
    </section>
  );
}
