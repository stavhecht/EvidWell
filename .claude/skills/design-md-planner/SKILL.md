---
name: design-md-planner
description: >-
  Interview-driven design planning that produces a design.md file BEFORE any UI is built. Use this skill
  WHENEVER the user is about to design or build a frontend, website, web app, landing page, dashboard,
  portfolio, or any visual interface — especially if they say they want it to look distinctive, custom,
  intentional, "not generic", or explicitly want to "avoid AI slop". Also trigger when the user asks to
  plan a design, choose a color palette / fonts / layout direction, or complains that a design looks
  generic and wants a stronger point of view. The skill runs a short adaptive interview to extract genuine
  design intent, then writes a concrete design.md — a specific color story, type system, layout logic, and
  one signature element — deliberately steered away from default AI-generated aesthetics (the indigo/violet
  gradient, Inter everywhere, centered hero + gradient blob, glassmorphism bento grids, emoji bullets,
  "Elevate your workflow" copy). Do NOT skip the interview and jump straight to code.
---

# Design.md Planner

Produce a `design.md` — a written design brief with concrete, buildable decisions — through a short
interview, *before* writing any UI code. The output is a single file another developer (or a future Claude
session) can build from without guessing.

**The whole point of this skill is to defeat AI slop.** Left to autopilot, AI-generated interfaces converge
on the same handful of choices: the same violet gradient, the same font, the same centered hero, the same
glassy bento grid. That convergence is the enemy. Every decision in the `design.md` must be *chosen*, with a
reason, and must survive the slop filter below.

---

## Non-negotiables

1. **Run the interview.** Do not infer a design and skip to output. The interview is where intent comes from.
   If the user already stated some answers (in this conversation or the request), use them — don't re-ask.
2. **No default aesthetics.** Read `references/slop-catalog.md` before synthesizing decisions. Nothing from
   the "slop" column ships unless the user *explicitly and specifically* asks for it and there's a real reason.
3. **Concrete over vague.** "Modern and clean" is not a decision. Exact hex values, named typefaces with
   fallbacks, a real spacing scale, and one nameable signature move are decisions.
4. **Push back.** When the user gives an empty adjective ("professional", "sleek") or reaches for a cliché,
   don't just accept it — name what's generic about it and offer 2–3 specific, more interesting directions.

---

## The core commitment: no AI slop

A condensed hit-list (full catalog + antidotes in `references/slop-catalog.md`). If a decision resembles any
of these, stop and choose deliberately instead:

- **Color:** the indigo/violet primary (`#6366f1`, `#7c3aed`, `#8b5cf6`); purple→pink→blue "AI startup"
  gradients; raw untuned Tailwind palette; "trust blue" `#2563eb` on every SaaS; neon-on-slate cyberpunk by reflex.
- **Type:** Inter for everything (and its reflex substitutes — Geist, DM Sans — chosen without thought);
  all-sans with no pairing; giant `bg-clip-text` gradient hero text; one weight, no hierarchy.
- **Layout:** centered hero = headline + subtitle + two buttons + gradient blob; bento grid for everything;
  three feature cards each with an icon-in-a-circle; glassmorphism (`backdrop-blur` + `white/10` border);
  everything centered and symmetrical; floating mesh-gradient orbs; `rounded-2xl` + soft shadow on every card.
- **Details:** Lucide icons in pastel circles; emoji as bullets (✨🚀⚡); gradient "New"/"Beta" pills;
  avatar + 5-star testimonial cards; the sparkle ✨ "AI" motif.
- **Copy:** "Elevate", "Supercharge", "Unleash", "Seamless", "Effortless", "The future of X", "X, reimagined".
- **Motion:** fade-up-on-scroll on every element; the same 300ms ease on everything.

The antidote is always the same shape: **start from content and a point of view, commit to a real reference,
constrain hard, and add one memorable signature.** Details in the catalog.

---

## How to run the interview

Conversational, not a form. Follow these rules:

