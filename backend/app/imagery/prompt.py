"""What the picture is allowed to be. Composed here, never by a model.

**A generated picture must not become a claim.** This product exists to avoid
asserting more than the evidence supports, and an image is the one register a
reader does not read critically. A photograph of someone visibly healthier
beside a supplement asserts efficacy that no citation backs; a clinical or
laboratory setting borrows authority the article has not earned; a
before-and-after pair states a result outright. None of those need a caption to
be read as an argument.

So the prompt is assembled here from fixed parts, and the exclusions are
hard-coded. Three consequences, all deliberate:

* **No second model call writes it.** Asking an LLM for a nice image prompt
  reintroduces exactly the ungrounded generation the pipeline is built to
  prevent, one layer down and unvalidated, and adds a failure mode and a cost
  to a decorative feature.
* **The verdict never reaches the prompt**, and neither does the headline —
  which is where the verdict usually is, in twelve words or fewer. An
  illustration that "looks positive" for a `supported` article and bleak for a
  `weak` one is a scoreboard drawn in pictures, and it would be drawn *before*
  a human had approved the verdict. ``headline`` is a parameter here, and is
  used for the alt text only.
* **The subject noun is scrubbed**, because ``product`` is free text an
  extraction model produced from an arbitrary blurb. It is pinned to a short
  plain noun phrase so it cannot carry style directions, a second sentence, or
  an instruction of its own into the render.

## Two paths: objects, and one person doing something

A person may appear, and only under a rule narrow enough to keep the paragraph
above true. **The distinction is depicting the subject versus depicting a
result.** A protocol *is* an activity — someone on a mat mid-stretch, a forearm
mid-lift — so photographing the activity is photographing what the article is
about, and asserts nothing beyond "this is the thing we looked at". A person
beside a supplement jar, a bowl of food or a tube of cream is the other case
entirely: nothing in the frame is doing anything, so the only thing the person
can be there to communicate is an outcome. That is the efficacy claim, and it
stays refused.

So people are gated on the motif, not offered globally — ``_PEOPLE_MOTIFS``
holds the subjects that are activities, and every other subject renders a still
life exactly as before. Two further guards, both because the cost of being
wrong here is much higher than the cost of a plain still life:

* **Only a confident activity signal opens the gate** — a reviewer's explicit
  ``subject``, or a motif matched against ``product`` itself. A motif inferred
  from the *topic* does not, because topics name outcomes ("magnesium for
  sleep") and ``sleep`` is a protocol word. Uncertainty falls back to objects.
* **The activity is in progress, never posed**, and ``EXCLUSIONS_WITH_PEOPLE``
  says so at length. A body displayed rather than used — a physique, a
  transformation, eye contact with the camera — is an advertisement, which is
  the claim wearing a different coat.

## Why the prompt varies, and how

The first version of this module locked *everything* — one motif, one camera
angle, one arrangement — leaving the subject noun as the only variable. Three
words of eighty. Measured on real renders, that produced a feed where every
article was white pills on warm beige: the prompt described a space so narrow
that different sampler noise still landed in the same picture.

The fix is not more randomness at the sampler. It cannot be, and that was
measured too: the provider ignores ``seed`` entirely (see
``services/illustration.py::seed_for_run``), so those identical-looking renders
*already* came from different noise. Variety has to come from the prompt.

So the prompt is assembled from four varying axes, selected by a seed we
control rather than one we hope the provider honours: framing, arrangement,
light and surface. Each path supplies its own set — a still life is *scattered
across a surface*, a person is *mid-stretch on a mat*, and the two vocabularies
do not survive being swapped — but the axis count and the option counts are
identical, so both paths give the same ~500 combinations from the same seed.

The one fixed axis is ``TREATMENT``: palette and register, and nothing else.
That split is the whole design: **treatment locked, composition free.** Locking
the treatment is what makes a feed read as one publication; locking the
composition as well is what made it read as one photograph. It is also what
keeps the people path from drifting into stock fitness photography — the same
muted, matte, understated register applies to it unchanged.

One technical caveat worth stating plainly, because it looks like a bug
otherwise: **``negative_prompt`` is close to a no-op on FLUX.1-schnell.** The
checkpoint is guidance-distilled and runs at ``guidance_scale=0.0``, and a
CFG-style negative prompt has nothing to act on at that guidance. It is sent
anyway — it costs nothing, providers accept it, and it applies immediately if
the model or the guidance setting ever changes — but it is *not* what keeps
people out of these images. The positive prompt is. That is why the exclusions
below are written into ``EXCLUSIONS`` as plain language in the prompt itself,
and why the object motifs are specified concretely enough that a person has
nowhere to appear. Do not "simplify" by moving the exclusions into the negative
prompt.
"""

