"""``config.yaml``, parsed and checked.

Typed so a misspelt key fails at load rather than silently using a default —
the same reason ``EvalCase`` forbids extra fields.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field

EVAL_ROOT = Path(__file__).resolve().parent
BACKEND_ROOT = EVAL_ROOT.parent
DEFAULT_CONFIG = EVAL_ROOT / "config.yaml"

Mode = Literal["cached", "live", "replay", "frozen"]


class _Strict(BaseModel):
    model_config = {"extra": "forbid"}


class RunConfig(_Strict):
    mode: Mode = "cached"
    case_timeout_seconds: float = 1500
    cache_path: str = "evaluation/.cache/cassettes.sqlite"
    reports_dir: str = "evaluation/reports"
    baselines_dir: str = "evaluation/baselines"


class JudgeConfig(_Strict):
    enabled: bool = True
    provider: Literal["ollama", "none"] = "ollama"
    model: str = "llama3.1:8b"
    allow_same_model_as_generator: bool = False
    num_ctx: int = 8192
    max_citation_checks: int = 6
    max_source_words: int = 380


class SourceValidationConfig(_Strict):
    enabled: bool = True
    check_urls: bool = False


class FailureSuiteConfig(_Strict):
    provider_max_retries: int = 2
    provider_max_retry_wait_seconds: float = 0.2


class EvaluationConfig(_Strict):
    recency_years: int = 5
    retrieval_ks: list[int] = Field(default_factory=lambda: [1, 3, 5, 10])


class SuiteConfig(_Strict):
    datasets: list[str]
    stop_after: Literal["rank"] | None = None
    #: Metric groups reported for this suite; empty means every group.
    groups: list[str] = Field(default_factory=list)


class Threshold(_Strict):
    direction: Literal["min", "max"]
    value: float
    critical: bool = False
    warn_margin: float = 0.05


class RegressionConfig(_Strict):
    tolerance: float = 0.02
    fail_on_regression: bool = False


class EvalConfig(_Strict):
    run: RunConfig = Field(default_factory=RunConfig)
    judge: JudgeConfig = Field(default_factory=JudgeConfig)
    source_validation: SourceValidationConfig = Field(default_factory=SourceValidationConfig)
    failure_suite: FailureSuiteConfig = Field(default_factory=FailureSuiteConfig)
    evaluation: EvaluationConfig = Field(default_factory=EvaluationConfig)
    suites: dict[str, SuiteConfig]
    thresholds: dict[str, Threshold]
    regression: RegressionConfig = Field(default_factory=RegressionConfig)

    def path(self, relative: str) -> Path:
        """Config paths are relative to ``backend/``, where the command runs."""
        candidate = Path(relative)
        return candidate if candidate.is_absolute() else BACKEND_ROOT / candidate


def load_config(path: Path = DEFAULT_CONFIG) -> EvalConfig:
    with path.open() as handle:
        return EvalConfig.model_validate(yaml.safe_load(handle))
