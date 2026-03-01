import React, { useEffect, useMemo, useRef, useState } from "react";

type JsonRecord = Record<string, unknown>;

type GraphNode = {
  id: string;
  label: string;
  stage?: string;
};

type GraphEdge = {
  from: string;
  to: string;
  condition?: string;
};

type SkillSummary = {
  skill_id: string;
  name?: string;
  description?: string;
  domain_hint?: string;
  triggers?: string[];
};

type SkillStep = {
  step_id?: string;
  domain?: string;
  op?: string;
  params?: JsonRecord;
  resource_keys?: string[];
  requires_approvals?: string[];
  rollback?: JsonRecord;
};

type Lane = {
  key: string;
  label: string;
  x: number;
  w: number;
};

type BoxNode = {
  id: string;
  label: string;
  x: number;
  y: number;
  w: number;
  h: number;
  laneKey: string;
  stepIndex?: number;
};

type WorkflowLayout = {
  width: number;
  height: number;
  lanes: Lane[];
  nodes: BoxNode[];
  nodeById: Record<string, BoxNode>;
  laneIndex: Record<string, number>;
  endNode: BoxNode;
};

type SkillLayout = {
  width: number;
  height: number;
  lanes: Lane[];
  nodes: BoxNode[];
  rollbackNodes: BoxNode[];
};

type DiagramView = {
  zoom: number;
  panX: number;
  panY: number;
};

type DragState = {
  pointerId: number;
  startX: number;
  startY: number;
  originPanX: number;
  originPanY: number;
  moved: boolean;
};

function isObject(x: unknown): x is JsonRecord {
  return typeof x === "object" && x !== null;
}

function asRecord(x: unknown): JsonRecord | null {
  return isObject(x) ? x : null;
}

function asArrayRecords(x: unknown): JsonRecord[] {
  return Array.isArray(x) ? x.filter(isObject).map((rec) => ({ ...rec })) : [];
}

function normalizeBaseUrl(url: string): string {
  return url.trim().replace(/\/+$/, "");
}

const cardStyle: React.CSSProperties = {
  background: "#fff",
  border: "1px solid #eaecf0",
  borderRadius: 14,
  padding: 12,
  boxShadow: "0 1px 2px rgba(16,24,40,0.04)",
};

const inputStyle: React.CSSProperties = {
  width: "100%",
  borderRadius: 8,
  border: "1px solid #d0d5dd",
  padding: "8px 10px",
  fontSize: 13,
};

const controlButtonStyle: React.CSSProperties = {
  borderRadius: 8,
  border: "1px solid #d0d5dd",
  background: "#fff",
  color: "#344054",
  padding: "5px 8px",
  fontSize: 12,
  fontWeight: 700,
  cursor: "pointer",
};

function clamp(num: number, min: number, max: number): number {
  if (num < min) return min;
  if (num > max) return max;
  return num;
}

const STAGE_COLORS: Record<string, { bg: string; fg: string; bd: string }> = {
  intake: { bg: "#eef4ff", fg: "#155eef", bd: "#b2ccff" },
  context: { bg: "#ecfdf3", fg: "#027a48", bd: "#abefc6" },
  planning: { bg: "#fff7e6", fg: "#b54708", bd: "#fedf89" },
  guardrail: { bg: "#f9f5ff", fg: "#6941c6", bd: "#d9d6fe" },
  execution: { bg: "#f0f9ff", fg: "#026aa2", bd: "#b9e6fe" },
  recovery: { bg: "#fef3f2", fg: "#b42318", bd: "#fecdca" },
  finalize: { bg: "#f2f4f7", fg: "#344054", bd: "#d0d5dd" },
  end: { bg: "#f2f4f7", fg: "#344054", bd: "#d0d5dd" },
};

function stageTone(stageRaw: string | undefined): { bg: string; fg: string; bd: string } {
  const stage = String(stageRaw ?? "unknown").toLowerCase();
  return STAGE_COLORS[stage] ?? { bg: "#f2f4f7", fg: "#475467", bd: "#d0d5dd" };
}

function buildWorkflowLayout(nodes: GraphNode[]): WorkflowLayout {
  const stageOrder = ["intake", "context", "planning", "guardrail", "execution", "recovery", "finalize", "end"];
  const laneLabel: Record<string, string> = {
    intake: "Intake",
    context: "Context",
    planning: "Planning",
    guardrail: "Guardrail",
    execution: "Execution",
    recovery: "Recovery",
    finalize: "Finalize",
    end: "End",
  };
  const laneW = 210;
  const laneGap = 24;
  const left = 18;
  const right = 18;
  const top = 62;
  const nodeW = 172;
  const nodeH = 58;
  const rowGap = 24;

  const buckets: Record<string, GraphNode[]> = {};
  for (const k of stageOrder) buckets[k] = [];

  for (const n of nodes) {
    const s = String(n.stage ?? "execution").toLowerCase();
    const key = stageOrder.includes(s) && s !== "end" ? s : "execution";
    buckets[key].push(n);
  }

  const lanes: Lane[] = stageOrder.map((k, idx) => ({
    key: k,
    label: laneLabel[k] ?? k,
    x: left + idx * (laneW + laneGap),
    w: laneW,
  }));
  const laneIndex: Record<string, number> = {};
  for (let i = 0; i < lanes.length; i += 1) laneIndex[lanes[i].key] = i;

  const maxRows = Math.max(1, ...stageOrder.filter((k) => k !== "end").map((k) => buckets[k].length));
  const nodesOut: BoxNode[] = [];
  const nodeById: Record<string, BoxNode> = {};

  for (const lane of lanes) {
    if (lane.key === "end") continue;
    const rows = buckets[lane.key] ?? [];
    rows.forEach((row, idx) => {
      const x = lane.x + (laneW - nodeW) / 2;
      const y = top + idx * (nodeH + rowGap);
      const box: BoxNode = {
        id: row.id,
        label: row.label || row.id,
        x,
        y,
        w: nodeW,
        h: nodeH,
        laneKey: lane.key,
      };
      nodesOut.push(box);
      nodeById[box.id] = box;
    });
  }

  const release = nodeById.release_locks;
  const endLane = lanes[lanes.length - 1];
  const endY = release ? release.y : top;
  const endNode: BoxNode = {
    id: "END",
    label: "END",
    x: endLane.x + (endLane.w - nodeW) / 2,
    y: endY,
    w: nodeW,
    h: nodeH,
    laneKey: "end",
  };

  const width = left + lanes.length * laneW + (lanes.length - 1) * laneGap + right;
  const height = top + maxRows * (nodeH + rowGap) + 20;

  return { width, height, lanes, nodes: nodesOut, nodeById, laneIndex, endNode };
}