from __future__ import annotations

import operator
import re
from itertools import accumulate

from app.domain.enums import Subject

#: What the picture shows, per subject — **objects only**, no arrangement.
#:
#: Arrangement moved out to ``_ARRANGEMENTS`` so it can vary; a motif that also
#: fixed the layout was half the reason every render looked alike.
#:
#: Concrete and object-named on purpose: a motif that names its objects leaves
#: no room for the model to populate the frame with a person, which is a
#: stronger guarantee than asking for one to be absent. Everything is unbranded
#: and unlabelled — a generated label is either an invented brand or a real one
#: we have no right to depict, and a readable word on a wellness image reads as
#: a product endorsement.
_MOTIFS: dict[Subject, str] = {
    Subject.SUPPLEMENT: (
        "loose capsules, a small heap of pale powder and a plain unlabelled "
        "glass jar"
    ),
    Subject.DEVICE: "a small matte unbranded consumer device and its coiled cable",
    Subject.PROTOCOL: "a folded towel, a glass of water and a simple timer",
    Subject.FOOD: "whole raw ingredients and a shallow ceramic bowl",
    Subject.TOPICAL: "a plain unlabelled tube and a small smear of pale cream",
}

#: The subjects a person may appear in, and what that person is there to do.
#:
#: **Only the ones that are activities.** See the module docstring: a protocol
#: is a thing someone does, so a body mid-movement depicts the subject. Every
#: other motif is a thing someone *takes*, where a body in frame can only be
#: read as the result of taking it. Adding a key here is therefore a content
#: decision about claims, not a styling choice — ``Subject.DEVICE`` is the one
#: with a real argument on both sides (a hand holding a massager is use; a
#: person glowing beside a red-light panel is efficacy) and is deliberately
#: left out until someone wants to make it.
#:
#: The objects from ``_MOTIFS`` are kept alongside the person on purpose. They
#: anchor the frame to the same props the still-life path uses, which is most
#: of why these renders stay in the same magazine as the others.
_PEOPLE_MOTIFS: dict[Subject, str] = {
    Subject.PROTOCOL: (
        "one ordinary person in plain unbranded everyday clothing, in the "
        "middle of the activity, with a folded towel and a glass of water "
        "nearby"
    ),
}

