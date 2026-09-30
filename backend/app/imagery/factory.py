"""Selects the image provider from config, or none at all.

The mirror of ``llm/factory.py`` with one difference that matters: this one is
allowed to return ``None``. A missing ``IMAGE_GEN_KEY`` must not stop the API
booting or a pipeline run completing — an article with no picture is
publishable, and the feed already draws a typographic tile for it.

Returning ``None`` here rather than a client that answers with nothing is the
same call ``retrieval/factory.py`` makes for a keyless Semantic Scholar:
skipped at construction, so no caller downstream has to tell "there is no
provider" apart from "the provider produced nothing". Here that distinction is
what lets ``IllustrateStage`` report ``not_configured`` instead of a made-up
provider error.
"""

from __future__ import annotations

import logging

from app.config import Settings
from app.imagery.base import ImageClient
from app.imagery.huggingface import HuggingFaceImageClient

logger = logging.getLogger(__name__)


def build_image_client(settings: Settings) -> ImageClient | None:
    """The configured image client, or ``None`` when imagery is off.

    ``None`` for three reasons, all normal: the provider is set to ``none``, no
    key is configured, or the provider name is unrecognised. The last one logs
    at WARNING rather than raising — a typo in ``IMAGE_PROVIDER`` should cost a
    picture, not a run.
    """
    name = settings.image_provider.strip().lower()

    if name in ("", "none", "off"):
        logger.info("article imagery is disabled (image_provider=%r)", settings.image_provider)
        return None

    if name == "huggingface":
        if not settings.image_gen_key:
            logger.warning(
                "article imagery is enabled but IMAGE_GEN_KEY is unset; drafts will "
                "be written without pictures. The token needs the 'Make calls to "
                "Inference Providers' permission — a plain read token authenticates "
                "and then 403s on the first render."
            )
            return None
        logger.info(
            "image provider: huggingface (model=%s, inference_provider=%s)",
            settings.image_model,
            settings.image_inference_provider,
        )
        return HuggingFaceImageClient(
            settings.image_gen_key,
            settings.image_model,
            provider=settings.image_inference_provider,
            timeout=settings.image_timeout_seconds,
            steps=settings.image_steps,
            guidance_scale=settings.image_guidance_scale,
        )

    logger.warning(
        "unknown image_provider %r; expected 'huggingface' or 'none'. Drafts will "
        "be written without pictures.",
        settings.image_provider,
    )
    return None