function buildSkillLayout(steps: SkillStep[]): SkillLayout {
  const domainOrder: string[] = [];
  for (const s of steps) {
    const d = String(s.domain || "unknown").toLowerCase();
    if (!domainOrder.includes(d)) domainOrder.push(d);
  }
  if (domainOrder.length === 0) domainOrder.push("none");

  const laneW = 220;
  const laneGap = 22;
  const left = 18;
  const right = 18;
  const top = 62;
  const nodeW = 180;
  const nodeH = 54;
  const rowGap = 24;

  const lanes: Lane[] = domainOrder.map((d, idx) => ({
    key: d,
    label: d.toUpperCase(),
    x: left + idx * (laneW + laneGap),
    w: laneW,
  }));

  const nodes: BoxNode[] = [];
  const rollbackNodes: BoxNode[] = [];

  steps.forEach((s, idx) => {
    const d = String(s.domain || "unknown").toLowerCase();
    const lane = lanes.find((l) => l.key === d) ?? lanes[0];
    const x = lane.x + (lane.w - nodeW) / 2;
    const y = top + idx * (nodeH + rowGap);
    const node: BoxNode = {
      id: s.step_id || `step-${idx + 1}`,
      label: `${s.step_id || `step-${idx + 1}`} | ${String(s.op || "op")}`,
      x,
      y,
      w: nodeW,
      h: nodeH,
      laneKey: lane.key,
    };
    nodes.push(node);

    if (isObject(s.rollback)) {
      const op = String((s.rollback as JsonRecord).op || "rollback");
      rollbackNodes.push({
        id: `rb-${idx}`,
        label: `rollback: ${op}`,
        x: node.x + node.w + 20,
        y: node.y + 7,
        w: 136,
        h: 40,
        laneKey: lane.key,
        stepIndex: idx,
      });
    }
  });

  const maxRows = Math.max(1, nodes.length);
  const rollbackExtra = rollbackNodes.length > 0 ? 166 : 0;
  const width = left + lanes.length * laneW + (lanes.length - 1) * laneGap + right + rollbackExtra;
  const height = top + maxRows * (nodeH + rowGap) + 20;
  return { width, height, lanes, nodes, rollbackNodes };
}

function routeEdge(from: BoxNode, to: BoxNode, fromLaneIdx: number, toLaneIdx: number): { d: string; labelX: number; labelY: number; loop: boolean } {
  const sx = from.x + from.w;
  const sy = from.y + from.h / 2;
  const tx = to.x;
  const ty = to.y + to.h / 2;
  const forward = toLaneIdx >= fromLaneIdx;

  if (forward && Math.abs(ty - sy) < 8) {
    const c1x = sx + 44;
    const c2x = tx - 44;
    return {
      d: `M ${sx} ${sy} C ${c1x} ${sy}, ${c2x} ${ty}, ${tx} ${ty}`,
      labelX: (sx + tx) / 2,
      labelY: sy - 8,
      loop: false,
    };
  }

  if (toLaneIdx === fromLaneIdx) {
    const bendX = sx + 28;
    const upper = ty < sy;
    const bendY1 = upper ? sy - 30 : sy + 30;
    const bendY2 = upper ? ty + 30 : ty - 30;
    return {
      d: `M ${sx} ${sy} C ${bendX} ${sy}, ${bendX} ${bendY1}, ${bendX} ${(sy + ty) / 2} C ${bendX} ${bendY2}, ${tx - 24} ${ty}, ${tx} ${ty}`,
      labelX: bendX + 8,
      labelY: (sy + ty) / 2 - 8,
      loop: true,
    };
  }

  if (toLaneIdx < fromLaneIdx) {
    const lift = Math.min(sy, ty) - (46 + (fromLaneIdx - toLaneIdx) * 12);
    const c1x = sx + 42;
    const c2x = tx - 42;
    return {
      d: `M ${sx} ${sy} C ${c1x} ${sy}, ${c1x} ${lift}, ${(sx + tx) / 2} ${lift} C ${c2x} ${lift}, ${c2x} ${ty}, ${tx} ${ty}`,
      labelX: (sx + tx) / 2,
      labelY: lift - 6,
      loop: true,
    };
  }

  return {
    d: `M ${sx} ${sy} C ${sx + 44} ${sy}, ${tx - 44} ${ty}, ${tx} ${ty}`,
    labelX: (sx + tx) / 2,
    labelY: (sy + ty) / 2 - 8,
    loop: false,
  };
}