#: Words that suggest a motif, checked against product, then topic+ingredients.
#:
#: **This is not a classification and must never become one.** It picks which
#: objects get photographed, is never shown to a reader, never coloured,
#: never written to ``articles.subject``. That column stays reviewer-set for
#: the reason CLAUDE.md gives — a guessed subject puts a confident colour on an
#: unchecked classification — and the difference is what it costs to be wrong:
#: a wrong subject is a false statement on the page, a wrong motif is a
#: slightly odd still life.
#:
#: **Precedence is by field first, then by position here** — and the field half
#: is the one that matters, because within-list order cannot fix what a joined
#: haystack breaks. This table was searched subject-by-subject over product,
#: topic and ingredients concatenated, so a low-precision hint in an early list
#: beat a high-precision hint in a late one. ``SUPPLEMENT`` is last and
#: ``PROTOCOL`` holds ``sleep``, ``exercise``, ``training`` and ``therapy`` —
#: which is how articles name their *outcome*. Measured on realistic inputs,
#: "Magnesium glycinate" / "magnesium for sleep quality", "Melatonin" /
#: "melatonin for sleep", "Ashwagandha KSM-66" / "ashwagandha for stress and
#: sleep" and "Whey protein" / "protein for muscle after exercise" all resolved
#: to ``PROTOCOL`` and drew a towel and a timer. Since topics routinely name an
#: outcome, that was the common path for supplements, not an edge case.
#: ``infer_motif_subject`` now takes its arguments in trust order and stops at
#: the first field that matches anything.
#:
#: Matched on word starts, so "creatine" hits `supplement` without "cream"
#: hitting `topical` too.
_MOTIF_HINTS: tuple[tuple[Subject, tuple[str, ...]], ...] = (
    (
        Subject.TOPICAL,
        ("cream", "serum", "gel", "balm", "lotion", "ointment", "patch",
         "sunscreen", "moisturis", "topical", "salve"),
    ),
    (
        Subject.DEVICE,
        ("device", "panel", "lamp", "mask", "tracker", "monitor", "wearable",
         "ring", "band", "roller", "massager", "machine", "sauna", "mat",
         "headset", "torch", "led"),
    ),
    (
        Subject.PROTOCOL,
        ("yoga", "pilates", "plunge", "fasting", "fast", "breathwork",
         "meditation", "sleep", "exercise", "training", "workout", "walking",
         "running", "stretch", "massage", "bathing", "shower", "journaling",
         "grounding", "hiit", "cardio", "therapy"),
    ),
    (
        Subject.FOOD,
        ("juice", "tea", "coffee", "oil", "honey", "vinegar", "milk", "kefir",
         "yoghurt", "yogurt", "diet", "fruit", "berry", "nut", "seed", "cocoa",
         "chocolate", "egg", "fish", "broth", "kombucha", "water"),
    ),
    (
        Subject.SUPPLEMENT,
        ("capsule", "tablet", "powder", "vitamin", "mineral", "extract",
         "supplement", "creatine", "magnesium", "collagen", "protein",
         "omega", "probiotic", "ashwagandha", "melatonin", "zinc", "iron",
         "curcumin", "caffeine", "nootropic", "amino"),
    ),
)


def _default_motif(noun: str) -> str:
    """When nothing matched, let the subject noun choose the objects.

    The old default named no objects at all — "plain unlabelled objects" — and
    a model given that plus a supplement noun draws pills, every time, for
    every article. Deferring to the noun is what lets a hot-yoga article get a
    mat and a towel instead.
    """
    return f"plain, unbranded objects associated with {noun}" if noun else (
        "a few plain, unbranded everyday objects"
    )


# --- the varying axes -------------------------------------------------------
#
# Two parallel sets, one per path, in stride order: framing, arrangement,
# light, surface. The option counts must match across both — see _AXIS_RADIX.

#: Camera distance and angle, objects. This axis does most of the work — it is
#: the difference between two photographs rather than two crops, which is why
#: it is the fastest-varying one (stride 1).
_FRAMINGS: tuple[str, ...] = (
    "flat-lay seen from directly overhead, generous negative space",
    "low three-quarter angle close to the surface",
    "close macro crop, shallow depth of field, the subject filling the frame",
    "wide shot with the subject small in an open field",
    "slightly raised three-quarter view, shallow depth of field",
)

#: How the objects sit.
_ARRANGEMENTS: tuple[str, ...] = (
    "scattered loosely across the surface",
    "gathered into one tight cluster",
    "laid out in an orderly row",
    "grouped near one edge of the frame",
    "resting apart from one another with space between",
)

#: What they sit on. Varies in material, never in colour — the palette is
#: locked in ``TREATMENT``, and that is what holds the feed together.
_SURFACES: tuple[str, ...] = (
    "on a matte paper backdrop",
    "on raw plaster",
    "on pale linen cloth",
    "on unpolished stone",
    "on a smooth clay surface",
)

