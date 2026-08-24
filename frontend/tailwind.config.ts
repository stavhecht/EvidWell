import type { Config } from "tailwindcss";

/**
 * Every value here resolves to a design-system custom property rather than a
 * literal. Components therefore say `text-ink-3` / `border-rule-soft`, and the
 * theme — including the light/dark flip — is decided entirely in
 * `src/styles/youth.css`. A hex in a component is a bug: it cannot follow the
 * theme, and it is invisible to anyone retuning the system.
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
        // A third ground, warmer and one step down from `surface`: the resting
        // state of a tile before its picture loads, and the fill behind the
        // source block on an article.
        "surface-2": "var(--ew-surface-2)",
        tile: "var(--ew-tile)",

        // The inverted pair, named for the relationship rather than the
        // colour, so they flip with the theme instead of becoming a
        // light-on-light bug. `bg-invert text-invert-fg` is the filled
        // control; `bg-panel text-panel-fg` is the dark explainer block.
        invert: {
          DEFAULT: "var(--ew-invert-bg)",
          fg: "var(--ew-invert-fg)",
        },
        panel: {
          DEFAULT: "var(--ew-panel)",
          fg: "var(--ew-panel-fg)",
        },

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
          hover: "var(--ew-accent-hover)",
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
        /**
         * Playfair Display, and it earns its place on exactly one element: the
         * statement over the hero video. A serif at 60px against a moving
         * image is the comp's one flourish, and spending a second font file on
         * one line is the trade it makes deliberately. Anywhere else in the
         * product this is the wrong face — reach for `font-heading`.
         */
        display: '"Playfair Display", Georgia, serif',
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

      /**
       * Modernist is a zero-radius system and You.th is not, which is the
       * single largest visual difference between the two. Three steps only,
       * named for what they belong to rather than by size, so a component
       * cannot pick "the medium one" and drift:
       *
       *   `tile`  — anything showing a picture: feed tiles, lead images.
       *   `panel` — a block of content resting on the page: source lists,
       *             the dark explainer, the sign-up card.
       *   `field` — text inputs and textareas, softer than a panel so a form
       *             row does not read as a stack of cards.
       *
       * Actions are `rounded-full`, not one of these. A pill against a rounded
       * rectangle is what still separates a control from a card now that both
       * have corners.
       */
      borderRadius: {
        tile: "20px",
        panel: "22px",
        field: "14px",
      },

      keyframes: {
        // The category drawer, in from the left edge it is anchored to.
        "slide-in": {
          from: { transform: "translateX(-100%)" },
          to: { transform: "translateX(0)" },
        },
        // The toast, up into place rather than simply appearing — a message
        // that arrives without motion reads as something that was always
        // there and went unnoticed.
        "fade-up": {
          from: { opacity: "0", transform: "translateY(6px)" },
          to: { opacity: "1", transform: "translateY(0)" },
        },
      },
      animation: {
        "slide-in": "slide-in 0.22s ease-out",
        "fade-up": "fade-up 0.2s ease-out",
      },

      // The comp's page frame: a 1240px measure inset 28px. `prose` is the
      // article's own narrower measure — the feed is a grid and the article is
      // a column, and they are not the same page width.
      maxWidth: { page: "1240px", prose: "760px", console: "1040px" },
      spacing: { gutter: "28px", header: "68px" },
    },
  },
  plugins: [],
} satisfies Config;
