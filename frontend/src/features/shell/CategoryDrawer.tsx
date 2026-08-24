/**
 * Browse: the left drawer behind the hamburger.
 *
 * **Subject only.** The drawer also listed the four verdicts for a while, and
 * they are gone deliberately rather than for space: a standing menu of
 * Supported / Mixed / Weak / No evidence is a scoreboard, and offering it as a
 * primary way into the feed invites browsing by score instead of by subject —
 * the framing this product exists to avoid. Verdict is still a real narrowing
 * and still survives in the URL as `?verdict=`, so a link someone shares keeps
 * working and the feed heading still names it; it is just not something the
 * navigation proposes.
 *
 * The counts are server-side totals over everything published, not a tally of
 * what has loaded — a number that climbs as you scroll reads as being wrong.
 * They exist because this feed fills slowly on purpose, so a category with
 * nothing in it is the normal case, and a row with no number is a door you have
 * to open to find that out.
 */

import { useEffect } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";

import { SUBJECTS, SUBJECT_LABELS } from "@/features/evidence/subject";
import { fetchFacets, feedKeys } from "@/lib/api/feed";
import { useReader } from "@/features/reader/auth";
import {
  DRAWER,
  DRAWER_CLOSE,
  DRAWER_COUNT,
  DRAWER_FOOTER,
  DRAWER_HEAD,
  DRAWER_LINK,
  DRAWER_LINK_ACCENT,
  DRAWER_LIST,
  DRAWER_SCRIM,
  DRAWER_TITLE,
  drawerRow,
} from "./styles";
import { useBrowseState } from "./useBrowseState";

export function CategoryDrawer({
  open,
  onClose,
}: {
  open: boolean;
  onClose: () => void;
}) {
  const { subject, verdict, setSubject, clear } = useBrowseState();
  const { signedIn } = useReader();

  const { data: facets } = useQuery({
    queryKey: feedKeys.facets,
    queryFn: fetchFacets,
    // Only fetched once the drawer has been opened. The counts are not on the
    // page until then, and the feed's first paint should not wait behind them.
    enabled: open,
    staleTime: 5 * 60_000,
  });

  // Escape closes it. Pointer users have the scrim; without this, keyboard
  // users have nothing but the close button they may not have reached.
  useEffect(() => {
    if (!open) return;
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") onClose();
    }
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [open, onClose]);

  if (!open) return null;

  function choose(action: () => void) {
    return () => {
      action();
      onClose();
    };
  }

  return (
    <>
      <div onClick={onClose} aria-hidden className={DRAWER_SCRIM} />

      <aside className={DRAWER} aria-label="Browse">
        <div className={DRAWER_HEAD}>
          <span className={DRAWER_TITLE}>Categories</span>
          <button onClick={onClose} aria-label="Close" className={DRAWER_CLOSE}>
            ×
          </button>
        </div>

        <div className={DRAWER_LIST}>
          <button
            onClick={choose(clear)}
            className={drawerRow(subject === null && verdict === null)}
          >
            <span>Everything</span>
            <Count value={facets?.total} />
          </button>

          {SUBJECTS.map((option) => (
            <button
              key={option}
              onClick={choose(() => setSubject(option))}
              className={drawerRow(subject === option)}
            >
              <span>{SUBJECT_LABELS[option]}</span>
              <Count value={facets?.subjects[option]} />
            </button>
          ))}
        </div>

        <div className={DRAWER_FOOTER}>
          <Link to="/about" onClick={onClose} className={DRAWER_LINK}>
            About us
          </Link>
          <Link to={signedIn ? "/you" : "/join"} onClick={onClose} className={DRAWER_LINK}>
            {signedIn ? "Saved folders" : "Choose your feed"}
          </Link>
          <Link to="/contact" onClick={onClose} className={DRAWER_LINK_ACCENT}>
            Let us know
          </Link>
        </div>
      </aside>
    </>
  );
}

/**
 * A count, or nothing at all.
 *
 * `undefined` means the facets have not arrived; zero is a real answer and is
 * shown. Rendering a `0` placeholder while loading would say "this category is
 * empty" about categories that are not.
 */
function Count({ value }: { value: number | undefined }) {
  if (value === undefined) return null;
  return <span className={DRAWER_COUNT}>{value}</span>;
}