#: Camera distance and angle, person. The still-life framings do not transfer:
#: a flat-lay of a human being is a mortuary photograph.
_PEOPLE_FRAMINGS: tuple[str, ...] = (
    "wide shot, the figure small in an open room, generous negative space",
    "medium shot from waist height, the whole movement visible",
    "close crop, shallow depth of field, one limb filling the frame",
    "low three-quarter angle near floor level",
    "seen from slightly above at conversational distance",
)

#: What the person is doing. The counterpart of ``_ARRANGEMENTS``.
#:
#: Every option names an action in progress and most of them turn the face
#: away or out of frame. That is not squeamishness about faces — it is the
#: cheapest way to keep the render on the "someone doing the thing" side of
#: the line, since a face addressing the camera is the single strongest cue
#: that a photograph is selling rather than describing.
_POSES: tuple[str, ...] = (
    "seated cross-legged on a mat, seen from behind",
    "mid-stretch with the body turned away from the camera",
    "cropped close on a forearm and hand mid-effort, the rest out of frame",
    "standing at rest, shoulders loose, pausing between efforts",
    "lying on a mat, seen from a distance",
)

#: Where they are. The counterpart of ``_SURFACES``: same materials, same
#: locked palette, scaled up from a tabletop to a room.
_PEOPLE_SETTINGS: tuple[str, ...] = (
    "in a plain room with a bare wooden floor",
    "on a mat over pale concrete",
    "beside a window hung with linen",
    "against a bare plaster wall",
    "on a smooth clay-coloured floor",
)

#: Direction and hardness. **Shared by both paths**, and stays inside the same
#: daylight family so the feed does not swing between studio and candlelight.
_LIGHTS: tuple[str, ...] = (
    "soft diffused daylight from one side, gentle shadows",
    "low raking light casting long soft shadows",
    "even overcast light, almost shadowless",
    "warm late-afternoon light from behind, soft rim highlights",
)


# --- the fixed axis ---------------------------------------------------------

#: Palette and register. **Do not add composition here.**
#:
#: Everything in this string is a property of the *look* — what makes twenty
#: articles read as one publication. Camera angle, framing, arrangement and
#: light are deliberately absent; they live in the tuples above precisely so
#: they can differ. A clause added here is a clause that stops varying, which
#: is how this module once produced the same photograph every time.
#:
#: The genre word ("still life", "unposed documentary") used to live here as
#: ``editorial still-life photograph`` and moved out to ``_Path.genre`` when
#: the people path arrived — it was a composition clause hiding in the treatment
#: string, and it flatly contradicted a motif with a person in it. What is left
#: is the part that genuinely never varies, and it is what stops the people
#: renders from turning into stock fitness photography: same muted palette,
#: same matte surfaces, same understated register as every still life beside
#: them in the feed.
TREATMENT = (
    "editorial photograph, muted warm neutral palette, natural colour, matte "
    "surfaces, calm and understated"
)

#: Carried in the *positive* prompt. See the module docstring: on a
#: guidance-distilled checkpoint this is the half that actually works. Applies
#: to every still-life combination.
#:
#: Two words are deliberately narrower than the obvious version. "**Branded**
#: packaging", not packaging: several motifs name a jar or a tube, and a blanket
#: ban contradicted them — the model was left to split the difference on every
#: render. And "numbers" was dropped from the text ban for the same reason: it
#: forbade the face of the timer the protocol motif asks for, while "text,
#: lettering, labels" already covers every case that matters.
EXCLUSIONS = (
    "No people, no faces, hands or any part of a body. No clinical, hospital "
    "or laboratory setting and no medical equipment. No text, lettering, "
    "labels, logos or branded packaging. No charts, graphs, arrows or "
    "before-and-after comparison"
)

