"""Mission supervisor runtime API for mission lifecycle control and state inspection."""

from __future__ import annotations

import os
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

try:
    from fastapi import FastAPI, HTTPException
    from fastapi.middleware.cors import CORSMiddleware
    from pydantic import BaseModel, Field
except Exception as e:  # pragma: no cover
    raise RuntimeError("mission_supervisor_agent.api requires fastapi and pydantic") from e

from .runtime import MISSION_RUNTIME
from .planner import list_skills, match_skill
from .skill_catalog import get_agent_skill, render_skill_plan


class MissionStartPayload(BaseModel):
    request_text: str = Field(..., min_length=1)
    mission_id: Optional[str] = None
    initial_state: Optional[Dict[str, Any]] = None
    metadata: Optional[Dict[str, Any]] = None


class MissionStopPayload(BaseModel):
    mission_id: str
    reason: str = "operator_request"


class MissionTestsRunPayload(BaseModel):
    timeout_sec: int = Field(default=120, ge=5, le=1800)


class MissionSkillMatchPayload(BaseModel):
    request_text: str = Field(..., min_length=1)


app = FastAPI(title="Mission Supervisor API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://127.0.0.1:5173",
        "http://localhost:5173",
        "http://127.0.0.1:5174",
        "http://localhost:5174",
        "http://127.0.0.1:5175",
        "http://localhost:5175",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

_REPO_ROOT = Path(__file__).resolve().parents[2]
_TEST_SCRIPT_PATH = _REPO_ROOT / "backend" / "test" / "run_mission_supervisor_regressions.sh"
_TEST_LATEST_KEY = "mission_supervisor:tests:latest"
_TEST_LINE_RE = re.compile(
    r"^(?P<name>test[^\s]+)\s+\([^)]+\)\s+\.\.\.\s+(?P<status>ok|FAIL|ERROR|skipped.*)$"
)
_RAN_RE = re.compile(r"^Ran\s+(?P<total>\d+)\s+tests?\s+in\s+(?P<secs>[0-9.]+)s$", re.IGNORECASE)

_MISSION_GRAPH_NODES: list[Dict[str, Any]] = [
    {"id": "ingest_request", "label": "Ingest Request", "stage": "intake"},
    {"id": "parse_intent", "label": "Parse Intent", "stage": "intake"},
    {"id": "risk_assessment", "label": "Risk Assessment", "stage": "intake"},
    {"id": "refresh_uav_state", "label": "Refresh UAV State", "stage": "context"},
    {"id": "refresh_utm_state", "label": "Refresh UTM State", "stage": "context"},
    {"id": "refresh_network_state", "label": "Refresh Network State", "stage": "context"},
    {"id": "ingest_events", "label": "Ingest Events", "stage": "context"},
    {"id": "plan_build", "label": "Build Plan", "stage": "planning"},
    {"id": "approval_check", "label": "Approval Check", "stage": "planning"},
    {"id": "approval_gate", "label": "Approval Gate", "stage": "guardrail"},
    {"id": "policy_check", "label": "Policy Check", "stage": "guardrail"},
    {"id": "lock_manager", "label": "Lock Manager", "stage": "execution"},
    {"id": "dispatch_step", "label": "Dispatch Step", "stage": "execution"},
    {"id": "execute_step", "label": "Execute Step", "stage": "execution"},
    {"id": "verify_outcome", "label": "Verify Outcome", "stage": "execution"},
    {"id": "progress", "label": "Progress", "stage": "execution"},
    {"id": "recovery", "label": "Recovery", "stage": "recovery"},
    {"id": "complete", "label": "Complete", "stage": "finalize"},
    {"id": "release_locks", "label": "Release Locks", "stage": "finalize"},
]

_MISSION_GRAPH_EDGES: list[Dict[str, Any]] = [
    {"from": "ingest_request", "to": "parse_intent"},
    {"from": "parse_intent", "to": "risk_assessment"},
    {"from": "risk_assessment", "to": "refresh_uav_state"},
    {"from": "refresh_uav_state", "to": "refresh_utm_state"},
    {"from": "refresh_utm_state", "to": "refresh_network_state"},
    {"from": "refresh_network_state", "to": "ingest_events"},
    {"from": "ingest_events", "to": "plan_build"},
    {"from": "plan_build", "to": "approval_check"},
    {"from": "approval_check", "to": "approval_gate", "condition": "approval_required=true"},
    {"from": "approval_check", "to": "policy_check", "condition": "approval_required=false"},
    {"from": "approval_gate", "to": "release_locks"},
    {"from": "policy_check", "to": "recovery", "condition": "next_action=rollback"},
    {"from": "policy_check", "to": "lock_manager", "condition": "next_action=continue"},
    {"from": "lock_manager", "to": "dispatch_step"},
    {"from": "dispatch_step", "to": "execute_step"},
    {"from": "execute_step", "to": "verify_outcome"},
    {"from": "verify_outcome", "to": "recovery", "condition": "next_action=rollback"},
    {"from": "verify_outcome", "to": "progress", "condition": "next_action=continue"},
    {"from": "progress", "to": "complete", "condition": "next_action=complete"},
    {"from": "progress", "to": "approval_check", "condition": "next_action=continue"},
    {"from": "recovery", "to": "release_locks"},
    {"from": "complete", "to": "release_locks"},
    {"from": "release_locks", "to": "END"},
]


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _normalize_test_status(raw: str) -> str:
    token = raw.strip().lower()
    if token == "ok":
        return "passed"
    if token.startswith("skip"):
        return "skipped"
    if token == "fail":
        return "failed"
    if token == "error":
        return "error"
    return token


def _parse_unittest_output(raw_log: str, returncode: int) -> Dict[str, Any]:
    tests: list[Dict[str, Any]] = []
    total: Optional[int] = None
    duration_sec: Optional[float] = None

    for raw_line in raw_log.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        m = _TEST_LINE_RE.match(line)
        if m:
            tests.append(
                {
                    "name": m.group("name"),
                    "status": _normalize_test_status(m.group("status")),
                }
            )
            continue
        ran = _RAN_RE.match(line)
        if ran:
            total = int(ran.group("total"))
            duration_sec = float(ran.group("secs"))

    if total is None:
        total = len(tests)
    passed = sum(1 for t in tests if str(t.get("status")) == "passed")
    failed = sum(1 for t in tests if str(t.get("status")) in {"failed", "error"})
    skipped = sum(1 for t in tests if str(t.get("status")) == "skipped")
    ok = returncode == 0 and failed == 0

    result: Dict[str, Any] = {
        "suite": "mission_supervisor_regressions",
        "ran_at": _utc_now(),
        "ok": ok,
        "total": total,
        "passed": passed,
        "failed": failed,
        "skipped": skipped,
        "tests": tests,
        "raw_log": raw_log,
        "exit_code": returncode,
    }
    if duration_sec is not None:
        result["duration_sec"] = duration_sec
    return result


def _mission_db():
    return MISSION_RUNTIME.db


@app.post("/api/mission/start")
def post_mission_start(payload: MissionStartPayload) -> Dict[str, Any]:
    try:
        snapshot = MISSION_RUNTIME.start_mission(
            request_text=payload.request_text,
            mission_id=payload.mission_id,
            initial_state=payload.initial_state,
            metadata=payload.metadata,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return {"status": "success", "result": snapshot}


@app.post("/api/mission/stop")
def post_mission_stop(payload: MissionStopPayload) -> Dict[str, Any]:
    try:
        snapshot = MISSION_RUNTIME.stop_mission(payload.mission_id, reason=payload.reason)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"mission not found: {payload.mission_id}") from None
    return {"status": "success", "result": snapshot}


@app.get("/api/mission/{mission_id}/state")
def get_mission_state(mission_id: str) -> Dict[str, Any]:
    try:
        snapshot = MISSION_RUNTIME.get_mission_state(mission_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"mission not found: {mission_id}") from None
    return {"status": "success", "result": snapshot}


@app.get("/api/mission/{mission_id}/events")
def get_mission_events(mission_id: str, limit: int = 100) -> Dict[str, Any]:
    try:
        events = MISSION_RUNTIME.get_mission_events(mission_id, limit=limit)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"mission not found: {mission_id}") from None
    return {"status": "success", "result": {"mission_id": mission_id, "events": events}}


