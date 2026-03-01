from __future__ import annotations

import copy
from typing import Any, Dict, List, Optional


def _deep_format(value: Any, values: Dict[str, Any]) -> Any:
    if isinstance(value, str):
        out = value
        for k, v in values.items():
            out = out.replace("{" + str(k) + "}", str(v))
        return out
    if isinstance(value, list):
        return [_deep_format(v, values) for v in value]
    if isinstance(value, dict):
        return {k: _deep_format(v, values) for k, v in value.items()}
    return value


SKILL_CATALOG: List[Dict[str, Any]] = [
    {
        "skill_id": "uav_utm_standard_mission",
        "name": "UAV UTM Standard Mission",
        "description": "Standard UAV + UTM workflow: plan route, geofence, verify, approval, launch, status.",
        "domain_hint": "uav_mission",
        "triggers": ["uav", "utm", "mission", "launch", "flight", "approval", "geofence"],
        "plan_template": [
            {
                "step_id": "skill-uav-plan",
                "domain": "uav",
                "op": "plan_route",
                "params": {"uav_id": "{uav_id}", "route_id": "{route_id}"},
                "resource_keys": ["uav:{uav_id}:flight_control"],
            },
            {
                "step_id": "skill-uav-geofence",
                "domain": "uav",
                "op": "submit_route_geofence",
                "params": {"uav_id": "{uav_id}", "airspace_segment": "{airspace_segment}"},
                "resource_keys": ["utm:{airspace_segment}"],
            },
            {
                "step_id": "skill-utm-verify",
                "domain": "utm",
                "op": "verify_flight_plan",
                "params": {"uav_id": "{uav_id}", "airspace_segment": "{airspace_segment}", "route_id": "{route_id}"},
                "requires_approvals": ["utm"],
                "resource_keys": ["utm:{airspace_segment}", "uav:{uav_id}:flight_control"],
            },
            {
                "step_id": "skill-uav-approval",
                "domain": "uav",
                "op": "request_utm_approval",
                "params": {"uav_id": "{uav_id}", "airspace_segment": "{airspace_segment}"},
                "resource_keys": ["utm:{airspace_segment}"],
            },
            {
                "step_id": "skill-uav-launch",
                "domain": "uav",
                "op": "launch",
                "params": {"uav_id": "{uav_id}"},
                "requires_approvals": ["utm"],
                "resource_keys": ["uav:{uav_id}:flight_control"],
                "rollback": {"domain": "uav", "op": "rth", "params": {"uav_id": "{uav_id}"}},
            },
            {
                "step_id": "skill-uav-status",
                "domain": "uav",
                "op": "status",
                "params": {"uav_id": "{uav_id}"},
                "resource_keys": [],
            },
        ],
    },
    {
        "skill_id": "cross_domain_network_assured",
        "name": "Cross-Domain Network-Assured Mission",
        "description": "Cross-domain mission using O-RAN/FlexRIC slice and KPM checks around UAV execution.",
        "domain_hint": "cross_domain",
        "triggers": ["cross", "network", "slice", "kpm", "oran", "qos", "latency"],
        "plan_template": [
            {"step_id": "skill-net-health", "domain": "network", "op": "health", "params": {}, "resource_keys": []},
            {
                "step_id": "skill-net-slice",
                "domain": "network",
                "op": "slice_apply_profile",
                "params": {"profile": "nvs-rate", "duration_s": 60},
                "resource_keys": ["ran:slice"],
            },
            {
                "step_id": "skill-net-kpm",
                "domain": "network",
                "op": "kpm_monitor",
                "params": {"duration_s": 20},
                "resource_keys": ["telemetry:kpm_rc"],
            },
            {
                "step_id": "skill-uav-step",
                "domain": "uav",
                "op": "sim_step",
                "params": {"uav_id": "{uav_id}", "ticks": 2},
                "resource_keys": ["uav:{uav_id}:flight_control"],
            },
            {"step_id": "skill-uav-status", "domain": "uav", "op": "status", "params": {"uav_id": "{uav_id}"}, "resource_keys": []},
        ],
    },
    {
        "skill_id": "dss_conflict_and_subscription",
        "name": "DSS Conflict and Subscription Operations",
        "description": "DSS operations for conflict visibility, subscription health, and notification handling.",
        "domain_hint": "dss_ops",
        "triggers": ["dss", "operational intent", "conflict", "subscription", "notification", "conformance"],
        "plan_template": [
            {
                "step_id": "skill-dss-query-intents",
                "domain": "dss",
                "op": "query_operational_intents",
                "params": {"states": ["accepted", "activated", "contingent", "nonconforming"]},
                "resource_keys": [],
            },
            {"step_id": "skill-dss-query-subs", "domain": "dss", "op": "query_subscriptions", "params": {}, "resource_keys": []},
            {
                "step_id": "skill-dss-query-notifs",
                "domain": "dss",
                "op": "query_notifications",
                "params": {"status": "pending", "limit": 50},
                "resource_keys": [],
            },
            {"step_id": "skill-dss-conformance", "domain": "dss", "op": "run_local_conformance", "params": {}, "resource_keys": []},
        ],
    },
    {
        "skill_id": "uss_publication_and_watch",
        "name": "USS Publication and Watch",
        "description": "USS operations for publishing intent and maintaining subscription/notification watch loops.",
        "domain_hint": "uss_ops",
        "triggers": ["uss", "publish", "intent", "subscribe", "watch", "callback"],
        "plan_template": [
            {"step_id": "skill-uss-state", "domain": "uss", "op": "state", "params": {}, "resource_keys": []},
            {
                "step_id": "skill-uss-query-intents",
                "domain": "uss",
                "op": "query_operational_intents",
                "params": {"manager_uss_id": "uss-local"},
                "resource_keys": [],
            },
            {
                "step_id": "skill-uss-pull-notifs",
                "domain": "uss",
                "op": "pull_notifications",
                "params": {"status": "pending", "limit": 50},
                "resource_keys": [],
            },
        ],
    },
]


