"""Which MeSH descriptors are substances, which are outcomes, and which are noise.

Three lists and one rule. The rule is the interesting part; the lists exist
because it is not quite enough on its own.

**The rule.** A paper about an *intervention* tags it with an intervention
qualifier — /administration & dosage, /therapeutic use, /pharmacology, /adverse
effects. A paper about a *biomarker* tags it with /blood, /analysis,
/metabolism. That distinction is already in the efetch XML, it is made by a human
indexer, and it costs nothing to read. It is what stops the scan proposing an
article about cortisol.

**Why the lists are still needed.** The rule under-identifies botanicals, which
MeSH indexes as organisms (Withania, Curcuma) and which therefore never appear in
a ``<ChemicalList>``; and it over-identifies a handful of endogenous compounds
that genuinely are administered sometimes (testosterone, insulin). Neither is
fixable by tuning the threshold, so both get an explicit list.

Everything here is keyed by **UI, not name**. Descriptor labels change between
MeSH editions — "Diet, Food, and Nutrition" has been renamed twice — and a
stoplist that silently stops matching is a stoplist that stops working without
saying so.
"""

from __future__ import annotations

from app.discovery.seeds import SEED_ANCHOR_UIS
from app.domain.enums import DiscoveryDescriptorKind

#: Qualifiers that mark a descriptor as something *given to* people. The whole
#: substance-vs-biomarker rule rests on these four.
INTERVENTION_QUALIFIER_UIS: frozenset[str] = frozenset(
    {
        "Q000008",  # administration & dosage
        "Q000627",  # therapeutic use
        "Q000493",  # pharmacology
        "Q000009",  # adverse effects
    }
)

#: Share of a descriptor's papers that must carry an intervention qualifier
#: before it counts as a substance. A guess, and the first number to retune
#: against a real scan — see the checklist in the plan.
INTERVENTION_QUALIFIER_RATIO = 0.25

#: Botanicals and other non-chemical interventions. MeSH indexes these as
#: organisms, so they never reach a ``<ChemicalList>`` and the rule alone would
#: classify every one of them as an outcome — losing exactly the category this
#: product writes about most. Seeded from the values already in
#: ``retrieval/query_builder.MESH_HINTS``, which is the same vocabulary reached
#: from the other direction.
BOTANICAL_UIS: dict[str, str] = {
    "D014988": "Withania",
    "D020940": "Curcuma",
    "D010292": "Panax",
    "D036741": "Rhodiola",
    "D014672": "Valerian",
    "D005512": "Ginkgo biloba",
    "D005968": "Glycyrrhiza",
    "D019804": "Camellia sinensis",
    "D029841": "Silybum marianum",
    "D029968": "Echinacea",
    "D000068298": "Bacopa",
    "D000069575": "Eurycoma",
    "D019653": "Trigonella",
    "D006247": "Hypericum",
}

#: Endogenous compounds that appear in ``<ChemicalList>`` and are measured, not
#: administered — so the rule lets them through and they must be pushed back.
#:
#: The failure this prevents is specific and would look plausible: an article
#: proposed about "cortisol for stress" or "c-reactive protein for
#: inflammation". Both are outcomes wearing an intervention's clothes, and
#: neither is a thing anyone takes.
#:
#: Testosterone and insulin are genuinely administered in clinical medicine.
#: They are here anyway, because in *this* product's literature they are
#: overwhelmingly the measured endpoint of a supplement trial.
BIOMARKER_UIS: dict[str, str] = {
    "D006854": "Hydrocortisone",
    "D013739": "Testosterone",
    "D002097": "C-Reactive Protein",
    "D007328": "Insulin",
    "D001786": "Blood Glucose",
    "D002784": "Cholesterol",
    "D014280": "Triglycerides",
    "D015850": "Interleukin-6",
    "D014409": "Tumor Necrosis Factor-alpha",
    "D002331": "Carbon Dioxide",
    "D005947": "Glucose",
    "D006442": "Hemoglobins",
    "D003847": "Dehydroepiandrosterone",
    "D004967": "Estradiol",
}