@app.get("/api/mission/{mission_id}/protocol-trace")
def get_mission_protocol_trace(mission_id: str, limit: int = 200, include_replayed: bool = True) -> Dict[str, Any]:
    try:
        trace = MISSION_RUNTIME.get_protocol_trace(mission_id, limit=limit, include_replayed=include_replayed)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"mission not found: {mission_id}") from None
    return {"status": "success", "result": {"mission_id": mission_id, "protocol_trace": trace}}


@app.get("/api/mission")
def list_missions(limit: int = 50) -> Dict[str, Any]:
    return {"status": "success", "result": {"missions": MISSION_RUNTIME.list_missions(limit=limit)}}


@app.get("/api/mission/graph")
def get_mission_graph() -> Dict[str, Any]:
    return {
        "status": "success",
        "result": {
            "name": "mission_supervisor_state_graph",
            "nodes": _MISSION_GRAPH_NODES,
            "edges": _MISSION_GRAPH_EDGES,
        },
    }


@app.get("/api/mission/skills")
def get_mission_skills() -> Dict[str, Any]:
    return {"status": "success", "result": {"skills": list_skills()}}


@app.get("/api/mission/skills/{skill_id}")
def get_mission_skill_detail(
    skill_id: str,
    uav_id: str = "uav-1",
    route_id: str = "route-1",
    airspace_segment: str = "sector-A3",
) -> Dict[str, Any]:
    skill = get_agent_skill(skill_id)
    if not isinstance(skill, dict):
        raise HTTPException(status_code=404, detail=f"skill not found: {skill_id}")
    values = {
        "uav_id": str(uav_id or "uav-1"),
        "route_id": str(route_id or "route-1"),
        "airspace_segment": str(airspace_segment or "sector-A3"),
    }
    rendered = render_skill_plan(skill_id, values)
    return {"status": "success", "result": {"skill": skill, "render_values": values, "rendered_plan": rendered}}


