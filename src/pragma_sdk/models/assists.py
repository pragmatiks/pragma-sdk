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

from pydantic import BaseModel, Field


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

    Either pass ``title`` and ``description`` for a brand-new task, or
    pass ``parent_context`` to seed the assist from an existing task's
    body when generating subtasks for it.

    Attributes:
        title: Title of the parent task or proposed work item.
        description: Detailed description of the parent task.
        parent_context: Existing parent task body, when generating
            subtasks under an already-created task.
    """

    title: str
    description: str | None = None
    parent_context: str | None = None


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
