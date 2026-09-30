"""The image-generation seam: one Protocol and the two shapes crossing it.

Modelled on ``llm/base.py`` so the two read alike, with one deliberate
difference: there is no ``TokenUsage`` here and no ``LLMResult``-style wrapper
carrying one. An image is billed per render (or per second of GPU), not per
token, and inventing a token count to fit the existing ledger would put a
number in ``pipeline_stage_runs`` that means nothing. See the package docstring.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class ImageRequest:
    """One render. Dimensions are exact, not hints.

    ``seed`` is required rather than optional so a caller must decide: the
    pipeline derives it from the run id, and the console's regenerate action
    rolls a fresh one. Note that honouring it is the *provider's* choice and
    the current one does not — see ``services/illustration.py::seed_for_run``.
    It is still recorded on the article, so "which seed produced this" stays
    answerable if the provider ever changes.
    """

    prompt: str
    negative_prompt: str
    width: int
    height: int
    seed: int


@dataclass(frozen=True, slots=True)
class RenderedImage:
    """Encoded image bytes, plus what they actually turned out to be.

    ``width``/``height`` are read back off the decoded image rather than echoed
    from the request. A provider that silently rounds a dimension to its own
    grid produces a tile cropped to the wrong shape, and that is the only place
    the discrepancy is visible.
    """

    data: bytes
    content_type: str
    width: int
    height: int
    seed: int


class ImageClient(Protocol):
    """One text-to-image provider.

    Implementations raise ``ImageError`` for every failure — transport, HTTP
    status, refusal, decode. Callers of this Protocol treat a failure as "no
    picture", never as a reason to fail a run.
    """

    @property
    def model_id(self) -> str:
        """Provider-namespaced, e.g. ``huggingface/black-forest-labs/FLUX.1-schnell``.

        Same convention as ``llm/pricing.py``'s keys, so a future per-image
        price table has a key to use. Declared read-only so an implementation
        may satisfy it with either a property or a plain class attribute.
        """
        ...

    async def render(self, request: ImageRequest) -> RenderedImage: ...


class ImageError(RuntimeError):
    """A render failed.

    Never fatal to a pipeline run. ``IllustrateStage`` catches this, records the
    cause in metrics and hands back the context untouched — an article with no
    picture is publishable and the feed already draws a typographic tile for it.
    Contrast ``retrieval/throttle.py``, where a failed search *must* fail the
    stage: lost recall is invisible and reads as a correct, cautious answer,
    whereas a missing picture is visible to the reviewer on the next screen.
    """
