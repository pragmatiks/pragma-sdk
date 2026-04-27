"""Task-board AI assist request and response models.

Mirrors the Pydantic models exposed by the API at
``/agents/assists/*``. Each assist is a typed, per-endpoint contract:
``AgentInvoker.invoke_platform_agent()`` is dispatched server-side
behind one of seven dedicated POST endpoints, so the SDK exposes one
``request`` and one ``response`` model per assist rather than a single
generic dispatcher payload.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator


class ImproveTaskRequest(BaseModel):
    """Request body for the ``improve-task`` assist.

    Attributes:
        title: Existing task title to refine.
        description: Existing task description, when available.
    """

    title: str
    description: str | None = None


class ImproveTaskResponse(BaseModel):
    """Response body for the ``improve-task`` assist.

    Attributes:
        title: Suggested replacement title.
        description: Suggested replacement description.
        rationale: Short explanation of the improvements applied.
    """

    title: str
    description: str
    rationale: str


class ExplainTaskRequest(BaseModel):
    """Request body for the ``explain-task`` assist.

    The server fetches the referenced task and, when provided, the
    correlation bucket so the assist can ground its explanation in the
    surrounding event stream.

    Attributes:
        task_id: Task to explain.
        correlation_bucket_id: Optional correlation bucket whose recent
            events should enrich the explanation.
    """

    task_id: str
    correlation_bucket_id: str | None = None


class ExplainTaskResponse(BaseModel):
    """Response body for the ``explain-task`` assist.

    Attributes:
        summary: Human-readable summary of the task.
        key_points: Salient details a reader should know.
        suggested_next_action: Recommended next step for the assignee.
    """

    summary: str
    key_points: list[str]
    suggested_next_action: str


class SummarizeThreadRequest(BaseModel):
    """Request body for the ``summarize-thread`` assist.

    The server pulls the comment thread for ``task_id`` — callers do
    not (and cannot) inline the messages themselves.

    Attributes:
        task_id: Task whose comment thread should be summarized.
    """

    task_id: str


class SummarizeThreadResponse(BaseModel):
    """Response body for the ``summarize-thread`` assist.

    Attributes:
        summary: Plain-language summary of the conversation.
        decisions: Decisions that were reached in the thread.
        open_questions: Outstanding questions that still need answers.
        action_items: Concrete follow-ups extracted from the thread.
    """

    summary: str
    decisions: list[str]
    open_questions: list[str]
    action_items: list[str]


class AssigneeCandidate(BaseModel):
    """An agent instance the caller wants the assist to consider.

    Attributes:
        instance_id: Candidate agent instance identifier.
        type: Candidate type (e.g., the agent type slug).
        current_load: Current in-flight task count for the candidate.
        recent_tasks: Identifiers of tasks the candidate worked on
            recently — used as evidence of capability fit.
    """

    instance_id: str
    type: str
    current_load: int
    recent_tasks: list[str]


class SuggestAssigneeRequest(BaseModel):
    """Request body for the ``suggest-assignee`` assist.

    Attributes:
        task_id: Task to assign.
        candidates: Eligible agent instances for the assist to rank.
    """

    task_id: str
    candidates: list[AssigneeCandidate]


class SuggestAssigneeResponse(BaseModel):
    """Response body for the ``suggest-assignee`` assist.

    Attributes:
        instance_id: Recommended agent instance identifier.
        rationale: Short explanation of why this candidate was picked.
        confidence: Model confidence in the suggestion, in ``[0.0, 1.0]``.
    """

    instance_id: str
    rationale: str
    confidence: float


class GenerateSubtasksRequest(BaseModel):
    """Request body for the ``generate-subtasks`` assist.

    Two mutually exclusive modes are supported:

    * **New task mode** — supply ``title`` (with optional ``description``)
      to generate subtasks for a proposed work item that does not yet
      exist on the board.
    * **Existing task mode** — supply ``parent_context`` (the body of
      an already-created parent task) to generate subtasks beneath it.

    Exactly one mode must be used per request: omit both fields and
    there is nothing to seed the assist with; provide both and the
    intended parent is ambiguous.

    Attributes:
        title: Title of the proposed work item, for new-task mode.
        description: Detailed description of the proposed work item,
            paired with ``title`` in new-task mode.
        parent_context: Body of an already-created parent task, for
            existing-task mode.
    """

    title: str | None = None
    description: str | None = None
    parent_context: str | None = None

    @model_validator(mode="after")
    def _require_exactly_one_mode(self) -> GenerateSubtasksRequest:
        """Enforce that exactly one of the two input modes is supplied.

        Returns:
            The validated :class:`GenerateSubtasksRequest` instance.

        Raises:
            ValueError: If neither or both of ``title`` and
                ``parent_context`` are provided.
        """
        has_title = bool(self.title and self.title.strip())
        has_parent_context = bool(self.parent_context and self.parent_context.strip())

        if has_title == has_parent_context:
            raise ValueError(
                "provide either `title` (with optional `description`) for "
                "new tasks, or `parent_context` for an existing parent task "
                "— not both, not neither"
            )

        return self


class ProposedSubtask(BaseModel):
    """A single subtask proposed by the ``generate-subtasks`` assist.

    Attributes:
        title: Suggested subtask title.
        description: Suggested subtask description.
        priority: Priority level (1=urgent, 2=high, 3=normal, 4=low).
    """

    title: str
    description: str
    priority: int = Field(ge=1, le=4)


class GenerateSubtasksResponse(BaseModel):
    """Response body for the ``generate-subtasks`` assist.

    Attributes:
        subtasks: Ordered list of proposed subtasks for the caller
            to review before persisting.
    """

    subtasks: list[ProposedSubtask]


class ReviewSummaryRequest(BaseModel):
    """Request body for the ``review-summary`` assist.

    The server pulls the task's graph diff, affected resources, and
    risk signals — callers only supply the task identifier.

    Attributes:
        task_id: Task to review.
    """

    task_id: str


class ReviewSummaryResponse(BaseModel):
    """Response body for the ``review-summary`` assist.

    Attributes:
        summary: Plain-language summary of the proposed changes.
        risk_level: Overall risk classification.
        review_checklist: Concrete checks a reviewer should perform
            before approving the change.
    """

    summary: str
    risk_level: Literal["low", "medium", "high"]
    review_checklist: list[str]


class DeepAnalysisRequest(BaseModel):
    """Request body for the ``deep-analysis`` assist.

    Attributes:
        task_id: Task that anchors the analysis.
        user_question: Free-form question the assist should answer.
    """

    task_id: str
    user_question: str


class DeepAnalysisResponse(BaseModel):
    """Response body for the ``deep-analysis`` assist.

    Attributes:
        analysis: Detailed analysis answering ``user_question``.
        concerns: Risks or open issues surfaced during analysis.
        recommendations: Recommended follow-ups for the caller.
    """

    analysis: str
    concerns: list[str]
    recommendations: list[str]