@app.post("/api/mission/skills/match")
def post_mission_skills_match(payload: MissionSkillMatchPayload) -> Dict[str, Any]:
    matched = match_skill(payload.request_text)
    return {"status": "success", "result": {"matched_skill": matched}}


@app.post("/api/mission/tests/run")
def post_mission_tests_run(payload: MissionTestsRunPayload | None = None) -> Dict[str, Any]:
    if not _TEST_SCRIPT_PATH.exists():
        raise HTTPException(status_code=500, detail=f"test script not found: {_TEST_SCRIPT_PATH}")

    timeout_sec = int((payload.timeout_sec if payload else 120))
    env = dict(os.environ)
    try:
        proc = subprocess.run(
            ["bash", str(_TEST_SCRIPT_PATH)],
            cwd=str(_REPO_ROOT),
            capture_output=True,
            text=True,
            timeout=timeout_sec,
            env=env,
        )
        raw_log = (proc.stdout or "") + (("\n" if proc.stdout and proc.stderr else "") + (proc.stderr or ""))
        result = _parse_unittest_output(raw_log, proc.returncode)
    except subprocess.TimeoutExpired as e:
        partial = ((e.stdout or "") if isinstance(e.stdout, str) else "") + ("\n" if e.stdout and e.stderr else "") + (
            (e.stderr or "") if isinstance(e.stderr, str) else ""
        )
        result = {
            "suite": "mission_supervisor_regressions",
            "ran_at": _utc_now(),
            "ok": False,
            "total": 0,
            "passed": 0,
            "failed": 1,
            "tests": [],
            "raw_log": (partial + ("\n" if partial else "") + f"Timed out after {timeout_sec}s").strip(),
            "exit_code": None,
            "error": f"timeout after {timeout_sec}s",
        }

    db = _mission_db()
    sync = db.record_action("mission_tests_run", payload={"timeout_sec": timeout_sec}, result=result, entity_id="mission-tests")
    db.set_state(_TEST_LATEST_KEY, result)
    return {"status": "success", "result": result, "sync": sync}


@app.get("/api/mission/tests/latest")
def get_mission_tests_latest() -> Dict[str, Any]:
    db = _mission_db()
    latest = db.get_state(_TEST_LATEST_KEY)
    return {"status": "success", "result": latest, "sync": db.get_sync()}
