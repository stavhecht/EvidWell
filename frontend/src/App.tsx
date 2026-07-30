/**
 * Route tree.
 *
 * Structured to mirror a Next.js `app/` directory one-for-one, so the port
 * described in DESIGN.md §3.1 is mechanical:
 *
 *   /              -> app/(public)/page.tsx
 *   /a/:slug       -> app/(public)/a/[slug]/page.tsx
 *   /console/*     -> app/(console)/...  (noindex, authenticated)
 *
 * The two groups share nothing but this file. Public routes never import from
 * features/console, and the console never renders public chrome — the same
 * separation the backend has between api/public and api/console.
 */

import { Suspense, lazy } from "react";
import { Route, Routes } from "react-router-dom";

import { AuthProvider } from "./features/console/auth";
import { ArticleRoute } from "./routes/article";
import { FeedRoute } from "./routes/feed";

/**
 * The console is lazy-loaded, and that is a correctness point rather than a
 * micro-optimisation: it carries the TipTap editor, which is most of the
 * bundle. Statically importing it would ship the whole editor to every public
 * reader — which is exactly what ArticleContent.tsx exists to avoid, so
 * importing it here would quietly undo that decision.
 */
const ConsoleRoutes = lazy(() => import("./routes/console"));

export function App() {
  return (
    <AuthProvider>
      <Routes>
        <Route path="/" element={<FeedRoute />} />
        <Route path="/a/:slug" element={<ArticleRoute />} />

        <Route
          path="/console/*"
          element={
            <Suspense
              fallback={<p className="p-8 text-sm text-stone-500">Loading console…</p>}
            >
              <ConsoleRoutes />
            </Suspense>
          }
        />
      </Routes>
    </AuthProvider>
  );
}
