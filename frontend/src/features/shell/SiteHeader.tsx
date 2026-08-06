/**
 * The site bar: wordmark, primary nav, theme toggle.
 *
 * Under the Next.js port this is the root `layout.tsx` — it is the one piece of
 * chrome both route groups share, which is why it lives in `features/shell`
 * rather than in either feature.
 *
 * The wordmark is proposal A from the design comp, the "spectrum rule": the
 * five-hue subject palette compressed into a 34×3px bar under the type. It is
 * the only place the subject colours appear unconditionally, which is what
 * teaches the axis before a reader has met a single card.
 */

import { Link, NavLink } from "react-router-dom";

import { useTheme } from "@/lib/theme";
import {
  HEADER_BAR,
  PRIMARY_NAV,
  SITE_HEADER,
  SPECTRUM_BAND_DEVICE,
  SPECTRUM_BAND_FOOD,
  SPECTRUM_BAND_PROTOCOL,
  SPECTRUM_BAND_SUPPLEMENT,
  SPECTRUM_RULE,
  THEME_TOGGLE,
  WORDMARK,
  WORDMARK_TYPE,
  navLink,
} from "./styles";

const NAV = [
  { to: "/", label: "Feed", end: true },
  { to: "/console", label: "Console", end: false },
];

export function SiteHeader() {
  const { theme, toggle } = useTheme();

  return (
    <header className={SITE_HEADER}>
      <div className={HEADER_BAR}>
        <Link to="/" className={WORDMARK} aria-label="EvidWell — home">
          <span className={WORDMARK_TYPE}>Evidwell</span>
          <span aria-hidden className={SPECTRUM_RULE}>
            <span className={SPECTRUM_BAND_SUPPLEMENT} />
            <span className={SPECTRUM_BAND_DEVICE} />
            <span className={SPECTRUM_BAND_PROTOCOL} />
            <span className={SPECTRUM_BAND_FOOD} />
          </span>
        </Link>

        <nav className={PRIMARY_NAV}>
          {NAV.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              end={item.end}
              className={({ isActive }) => navLink(isActive)}
            >
              {item.label}
            </NavLink>
          ))}

          <button
            onClick={toggle}
            // The label names the destination, not the current state: a control
            // reading "Light" while you are on the light theme is ambiguous
            // about whether it reports or acts.
            aria-label={`Switch to the ${theme === "light" ? "dark" : "light"} theme`}
            className={THEME_TOGGLE}
          >
            {theme === "light" ? "Dark" : "Light"}
          </button>
        </nav>
      </div>
    </header>
  );
}
