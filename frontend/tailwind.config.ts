import type { Config } from "tailwindcss";

/**
 * Every value here resolves to a design-system custom property rather than a
 * literal. Components therefore say `text-ink-3` / `border-rule-soft`, and the
 * theme — including the light/dark flip — is decided entirely in
 * `src/styles/evidwell.css`. A hex in a component is a bug: it cannot follow
 * the theme, and it is invisible to anyone retuning the system.
 *
 * The old `verdict.*` ramp (green / amber / orange / stone) is deliberately
 * gone. The design direction takes verdict off the colour axis entirely and
 * carries it in geometry instead — see `features/evidence/VerdictMark.tsx`.
 * Colour now means *subject*, never judgment.
 *
 * The consequence to know about: colours defined as `var(...)` do not support
 * Tailwind's slash-opacity syntax (`text-ink/50` will not work). That is
 * intentional pressure toward the four-step ink ramp, which hits its contrast
 * ratios predictably on both grounds where a faded ink does not.
 */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  darkMode: ["class", 'html[data-theme="dark"]'],
  theme: {
    extend: {
      colors: {
        ground: "var(--ew-bg)",
        surface: "var(--ew-surface)",

        // The ink ramp. Each step has a contrast job: see the palette table on
        // the design-system page for the measured ratios.
        ink: {
          DEFAULT: "var(--ew-ink)", // headlines, body, verdict marks
          2: "var(--ew-ink-2)", //     excerpts, secondary prose
          3: "var(--ew-ink-3)", //     meta, qualifiers, study type
          4: "var(--ew-ink-4)", //     rules and 14px+ only, never body copy
        },

        rule: {
          DEFAULT: "var(--ew-rule)", //  the strong 2px structural rule
          soft: "var(--ew-rule-soft)", // hairline borders
        },

        accent: {
          DEFAULT: "var(--ew-accent)", // chrome, marks, focus — not text
          ink: "var(--ew-accent-ink)", // the accent at text size
          wash: "var(--ew-accent-wash)",
        },

        // The only chromatic axis in the product. Subject, never verdict.
        subject: {
          supplement: "var(--ew-cat-supplement)",
          device: "var(--ew-cat-device)",
          protocol: "var(--ew-cat-protocol)",
          food: "var(--ew-cat-food)",
          topical: "var(--ew-cat-topical)",
        },
      },

      fontFamily: {
        heading: "var(--font-heading)",
        body: "var(--font-body)",
      },

      /**
       * A fixed scale, not a ratio. Sizes are named for the job they do, so a
       * component cannot quietly promote a caption to a headline; the two fluid
       * steps are the only ones that respond to viewport width.
       */
      fontSize: {
        display: ["clamp(40px,5.4vw,66px)", { lineHeight: "1.02", letterSpacing: "-0.03em" }],
        title: ["clamp(32px,4vw,50px)", { lineHeight: "1.04", letterSpacing: "-0.03em" }],
        headline: ["30px", { lineHeight: "1.1", letterSpacing: "-0.02em" }],
        subhead: ["27px", { lineHeight: "1.14", letterSpacing: "-0.025em" }],
        lede: ["20px", { lineHeight: "1.5" }],
        prose: ["17px", { lineHeight: "1.7" }],
        standfirst: ["16px", { lineHeight: "1.55" }],
        row: ["16px", { lineHeight: "1.3", letterSpacing: "-0.01em" }],
        excerpt: ["14px", { lineHeight: "1.55" }],
        field: ["13.5px", { lineHeight: "1.3" }],
        meta: ["12.5px", { lineHeight: "1.45" }],
        micro: ["11.5px", { lineHeight: "1.4" }],
        label: ["11px", { lineHeight: "1", letterSpacing: "0.11em" }],
        "label-sm": ["10.5px", { lineHeight: "1", letterSpacing: "0.12em" }],
        kicker: ["10px", { lineHeight: "1", letterSpacing: "0.13em" }],
      },

      boxShadow: {
        pop: "var(--ew-shadow-pop)",
        panel: "var(--ew-shadow-panel)",
      },

      // The comp's page frame: a 1240px measure inset 28px, under a 62px bar.
      maxWidth: { page: "1240px", console: "1040px" },
      spacing: { gutter: "28px", header: "62px" },
    },
  },
  plugins: [],
} satisfies Config;
