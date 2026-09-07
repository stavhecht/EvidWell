"""The stage sequence as a LangGraph state machine.

Split from ``orchestrator.py`` because the two answer different questions. This
file knows *what runs next*; the orchestrator knows *what is committed and what
is recorded*, and that division is the reason the port is safe. Every
transaction boundary, every ``pipeline_stage_runs`` row and every failure path
stays where it was; what moved here is the ``for`` loop that used to decide the
order, and it moved because a loop cannot express the one thing the pipeline now
needs to do that is not a straight line.

**The refinement edge is the whole reason this is a graph.** RANK can tell that
a claim has fewer than ``QUORUM_FOR_SUPPORTED`` supported-tier sources, which
means the article's verdict is already capped by what retrieval found rather
than by what the evidence says. Sending that claim back through RETRIEVE with a
broader query is a cycle, and a cycle is what the old shape could not hold: the
stage list is ordered and each stage runs once. DESIGN.md §5 anticipated exactly
this ("a loop *around* RETRIEVE and RANK") and deferred it.

Two properties are load-bearing and easy to lose:

* **The context is carried by reference, not merged.** ``GraphState`` holds one
  key whose value is the whole ``PipelineContext``, so LangGraph replaces it
  wholesale and never reduces its fields. ``usage_by_stage`` and ``metrics`` are
  mutated in place on purpose — a stage that raises returns no context, and its
  burned tokens would otherwise vanish from exactly the runs whose cost is least
  visible — and a per-field reducer would quietly turn those mutations into
  writes that get dropped on the failure path.
* **Node names are positional.** The pipeline is a list, tests pass lists of one
  stage, and nothing guarantees a given ``StageName`` is present. Keying nodes on
  the enum would make the graph undefined for any list that is not the full
  seven.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, TypedDict

from langgraph.graph import END, StateGraph

from app.pipeline.stages import PipelineContext, Stage, StageName

#: How many extra passes through RETRIEVE a run may make. One, deliberately:
#: each pass spends real provider budget against a per-IP ceiling shared with
#: every other worker, and the second look either finds the missing trials or
#: they are not there. A claim still thin after broadening is a fact about the
#: literature, and the honest response to it is the cautious verdict the quorum
#: already produces, not a third query.
MAX_REFINE_ROUNDS = 1

#: Extra supersteps allowed beyond the stages themselves, covering the refine
#: node and the re-run of RETRIEVE and RANK on each round. Set from the shape of
#: the graph rather than left at LangGraph's default, so that adding a stage
#: cannot silently walk into a recursion limit that has nothing to say about the
#: actual problem.
_STEPS_PER_REFINEMENT = 3


class GraphState(TypedDict):
    """The context, plus the attempt number the stage rows are written under.

    ``run_id`` is not here because ``PipelineContext`` already carries it, and
    two copies of an identifier is one more than can be kept consistent.
    ``attempt`` has no home on the context — it is a fact about this execution
    of the run, not about the article being built — and threading it through
    state rather than storing it on the orchestrator is what keeps ``run``
    reentrant.
    """

    ctx: PipelineContext
    attempt: int


#: ``(ordinal, stage, ctx, attempt) -> ctx``. The orchestrator's per-stage
#: wrapper: it records the stage row, runs the stage, commits, and records the
#: end. Passed in rather than imported so this module needs no session and no
#: database.
StageRunner = Callable[
    [int, Stage, PipelineContext, int], Awaitable[PipelineContext]
]


def build_pipeline_graph(
    stages: list[Stage],
    runner: StageRunner,
    *,
    max_refine_rounds: int = MAX_REFINE_ROUNDS,
) -> Any:
    """Compile the stage list into a graph, with the refinement cycle if it fits.

    The cycle is added only when RETRIEVE and RANK are both present and RETRIEVE
    comes first. Any other list — a single fake stage in a test, a future subset
    — compiles as the plain chain it already was.
    """
    graph: StateGraph = StateGraph(GraphState)
    names = [_node_name(index) for index in range(len(stages))]

    for index, stage in enumerate(stages):
        graph.add_node(names[index], _stage_node(index, stage, runner))

    graph.set_entry_point(names[0])

    retrieve_at = _index_of(stages, StageName.RETRIEVE)
    rank_at = _index_of(stages, StageName.RANK)
    loops = (
        retrieve_at is not None
        and rank_at is not None
        and retrieve_at < rank_at
        and max_refine_rounds > 0
    )

    for index in range(len(stages)):
        following = names[index + 1] if index + 1 < len(stages) else END
        if loops and index == rank_at:
            graph.add_node(_REFINE, _refine_node)
            graph.add_edge(_REFINE, names[retrieve_at])  # type: ignore[index]
            graph.add_conditional_edges(
                names[index],
                _router(max_refine_rounds),
                {_REFINE: _REFINE, _CONTINUE: following},
            )
        else:
            graph.add_edge(names[index], following)

    return graph.compile()


def recursion_limit(stages: list[Stage], max_refine_rounds: int) -> int:
    """Enough supersteps for every stage plus each refinement pass, and no more."""
    return len(stages) + max_refine_rounds * _STEPS_PER_REFINEMENT + 2


_REFINE = "refine"
_CONTINUE = "continue"


def _node_name(index: int) -> str:
    return f"stage_{index}"


def _index_of(stages: list[Stage], name: StageName) -> int | None:
    return next(
        (index for index, stage in enumerate(stages) if stage.name is name), None
    )


def _stage_node(
    index: int, stage: Stage, runner: StageRunner
) -> Any:
    async def node(state: GraphState) -> dict[str, PipelineContext]:
        return {"ctx": await runner(index, stage, state["ctx"], state["attempt"])}

    return node


async def _refine_node(state: GraphState) -> dict[str, PipelineContext]:
    """Bump the round counter. No stage row, no commit, no model call.

    A node rather than something the router does, because a LangGraph router
    returns an edge and cannot write state — and because a counter incremented
    invisibly is how a bounded loop becomes an unbounded one.
    """
    ctx = state["ctx"]
    return {"ctx": ctx.model_copy(update={"refine_round": ctx.refine_round + 1})}


def _router(max_refine_rounds: int) -> Callable[[GraphState], str]:
    def route(state: GraphState) -> str:
        ctx = state["ctx"]
        if ctx.thin_claims and ctx.refine_round < max_refine_rounds:
            return _REFINE
        return _CONTINUE

    return route
