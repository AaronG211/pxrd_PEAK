import { Code2, Database } from "lucide-react";
import { Link } from "react-router-dom";

export function Header() {
  return (
    <header className="sticky top-0 z-50 w-full border-b border-slate-200 bg-white/85 backdrop-blur-md">
      <div className="section-container flex h-16 items-center justify-between">
        <Link to="/" className="flex items-center gap-2 reveal-up">
          <Database className="h-6 w-6 text-slate-900 transition-transform duration-300 hover:rotate-6" />
          <span className="text-xl font-semibold tracking-tight text-slate-900">
            Open <span className="font-light text-slate-600">PXRD</span>
          </span>
        </Link>
        <nav className="flex items-center gap-5">
          <Link
            to="/"
            className="text-sm font-medium text-slate-600 transition-colors hover:text-slate-900"
          >
            Browse
          </Link>
          <a
            href="/#about"
            className="text-sm font-medium text-slate-600 transition-colors hover:text-slate-900"
          >
            About
          </a>
          <a
            href="https://github.com/AaronG211/cof_database"
            target="_blank"
            rel="noreferrer"
            aria-label="GitHub"
            className="text-slate-500 transition-colors hover:text-slate-900"
          >
            <Code2 className="h-4 w-4" />
          </a>
        </nav>
      </div>
    </header>
  );
}
