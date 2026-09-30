"""FLUX.1-schnell through Hugging Face Inference Providers.

Why the SDK rather than httpx, when every other outbound HTTP call in this
codebase is hand-rolled: text-to-image is **not** served by Hugging Face's
OpenAI-compatible ``/v1`` endpoint — that one is chat-only — and HF's own docs
state that the raw request shape differs per inference provider (nscale,
fal-ai, replicate, together all serve this model with different bodies), with
the client libraries existing to normalise exactly that. Pinning one provider's
private request shape here would break the day ``provider="auto"`` failed over,
which is the day it is most needed. Same reasoning that puts ``anthropic``,
``ollama`` and ``voyageai`` in this project's dependencies.

The transport is built **per render** rather than held on the instance. The SDK
wraps an aiohttp session that has to be closed, and this client is used from
two places with different lifetimes — a long-lived worker process and a
per-request API route. A held session leaks one per request in the second case;
a fresh one costs a TLS handshake against a call that takes several seconds
anyway.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, cast

from app.imagery.base import ImageError, ImageRequest, RenderedImage
from app.imagery.encode import WEBP_CONTENT_TYPE, encode_webp

logger = logging.getLogger(__name__)

PROVIDER = "huggingface"

def qualified(model: str) -> str:
    """``huggingface/black-forest-labs/FLUX.1-schnell``.

    Provider-namespaced like ``llm/pricing.py``'s keys, so a local model is
    never confused with a hosted one and a future per-image price table has a
    key to use.
    """
    return f"{PROVIDER}/{model}"


class HuggingFaceImageClient:
    """Renders one image per call. Never raises anything but ``ImageError``."""

    def __init__(
        self,
        api_key: str,
        model: str,
        *,
        provider: str = "auto",
        timeout: float = 120.0,
        steps: int = 4,
        guidance_scale: float = 0.0,
    ) -> None:
        self._api_key = api_key
        self._model = model
        self._provider = provider
        self._timeout = timeout
        self._steps = steps
        self._guidance_scale = guidance_scale

    @property
    def model_id(self) -> str:
        return qualified(self._model)

    async def render(self, request: ImageRequest) -> RenderedImage:
        """Generate one image and return it encoded as WebP.

        Raises:
            ImageError: any transport, HTTP, authorisation or decode failure,
                with the provider's own message preserved — a 403 from a token
                lacking the "Inference Providers" permission is the single most
                likely first-run failure and reads nothing like a timeout.
        """
        image = await self._generate(request)
        data, width, height = await asyncio.to_thread(encode_webp, image)
        if (width, height) != (request.width, request.height):
            # Not fatal: the picture is fine, it is just not the shape asked
            # for, and the caller decides whether a mis-shaped cover is worth
            # using. Logged because the cause is a provider quietly snapping to
            # its own grid, which is invisible everywhere else.
            logger.warning(
                "%s returned %dx%d for a %dx%d request",
                self.model_id,
                width,
                height,
                request.width,
                request.height,
            )
        return RenderedImage(
            data=data,
            content_type=WEBP_CONTENT_TYPE,
            width=width,
            height=height,
            seed=request.seed,
        )

    async def _generate(self, request: ImageRequest) -> Any:
        # Imported inside the method so that neither the API nor the test suite
        # pays the SDK's import cost (it pulls in aiohttp and the whole hub
        # client) on a process that never renders anything.
        from huggingface_hub import AsyncInferenceClient

        client = AsyncInferenceClient(
            # The SDK types this as a Literal of the providers it knew about
            # when it shipped. Ours is a config string, and pinning it to that
            # Literal would mean a new provider needs a library upgrade rather
            # than an env edit — so the check is deferred to the SDK, which
            # validates the name anyway and reports it as an ImageError.
            provider=cast(Any, self._provider),
            api_key=self._api_key,
            timeout=self._timeout,
        )
        try:
            return await client.text_to_image(
                request.prompt,
                model=self._model,
                negative_prompt=request.negative_prompt,
                width=request.width,
                height=request.height,
                num_inference_steps=self._steps,
                guidance_scale=self._guidance_scale,
                seed=request.seed,
            )
        except Exception as exc:
            raise ImageError(f"{self.model_id}: {type(exc).__name__}: {exc}") from exc
        finally:
            close = getattr(client, "close", None)
            if close is not None:
                try:
                    await close()
                except Exception:
                    logger.debug("closing the inference client failed", exc_info=True)
