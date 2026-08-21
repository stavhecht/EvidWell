# Slop Catalog — clichés to reject, and what to do instead

Read this before synthesizing design decisions. Each entry pairs the **tell** (how to recognize the slop)
with an **antidote** (a specific, more interesting direction). The goal isn't to ban categories outright —
it's to ensure every choice is *deliberate* rather than defaulted.

The meta-rule: **AI slop is what you get when you skip having a point of view.** The fixes below all reduce
to the same move — start from content and a real reference, constrain hard, and commit to one signature.

---

## 1. Color

**Tells**
- The indigo/violet primary: `#6366f1`, `#7c3aed`, `#8b5cf6`, `#4f46e5` (Tailwind `indigo/violet-500/600`).
- Purple → pink → blue gradients — the "AI startup" mesh.
- "Trust blue" `#2563eb` as the reflex SaaS primary.
- Raw, untuned Tailwind palette used straight out of the box.
- Slate-900 (`#0f172a`) dark background with a single cyan/teal neon accent, by default rather than by choice.
- Fifteen shades of one hue because a generator produced them.

**Antidote**
- Derive the palette from a **named source**: a photograph, a physical material (paper, oxidized copper,
  concrete, a specific fruit), a place, an art movement, a film's grade. Pull real colors from it.
- **Constrain**: a small, opinionated set with clear semantic roles (surface, raised surface, primary text,
  muted text, one accent, state colors). Not a rainbow.
- Add **one unexpected accent** the reference justifies — an ochre, a rust, a sharp chartreuse, a dusty rose.
- Verify contrast (WCAG AA at minimum for text). Distinctive is not an excuse for illegible.
- Neutrals are a choice too: warm greys, cool greys, near-blacks with a hue — not `#000` and `#fff`.

---

## 2. Typography

**Tells**
- Inter for everything — and its no-thought substitutes (Geist, DM Sans, Manrope) chosen as "the safe pick".
- All-sans, all one family, differentiated only by size.
- Giant hero headline with a `bg-clip-text` gradient fill.
- One weight everywhere; hierarchy carried by size alone.
- Body text set at generic 16px/1.5 with no attention to measure or rhythm.

**Antidote**
- **Pair with contrast**: a characterful display/heading face against a clean workhorse for text (e.g. a
  distinctive serif or grotesque for headings, a legible neutral for body) — or one genuinely interesting
  family used across a real range of weights and sizes.
- Consider faces with a point of view: a transitional serif, a Swiss grotesque, a mono for a technical
  feel, a humanist sans with actual character. Justify the pick against the brand.
- Always name **fallbacks** and the full stack.
- Define the **type scale** explicitly and decide where contrast lives (big jump display→body, tight tracking
  on caps, an italic for emphasis, generous or tight leading — chosen, not defaulted).

---

## 3. Layout & structure

**Tells**
- The centered hero: headline + one-line subtitle + two buttons ("Get Started" / "Learn More") + a blurred
  gradient blob behind it.
- Bento grid applied to everything regardless of content.
- Three (or four) feature cards in a row, each an icon-in-a-circle + bold title + two lines of filler.
- Glassmorphism: `backdrop-blur` cards with a `white/10` border and soft glow.
- Everything centered, symmetrical, evenly spaced — no tension, no focal hierarchy.
- Floating mesh-gradient orbs / aurora backgrounds.
- `rounded-2xl` + `shadow-lg` on every surface, uniformly.

**Antidote**
- **Design around real content** (Phase 4). Real sections and real data resist template shapes.
- Choose an underlying **grid** and break its symmetry on purpose — an off-center focal point, an asymmetric
  split, an editorial column structure, an intentional overlap.
- Let **density and whitespace express the point of view**: aggressively tight and information-rich, or
  expansively spare — chosen, with rationale.
- Vary corner radius, border, and elevation by role instead of applying one treatment everywhere. Sometimes
  a hard 1px rule or a flat surface is stronger than another soft shadow.

---

## 4. Components & details

**Tells**
- Lucide icons sitting in pastel-tinted circles.
- Emoji as section markers or feature bullets (✨ 🚀 ⚡ 💡 🔥).
- Gradient "New" / "Beta" pills.
- Testimonial cards: round avatar, name, role, five gold stars.
- The sparkle ✨ used as shorthand for "AI".
- Buttons that are all the same pill with a gradient and a hover-scale.

**Antidote**
- Pick an **icon approach with intent** — a single coherent set (outline, or duotone, or a custom mark), or
  no icons at all if type and layout carry it. Skip the tinted-circle treatment.
- Replace emoji with real typographic or graphic marks — numerals, a custom bullet, a rule, a small caps label.
- Give components **states and roles** that reflect the system, not decoration. Distinguish primary/secondary
  actions by weight and placement, not by stacking gradients.
- Let one or two components carry the **signature** rather than dressing every element identically.

---

## 5. Copy & voice

**Tells**
- "Elevate your workflow", "Supercharge your X", "Unleash", "Seamless", "Effortless", "Powerful yet simple".
- "The future of X", "X, reimagined", "Meet the new way to Y".
- Feature names that are generic verb+noun with no specificity.

**Antidote**
- Write from the **actual thing** the product does, in the product's voice. Specific beats aspirational.
- Name features for what they concretely are. Say the real benefit, not the hype adjective.
- Let the voice match the visual point of view (dry and technical, warm and plainspoken, playful, editorial).

---

## 6. Motion

**Tells**
- Fade-up-on-scroll (AOS-style) applied to every element on the page.
- The same `300ms ease` transition on everything.
- Parallax and floating animations with no purpose.

**Antidote**
- Choose **a few intentional moments** that express the brand (a deliberate reveal, a considered hover, a
  state transition that clarifies) rather than blanket entrance animations.
- Vary duration and easing by intent. Snappy for controls, slower for large reveals.
- Always honor `prefers-reduced-motion`.

---

## Quick self-check before writing design.md

For each decision, ask:
1. Could I name the specific default I'd have reached for on autopilot? Did I reject it?
2. Does this trace back to a **named reference or source**, or to a generator/template?
3. Is there **one signature move** no template would produce?
4. Is it **concrete** — hex, font names, a scale — not adjectives?
5. Would this be legible and accessible, not just novel?

If any answer is weak, the design isn't ready — go back to the interview or the synthesis.