#: Descriptors that carry no signal, in four groups: check tags, method terms,
#: broad nutrition context, and category abstractions.
#:
#: The seed anchors are deliberately **not** here — they live on the seeds that
#: name them (``seeds.SEED_ANCHOR_UIS``), so a query and the terms it must not
#: rank cannot drift apart. The two groups fail the same way and are fixed in
#: two different places on purpose.
STOPLIST_UIS: dict[str, str] = {
    # Check tags and demographics — present on nearly every human study.
    "D006801": "Humans",
    "D008297": "Male",
    "D005260": "Female",
    "D000818": "Animals",
    "D000328": "Adult",
    "D008875": "Middle Aged",
    "D000368": "Aged",
    "D055815": "Young Adult",
    "D000293": "Adolescent",
    "D002648": "Child",
    "D051381": "Rats",
    "D051379": "Mice",
    "D005843": "Sex Factors",
    "D000369": "Aged, 80 and over",
    # Method descriptors — how a study was done, not what it was about.
    "D004311": "Double-Blind Method",
    "D018592": "Cross-Over Studies",
    "D016896": "Treatment Outcome",
    "D011446": "Prospective Studies",
    "D012189": "Retrospective Studies",
    "D013997": "Time Factors",
    "D011795": "Surveys and Questionnaires",
    "D015203": "Reproducibility of Results",
    "D016032": "Randomized Controlled Trials as Topic",
    "D016449": "Randomized Controlled Trial",
    "D011788": "Quality of Life",
    "D064888": "Research Design",
    "D064886": "Dietary Supplements as Topic",
    # Study designs. These reached the desk as *outcomes* — "vitamin d for
    # cross-sectional studies" was a real proposal on 2026-09-06 — because
    # anything not identified as a substance falls through to outcome, and a
    # design term is neither. UIs verified against live records.
    "D003430": "Cross-Sectional Studies",
    "D015331": "Cohort Studies",
    "D016022": "Case-Control Studies",
    "D005500": "Follow-Up Studies",
    "D008137": "Longitudinal Studies",
    "D010865": "Pilot Projects",
    "D005240": "Feasibility Studies",
    "D015415": "Biomarkers",
    # Broad nutrition context. Not seed anchors — nothing queries for these —
    # but they ride along on most of this corpus and name no intervention.
    "D004032": "Diet",
    "D004044": "Dietary Proteins",
    "D004040": "Dietary Carbohydrates",
    "D004041": "Dietary Fats",
    "D009752": "Nutritional Status",
    "D013177": "Sports",
    "D005502": "Food",
    "D000073363": "Healthy Volunteers",
    # Dosage forms. A capsule is how a substance arrives, not what it is, and
    # "capsules for muscle strength" is not an article anyone can write.
    "D002214": "Capsules",
    "D013607": "Tablets",
    "D011208": "Powders",
    # Pharmacologic-action and supertype categories. These pass the substance
    # rule cleanly — they are chemicals and they carry /therapeutic use — and
    # they are still not topics: "antineoplastic agents for cancer" names a
    # class, not a thing anyone takes. Measured on 2026-09-04, five of the top
    # twenty "substances" were these. The real fix is a MeSH tree lookup against
    # D27 (Chemical Actions and Uses), which is a network hop per descriptor;
    # this list is the cheap version and covers what actually shows up.
    "D000900": "Anti-Bacterial Agents",
    "D000893": "Anti-Inflammatory Agents",
    "D019440": "Anti-Obesity Agents",
    "D000970": "Antineoplastic Agents",
    "D000975": "Antioxidants",
    "D018696": "Neuroprotective Agents",
    "D007004": "Hypoglycemic Agents",
    "D001688": "Biological Products",
    "D004365": "Drugs, Chinese Herbal",
    "D011134": "Polysaccharides",
    "D010938": "Plant Oils",
    "D009822": "Oils, Volatile",
    "D014665": "Vasodilator Agents",
    "D058573": "Performance-Enhancing Substances",
    # Excipients and controls. Present *because* a trial was run, not because
    # anyone studied them: PEG is a vehicle, and a placebo descriptor rising
    # would mean the scan had discovered that trials use placebos.
    "D011092": "Polyethylene Glycols",
    "D010919": "Placebos",
}


def classify_descriptor(
    ui: str,
    *,
    papers: int,
    chemical_list_papers: int,
    intervention_qualifier_papers: int,
) -> DiscoveryDescriptorKind:
    """What kind of thing this descriptor is, given how it was tagged.

    Order is load-bearing and runs strongest-signal-first:

    1. The stoplist and the seed anchors win outright — a curated "this carries
       no signal" beats any amount of evidence about how it was tagged, and a
       descriptor a seed query *names* is on every record in that net by
       construction, so it cannot distinguish any part of it. Anchors come from
       ``seeds.py`` rather than being restated here, so editing a seed's terms
       cannot leave its own subject topping its own ranking.
    2. The biomarker denylist demotes to an outcome, *before* the chemical-list
       check, because that is the check it exists to override.
    3. The botanical allowlist promotes to a substance, because the rule below
       cannot see organisms at all.
    4. Otherwise: it must have reached a ``<ChemicalList>`` **and** carry an
       intervention qualifier on at least ``INTERVENTION_QUALIFIER_RATIO`` of
       its papers.

    Being wrong in the two directions costs very differently, which is why the
    rule is conjunctive rather than either-signal. A missed substance is a topic
    nobody writes about this fortnight — invisible, recoverable, and the trend
    will still be there next time. A false substance is a proposal for an
    article about a blood measurement, which wastes a reviewer's attention and,
    if promoted, produces a run whose query names no product.

    Args:
        papers: Papers this descriptor appeared on in the window.
        chemical_list_papers: How many of those listed it as a chemical.
        intervention_qualifier_papers: How many tagged it with an intervention
            qualifier.
    """
    if ui in STOPLIST_UIS or ui in SEED_ANCHOR_UIS:
        return DiscoveryDescriptorKind.STOPLISTED
    if ui in BIOMARKER_UIS:
        return DiscoveryDescriptorKind.OUTCOME
    if ui in BOTANICAL_UIS:
        return DiscoveryDescriptorKind.SUBSTANCE
    if not papers or not chemical_list_papers:
        return DiscoveryDescriptorKind.OUTCOME
    if intervention_qualifier_papers / papers >= INTERVENTION_QUALIFIER_RATIO:
        return DiscoveryDescriptorKind.SUBSTANCE
    return DiscoveryDescriptorKind.OUTCOME


def is_too_common(papers: int, corpus_size: int, ceiling: float) -> bool:
    """Whether a descriptor is so ubiquitous in this scan that it says nothing.

    The backstop that keeps ``STOPLIST_UIS`` from having to be complete. MeSH
    has ~30,000 descriptors and the check tags alone change between editions, so
    a hand list will always be missing something — and what it is missing is
    always the same shape: a term on most of the corpus, which by construction
    cannot distinguish any part of it.

    Guards ``corpus_size == 0`` because a scan that matched nothing must report
    nothing rather than divide by zero — a seed can legitimately return no
    records over a two-week window.
    """
    if corpus_size <= 0:
        return False
    return papers / corpus_size > ceiling
