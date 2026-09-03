import { AlertCircle, LoaderCircle } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { consumeAuthReturnTo, peekAuthReturnTo, useAuth } from "../auth/context";
import { supabase } from "../lib/supabase";
import { useDocumentTitle } from "../hooks/useDocumentTitle";

export function AuthCallbackPage() {
  useDocumentTitle("Signing in");
  const navigate = useNavigate();
  const { signInWithGoogle } = useAuth();
  const started = useRef(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (started.current) return;
    started.current = true;

    const completeSignIn = async () => {
      const params = new URLSearchParams(window.location.search);
      const providerError = params.get("error_description") ?? params.get("error");
      if (providerError) {
        setError(providerError);
        return;
      }
      if (!supabase) {
        setError("Supabase is not configured for this deployment.");
        return;
      }

      const code = params.get("code");
      if (code) {
        const { error: exchangeError } = await supabase.auth.exchangeCodeForSession(code);
        if (exchangeError) {
          setError(exchangeError.message);
          return;
        }
      } else {
        const { data, error: sessionError } = await supabase.auth.getSession();
        if (sessionError || !data.session) {
          setError(sessionError?.message ?? "The sign-in callback did not include a valid code.");
          return;
        }
      }

      navigate(consumeAuthReturnTo(), { replace: true });
    };

    void completeSignIn();
  }, [navigate]);

  if (error) {
    return (
      <section className="section-container flex min-h-[60vh] items-center justify-center py-16">
        <div className="w-full max-w-lg rounded-2xl border border-rose-200 bg-white p-7 text-center shadow-sm">
          <AlertCircle className="mx-auto h-9 w-9 text-rose-600" />
          <h1 className="mt-4 text-2xl font-semibold text-slate-900">Sign-in could not be completed</h1>
          <p className="mt-2 text-sm leading-relaxed text-slate-600">{error}</p>
          <button
            type="button"
            onClick={() => void signInWithGoogle(peekAuthReturnTo())}
            className="mt-6 rounded-lg bg-slate-900 px-4 py-2.5 text-sm font-semibold text-white transition hover:bg-slate-700"
          >
            Try Google sign-in again
          </button>
        </div>
      </section>
    );
  }

  return (
    <section className="section-container flex min-h-[60vh] items-center justify-center py-16">
      <div className="text-center">
        <LoaderCircle className="mx-auto h-9 w-9 animate-spin text-sky-700" />
        <h1 className="mt-4 text-xl font-semibold text-slate-900">Completing secure sign-in</h1>
        <p className="mt-2 text-sm text-slate-600">You will return to your private workspace shortly.</p>
      </div>
    </section>
  );
}