def list_agent_skills() -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for skill in SKILL_CATALOG:
        out.append(
            {
                "skill_id": str(skill.get("skill_id") or ""),
                "name": str(skill.get("name") or ""),
                "description": str(skill.get("description") or ""),
                "domain_hint": str(skill.get("domain_hint") or ""),
                "triggers": list(skill.get("triggers") or []),
            }
        )
    return out


def get_agent_skill(skill_id: str) -> Optional[Dict[str, Any]]:
    sid = str(skill_id or "").strip().lower()
    if not sid:
        return None
    for skill in SKILL_CATALOG:
        if str(skill.get("skill_id") or "").strip().lower() == sid:
            return copy.deepcopy(skill)
    return None


def match_agent_skill(request_text: str) -> Optional[Dict[str, Any]]:
    text = str(request_text or "").strip().lower()
    if not text:
        return None
    best: Dict[str, Any] | None = None
    for skill in SKILL_CATALOG:
        triggers = [str(t).strip().lower() for t in (skill.get("triggers") or []) if str(t).strip()]
        if not triggers:
            continue
        score = sum(1 for trig in triggers if trig and trig in text)
        if score <= 0:
            continue
        cand = {
            "skill_id": str(skill.get("skill_id") or ""),
            "name": str(skill.get("name") or ""),
            "description": str(skill.get("description") or ""),
            "domain_hint": str(skill.get("domain_hint") or ""),
            "score": score,
            "triggers_matched": [trig for trig in triggers if trig in text],
        }
        if best is None or int(cand["score"]) > int(best["score"]):
            best = cand
    return dict(best) if isinstance(best, dict) else None


def render_skill_plan(skill_id: str, values: Dict[str, Any]) -> List[Dict[str, Any]]:
    skill = get_agent_skill(skill_id)
    if not isinstance(skill, dict):
        return []
    template = list(skill.get("plan_template") or [])
    if not template:
        return []
    out: List[Dict[str, Any]] = []
    for step in template:
        if not isinstance(step, dict):
            continue
        rendered = _deep_format(step, values)
        if isinstance(rendered, dict):
            out.append(rendered)
    return out


__all__ = [
    "list_agent_skills",
    "get_agent_skill",
    "match_agent_skill",
    "render_skill_plan",
]