export function AgentSkillGraphPage() {
  const [apiBase, setApiBase] = useState("http://127.0.0.1:8023");
  const [uavId, setUavId] = useState("uav-1");
  const [routeId, setRouteId] = useState("route-1");
  const [airspaceSegment, setAirspaceSegment] = useState("sector-A3");

  const [nodes, setNodes] = useState<GraphNode[]>([]);
  const [edges, setEdges] = useState<GraphEdge[]>([]);
  const [skills, setSkills] = useState<SkillSummary[]>([]);
  const [selectedSkillId, setSelectedSkillId] = useState("");
  const [skillDetail, setSkillDetail] = useState<JsonRecord | null>(null);
  const [renderedPlan, setRenderedPlan] = useState<SkillStep[]>([]);
  const [matchRequest, setMatchRequest] = useState("run dss conflict and subscription checks");
  const [matchedSkill, setMatchedSkill] = useState<JsonRecord | null>(null);
  const [busy, setBusy] = useState(false);
  const [statusMsg, setStatusMsg] = useState("Graph view ready");
  const [errorMsg, setErrorMsg] = useState("");
  const [workflowView, setWorkflowView] = useState<DiagramView>({ zoom: 1, panX: 0, panY: 0 });
  const [skillView, setSkillView] = useState<DiagramView>({ zoom: 1, panX: 0, panY: 0 });
  const [selectedWorkflowNodeId, setSelectedWorkflowNodeId] = useState<string | null>(null);
  const [selectedSkillNodeId, setSelectedSkillNodeId] = useState<string | null>(null);

  const workflowDragRef = useRef<DragState | null>(null);
  const skillDragRef = useRef<DragState | null>(null);
  const workflowSuppressClickRef = useRef(false);
  const skillSuppressClickRef = useRef(false);

  const workflowLayout = useMemo(() => buildWorkflowLayout(nodes), [nodes]);
  const skillLayout = useMemo(() => buildSkillLayout(renderedPlan), [renderedPlan]);

  const workflowTrace = useMemo(() => {
    const nodeIds = new Set<string>();
    const edgeIds = new Set<string>();
    if (!selectedWorkflowNodeId) return { nodeIds, edgeIds };

    const available = new Set<string>(["END", ...nodes.map((n) => n.id)]);
    if (!available.has(selectedWorkflowNodeId)) return { nodeIds, edgeIds };

    const adjacency = new Map<string, Array<{ to: string; key: string }>>();
    edges.forEach((edge, idx) => {
      const key = `${edge.from}-${edge.to}-${idx}`;
      const next = adjacency.get(edge.from) ?? [];
      next.push({ to: edge.to, key });
      adjacency.set(edge.from, next);
    });

    const queue: string[] = [selectedWorkflowNodeId];
    const seen = new Set<string>([selectedWorkflowNodeId]);
    nodeIds.add(selectedWorkflowNodeId);

    while (queue.length > 0) {
      const current = queue.shift() as string;
      const outs = adjacency.get(current) ?? [];
      for (const out of outs) {
        edgeIds.add(out.key);
        if (!seen.has(out.to)) {
          seen.add(out.to);
          nodeIds.add(out.to);
          queue.push(out.to);
        }
      }
    }

    return { nodeIds, edgeIds };
  }, [edges, nodes, selectedWorkflowNodeId]);

  const skillTrace = useMemo(() => {
    const nodeIds = new Set<string>();
    const seqEdgeIds = new Set<string>();
    const rollbackNodeIds = new Set<string>();
    const rollbackEdgeIds = new Set<string>();
    if (!selectedSkillNodeId) return { nodeIds, seqEdgeIds, rollbackNodeIds, rollbackEdgeIds };

    const startIdx = skillLayout.nodes.findIndex((n) => n.id === selectedSkillNodeId);
    if (startIdx < 0) return { nodeIds, seqEdgeIds, rollbackNodeIds, rollbackEdgeIds };

    for (let i = startIdx; i < skillLayout.nodes.length; i += 1) {
      nodeIds.add(skillLayout.nodes[i].id);
      if (i < skillLayout.nodes.length - 1) {
        const from = skillLayout.nodes[i];
        const to = skillLayout.nodes[i + 1];
        seqEdgeIds.add(`seq-${from.id}-${to.id}`);
      }
    }

    for (const rb of skillLayout.rollbackNodes) {
      const sourceIdx = typeof rb.stepIndex === "number" ? rb.stepIndex : -1;
      if (sourceIdx >= startIdx) {
        rollbackNodeIds.add(rb.id);
        rollbackEdgeIds.add(`rb-edge-${rb.id}`);
      }
    }

    return { nodeIds, seqEdgeIds, rollbackNodeIds, rollbackEdgeIds };
  }, [selectedSkillNodeId, skillLayout.nodes, skillLayout.rollbackNodes]);

  const loadGraphAndSkills = async () => {
    setBusy(true);
    setErrorMsg("");
    setStatusMsg("");
    try {
      const base = normalizeBaseUrl(apiBase);
      const [graphRes, skillsRes] = await Promise.all([fetch(`${base}/api/mission/graph`), fetch(`${base}/api/mission/skills`)]);
      const graphData = (await graphRes.json()) as unknown;
      const skillsData = (await skillsRes.json()) as unknown;

      if (!graphRes.ok) throw new Error(String(asRecord(graphData)?.detail ?? "Graph request failed"));
      if (!skillsRes.ok) throw new Error(String(asRecord(skillsData)?.detail ?? "Skills request failed"));

      const graphResult = asRecord(asRecord(graphData)?.result);
      const nodeRows = asArrayRecords(graphResult?.nodes).map((row) => ({
        id: String(row.id ?? ""),
        label: String(row.label ?? row.id ?? ""),
        stage: typeof row.stage === "string" ? row.stage : undefined,
      }));
      const edgeRows = asArrayRecords(graphResult?.edges).map((row) => ({
        from: String(row.from ?? ""),
        to: String(row.to ?? ""),
        condition: typeof row.condition === "string" ? row.condition : undefined,
      }));
      setNodes(nodeRows.filter((n) => n.id));
      setEdges(edgeRows.filter((e) => e.from && e.to));

      const skillsResult = asRecord(asRecord(skillsData)?.result);
      const skillRows = asArrayRecords(skillsResult?.skills).map((row) => ({
        skill_id: String(row.skill_id ?? ""),
        name: typeof row.name === "string" ? row.name : undefined,
        description: typeof row.description === "string" ? row.description : undefined,
        domain_hint: typeof row.domain_hint === "string" ? row.domain_hint : undefined,
        triggers: Array.isArray(row.triggers) ? row.triggers.map(String) : [],
      }));
      setSkills(skillRows.filter((s) => s.skill_id));
      if (!selectedSkillId && skillRows.length > 0) setSelectedSkillId(skillRows[0].skill_id);

      setStatusMsg("Loaded graph and skills");
    } catch (e) {
      setErrorMsg(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const loadSkillDetail = async (skillId: string) => {
    if (!skillId) return;
    setBusy(true);
    setErrorMsg("");
    try {
      const base = normalizeBaseUrl(apiBase);
      const q = new URLSearchParams();
      q.set("uav_id", uavId);
      q.set("route_id", routeId);
      q.set("airspace_segment", airspaceSegment);
      const res = await fetch(`${base}/api/mission/skills/${encodeURIComponent(skillId)}?${q.toString()}`);
      const data = (await res.json()) as unknown;
      if (!res.ok) throw new Error(String(asRecord(data)?.detail ?? "Skill detail request failed"));
      const result = asRecord(asRecord(data)?.result);
      const detailSkill = asRecord(result?.skill);
      const rendered = asArrayRecords(result?.rendered_plan).map((row) => ({
        step_id: typeof row.step_id === "string" ? row.step_id : undefined,
        domain: typeof row.domain === "string" ? row.domain : undefined,
        op: typeof row.op === "string" ? row.op : undefined,
        params: asRecord(row.params) ?? undefined,
        resource_keys: Array.isArray(row.resource_keys) ? row.resource_keys.map(String) : undefined,
        requires_approvals: Array.isArray(row.requires_approvals) ? row.requires_approvals.map(String) : undefined,
        rollback: asRecord(row.rollback) ?? undefined,
      }));
      setSkillDetail(detailSkill);
      setRenderedPlan(rendered);
      setStatusMsg(`Loaded skill procedure: ${skillId}`);
    } catch (e) {
      setErrorMsg(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const matchSkillFromText = async () => {
    setBusy(true);
    setErrorMsg("");
    try {
      const res = await fetch(`${normalizeBaseUrl(apiBase)}/api/mission/skills/match`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ request_text: matchRequest }),
      });
      const data = (await res.json()) as unknown;
      if (!res.ok) throw new Error(String(asRecord(data)?.detail ?? "Match request failed"));
      const matched = asRecord(asRecord(data)?.result)?.matched_skill;
      const rec = asRecord(matched);
      setMatchedSkill(rec);
      const sid = typeof rec?.skill_id === "string" ? rec.skill_id : "";
      if (sid) {
        setSelectedSkillId(sid);
        await loadSkillDetail(sid);
      }
      setStatusMsg(sid ? `Matched skill: ${sid}` : "No skill matched");
    } catch (e) {
      setErrorMsg(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  useEffect(() => {
    void loadGraphAndSkills();
  }, [apiBase]);

  useEffect(() => {
    if (!selectedSkillId) return;
    void loadSkillDetail(selectedSkillId);
  }, [selectedSkillId, uavId, routeId, airspaceSegment]);

  useEffect(() => {
    if (!selectedWorkflowNodeId) return;
    const exists = selectedWorkflowNodeId === "END" || nodes.some((n) => n.id === selectedWorkflowNodeId);
    if (!exists) setSelectedWorkflowNodeId(null);
  }, [nodes, selectedWorkflowNodeId]);

  useEffect(() => {
    setSelectedSkillNodeId(null);
  }, [selectedSkillId]);

  const zoomDiagram = (
    setter: React.Dispatch<React.SetStateAction<DiagramView>>,
    direction: 1 | -1,
  ) => {
    setter((v) => ({
      ...v,
      zoom: clamp(Math.round((v.zoom + direction * 0.15) * 100) / 100, 0.45, 2.6),
    }));
  };

  const resetDiagramView = (setter: React.Dispatch<React.SetStateAction<DiagramView>>) => {
    setter({ zoom: 1, panX: 0, panY: 0 });
  };

  const onWorkflowPointerDown = (e: React.PointerEvent<SVGSVGElement>) => {
    if (e.button !== 0) return;
    workflowSuppressClickRef.current = false;
    workflowDragRef.current = {
      pointerId: e.pointerId,
      startX: e.clientX,
      startY: e.clientY,
      originPanX: workflowView.panX,
      originPanY: workflowView.panY,
      moved: false,
    };
    e.currentTarget.setPointerCapture(e.pointerId);
  };

  const onWorkflowPointerMove = (e: React.PointerEvent<SVGSVGElement>) => {
    const drag = workflowDragRef.current;
    if (!drag || drag.pointerId !== e.pointerId) return;
    const dx = e.clientX - drag.startX;
    const dy = e.clientY - drag.startY;
    if (Math.abs(dx) + Math.abs(dy) > 3) drag.moved = true;
    setWorkflowView((v) => ({ ...v, panX: drag.originPanX + dx, panY: drag.originPanY + dy }));
  };

  const onWorkflowPointerEnd = (e: React.PointerEvent<SVGSVGElement>) => {
    const drag = workflowDragRef.current;
    if (!drag || drag.pointerId !== e.pointerId) return;
    workflowSuppressClickRef.current = drag.moved;
    if (e.currentTarget.hasPointerCapture(e.pointerId)) e.currentTarget.releasePointerCapture(e.pointerId);
    workflowDragRef.current = null;
  };

  const onSkillPointerDown = (e: React.PointerEvent<SVGSVGElement>) => {
    if (e.button !== 0) return;
    skillSuppressClickRef.current = false;
    skillDragRef.current = {
      pointerId: e.pointerId,
      startX: e.clientX,
      startY: e.clientY,
      originPanX: skillView.panX,
      originPanY: skillView.panY,
      moved: false,
    };
    e.currentTarget.setPointerCapture(e.pointerId);
  };

  const onSkillPointerMove = (e: React.PointerEvent<SVGSVGElement>) => {
    const drag = skillDragRef.current;
    if (!drag || drag.pointerId !== e.pointerId) return;
    const dx = e.clientX - drag.startX;
    const dy = e.clientY - drag.startY;
    if (Math.abs(dx) + Math.abs(dy) > 3) drag.moved = true;
    setSkillView((v) => ({ ...v, panX: drag.originPanX + dx, panY: drag.originPanY + dy }));
  };

  const onSkillPointerEnd = (e: React.PointerEvent<SVGSVGElement>) => {
    const drag = skillDragRef.current;
    if (!drag || drag.pointerId !== e.pointerId) return;
    skillSuppressClickRef.current = drag.moved;
    if (e.currentTarget.hasPointerCapture(e.pointerId)) e.currentTarget.releasePointerCapture(e.pointerId);
    skillDragRef.current = null;
  };

  return (
    <div style={{ maxWidth: 1360, margin: "0 auto", padding: 16, display: "grid", gap: 14 }}>
      <div style={{ ...cardStyle, display: "grid", gap: 10 }}>
        <div style={{ display: "flex", justifyContent: "space-between", gap: 12, alignItems: "center", flexWrap: "wrap" }}>
          <div>
            <div style={{ fontSize: 18, fontWeight: 700, color: "#101828" }}>Agent + Skill Procedure Graph</div>
            <div style={{ fontSize: 12, color: "#667085" }}>SVG lane diagrams with arrows and loop paths</div>
          </div>
          <button
            type="button"
            onClick={() => void loadGraphAndSkills()}
            disabled={busy}
            style={{ borderRadius: 10, border: "1px solid #d0d5dd", background: "#fff", color: "#344054", padding: "8px 12px", fontWeight: 600, cursor: "pointer" }}
          >
            Refresh
          </button>
        </div>
        <div style={{ display: "grid", gridTemplateColumns: "2fr 1fr 1fr 1fr", gap: 8 }}>
          <label style={{ fontSize: 12, color: "#344054" }}>
            Mission API Base
            <input style={inputStyle} value={apiBase} onChange={(e) => setApiBase(e.target.value)} />
          </label>
          <label style={{ fontSize: 12, color: "#344054" }}>
            UAV ID
            <input style={inputStyle} value={uavId} onChange={(e) => setUavId(e.target.value)} />
          </label>
          <label style={{ fontSize: 12, color: "#344054" }}>
            Route ID
            <input style={inputStyle} value={routeId} onChange={(e) => setRouteId(e.target.value)} />
          </label>
          <label style={{ fontSize: 12, color: "#344054" }}>
            Airspace Segment
            <input style={inputStyle} value={airspaceSegment} onChange={(e) => setAirspaceSegment(e.target.value)} />
          </label>
        </div>
        <div style={{ display: "grid", gridTemplateColumns: "1fr auto", gap: 8, alignItems: "end" }}>
          <label style={{ fontSize: 12, color: "#344054" }}>
            Match Skill From Request Text
            <input style={inputStyle} value={matchRequest} onChange={(e) => setMatchRequest(e.target.value)} />
          </label>
          <button
            type="button"
            onClick={() => void matchSkillFromText()}
            disabled={busy || !matchRequest.trim()}
            style={{ borderRadius: 10, border: "1px solid #1570ef", background: "#1570ef", color: "#fff", padding: "8px 12px", fontWeight: 700, cursor: "pointer" }}
          >
            Match
          </button>
        </div>
        <div style={{ fontSize: 12, color: errorMsg ? "#b42318" : "#475467" }}>{errorMsg || statusMsg}</div>
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "1.25fr 1fr", gap: 14, alignItems: "start" }}>
        <div style={{ ...cardStyle, display: "grid", gap: 10 }}>
          <div style={{ display: "flex", justifyContent: "space-between", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
            <div>
              <div style={{ fontSize: 15, fontWeight: 700, color: "#101828" }}>Mission Supervisor Workflow (Lane Diagram)</div>
              <div style={{ fontSize: 12, color: "#667085" }}>Drag to pan, zoom in/out, click a node to trace downstream path.</div>
            </div>
            <div style={{ display: "flex", gap: 6, alignItems: "center", flexWrap: "wrap" }}>
              <button type="button" style={controlButtonStyle} onClick={() => zoomDiagram(setWorkflowView, -1)} disabled={workflowView.zoom <= 0.46}>
                -
              </button>
              <div style={{ minWidth: 52, textAlign: "center", fontSize: 12, color: "#344054", fontWeight: 700 }}>
                {Math.round(workflowView.zoom * 100)}%
              </div>
              <button type="button" style={controlButtonStyle} onClick={() => zoomDiagram(setWorkflowView, 1)} disabled={workflowView.zoom >= 2.59}>
                +
              </button>
              <button type="button" style={controlButtonStyle} onClick={() => resetDiagramView(setWorkflowView)}>
                Reset
              </button>
              {selectedWorkflowNodeId ? (
                <button type="button" style={controlButtonStyle} onClick={() => setSelectedWorkflowNodeId(null)}>
                  Clear Trace
                </button>
              ) : null}
            </div>
          </div>
          {selectedWorkflowNodeId ? (
            <div style={{ fontSize: 12, color: "#155eef", fontWeight: 700 }}>Tracing from node: {selectedWorkflowNodeId}</div>
          ) : null}
          <div style={{ overflowX: "auto", border: "1px solid #eaecf0", borderRadius: 10, background: "#fcfcfd" }}>
            <svg
              width={workflowLayout.width}
              height={workflowLayout.height}
              viewBox={`0 0 ${workflowLayout.width} ${workflowLayout.height}`}
              style={{ display: "block", cursor: workflowDragRef.current ? "grabbing" : "grab", touchAction: "none", userSelect: "none" }}
              onPointerDown={onWorkflowPointerDown}
              onPointerMove={onWorkflowPointerMove}
              onPointerUp={onWorkflowPointerEnd}
              onPointerCancel={onWorkflowPointerEnd}
              onClick={() => {
                if (workflowSuppressClickRef.current) {
                  workflowSuppressClickRef.current = false;
                  return;
                }
                setSelectedWorkflowNodeId(null);
              }}
            >
              <defs>
                <marker id="wf-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
                  <path d="M 0 0 L 10 5 L 0 10 z" fill="#667085" />
                </marker>
                <marker id="wf-arrow-trace" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
                  <path d="M 0 0 L 10 5 L 0 10 z" fill="#155eef" />
                </marker>
                <marker id="wf-arrow-loop" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
                  <path d="M 0 0 L 10 5 L 0 10 z" fill="#b54708" />
                </marker>
                <marker id="wf-arrow-muted" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
                  <path d="M 0 0 L 10 5 L 0 10 z" fill="#d0d5dd" />
                </marker>
              </defs>
              <g transform={`translate(${workflowView.panX} ${workflowView.panY}) scale(${workflowView.zoom})`}>
                {workflowLayout.lanes.map((lane) => {
                  const tone = stageTone(lane.key);
                  return (
                    <g key={lane.key}>
                      <rect x={lane.x} y={10} width={lane.w} height={workflowLayout.height - 20} rx={10} fill={tone.bg} opacity={0.35} stroke={tone.bd} />
                      <text x={lane.x + lane.w / 2} y={30} textAnchor="middle" fontSize="11" fontWeight="700" fill={tone.fg}>
                        {lane.label.toUpperCase()}
                      </text>
                    </g>
                  );
                })}

                {edges.map((edge, idx) => {
                  const edgeKey = `${edge.from}-${edge.to}-${idx}`;
                  const from = workflowLayout.nodeById[edge.from];
                  const to = edge.to === "END" ? workflowLayout.endNode : workflowLayout.nodeById[edge.to];
                  if (!from || !to) return null;
                  const fromLaneIdx = workflowLayout.laneIndex[from.laneKey] ?? 0;
                  const toLaneIdx = workflowLayout.laneIndex[to.laneKey] ?? fromLaneIdx;
                  const routed = routeEdge(from, to, fromLaneIdx, toLaneIdx);
                  const traced = !selectedWorkflowNodeId || workflowTrace.edgeIds.has(edgeKey);
                  const color = selectedWorkflowNodeId
                    ? traced
                      ? routed.loop
                        ? "#b54708"
                        : "#155eef"
                      : "#d0d5dd"
                    : routed.loop
                      ? "#b54708"
                      : "#667085";
                  const markerId = routed.loop
                    ? "wf-arrow-loop"
                    : selectedWorkflowNodeId
                      ? traced
                        ? "wf-arrow-trace"
                        : "wf-arrow-muted"
                      : "wf-arrow";
                  return (
                    <g key={edgeKey}>
                      <path d={routed.d} fill="none" stroke={color} strokeWidth={selectedWorkflowNodeId ? (traced ? 2.8 : 1.2) : 2} markerEnd={`url(#${markerId})`} />
                      {edge.condition ? (
                        <text x={routed.labelX} y={routed.labelY} textAnchor="middle" fontSize="10" fill={selectedWorkflowNodeId && !traced ? "#98a2b3" : "#b54708"} fontWeight="700">
                          {edge.condition}
                        </text>
                      ) : null}
                    </g>
                  );
                })}

                {[...workflowLayout.nodes, workflowLayout.endNode].map((node) => {
                  const selected = node.id === selectedWorkflowNodeId;
                  const traced = !selectedWorkflowNodeId || workflowTrace.nodeIds.has(node.id);
                  const stroke = selected ? "#155eef" : traced ? "#84adff" : "#d0d5dd";
                  const fill = selected ? "#eef4ff" : traced ? "#fff" : "#f8fafc";
                  return (
                    <g
                      key={node.id}
                      onClick={(e) => {
                        e.stopPropagation();
                        if (workflowSuppressClickRef.current) {
                          workflowSuppressClickRef.current = false;
                          return;
                        }
                        setSelectedWorkflowNodeId((cur) => (cur === node.id ? null : node.id));
                      }}
                      style={{ cursor: "pointer" }}
                    >
                      <rect x={node.x} y={node.y} width={node.w} height={node.h} rx={10} fill={fill} stroke={stroke} strokeWidth={selected ? 2.2 : 1.2} />
                      <text x={node.x + 8} y={node.y + 22} fontSize="12" fontWeight="700" fill={selectedWorkflowNodeId && !traced ? "#98a2b3" : "#101828"}>
                        {node.label.length > 24 ? `${node.label.slice(0, 24)}...` : node.label}
                      </text>
                      <text x={node.x + 8} y={node.y + 40} fontSize="10" fill={selectedWorkflowNodeId && !traced ? "#98a2b3" : "#667085"}>
                        {node.id}
                      </text>
                    </g>
                  );
                })}
              </g>
            </svg>
          </div>
        </div>

        <div style={{ ...cardStyle, display: "grid", gap: 10 }}>
          <div style={{ display: "flex", justifyContent: "space-between", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
            <div>
              <div style={{ fontSize: 15, fontWeight: 700, color: "#101828" }}>Skill Procedure (Lane Diagram)</div>
              <div style={{ fontSize: 12, color: "#667085" }}>Drag to pan, zoom in/out, click a step to trace remaining flow.</div>
            </div>
            <div style={{ display: "flex", gap: 6, alignItems: "center", flexWrap: "wrap" }}>
              <button type="button" style={controlButtonStyle} onClick={() => zoomDiagram(setSkillView, -1)} disabled={skillView.zoom <= 0.46}>
                -
              </button>
              <div style={{ minWidth: 52, textAlign: "center", fontSize: 12, color: "#344054", fontWeight: 700 }}>
                {Math.round(skillView.zoom * 100)}%
              </div>
              <button type="button" style={controlButtonStyle} onClick={() => zoomDiagram(setSkillView, 1)} disabled={skillView.zoom >= 2.59}>
                +
              </button>
              <button type="button" style={controlButtonStyle} onClick={() => resetDiagramView(setSkillView)}>
                Reset
              </button>
              {selectedSkillNodeId ? (
                <button type="button" style={controlButtonStyle} onClick={() => setSelectedSkillNodeId(null)}>
                  Clear Trace
                </button>
              ) : null}
            </div>
          </div>
          <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
            {skills.map((s) => {
              const active = s.skill_id === selectedSkillId;
              return (
                <button
                  key={s.skill_id}
                  type="button"
                  onClick={() => setSelectedSkillId(s.skill_id)}
                  style={{
                    borderRadius: 999,
                    border: active ? "1px solid #155eef" : "1px solid #d0d5dd",
                    background: active ? "#eef4ff" : "#fff",
                    color: active ? "#155eef" : "#344054",
                    padding: "6px 10px",
                    fontSize: 12,
                    fontWeight: 700,
                    cursor: "pointer",
                  }}
                >
                  {s.name || s.skill_id}
                </button>
              );
            })}
          </div>

          {matchedSkill ? (
            <div style={{ fontSize: 12, color: "#344054", border: "1px solid #d0d5dd", background: "#f8fafc", borderRadius: 8, padding: "6px 8px" }}>
              Matched skill: <strong>{String(matchedSkill.name ?? matchedSkill.skill_id ?? "-")}</strong>
              {Array.isArray(matchedSkill.triggers_matched) && matchedSkill.triggers_matched.length > 0 ? (
                <span> | triggers: {(matchedSkill.triggers_matched as unknown[]).map(String).join(", ")}</span>
              ) : null}
            </div>
          ) : null}
          {selectedSkillNodeId ? (
            <div style={{ fontSize: 12, color: "#155eef", fontWeight: 700 }}>Tracing from step: {selectedSkillNodeId}</div>
          ) : null}

          <div style={{ overflowX: "auto", border: "1px solid #eaecf0", borderRadius: 10, background: "#fcfcfd" }}>
            <svg
              width={skillLayout.width}
              height={skillLayout.height}
              viewBox={`0 0 ${skillLayout.width} ${skillLayout.height}`}
              style={{ display: "block", cursor: skillDragRef.current ? "grabbing" : "grab", touchAction: "none", userSelect: "none" }}
              onPointerDown={onSkillPointerDown}
              onPointerMove={onSkillPointerMove}
              onPointerUp={onSkillPointerEnd}
              onPointerCancel={onSkillPointerEnd}
              onClick={() => {
                if (skillSuppressClickRef.current) {
                  skillSuppressClickRef.current = false;
                  return;
                }
                setSelectedSkillNodeId(null);
              }}
            >
              <defs>
                <marker id="sk-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
                  <path d="M 0 0 L 10 5 L 0 10 z" fill="#667085" />
                </marker>
                <marker id="sk-arrow-trace" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
                  <path d="M 0 0 L 10 5 L 0 10 z" fill="#155eef" />
                </marker>
                <marker id="sk-arrow-rb" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
                  <path d="M 0 0 L 10 5 L 0 10 z" fill="#b42318" />
                </marker>
                <marker id="sk-arrow-muted" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
                  <path d="M 0 0 L 10 5 L 0 10 z" fill="#d0d5dd" />
                </marker>
              </defs>
              <g transform={`translate(${skillView.panX} ${skillView.panY}) scale(${skillView.zoom})`}>
                {skillLayout.lanes.map((lane) => {
                  const tone = stageTone(lane.key);
                  return (
                    <g key={lane.key}>
                      <rect x={lane.x} y={10} width={lane.w} height={skillLayout.height - 20} rx={10} fill={tone.bg} opacity={0.28} stroke={tone.bd} />
                      <text x={lane.x + lane.w / 2} y={30} textAnchor="middle" fontSize="11" fontWeight="700" fill={tone.fg}>
                        {lane.label}
                      </text>
                    </g>
                  );
                })}

                {skillLayout.nodes.map((from, idx) => {
                  const to = skillLayout.nodes[idx + 1];
                  if (!to) return null;
                  const edgeKey = `seq-${from.id}-${to.id}`;
                  const fromLane = skillLayout.lanes.findIndex((l) => l.key === from.laneKey);
                  const toLane = skillLayout.lanes.findIndex((l) => l.key === to.laneKey);
                  const routed = routeEdge(from, to, fromLane < 0 ? 0 : fromLane, toLane < 0 ? 0 : toLane);
                  const traced = !selectedSkillNodeId || skillTrace.seqEdgeIds.has(edgeKey);
                  const color = selectedSkillNodeId ? (traced ? "#155eef" : "#d0d5dd") : "#667085";
                  const marker = selectedSkillNodeId ? (traced ? "url(#sk-arrow-trace)" : "url(#sk-arrow-muted)") : "url(#sk-arrow)";
                  return <path key={edgeKey} d={routed.d} fill="none" stroke={color} strokeWidth={selectedSkillNodeId ? (traced ? 2.8 : 1.2) : 2} markerEnd={marker} />;
                })}

                {skillLayout.rollbackNodes.map((rb, idx) => {
                  const sourceIdx = typeof rb.stepIndex === "number" ? rb.stepIndex : idx;
                  const from = skillLayout.nodes[sourceIdx];
                  if (!from) return null;
                  const edgeKey = `rb-edge-${rb.id}`;
                  const traced = !selectedSkillNodeId || skillTrace.rollbackEdgeIds.has(edgeKey);
                  const sx = from.x + from.w;
                  const sy = from.y + from.h / 2;
                  const tx = rb.x;
                  const ty = rb.y + rb.h / 2;
                  const d = `M ${sx} ${sy} C ${sx + 20} ${sy}, ${tx - 16} ${ty}, ${tx} ${ty}`;
                  return (
                    <path
                      key={edgeKey}
                      d={d}
                      fill="none"
                      stroke={selectedSkillNodeId ? (traced ? "#b42318" : "#fecdca") : "#b42318"}
                      strokeWidth={selectedSkillNodeId ? (traced ? 2 : 1.2) : 1.8}
                      strokeDasharray="5 4"
                      markerEnd={selectedSkillNodeId ? (traced ? "url(#sk-arrow-rb)" : "url(#sk-arrow-muted)") : "url(#sk-arrow-rb)"}
                    />
                  );
                })}

                {skillLayout.nodes.map((n) => {
                  const selected = n.id === selectedSkillNodeId;
                  const traced = !selectedSkillNodeId || skillTrace.nodeIds.has(n.id);
                  return (
                    <g
                      key={n.id}
                      onClick={(e) => {
                        e.stopPropagation();
                        if (skillSuppressClickRef.current) {
                          skillSuppressClickRef.current = false;
                          return;
                        }
                        setSelectedSkillNodeId((cur) => (cur === n.id ? null : n.id));
                      }}
                      style={{ cursor: "pointer" }}
                    >
                      <rect
                        x={n.x}
                        y={n.y}
                        width={n.w}
                        height={n.h}
                        rx={9}
                        fill={selected ? "#eef4ff" : traced ? "#fff" : "#f8fafc"}
                        stroke={selected ? "#155eef" : traced ? "#84adff" : "#d0d5dd"}
                        strokeWidth={selected ? 2.2 : 1.2}
                      />
                      <text x={n.x + 8} y={n.y + 22} fontSize="11" fontWeight="700" fill={selectedSkillNodeId && !traced ? "#98a2b3" : "#101828"}>
                        {n.label.length > 34 ? `${n.label.slice(0, 34)}...` : n.label}
                      </text>
                      <text x={n.x + 8} y={n.y + 39} fontSize="10" fill={selectedSkillNodeId && !traced ? "#98a2b3" : "#667085"}>
                        {n.laneKey}
                      </text>
                    </g>
                  );
                })}

                {skillLayout.rollbackNodes.map((n) => {
                  const traced = !selectedSkillNodeId || skillTrace.rollbackNodeIds.has(n.id);
                  return (
                    <g key={n.id}>
                      <rect x={n.x} y={n.y} width={n.w} height={n.h} rx={8} fill={traced ? "#fef3f2" : "#fff1f0"} stroke={traced ? "#fecdca" : "#fdd2ce"} />
                      <text x={n.x + 7} y={n.y + 24} fontSize="10" fontWeight="700" fill={traced ? "#b42318" : "#98a2b3"}>
                        {n.label.length > 22 ? `${n.label.slice(0, 22)}...` : n.label}
                      </text>
                    </g>
                  );
                })}
              </g>
            </svg>
          </div>

          {skillDetail ? (
            <div style={{ border: "1px solid #eaecf0", borderRadius: 8, padding: 8, background: "#fff" }}>
              <div style={{ fontSize: 14, fontWeight: 700, color: "#101828" }}>{String(skillDetail.name ?? selectedSkillId)}</div>
              <div style={{ fontSize: 12, color: "#475467", marginTop: 4 }}>{String(skillDetail.description ?? "")}</div>
              <div style={{ marginTop: 6, fontSize: 12, color: "#667085" }}>
                Domain hint: <code>{String(skillDetail.domain_hint ?? "-")}</code>
              </div>
            </div>
          ) : (
            <div style={{ fontSize: 13, color: "#667085" }}>Select a skill to view its procedure diagram.</div>
          )}
        </div>
      </div>
    </div>
  );
}
