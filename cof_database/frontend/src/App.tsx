import { Navigate, Route, Routes } from "react-router-dom";
import { Header } from "./components/Header";
import { HomePage } from "./pages/HomePage";
import { PaperDetailPage } from "./pages/PaperDetailPage";

function App() {
  return (
    <div className="flex min-h-screen flex-col bg-slate-50">
      <Header />
      <main className="flex-grow">
        <Routes>
          <Route path="/" element={<HomePage />} />
          <Route path="/paper/:paperId" element={<PaperDetailPage />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </main>

      <footer className="mt-auto border-t border-slate-200 bg-white py-10">
        <div className="section-container flex flex-col gap-3 text-sm text-slate-500 sm:flex-row sm:items-center sm:justify-between">
          <p className="font-medium text-slate-700">Open PXRD Database</p>
          <p>Traceable diffraction data for materials researchers.</p>
        </div>
      </footer>
    </div>
  );
}

export default App;