#: The same list with the body ban replaced rather than removed.
#:
#: Read the two side by side: everything about clinics, text, branding and
#: before-and-after framing is identical, because none of it was ever about
#: whether a person was in the frame. What changes is that "no body" becomes a
#: much more specific ban on the body being *displayed* — a physique, a
#: transformation, a camera-facing smile. Those are the forms in which a person
#: makes the efficacy claim, and they are what the still-life rule was actually
#: buying. A person mid-movement makes no claim, so it is allowed; an
#: advertisement for a result is refused in either path.
EXCLUSIONS_WITH_PEOPLE = (
    "The person is ordinary, unremarkable and mid-activity, never posed: no "
    "bare or displayed physique, no bodybuilding or fitness-model posing, no "
    "eye contact with the camera, no visible transformation or weight change, "
    "no glamour, advertising or stock-photo register. No clinical, hospital "
    "or laboratory setting and no medical equipment. No text, lettering, "
    "labels, logos or branded packaging. No charts, graphs, arrows or "
    "before-and-after comparison"
)

#: Sent regardless, for the providers and future checkpoints where it bites.
#: Split into three so the two paths cannot drift apart in the half they share.
_NEGATIVE_ALWAYS = (
    "clinic, hospital, laboratory, medical equipment, syringe, before and "
    "after, comparison, chart, graph, diagram, arrow, text, lettering, words, "
    "numbers, label, watermark, logo, brand, packaging, trademark, "
    "oversaturated, dramatic lighting, glamour, 3d render, cgi"
)

#: Added when no person may appear at all.
_NEGATIVE_NO_BODY = (
    "person, people, human, face, portrait, hand, fingers, body, skin, model, "
    "doctor, nurse, patient"
)

#: Added when one may. **Not the absence of the line above** — a different ban,
#: aimed at the posed and transformed body rather than at the body.
_NEGATIVE_NO_POSING = (
    "bodybuilder, fitness model, posing for the camera, flexing for the "
    "camera, bare torso, six-pack, muscle definition display, weight loss "
    "transformation, smiling at the camera, stock photo, advertisement, "
    "doctor, nurse, patient"
)

NEGATIVE_PROMPT = f"{_NEGATIVE_NO_BODY}, {_NEGATIVE_ALWAYS}"
NEGATIVE_PROMPT_WITH_PEOPLE = f"{_NEGATIVE_NO_POSING}, {_NEGATIVE_ALWAYS}"


