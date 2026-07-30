/**
 * Authenticated console routes, as a lazy-loaded bundle.
 *
 * Default-exports its own nested `<Routes>` so App.tsx can `lazy()` the whole
 * subtree — the console carries the TipTap editor, and the public feed must
 * not pay for it.
 *
 * Under a Next.js port this becomes a `(console)` route group marked noindex:
 * the review queue must never be crawlable.
 */

import { Route, Routes } from "react-router-dom";

import { RequireAuth } from "@/features/console/auth";
import { LoginForm } from "@/features/console/LoginForm";
import { ReviewDetail } from "@/features/console/ReviewDetail";
import { ReviewQueue } from "@/features/console/ReviewQueue";

export default function ConsoleRoutes() {
  return (
    <Routes>
      {/* Paths are relative to /console/* */}
      <Route path="login" element={<LoginForm />} />
      <Route
        index
        element={
          <RequireAuth>
            <ReviewQueue />
          </RequireAuth>
        }
      />
      <Route
        path="review/:id"
        element={
          <RequireAuth>
            <ReviewDetail />
          </RequireAuth>
        }
      />
    </Routes>
  );
}
