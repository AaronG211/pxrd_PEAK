import { Navigate, Route, Routes, useParams } from "react-router-dom";
import { useAuth } from "./auth/context";
import { Header } from "./components/Header";
import { SiteFooter } from "./components/SiteFooter";
import { ApiDocsPage } from "./pages/ApiDocsPage";
import { AuthCallbackPage } from "./pages/AuthCallbackPage";
import { DigitizationPlannerPage } from "./pages/DigitizationPlannerPage";
import { HomePage } from "./pages/HomePage";
import { PaperDetailPage } from "./pages/PaperDetailPage";

/**
 * Remounted per paper id so the previous record cannot survive a navigation.
 *
 * Without the key, moving between papers kept the old object in state for the
 * duration of the fetch, and moving to a paper that does not exist kept it
 * indefinitely: the visible page showed its error branch, but the document
 * title, meta description and Dataset JSON-LD still described the paper the
 * reader had left — so a crawler on a 404 URL read structured data for a
 * different record. Keying the route is React's own answer to "reset state when
 * a prop changes", and avoids resetting it from inside an effect.
 */
function PaperDetailRoute() {
  const { paperId } = useParams();
  return <PaperDetailPage key={paperId ?? "none"} />;
}

/**
 * /digitize and /auth/callback stay routed but are linked from nowhere public.
 * The digitization planner is unfinished, so the header no longer offers it or
 * a sign-in that only it uses; anyone holding the direct URL still reaches a
 * working page, and the code is kept for when it ships.
 */
function DigitizationRoute() {
  const { user } = useAuth();
  return <DigitizationPlannerPage key={user?.id ?? "anonymous"} />;
}

function App() {
  return (
    <div className="flex min-h-screen flex-col bg-slate-50">
      <a
        href="#main-content"
        className="sr-only z-[100] rounded-lg bg-slate-900 px-4 py-2 text-white focus:not-sr-only focus:fixed focus:left-4 focus:top-4"
      >
        Skip to main content
      </a>
      <Header />
      <main id="main-content" className="flex-grow">
        <Routes>
          <Route path="/" element={<HomePage />} />
          <Route path="/paper/:paperId" element={<PaperDetailRoute />} />
          <Route path="/digitize" element={<DigitizationRoute />} />
          <Route path="/docs/api" element={<ApiDocsPage />} />
          <Route path="/auth/callback" element={<AuthCallbackPage />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </main>

      <SiteFooter />
    </div>
  );
}

export default App;
