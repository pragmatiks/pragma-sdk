"""Agent models for the agents product."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel
from pydantic import Field as PydanticField

from pragma_sdk.models.enums import AgentInstanceStatus


class ConversationRoutingManifest(BaseModel):
    """Routing rules for conversation-to-agent assignment.

    Attributes:
        when: Natural language description of when this agent should handle a conversation.
        examples: Example messages that should route to this agent.
        avoid: Natural language description of messages this agent should not handle.
    """

    when: str
    examples: list[str] = PydanticField(default_factory=list)
    avoid: str | None = None


class ScheduleConfig(BaseModel):
    """Schedule configuration for recurring agent tasks.

    Attributes:
        repeat: Schedule frequency.
        time_of_day: Time to run (HH:MM format).
        days_of_week: Days to run on for weekly schedules.
        day_of_month: Day to run on for monthly schedules.
        timezone: IANA timezone for schedule evaluation.
        every_hours: Interval in hours for hourly schedules.
        cron_expression: Cron expression for custom schedules.
    """

    repeat: str
    time_of_day: str | None = None
    days_of_week: list[str] | None = None
    day_of_month: int | None = None
    timezone: str = "UTC"
    every_hours: int | None = None
    cron_expression: str | None = None


class AgentType(BaseModel):
    """Full agent type definition.

    Attributes:
        id: Unique identifier.
        name: Machine-readable name.
        organization_id: Owning organization.
        display_name: Human-readable name.
        description: Agent purpose and capabilities.
        icon: Icon identifier or URL.
        provider: LLM provider (e.g., "anthropic", "openai").
        model: Model identifier (e.g., "claude-sonnet-4-20250514").
        temperature: Sampling temperature.
        context_window: Maximum context window size.
        system_instructions: System prompt for the agent.
        mcp_servers: MCP server identifiers available to the agent.
        builtin_tools: Built-in tool identifiers available to the agent.
        default_plan_template: Default plan steps for new tasks.
        agent_resource_id: SurrealDB resource ID for the agent type.
        runner_resource_id: SurrealDB resource ID for the runner resource.
        created_by: User ID of the creator.
        version: Schema version for migration support.
        conversation_routing: Routing rules for conversation assignment.
        created_at: Creation timestamp.
        updated_at: Last update timestamp.
    """

    id: str
    name: str
    organization_id: str
    display_name: str
    description: str | None = None
    icon: str | None = None
    provider: str
    model: str
    temperature: float | None = None
    context_window: int | None = None
    system_instructions: str | None = None
    mcp_servers: list[str] = PydanticField(default_factory=list)
    builtin_tools: list[str] = PydanticField(default_factory=list)
    default_plan_template: list[str] | None = None
    agent_resource_id: str | None = None
    runner_resource_id: str | None = None
    created_by: str | None = None
    version: int = 1
    conversation_routing: ConversationRoutingManifest | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class AgentTypeCreate(BaseModel):
    """Request body for creating an agent type.

    Attributes:
        name: Machine-readable name.
        organization_id: Owning organization.
        display_name: Human-readable name.
        description: Agent purpose and capabilities.
        icon: Icon identifier or URL.
        provider: LLM provider.
        model: Model identifier.
        temperature: Sampling temperature.
        context_window: Maximum context window size.
        system_instructions: System prompt for the agent.
        mcp_servers: MCP server identifiers available to the agent.
        builtin_tools: Built-in tool identifiers available to the agent.
        default_plan_template: Default plan steps for new tasks.
        agent_resource_id: SurrealDB resource ID for the agent type.
        runner_resource_id: SurrealDB resource ID for the runner resource.
        created_by: User ID of the creator.
        version: Schema version for migration support.
        conversation_routing: Routing rules for conversation assignment.
    """

    name: str
    organization_id: str
    display_name: str
    description: str | None = None
    icon: str | None = None
    provider: str
    model: str
    temperature: float | None = None
    context_window: int | None = None
    system_instructions: str | None = None
    mcp_servers: list[str] = PydanticField(default_factory=list)
    builtin_tools: list[str] = PydanticField(default_factory=list)
    default_plan_template: list[str] | None = None
    agent_resource_id: str | None = None
    runner_resource_id: str | None = None
    created_by: str | None = None
    version: int = 1
    conversation_routing: ConversationRoutingManifest | None = None


class AgentTypeUpdate(BaseModel):
    """Request body for updating an agent type. All fields optional.

    Attributes:
        name: Machine-readable name.
        display_name: Human-readable name.
        description: Agent purpose and capabilities.
        icon: Icon identifier or URL.
        provider: LLM provider.
        model: Model identifier.
        temperature: Sampling temperature.
        context_window: Maximum context window size.
        system_instructions: System prompt for the agent.
        mcp_servers: MCP server identifiers available to the agent.
        builtin_tools: Built-in tool identifiers available to the agent.
        default_plan_template: Default plan steps for new tasks.
        agent_resource_id: SurrealDB resource ID for the agent type.
        runner_resource_id: SurrealDB resource ID for the runner resource.
        version: Schema version for migration support.
        conversation_routing: Routing rules for conversation assignment.
    """

    name: str | None = None
    display_name: str | None = None
    description: str | None = None
    icon: str | None = None
    provider: str | None = None
    model: str | None = None
    temperature: float | None = None
    context_window: int | None = None
    system_instructions: str | None = None
    mcp_servers: list[str] | None = None
    builtin_tools: list[str] | None = None
    default_plan_template: list[str] | None = None
    agent_resource_id: str | None = None
    runner_resource_id: str | None = None
    version: int | None = None
    conversation_routing: ConversationRoutingManifest | None = None


class AgentInstance(BaseModel):
    """A running or completed agent instance.

    Attributes:
        id: Unique identifier.
        organization_id: Owning organization.
        agent_type_id: Agent type this instance belongs to.
        task_id: Task being worked on, if any.
        status: Current instance lifecycle status.
        started_at: When the instance started.
        ended_at: When the instance ended.
        turn_count: Number of conversation turns.
        tokens_total: Total tokens consumed.
        needs_input: Whether the instance is waiting for user input.
        current_step: Currently executing plan step.
        last_action_at: Timestamp of the last action.
        session_id: External session identifier.
    """

    id: str
    organization_id: str
    agent_type_id: str
    task_id: str | None = None
    status: AgentInstanceStatus = AgentInstanceStatus.STARTING
    started_at: datetime | None = None
    ended_at: datetime | None = None
    turn_count: int = 0
    tokens_total: int = 0
    needs_input: bool = False
    current_step: str | None = None
    last_action_at: datetime | None = None
    session_id: str | None = None


class FleetVitals(BaseModel):
    """Aggregate health metrics for the agent fleet.

    Attributes:
        active_instances: Number of currently running agent instances.
        error_rate: Fraction of instances in error state (0.0 to 1.0).
        avg_turn_count: Average conversation turns per instance.
        avg_tokens_total: Average tokens consumed per instance.
        tasks_by_status: Task count breakdown by status.
        instances_by_type: Instance count breakdown by agent type.
    """

    active_instances: int = 0
    error_rate: float = 0.0
    avg_turn_count: float = 0.0
    avg_tokens_total: float = 0.0
    tasks_by_status: dict[str, int] = PydanticField(default_factory=dict)
    instances_by_type: dict[str, int] = PydanticField(default_factory=dict)
