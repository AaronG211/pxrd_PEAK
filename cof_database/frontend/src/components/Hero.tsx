import { ArrowRight, BookOpen, Download, ScanLine } from "lucide-react";
import { Link } from "react-router-dom";

export function Hero() {
  return (
    <section className="hero-surface relative overflow-hidden border-b border-slate-200 py-14 md:py-20">
      <div className="floating-orb absolute -right-20 -top-24 h-72 w-72 rounded-full bg-sky-200/30 blur-3xl" />
      <div className="floating-orb floating-orb-delay absolute -bottom-24 -left-16 h-64 w-64 rounded-full bg-emerald-200/30 blur-3xl" />
      <div className="section-container relative">
        <div className="max-w-4xl">
          <div className="reveal-up mb-6 inline-flex items-center rounded-full border border-emerald-200 bg-emerald-50/80 px-3 py-1 text-sm font-medium text-emerald-800">
            <span className="mr-2 flex h-2 w-2 rounded-full bg-emerald-500" />
            Open research data
          </div>
          <h1 className="reveal-up reveal-delay-1 mb-5 text-4xl font-semibold leading-tight tracking-tight text-slate-900 md:text-5xl lg:text-[3.5rem]">
            PXRD figures, digitized
            <br className="hidden md:block" /> and ready to reuse.
          </h1>
          <p className="reveal-up reveal-delay-2 mb-7 max-w-2xl text-lg leading-relaxed text-slate-600">
            Browse powder X-ray diffraction data recovered from the scientific
            literature. Every record keeps the source figure crop beside its
            digitized traces so the data stays inspectable and citable.
          </p>

          <div className="reveal-up reveal-delay-2 mb-9 flex flex-wrap gap-3">
            <a
              href="#browse"
              className="inline-flex items-center gap-2 rounded-xl bg-slate-900 px-4 py-2.5 text-sm font-semibold text-white transition hover:bg-slate-700"
            >
              Search the collection <ArrowRight className="h-4 w-4" />
            </a>
            <Link
              to="/digitize"
              className="inline-flex items-center gap-2 rounded-xl border border-slate-300 bg-white/80 px-4 py-2.5 text-sm font-semibold text-slate-700 transition hover:bg-white"
            >
              Plan a digitization job
            </Link>
          </div>

          <div className="grid grid-cols-1 gap-4 border-t border-slate-200 pt-7 md:grid-cols-3">
            <div className="feature-tile reveal-up reveal-delay-1 flex flex-col gap-2">
              <div className="flex items-center gap-2 font-medium text-slate-900">
                <BookOpen className="h-5 w-5 text-slate-700" />
                Paper-linked
              </div>
              <p className="text-sm text-slate-600">
                Each curve remains connected to its paper, page, figure, and DOI.
              </p>
            </div>
            <div className="feature-tile reveal-up reveal-delay-2 flex flex-col gap-2">
              <div className="flex items-center gap-2 font-medium text-slate-900">
                <ScanLine className="h-5 w-5 text-slate-700" />
                Visually auditable
              </div>
              <p className="text-sm text-slate-600">
                Compare the published crop with the reconstructed diffraction traces.
              </p>
            </div>
            <div className="feature-tile reveal-up reveal-delay-3 flex flex-col gap-2">
              <div className="flex items-center gap-2 font-medium text-slate-900">
                <Download className="h-5 w-5 text-slate-700" />
                Downloadable
              </div>
              <p className="text-sm text-slate-600">
                Export curve data for analysis, benchmarking, and method development.
              </p>
            </div>
          </div>
        </div>
      </div>
    </section>
  );
}