class _Path:
    """One route through the module: a vocabulary, its exclusions, its genre.

    Two instances, and the point of the class is that they are interchangeable
    at the call site — ``build_prompt`` picks one and never branches again. The
    axis tuples stay module-level constants rather than moving in here so they
    remain greppable and directly importable by the tests.
    """

    __slots__ = ("axes", "exclusions", "genre", "negative")

    def __init__(
        self,
        *,
        genre: str,
        framings: tuple[str, ...],
        arrangements: tuple[str, ...],
        lights: tuple[str, ...],
        surfaces: tuple[str, ...],
        exclusions: str,
        negative: str,
    ) -> None:
        # Stride order, and it must stay this order — see _AXIS_RADIX.
        self.axes = (framings, arrangements, lights, surfaces)
        self.genre = genre
        self.exclusions = exclusions
        self.negative = negative

    def compose(self, scene: str, seed: int) -> str:
        framing, arrangement, light, surface = (
            axis[(seed // stride) % len(axis)]
            for axis, stride in zip(self.axes, _STRIDES, strict=True)
        )
        return (
            f"{self.genre}: {scene}, {arrangement}. {framing}. "
            f"{light}, {surface}. {TREATMENT}. {self.exclusions}."
        )


#: Options per axis, in stride order: framing, arrangement, light, surface.
#: Both paths must match, which is checked at import — a mismatched length is
#: what would silently break the mixed radix below.
_AXIS_RADIX = (5, 5, 4, 5)

#: Mixed radix: each stride is the product of the radices *before* it, so the
#: four axes decompose ``seed % 500`` into four genuinely independent digits.
#:
#: The previous scheme used odd coprime strides ``(1, 7, 53, 401)`` on the
#: theory that coprimality between the strides buys independence. It does not,
#: and the failure is arithmetic rather than statistical: with stride 7 against
#: a 5-option axis, writing ``seed = 35q + r`` gives
#: ``(seed % 5, seed // 7 % 5) == (r % 5, r // 7)`` — 35 values of ``r`` landing
#: on 25 pairs, so 10 combinations came up exactly twice as often as the other
#: 15. Measured over 400k random seeds: framing × arrangement scored chi-square
#: 49073 on 16 degrees of freedom, commonest pair 23008 against 11272 for the
#: rarest. The quantity that matters is each stride against the *product of the
#: preceding axis lengths*, not the strides against each other. Under the radix
#: below the same measurement is 511 on 499, and every one of the 500
#: combinations appears exactly once per 500 consecutive seeds.
#:
#: Derived rather than written out so it cannot fall out of step with the axis
#: lengths when someone adds a sixth framing.
_STRIDES = tuple(accumulate(_AXIS_RADIX[:-1], operator.mul, initial=1))

_OBJECTS = _Path(
    genre="Still life",
    framings=_FRAMINGS,
    arrangements=_ARRANGEMENTS,
    lights=_LIGHTS,
    surfaces=_SURFACES,
    exclusions=EXCLUSIONS,
    negative=NEGATIVE_PROMPT,
)

_WITH_PEOPLE = _Path(
    genre="Unposed documentary photograph",
    framings=_PEOPLE_FRAMINGS,
    arrangements=_POSES,
    lights=_LIGHTS,
    surfaces=_PEOPLE_SETTINGS,
    exclusions=EXCLUSIONS_WITH_PEOPLE,
    negative=NEGATIVE_PROMPT_WITH_PEOPLE,
)

for _path in (_OBJECTS, _WITH_PEOPLE):
    if tuple(len(axis) for axis in _path.axes) != _AXIS_RADIX:
        raise RuntimeError(
            f"{_path.genre!r} axis lengths "
            f"{tuple(len(axis) for axis in _path.axes)} do not match "
            f"_AXIS_RADIX {_AXIS_RADIX}; update the radix and re-check the "
            "strides, or the axes stop being independent"
        )

#: Longest subject noun phrase allowed into the prompt.
SUBJECT_MAX_CHARS = 60

#: Everything a plain product name needs and nothing that can steer a render.
#: Letters, digits, spaces and the punctuation that shows up inside real
#: supplement names (``KSM-66``, ``omega-3``, ``vitamin D3``). Commas, colons
#: and full stops are dropped precisely because they are how a second clause
#: would be attached.
_UNSAFE_CHARS_RE = re.compile(r"[^A-Za-z0-9 \-+&%]")
_WHITESPACE_RE = re.compile(r"\s+")


def scrub_subject(product: str) -> str:
    """Reduce free text to a short plain noun phrase.

    Returns ``""`` when nothing usable survives — the caller then renders the
    motif alone, which is a perfectly good picture, rather than interpolating an
    empty string into the sentence.
    """
    cleaned = _UNSAFE_CHARS_RE.sub(" ", product)
    cleaned = _WHITESPACE_RE.sub(" ", cleaned).strip()
    if len(cleaned) <= SUBJECT_MAX_CHARS:
        return cleaned
    clipped = cleaned[:SUBJECT_MAX_CHARS]
    # Cut on a word boundary; a half-word noun reads as a typo in the render.
    return (clipped[: clipped.rfind(" ")] if " " in clipped else clipped).strip()


def depicted_subject(product: str, fallback: str = "") -> str:
    """The noun the picture is of.

    ``product`` first because it is the extraction stage's own answer to "what
    is this article about". ``fallback`` is the run's topic, used only when the
    product scrubs away to nothing — which happens when a model returns a
    product that is entirely punctuation or entirely digits.
    """
    return scrub_subject(product) or scrub_subject(fallback)


def infer_motif_subject(*texts: str) -> Subject | None:
    """Guess which objects to photograph from whatever text we have.

    **Not a classification.** See ``_MOTIF_HINTS``. Returns ``None`` when
    nothing matches, and the caller falls back to the noun itself.

    Arguments are taken in **trust order** and searched one at a time, stopping
    at the first that matches anything — the caller passes ``product`` before
    ``topic``. Joining them into one haystack, which is what this did until it
    was measured, lets a vague word in a low-trust field outrank a precise one
    in a high-trust field: "Magnesium glycinate" / "magnesium for sleep quality"
    matched ``sleep`` and drew a towel and a timer.
    """
    for text in texts:
        haystack = text.lower()
        for subject, hints in _MOTIF_HINTS:
            for hint in hints:
                # Word-start match: "creatine" should hit, "increase" should not.
                if re.search(rf"\b{re.escape(hint)}", haystack):
                    return subject
    return None


def build_prompt(
    product: str,
    *,
    subject: Subject | None = None,
    fallback: str = "",
    ingredients: str = "",
    seed: int = 0,
) -> tuple[str, str]:
    """The (prompt, negative_prompt) pair for one article's imagery.

    Both renders of an article call this with the same ``seed``, so both get
    the same prompt — only the dimensions differ. That is what keeps the feed
    tile and the article picture recognisably part of one shoot.

    Args:
        subject: an explicit, reviewer-set classification. Wins over inference
            when present, which is why pressing Regenerate after classifying a
            draft can genuinely improve the picture. ``None`` on every pipeline
            call — the article row does not exist yet.
        seed: selects the four varying axes. The pipeline derives it from the
            run id (stable across retries); Regenerate rolls a fresh one. Note
            this seed does real work *here* even though the image provider
            ignores the one it is also sent.
    """
    noun = depicted_subject(product, fallback)

    # Trust order, and the tier that matched is load-bearing rather than
    # incidental: only the top two open the people path. See the module
    # docstring — a motif inferred from the topic is exactly the case where
    # "magnesium for sleep" reads as a protocol, and drawing a person there
    # would put a body in a supplement article.
    if subject is not None:
        chosen, confident = subject, True
    elif (matched := infer_motif_subject(product)) is not None:
        chosen, confident = matched, True
    else:
        chosen, confident = infer_motif_subject(fallback, ingredients), False

    if confident and chosen in _PEOPLE_MOTIFS:
        path, motif = _WITH_PEOPLE, _PEOPLE_MOTIFS[chosen]
    else:
        path, motif = _OBJECTS, _MOTIFS.get(chosen) if chosen else None

    scene = motif or _default_motif(noun)
    if motif and noun:
        scene = f"{scene}, suggesting {noun}"

    return path.compose(scene, seed), path.negative


def build_alt_text(
    product: str,
    fallback: str = "",
    *,
    subject: Subject | None = None,
) -> str:
    """What a screen reader hears.

    It says three things on purpose: what the picture is, what it is about, and
    that it is generated and decorative. The last part is not padding — a reader
    who cannot see the image otherwise has no way to know that it illustrates
    nothing and depicts no finding, which is precisely the misreading the locked
    exclusions exist to prevent for everyone else.

    Takes the same arguments as ``build_prompt`` and, pointedly, not the
    headline. Alt text sits *next to* the headline on the page, so repeating it
    tells a screen-reader user nothing twice — and the headline is a claim
    sentence, which would put a verdict inside a description of a decoration.

    ``subject`` is here only to name the picture correctly: "still life" is a
    lie about a photograph with a person in it, and a description that
    misdescribes the image is worse than a vague one.
    """
    kind = "photograph" if _draws_a_person(product, subject) else (
        "still-life illustration"
    )
    return (
        f"A generated {kind} for "
        f"{depicted_subject(product, fallback) or 'this article'}. "
        "Decorative: it depicts no research finding."
    )


def _draws_a_person(product: str, subject: Subject | None) -> bool:
    """The people-path predicate, minus the fallback tier.

    Deliberately consults neither topic nor ingredients, for the same reason
    ``build_prompt`` does not: only a reviewer's classification or a match
    against ``product`` itself is confident enough to put a body in frame.
    """
    chosen = subject if subject is not None else infer_motif_subject(product)
    return chosen in _PEOPLE_MOTIFS
