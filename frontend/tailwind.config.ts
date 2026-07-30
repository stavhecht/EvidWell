import type { Config } from "tailwindcss";

export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        // Verdict colours are semantic, not decorative — they are the fastest
        // signal on a card. Defined once here so the badge, the sources panel
        // flag, and the article header cannot drift apart.
        verdict: {
          supported: "#15803d",
          mixed: "#a16207",
          weak: "#c2410c",
          none: "#57534e",
        },
      },
    },
  },
  plugins: [],
} satisfies Config;
