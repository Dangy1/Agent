from typing import Any, Dict, List, Literal, Optional, TypedDict

DomainName = Literal["slice_ops", "tc_ops", "kpm_rc_ops", "uav_mission", "cross_domain", "dss_ops", "uss_ops", "unknown"]
ApprovalIssuer = Literal["human", "utm", "uas"]


class ApprovalRecord(TypedDict, total=False):
    issuer: ApprovalIssuer
    scope: Dict[str, Any]
    permissions: List[str]
    expires_at: str
    signature_verified: bool
    approved: bool
    reason: str


class PlanStep(TypedDict, total=False):
    step_id: str
    domain: str
    op: str
    description: str
    params: Dict[str, Any]
    resource_keys: List[str]
    requires_approvals: List[ApprovalIssuer]
    rollback: Dict[str, Any]
    verify: Dict[str, Any]


class MissionEventRecord(TypedDict, total=False):
    ts: str
    type: str
    source: str
    severity: str
    data: Dict[str, Any]


class MissionActionRecord(TypedDict, total=False):
    ts: str
    phase: str
    status: str
    command_id: str
    correlation_id: str
    operation_type: str
    domain: str
    op: str
    step_id: str
    params: Dict[str, Any]
    result: Dict[str, Any]
    reason: str


class MissionDecisionRecord(TypedDict, total=False):
    ts: str
    node: str
    decision: str
    reason: str
    inputs: Dict[str, Any]
    outputs: Dict[str, Any]


class MissionState(TypedDict, total=False):
    mission_id: str
    mission: Dict[str, Any]
    mission_phase: str
    mission_status: str
    task_id: str
    request_text: str
    task_idempotency_key: str
    intent: Dict[str, Any]
    selected_skill: Dict[str, Any]
    domain: DomainName
    plan: List[PlanStep]
    current_step: int
    current_command: Dict[str, Any]
    dispatch_domain: str
    resource_locks: Dict[str, str]
    pending_lock_keys: List[str]
    lock_owner: str
    active_runs: Dict[str, Any]
    network_state: Dict[str, Any]
    uav_state: Dict[str, Any]
    utm_state: Dict[str, Any]
    network_state_snapshot: Dict[str, Any]
    uav_state_snapshot: Dict[str, Any]
    utm_state_snapshot: Dict[str, Any]
    mission_state_snapshot: Dict[str, Any]
    approvals: List[ApprovalRecord]
    pending_approvals: List[ApprovalIssuer]
    approval_required: bool
    risk_level: str
    events: List[MissionEventRecord]
    proposed_actions: List[MissionActionRecord]
    applied_actions: List[MissionActionRecord]
    decision_log: List[MissionDecisionRecord]
    evidence_log: List[Dict[str, Any]]
    command_bus_log: List[Dict[str, Any]]
    task_memory: Dict[str, Any]
    protocol_trace: Dict[str, Any]
    rollback_context: List[Dict[str, Any]]
    last_tool_result: Dict[str, Any]
    execution_error: Optional[str]
    status: str
    next_action: str
    policy_notes: List[str]
    conflicts: List[str]
