import { Code2, Database, LogOut, X } from "lucide-react";
import { Link } from "react-router-dom";
import { useAuth } from "../auth/context";

/**
 * Site header.
 *
 * There is no "Digitize" link and no "Sign in" button. The digitization planner
 * is unfinished, and sign-in exists only for it — nothing in the database or the
 * API needs an account — so offering either in the primary navigation invited
 * every visitor into a feature that is not ready. /digitize and /auth/callback
 * still work for anyone with the direct URL (see App.tsx).
 *
 * The signed-in block stays, shown only when a session exists: the planner has
 * no sign-out of its own, so someone who signed in through the direct URL must
 * still be able to sign out from any page. A visitor with no session sees
 * nothing here, not even the brief "checking sign-in" spinner.
 */
export function Header() {
  const { user, error, signOut, clearError } = useAuth();
  const metadata = user?.user_metadata as
    | { avatar_url?: string; picture?: string; full_name?: string; name?: string }
    | undefined;
  const avatarUrl = metadata?.avatar_url ?? metadata?.picture;
  const displayName = metadata?.full_name ?? metadata?.name ?? user?.email ?? "Researcher";

  return (
    <header className="sticky top-0 z-50 w-full border-b border-slate-200 bg-white/85 backdrop-blur-md">
      <div className="section-container flex h-16 items-center justify-between">
        <Link to="/" className="flex items-center gap-2 reveal-up">
          <Database className="h-6 w-6 text-slate-900 transition-transform duration-300 hover:rotate-6" />
          <span className="text-xl font-semibold tracking-tight text-slate-900">
            Open <span className="font-light text-slate-600">PXRD</span>
          </span>
        </Link>
        <nav aria-label="Primary navigation" className="flex items-center gap-2 sm:gap-4">
          <Link
            to="/"
            className="text-sm font-medium text-slate-600 transition-colors hover:text-slate-900"
          >
            Browse
          </Link>
          <Link
            to="/docs/api"
            className="text-sm font-medium text-slate-600 transition-colors hover:text-slate-900"
          >
            API
          </Link>
          <a
            href="/#about"
            className="hidden text-sm font-medium text-slate-600 transition-colors hover:text-slate-900 sm:block"
          >
            About
          </a>
          <a
            href="https://github.com/AaronG211/pxrd_PEAK/tree/main/cof_database"
            target="_blank"
            rel="noreferrer"
            aria-label="GitHub"
            className="hidden text-slate-500 transition-colors hover:text-slate-900 sm:block"
          >
            <Code2 className="h-4 w-4" />
          </a>
          {user && (
            <div className="flex items-center gap-2 border-l border-slate-200 pl-2 sm:pl-4">
              {avatarUrl ? (
                <img
                  src={avatarUrl}
                  alt=""
                  referrerPolicy="no-referrer"
                  className="h-8 w-8 rounded-full border border-slate-200 bg-slate-100 object-cover"
                />
              ) : (
                <span className="flex h-8 w-8 items-center justify-center rounded-full bg-slate-900 text-xs font-semibold text-white">
                  {displayName.slice(0, 1).toUpperCase()}
                </span>
              )}
              <span className="hidden max-w-36 truncate text-sm font-medium text-slate-700 lg:block">
                {displayName}
              </span>
              <button
                type="button"
                onClick={() => void signOut()}
                className="inline-flex h-9 items-center gap-1.5 rounded-lg px-2.5 text-sm font-medium text-slate-600 transition hover:bg-slate-100 hover:text-slate-900"
                aria-label="Sign out"
              >
                <LogOut className="h-4 w-4" />
                <span className="hidden xl:inline">Sign out</span>
              </button>
            </div>
          )}
        </nav>
      </div>
      {error && (
        <div className="fixed right-4 top-20 z-[60] flex max-w-sm items-start gap-3 rounded-xl border border-rose-200 bg-white px-4 py-3 text-sm text-rose-800 shadow-lg" role="alert">
          <p>{error}</p>
          <button type="button" onClick={clearError} aria-label="Dismiss sign-in error" className="rounded p-0.5 hover:bg-rose-50">
            <X className="h-4 w-4" />
          </button>
        </div>
      )}
    </header>
  );
}