- **Small batches.** Ask 2–4 questions per turn, then wait. Never dump the whole interview at once.
- **Adaptive.** Skip what's already answered. Follow interesting threads. Reorder as needed.
- **Plain language.** Stav-style scannable markdown; no jargon walls. On mobile-friendly single-choice
  moments, the `ask_user_input` tool is fine — but most of this is open-ended prose, so free text is the default.
- **Ban empty adjectives.** If an answer is "clean / modern / professional / sleek / minimal", it doesn't
  count. Ask for a *referent* instead: an object, place, era, publication, film, brand, or scene it should
  feel like. "What does it feel like?" beats "what adjectives?".
- **Confront slop on the spot.** If the user asks for something on the hit-list, say what's generic about it
  and offer specific alternatives before moving on.

### Interview phases

Move through these, adapting freely. Sample questions are prompts, not a script.

**Phase 0 — What & who.** What are we designing (app / site / dashboard / portfolio / landing)? What's the
one job of the interface? Who uses it, in what setting (phone on the go? focused desktop work? big screen)?
Is it content/data-dense or expressive/marketing?

**Phase 1 — Point of view.** The single impression it should leave. Force a concrete referent:
> "Not adjectives — give me a *thing*. A place, an object, a magazine, a film still, a piece of software you
> love the feel of. What should someone's gut reaction be in the first two seconds?"

**Phase 2 — References & anti-references.** 2–3 designs (sites, apps, print, games, physical objects, brands)
they love the *look* of — and specifically *why*. Then the inverse: what they want to actively avoid, and
anything that would make them wince.

**Phase 3 — Constraints.** Existing brand / logo / colors to honor? Light, dark, or both? Accessibility bar
(contrast, reduced motion)? Tech constraints (framework, existing component lib, Tailwind or not)? Any hard
content requirements (a data table that must be legible, a specific hero asset)?

**Phase 4 — Content reality.** What *actually* goes on the primary screen(s)? Real sections, real data, the
real first thing a user sees. Designing around real content is the single best defense against template-shaped slop.

---

## From answers to decisions

Synthesize the interview into concrete, buildable choices. For **each** of the following, run it through the
slop filter: name the default you'd reflexively reach for, reject it, and choose deliberately.

- **Color story.** Derive the palette from a *source* the user named (a photo, a material, a place, an era) —
  not from a palette generator's defaults. Constrain it (a small set, not fifteen tints), assign real semantic
  roles (surface / text / accent / states), include one unexpected accent, and verify contrast. Give exact hex.
- **Type system.** Choose typefaces with intent — usually a characterful display/heading paired with a
  workhorse text face, or one distinctive family used across a real weight/size range. Name fallbacks. Define
  the scale and where contrast lives. Avoid the Inter-default reflex; if a neutral sans is genuinely right,
  justify it.
- **Layout & grid.** Decide the underlying structure and where you break symmetry deliberately. Density,
  rhythm, and whitespace should express the point of view — not default to centered-everything.
- **Motion.** Restrained and expressive of the brand, not fade-up-on-everything. Define a couple of
  intentional moments and honor reduced-motion.
- **The signature move.** One memorable, specific thing this design does that no template would — a
  typographic treatment, an unusual navigation, a distinctive use of the source material, an editorial layout
  device. If you can't name one, the design isn't done.

---

## Write the design.md

Use `assets/design.md.template` as the structure. Fill every section with concrete, buildable values — hex
codes, font names + fallbacks, the spacing scale, component-level intent, and the named signature element.

Always include the template's **"Slop we're explicitly avoiding"** section, listing the specific defaults
this project rejects and what it does instead — this keeps the commitments enforceable when someone (or a
later Claude session) builds from the file.

Create the file in the working directory and present it to the user with `present_files`. Then offer to
refine any section, or to build the UI from the brief.

Note: this skill complements the environment's `frontend-design` skill. If a build follows, that skill covers
implementation-level styling; `design.md` is the upstream point-of-view document that keeps the build from
sliding back into defaults.
