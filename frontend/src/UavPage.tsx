import React, { useEffect, useMemo, useRef, useState } from "react";
import { MissionSyncMap, type MissionBs, type MissionCoverage, type MissionNfz, type MissionRouteOverlay, type MissionTrack } from "./MissionSyncMap";
import { bumpSharedRevision, getSharedPageState, patchSharedPageState, subscribeSharedPageState } from "./pageSync";
import { shouldAutoReplanForDssConflict } from "./missionSubmitFlow";

type WaypointAction = "transit" | "photo" | "temperature" | "hover" | "inspect";
type UavWaypoint = {
  x: number;
  y: number;
  z: number;
  action?: WaypointAction;
  _wp_origin?: "original" | "agent_inserted";
  _wp_source?: string;
  _mapped_from_original_index?: number;
  _mapped_from_wp_source?: string;
};
type EditableWaypointRow = {
  x: string;
  y: string;
  z: string;
  action: WaypointAction;
  _wp_origin?: "original" | "agent_inserted";
  _wp_source?: string;
  _mapped_from_original_index?: number;
};
type UavSimState = {
  dataSource?: Record<string, unknown>;
  uav?: Record<string, unknown>;
  flight_gate?: Record<string, unknown>;
  uav_station_state?: Record<string, unknown>;
  uav_station_controls_recent?: Array<Record<string, unknown>>;
  uav_registry_user?: Record<string, unknown>;
  uav_registry_profile?: Record<string, unknown>;
  uav_mission_defaults?: Record<string, unknown>;
  utm?: {
    weather?: Record<string, unknown>;
    no_fly_zones?: Array<Record<string, unknown>>;
    regulations?: Record<string, unknown>;
    licenses?: Record<string, unknown>;
  };
};
type CopilotMessage =
  | { id: string; role: "user"; text: string; ts: string }
  | { id: string; role: "assistant"; lines: string[]; toolTrace: Array<Record<string, unknown>>; raw: Record<string, unknown> | null; ts: string; pending?: boolean };
type AgentActionLogItem = {
  id: number;
  action: string;
  entity_id?: unknown;
  payload?: unknown;
  result?: unknown;
  created_at: string;
  agent: "uav" | "utm";
};
type BackendLogFilter = "all" | "copilot" | "utm_verify" | "flight" | "utm_config" | "live_data";
type PlannerMapClickMode = "add_wp" | "add_uav";
type PlannerPathSourceKey = "user_planned" | "agent_replanned" | "dss_replanned" | "utm_confirmed";
type MissionActionType = "photo" | "measure" | "temperature" | "inspect" | "hover";
type DynamicFieldKind = "text" | "number" | "datetime-local" | "textarea" | "checkbox" | "select";
type DynamicFieldDef = {
  key: string;
  label: string;
  kind: DynamicFieldKind;
  placeholder?: string;
  step?: string;
  rows?: number;
  options?: Array<{ value: string; label: string }>;
};
type DynamicSectionDef = {
  title: string;
  columns: string;
  fields: DynamicFieldDef[];
};
type DssMitigationSnapshot = {
  started_at: string;
  conflict_detected: boolean;
  initial_dss_status: string;
  initial_blocking_conflicts: number;
  replan_status: "success" | "failed" | "skipped";
  retry_submit_status: "success" | "failed" | "skipped";
  final_dss_status: string;
  final_blocking_conflicts: number;
  resolved: boolean;
  notes: string[];
};

function isObject(x: unknown): x is Record<string, unknown> {
  return typeof x === "object" && x !== null;
}

function asRecord(x: unknown): Record<string, unknown> | null {
  return isObject(x) ? x : null;
}

function pickDssIntentResultFromAggregate(aggregate: Record<string, unknown> | null): Record<string, unknown> | null {
  if (!aggregate) return null;
  const approvalReq = asRecord(aggregate.approval_request);
  const approvalReqResult = asRecord(approvalReq?.result);
  const approvalReqInner = asRecord(approvalReqResult?.result);
  const candidates: unknown[] = [
    approvalReq?.dss_intent_result,
    approvalReqResult?.dss_intent_result,
    approvalReqInner?.dss_intent_result,
    asRecord(aggregate.verify_from_uav)?.dss_intent_result,
    asRecord(aggregate.geofence_submit)?.dss_intent_result,
  ];
  for (const candidate of candidates) {
    const rec = asRecord(candidate);
    if (rec) return rec;
  }
  return null;
}

function formatApiErrorDetail(detail: unknown): string {
  if (typeof detail === "string") return detail;
  const rec = asRecord(detail);
  if (!rec) return String(detail ?? "Request failed");
  const code = typeof rec.error === "string" ? rec.error : null;
  const issues = Array.isArray(rec.issues) ? rec.issues.map(String).filter(Boolean) : [];
  if (issues.length) return `${code ?? "Request failed"}: ${issues.join("; ")}`;
  try {
    return JSON.stringify(rec);
  } catch {
    return code ?? "Request failed";
  }
}

function assertApiPayloadOk(data: unknown): void {
  const root = asRecord(data);
  if (!root) return;
  const status = String(root.status ?? "").trim().toLowerCase();
  if (!status || ["success", "warning", "ok"].includes(status)) return;
  const nested = asRecord(root.result);
  const detail = root.detail ?? root.error ?? nested?.detail ?? nested?.error ?? root;
  throw new Error(formatApiErrorDetail(detail));
}

function resolveEffectiveUtmRegulationsFromState(
  utmState: Record<string, unknown> | null | undefined,
  operatorLicenseId: string,
): Record<string, unknown> | null {
  const utm = asRecord(utmState);
  if (!utm) return null;
  const direct = asRecord(utm.effective_regulations ?? utm.effectiveRegulations);
  if (direct) return direct;
  const regs = asRecord(utm.regulations);
  const profiles = asRecord(utm.regulation_profiles ?? utm.regulationProfiles);
  const licenses = asRecord(utm.licenses);
  const lic = asRecord(licenses?.[operatorLicenseId]);
  const size = String(lic?.uav_size_class ?? "middle");
  const profile = asRecord(profiles?.[size]);
  if (!regs && !profile) return null;
  return { ...(regs ?? {}), ...(profile ?? {}), uav_size_class: size, operator_license_id: operatorLicenseId };
}

function summarizeBackendAction(item: AgentActionLogItem): string {
  const result = asRecord(item.result);
  if (item.action.includes("agent_chat") || item.action.includes("copilot")) {
    const resultObj = asRecord(result?.result ?? result);
    const msgs = Array.isArray(resultObj?.messages) ? (resultObj!.messages as unknown[]).map(String) : [];
    return `copilot run${msgs.length ? ` • ${msgs.slice(0, 2).join(" | ")}` : ""}`;
  }
  if (item.action.includes("verify")) {
    const resultObj = asRecord(result?.result ?? result);
    const approved = resultObj?.approved;
    const decision = asRecord(resultObj?.decision);
    const reasons = Array.isArray(decision?.reasons) ? (decision!.reasons as unknown[]).map(String).join(", ") : "";
    return `verify ${approved === true ? "approved" : approved === false ? "rejected" : "done"}${reasons ? ` • ${reasons}` : ""}`;
  }
  if (item.action.includes("replan")) return "route replan via UTM/NFZ";
  if (item.action.includes("geofence")) return "geofence check/submit";
  if (item.action.includes("approval")) return "UTM approval request/update";
  if (item.action.includes("launch") || item.action === "step" || item.action === "hold" || item.action === "resume" || item.action === "rth" || item.action === "land") {
    return item.action.replaceAll("_", " ");
  }
  if (item.action.includes("utm_") && item.action.includes("nfz")) return "UTM no-fly-zone update";
  if (item.action.includes("utm_") && item.action.includes("weather")) return "UTM weather update";
  if (item.action.includes("license")) return "license check/update";
  if (item.action === "uav_live_ingest" || item.action === "utm_live_ingest") return "live data ingested";
  if (item.action.startsWith("mission_action_")) {
    const action = item.action.replace("mission_action_", "").replaceAll("_", " ");
    return `mission action ${action}`;
  }
  return item.action.replaceAll("_", " ");
}

function normalizeMissionAction(action: string): MissionActionType {
  const v = String(action || "").trim().toLowerCase().replace(" ", "_");
  if (v === "take_photo" || v === "capture_photo") return "photo";
  if (v === "temperature") return "temperature";
  if (v === "inspect") return "inspect";
  if (v === "hover") return "hover";
  if (v === "measure") return "measure";
  return "photo";
}

function missionActionToNetworkMode(action: MissionActionType): "coverage" | "qos" | "power" {
  if (action === "photo" || action === "inspect") return "qos";
  if (action === "temperature" || action === "measure") return "coverage";
  return "power";
}

function plannerWaypointTypeAbbrev(idx: number, total: number, action: WaypointAction): { label: string; title: string; color: string } {
  if (idx === 0) return { label: "HM", title: "HOME", color: "#155eef" };
  if (idx === total - 1) return { label: "EN", title: "END", color: "#b54708" };
  if (action === "hover") return { label: "LT", title: "LOITER", color: "#7a2e0e" };
  if (action === "photo" || action === "inspect" || action === "temperature") return { label: "TK", title: "TASK", color: "#087443" };
  return { label: "NV", title: "NAV", color: "#475467" };
}

function badge(ok: unknown): React.ReactNode {
  const pass = ok === true;
  const fail = ok === false;
  return (
    <span
      style={{
        display: "inline-block",
        borderRadius: 999,
        padding: "2px 8px",
        fontSize: 12,
        fontWeight: 700,
        background: pass ? "#ecfdf3" : fail ? "#fef3f2" : "#f2f4f7",
        color: pass ? "#027a48" : fail ? "#b42318" : "#475467",
        border: `1px solid ${pass ? "#abefc6" : fail ? "#fecdca" : "#d0d5dd"}`,
      }}
    >
      {pass ? "PASS" : fail ? "FAIL" : String(ok)}
    </span>
  );
}

function displayCount(value: unknown): string {
  if (typeof value === "number" && Number.isFinite(value)) return String(Math.trunc(value));
  if (typeof value === "string" && value.trim()) return value.trim();
  return "N/A";
}

function chipStyle(active = false): React.CSSProperties {
  return {
    borderRadius: 999,
    border: active ? "1px solid #0f766e" : "1px solid #cfd6e6",
    background: active ? "#e6fffb" : "#fff",
    color: "#1f2937",
    padding: "6px 10px",
    fontSize: 12,
    cursor: "pointer",
  };
}

function segmentedGroupStyle(): React.CSSProperties {
  return {
    display: "inline-flex",
    flexWrap: "wrap",
    gap: 4,
    padding: 4,
    borderRadius: 10,
    border: "1px solid #d0d5dd",
    background: "#f8fafc",
  };
}

function segmentedOptionStyle(active: boolean, tone: "neutral" | "good" | "warn" = "neutral"): React.CSSProperties {
  const activeBg = tone === "good" ? "#ecfdf3" : tone === "warn" ? "#fffaeb" : "#eef4ff";
  const activeBorder = tone === "good" ? "#abefc6" : tone === "warn" ? "#fedf89" : "#bfd2ff";
  const activeColor = tone === "good" ? "#027a48" : tone === "warn" ? "#b54708" : "#155eef";
  return {
    borderRadius: 8,
    border: `1px solid ${active ? activeBorder : "transparent"}`,
    background: active ? activeBg : "transparent",
    color: active ? activeColor : "#475467",
    padding: "6px 10px",
    fontSize: 12,
    fontWeight: active ? 700 : 600,
    cursor: "pointer",
  };
}

const inputStyle: React.CSSProperties = {
  width: "100%",
  minWidth: 0,
  padding: "6px 8px",
  borderRadius: 8,
  border: "1px solid #d0d5dd",
  fontSize: 12,
};

const codeSnippetStyle: React.CSSProperties = {
  margin: 0,
  padding: "8px 10px",
  borderRadius: 8,
  border: "1px solid #d0d5dd",
  background: "#f8fafc",
  color: "#101828",
  fontSize: 11,
  overflowX: "auto",
  whiteSpace: "pre",
};

const cardStyle: React.CSSProperties = {
  background: "#fcfcfd",
  border: "1px solid #eaecf0",
  borderRadius: 12,
  padding: 12,
};

const DEFAULT_SIM_ROUTE: UavWaypoint[] = [
  { x: 0, y: 0, z: 0, action: "transit" },
  { x: 100, y: 50, z: 40, action: "photo" },
  { x: 220, y: 120, z: 55, action: "temperature" },
  { x: 280, y: 180, z: 45, action: "inspect" },
];

const WAYPOINT_ACTIONS: Array<{ value: WaypointAction; label: string }> = [
  { value: "transit", label: "Transit" },
  { value: "photo", label: "Take Photo" },
  { value: "temperature", label: "Measure Temp" },
  { value: "hover", label: "Hover" },
  { value: "inspect", label: "Inspect" },
];
const MISSION_ACTION_CHOICES: Array<{ value: MissionActionType; label: string }> = [
  { value: "photo", label: "Take Photo" },
  { value: "measure", label: "Measure" },
  { value: "inspect", label: "Inspect" },
  { value: "hover", label: "Hover" },
];
const MIN_VISIBLE_WAYPOINT_ROWS = 5;
const PLACEHOLDER_WAYPOINT_ROW: EditableWaypointRow = { x: "0", y: "0", z: "0", action: "transit", _wp_origin: "original" };

const REGISTRY_PROFILE_TEXT_KEYS = [
  "uav_name",
  "uav_serial_number",
  "uav_registration_number",
  "manufacturer",
  "model",
  "platform_type",
  "uav_category",
  "uav_size_class",
  "battery_type",
  "remote_id",
  "c2_link_type",
  "launch_site_id",
  "landing_site_id",
  "contingency_action",
  "home_base_id",
  "status",
  "firmware_version",
  "airworthiness_status",
  "last_maintenance_at",
  "next_maintenance_due_at",
  "owner_org_id",
  "owner_name",
  "notes",
  "max_takeoff_weight_kg",
  "empty_weight_kg",
  "payload_capacity_kg",
  "max_speed_mps_capability",
  "max_altitude_m",
  "max_flight_time_min",
  "battery_capacity_mah",
  "weather_min_visibility_km",
  "weather_max_wind_mps",
  "home_x",
  "home_y",
  "home_z",
] as const;
const REGISTRY_PROFILE_NUMBER_KEYS = new Set<string>([
  "max_takeoff_weight_kg",
  "empty_weight_kg",
  "payload_capacity_kg",
  "max_speed_mps_capability",
  "max_altitude_m",
  "max_flight_time_min",
  "battery_capacity_mah",
  "weather_min_visibility_km",
  "weather_max_wind_mps",
  "home_x",
  "home_y",
  "home_z",
]);
const REGISTRY_PROFILE_DATETIME_KEYS = new Set<string>(["last_maintenance_at", "next_maintenance_due_at"]);
const MISSION_DEFAULT_EXTRA_KEYS = [
  "mission_priority",
  "operation_type",
  "c2_link_type",
] as const;
const MISSION_DEFAULT_EXTRA_NUMBER_KEYS = new Set<string>([]);

function defaultRegistryProfileForm(): Record<string, string | boolean> {
  const out: Record<string, string | boolean> = { remote_id_enabled: false };
  REGISTRY_PROFILE_TEXT_KEYS.forEach((k) => {
    out[k] = "";
  });
  return out;
}

function defaultMissionDefaultsExtraForm(): Record<string, string> {
  const out: Record<string, string> = {};
  MISSION_DEFAULT_EXTRA_KEYS.forEach((k) => {
    out[k] = "";
  });
  return out;
}

function registryProfileFormFromBackend(src: unknown): Record<string, string | boolean> {
  const out = defaultRegistryProfileForm();
  const rec = asRecord(src);
  if (!rec) return out;
  REGISTRY_PROFILE_TEXT_KEYS.forEach((k) => {
    const raw = rec[k];
    if (raw == null) {
      out[k] = "";
      return;
    }
    if (REGISTRY_PROFILE_DATETIME_KEYS.has(k) && typeof raw === "string" && raw) {
      out[k] = isoUtcToLocalInput(raw) || raw;
      return;
    }
    out[k] = String(raw);
  });
  out.remote_id_enabled = rec.remote_id_enabled === true;
  return out;
}

function registryProfilePayloadFromForm(form: Record<string, string | boolean>): Record<string, unknown> {
  const out: Record<string, unknown> = { remote_id_enabled: form.remote_id_enabled === true };
  REGISTRY_PROFILE_TEXT_KEYS.forEach((k) => {
    const raw = form[k];
    const str = typeof raw === "string" ? raw.trim() : String(raw ?? "").trim();
    if (REGISTRY_PROFILE_NUMBER_KEYS.has(k)) {
      out[k] = str ? Number(str) : null;
      return;
    }
    if (REGISTRY_PROFILE_DATETIME_KEYS.has(k)) {
      out[k] = str ? (localInputToIsoUtc(str) ?? str) : null;
      return;
    }
    out[k] = str || null;
  });
  return out;
}

function missionDefaultsExtraFormFromBackend(src: unknown): Record<string, string> {
  const out = defaultMissionDefaultsExtraForm();
  const rec = asRecord(src);
  if (!rec) return out;
  MISSION_DEFAULT_EXTRA_KEYS.forEach((k) => {
    const raw = rec[k];
    out[k] = raw == null ? "" : String(raw);
  });
  return out;
}

function missionDefaultsExtraPayloadFromForm(form: Record<string, string>): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  MISSION_DEFAULT_EXTRA_KEYS.forEach((k) => {
    const str = String(form[k] ?? "").trim();
    out[k] = MISSION_DEFAULT_EXTRA_NUMBER_KEYS.has(k) ? (str ? Number(str) : null) : (str || null);
  });
  return out;
}

const REGISTRY_PROFILE_SECTIONS: DynamicSectionDef[] = [
  {
    title: "Identity & Classification",
    columns: "repeat(3, minmax(0, 1fr))",
    fields: [
      { key: "uav_name", label: "UAV Name", kind: "text" },
      { key: "uav_serial_number", label: "Serial Number", kind: "text" },
      { key: "uav_registration_number", label: "Registration Number", kind: "text" },
      { key: "manufacturer", label: "Manufacturer", kind: "text" },
      { key: "model", label: "Model", kind: "text" },
      {
        key: "platform_type", label: "Platform Type", kind: "select", options: [
          { value: "", label: "Not set" },
          { value: "multirotor", label: "Multirotor" },
          { value: "fixed_wing", label: "Fixed Wing" },
          { value: "vtol", label: "VTOL" },
          { value: "hybrid", label: "Hybrid" },
        ],
      },
      {
        key: "uav_category", label: "UAV Category", kind: "select", options: [
          { value: "", label: "Not set" },
          { value: "recreational", label: "Recreational" },
          { value: "commercial", label: "Commercial" },
          { value: "industrial", label: "Industrial" },
          { value: "public_safety", label: "Public Safety" },
          { value: "research", label: "Research" },
          { value: "delivery", label: "Delivery" },
        ],
      },
      {
        key: "uav_size_class", label: "UAV Size Class", kind: "select", options: [
          { value: "", label: "Not set" },
          { value: "small", label: "Small" },
          { value: "middle", label: "Middle" },
          { value: "heavy", label: "Heavy" },
        ],
      },
      {
        key: "status", label: "Fleet Status", kind: "select", options: [
          { value: "", label: "Not set" },
          { value: "active", label: "Active" },
          { value: "maintenance", label: "Maintenance" },
          { value: "grounded", label: "Grounded" },
          { value: "retired", label: "Retired" },
        ],
      },
    ],
  },
  {
    title: "Airframe & Performance",
    columns: "repeat(3, minmax(0, 1fr))",
    fields: [
      { key: "max_takeoff_weight_kg", label: "Max Takeoff Weight (kg)", kind: "number", step: "0.1" },
      { key: "empty_weight_kg", label: "Empty Weight (kg)", kind: "number", step: "0.1" },
      { key: "payload_capacity_kg", label: "Payload Capacity (kg)", kind: "number", step: "0.1" },
      { key: "max_speed_mps_capability", label: "Max Speed Capability (m/s)", kind: "number", step: "0.1" },
      { key: "max_altitude_m", label: "Max Altitude (m)", kind: "number", step: "0.1" },
      { key: "max_flight_time_min", label: "Max Flight Time (min)", kind: "number", step: "0.1" },
      { key: "battery_type", label: "Battery Type", kind: "text" },
      { key: "battery_capacity_mah", label: "Battery Capacity (mAh)", kind: "number", step: "1" },
      { key: "firmware_version", label: "Firmware Version", kind: "text" },
    ],
  },
  {
    title: "Connectivity & Compliance",
    columns: "repeat(3, minmax(0, 1fr))",
    fields: [
      { key: "remote_id_enabled", label: "Remote ID Enabled", kind: "checkbox" },
      { key: "remote_id", label: "Remote ID", kind: "text" },
      {
        key: "c2_link_type", label: "C2 Link Type", kind: "select", options: [
          { value: "", label: "Not set" },
          { value: "rf", label: "RF" },
          { value: "lte", label: "LTE" },
          { value: "5g", label: "5G" },
          { value: "satellite", label: "Satellite" },
          { value: "hybrid", label: "Hybrid" },
        ],
      },
      {
        key: "airworthiness_status", label: "Airworthiness Status", kind: "select", options: [
          { value: "", label: "Not set" },
          { value: "airworthy", label: "Airworthy" },
          { value: "inspection_due", label: "Inspection Due" },
          { value: "maintenance_required", label: "Maintenance Required" },
          { value: "grounded", label: "Grounded" },
        ],
      },
      { key: "last_maintenance_at", label: "Last Maintenance", kind: "datetime-local" },
      { key: "next_maintenance_due_at", label: "Next Maintenance Due", kind: "datetime-local" },
    ],
  },
  {
    title: "Ownership & Home Base",
    columns: "repeat(3, minmax(0, 1fr))",
    fields: [
      { key: "owner_org_id", label: "Owner Org ID", kind: "text" },
      { key: "owner_name", label: "Owner Name", kind: "text" },
      { key: "home_base_id", label: "Home Base ID", kind: "text" },
      { key: "home_x", label: "Home X", kind: "number", step: "0.1" },
      { key: "home_y", label: "Home Y", kind: "number", step: "0.1" },
      { key: "home_z", label: "Home Z", kind: "number", step: "0.1" },
      { key: "notes", label: "Notes", kind: "textarea", rows: 3 },
    ],
  },
  {
    title: "Operational Defaults & Constraints",
    columns: "repeat(3, minmax(0, 1fr))",
    fields: [
      { key: "launch_site_id", label: "Launch Site ID", kind: "text" },
      { key: "landing_site_id", label: "Landing Site ID", kind: "text" },
      {
        key: "contingency_action", label: "Contingency Action", kind: "select", options: [
          { value: "", label: "Not set" },
          { value: "hover", label: "Hover" },
          { value: "rth", label: "Return to Home" },
          { value: "land", label: "Land" },
        ],
      },
      { key: "weather_min_visibility_km", label: "Min Visibility (km)", kind: "number", step: "0.1" },
      { key: "weather_max_wind_mps", label: "Max Wind (m/s)", kind: "number", step: "0.1" },
    ],
  },
];

const MISSION_DEFAULT_EXTRA_FIELDS: DynamicFieldDef[] = [
  {
    key: "mission_priority", label: "Mission Priority", kind: "select", options: [
      { value: "", label: "Not set" },
      { value: "normal", label: "Normal" },
      { value: "urgent", label: "Urgent" },
      { value: "critical", label: "Critical" },
    ],
  },
  {
    key: "operation_type", label: "Operation Type", kind: "select", options: [
      { value: "", label: "Not set" },
      { value: "inspection", label: "Inspection" },
      { value: "mapping", label: "Mapping" },
      { value: "patrol", label: "Patrol" },
      { value: "delivery", label: "Delivery" },
      { value: "test", label: "Test" },
    ],
  },
  {
    key: "c2_link_type", label: "C2 Link Choice", kind: "select", options: [
      { value: "", label: "Not set" },
      { value: "rf", label: "RF" },
      { value: "lte", label: "LTE" },
      { value: "5g", label: "5G" },
      { value: "satellite", label: "Satellite" },
      { value: "hybrid", label: "Hybrid" },
    ],
  },
];

function waypointToRow(wp: UavWaypoint): EditableWaypointRow {
  const ext = wp as UavWaypoint & { _wp_origin?: "original" | "agent_inserted"; _wp_source?: string };
  return {
    x: String(wp.x),
    y: String(wp.y),
    z: String(wp.z),
    action: (wp.action ?? "transit") as WaypointAction,
    _wp_origin: ext._wp_origin ?? "original",
    _wp_source: ext._wp_source,
    _mapped_from_original_index: typeof (ext as { _mapped_from_original_index?: unknown })._mapped_from_original_index === "number"
      ? (ext as { _mapped_from_original_index?: number })._mapped_from_original_index
      : undefined,
  };
}

function isoUtcToLocalInput(iso: string): string {
  const dt = new Date(iso);
  if (Number.isNaN(dt.getTime())) return "";
  const p = (n: number) => String(n).padStart(2, "0");
  return `${dt.getFullYear()}-${p(dt.getMonth() + 1)}-${p(dt.getDate())}T${p(dt.getHours())}:${p(dt.getMinutes())}`;
}

function localInputToIsoUtc(value: string): string | null {
  if (!value.trim()) return null;
  const dt = new Date(value);
  return Number.isNaN(dt.getTime()) ? null : dt.toISOString();
}

function formatIsoSummary(value: unknown): string {
  const raw = typeof value === "string" ? value.trim() : "";
  if (!raw) return "-";
  const dt = new Date(raw);
  if (Number.isNaN(dt.getTime())) {
    const local = isoUtcToLocalInput(raw);
    return local ? local.replace("T", " ") : raw;
  }
  const p = (n: number) => String(n).padStart(2, "0");
  return `${dt.getFullYear()}-${p(dt.getMonth() + 1)}-${p(dt.getDate())} ${p(dt.getHours())}:${p(dt.getMinutes())}`;
}

function formatPathPointBrief(point: unknown): string {
  const p = asRecord(point);
  if (!p) return "-";
  const x = Number(p.x ?? NaN);
  const y = Number(p.y ?? NaN);
  const z = Number(p.z ?? NaN);
  if (![x, y, z].every(Number.isFinite)) return "-";
  return `${x.toFixed(0)},${y.toFixed(0)},${z.toFixed(0)}`;
}

function formatFlightTimeBrief(seconds: unknown): string {
  const s = Number(seconds ?? NaN);
  if (!Number.isFinite(s) || s <= 0) return "-";
  if (s < 60) return `${Math.round(s)}s`;
  const mins = Math.floor(s / 60);
  const rem = Math.round(s % 60);
  return rem ? `${mins}m ${rem}s` : `${mins}m`;
}

function plannerPathColor(key: PlannerPathSourceKey): string {
  if (key === "user_planned") return "#2563eb";
  if (key === "agent_replanned") return "#f79009";
  if (key === "dss_replanned") return "#f04438";
  return "#12b76a";
}

function wpOriginTag(row: EditableWaypointRow): { label: "O" | "I"; title: string; color: string; bg: string; border: string } {
  const inserted = row._wp_origin === "agent_inserted";
  return inserted
    ? { label: "I", title: "Agent Inserted", color: "#b54708", bg: "#fffaeb", border: "#fedf89" }
    : { label: "O", title: "Original", color: "#155eef", bg: "#eef4ff", border: "#bfd2ff" };
}

function normalizeBaseUrl(url: string): string {
  return url.trim().replace(/\/+$/, "");
}

function utmAuthHeaders(token: string): Record<string, string> {
  const trimmed = token.trim();
  return trimmed ? { Authorization: `Bearer ${trimmed}` } : {};
}

function normalizeRouteIdBase(routeId: string): string {
  let rid = (routeId || "route-1").trim() || "route-1";
  let changed = true;
  while (changed) {
    changed = false;
    for (const s of ["-replan", "-reschedule"]) {
      if (rid.endsWith(s)) {
        rid = rid.slice(0, -s.length) || "route-1";
        changed = true;
      }
    }
  }
  return rid;
}

function compactUavLabel(uavId: string): string {
  const s = (uavId || "").trim();
  return s.replace(/^uav-/i, "") || s || "-";
}

function uiUavLabel(uavId: string): string {
  const compact = compactUavLabel(uavId);
  return compact && compact !== "-" ? `UAV ${compact}` : "UAV";
}

function parseCopilotPromptDirectives(prompt: string): {
  cleanedPrompt: string;
  profile: "safe" | "balanced" | "aggressive";
  networkMode: "coverage" | "qos" | "power" | null;
  autoVerify: boolean;
  autoNetworkOptimize: boolean;
} {
  let profile: "safe" | "balanced" | "aggressive" = "balanced";
  let networkMode: "coverage" | "qos" | "power" | null = null;
  let autoVerify = false;
  let autoNetworkOptimize = false;
  let cleaned = prompt;
  if (/@safe\b/i.test(cleaned)) profile = "safe";
  if (/@balanced\b/i.test(cleaned)) profile = "balanced";
  if (/@aggressive\b/i.test(cleaned)) profile = "aggressive";
  if (/@qos\b/i.test(cleaned)) networkMode = "qos";
  else if (/@coverage\b/i.test(cleaned)) networkMode = "coverage";
  else if (/@power\b/i.test(cleaned)) networkMode = "power";
  if (/@verify-on\b/i.test(cleaned) || /@verify\b/i.test(cleaned)) autoVerify = true;
  if (/@network-on\b/i.test(cleaned) || /@network\b/i.test(cleaned)) autoNetworkOptimize = true;
  if (/@verify-off\b/i.test(cleaned)) autoVerify = false;
  if (/@network-off\b/i.test(cleaned)) autoNetworkOptimize = false;
  cleaned = cleaned
    .replace(/@(safe|balanced|aggressive)\b/gi, "")
    .replace(/@(qos|coverage|power)\b/gi, "")
    .replace(/@(verify|verify-on|verify-off|network|network-on|network-off)\b/gi, "")
    .replace(/\s+/g, " ")
    .trim();
  return { cleanedPrompt: cleaned, profile, networkMode, autoVerify, autoNetworkOptimize };
}

function readSyncRevision(data: unknown): number | null {
  const root = asRecord(data);
  const sync = asRecord(root?.result && asRecord(root.result)?.sync ? asRecord(root.result)?.sync : root?.sync);
  const rev = sync?.revision;
  return typeof rev === "number" ? rev : null;
}

function defaultApprovalWindowLocal() {
  const start = new Date(Date.now() + 2 * 60 * 1000);
  const end = new Date(start.getTime() + 20 * 60 * 1000);
  return { start: isoUtcToLocalInput(start.toISOString()), end: isoUtcToLocalInput(end.toISOString()) };
}

export function UavPage() {
  const sharedInit = getSharedPageState();
  const defaults = defaultApprovalWindowLocal();
  const [uavApiBase, setUavApiBase] = useState(sharedInit.uavApiBase || "http://127.0.0.1:8020");
  const [utmAuthToken, setUtmAuthToken] = useState(sharedInit.utmAuthToken || "local-dev-token");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState("");
  const [simUavId, setSimUavId] = useState(sharedInit.uavId || "uav-1");
  const [simRouteId, setSimRouteId] = useState("demo-route");
  const [simAirspace, setSimAirspace] = useState(sharedInit.airspace || "sector-A3");
  const [simTicks] = useState("1");
  const [simOperatorLicenseId, setSimOperatorLicenseId] = useState("op-001");
  const [ownerUserId, setOwnerUserId] = useState("user-1");
  const [simLicenseClass] = useState("VLOS");
  const [simRequestedSpeedMps, setSimRequestedSpeedMps] = useState("12");
  const [simPlannedStartAt, setSimPlannedStartAt] = useState(defaults.start);
  const [simPlannedEndAt, setSimPlannedEndAt] = useState(defaults.end);
  const [holdReason, setHoldReason] = useState("operator_request");
  const [routeRows, setRouteRows] = useState<EditableWaypointRow[]>(DEFAULT_SIM_ROUTE.map(waypointToRow));
  const [plannerShowUserPath, setPlannerShowUserPath] = useState(false);
  const [plannerShowAgentPath, setPlannerShowAgentPath] = useState(true);
  const [plannerShowDssPath, setPlannerShowDssPath] = useState(true);
  const [plannerShowUtmPath, setPlannerShowUtmPath] = useState(true);
  const [plannerEditorSource, setPlannerEditorSource] = useState<PlannerPathSourceKey>("utm_confirmed");
  const [plannerMapResetSeq, setPlannerMapResetSeq] = useState(0);
  const [state, setState] = useState<UavSimState | null>(null);
  const [networkMap, setNetworkMap] = useState<{ bs: MissionBs[]; coverage: MissionCoverage[]; tracks: MissionTrack[] }>({ bs: [], coverage: [], tracks: [] });
  const [backendRevisions, setBackendRevisions] = useState<{ uav: number; utm: number; network: number }>({ uav: -1, utm: -1, network: -1 });
  const [agentPrompt, setAgentPrompt] = useState("");
  const [agentOptimizationProfile, setAgentOptimizationProfile] = useState<"safe" | "balanced" | "aggressive">("balanced");
  const [agentAutoVerify, setAgentAutoVerify] = useState(false);
  const [agentAutoNetworkOptimize, setAgentAutoNetworkOptimize] = useState(false);
  const [agentPreferredNetworkMode, setAgentPreferredNetworkMode] = useState<"coverage" | "qos" | "power">("coverage");
  const [agentBusy, setAgentBusy] = useState(false);
  const [agentConversation, setAgentConversation] = useState<CopilotMessage[]>([]);
  const [agentStatusMsg, setAgentStatusMsg] = useState("");
  const [backendActionLog, setBackendActionLog] = useState<AgentActionLogItem[]>([]);
  const [backendActionLogClearedAt, setBackendActionLogClearedAt] = useState<string | null>(null);
  const [backendLogFilter, setBackendLogFilter] = useState<BackendLogFilter>("all");
  const [uavBackendSource, setUavBackendSource] = useState<Record<string, unknown> | null>(null);
  const [utmBackendSource, setUtmBackendSource] = useState<Record<string, unknown> | null>(null);
  const [utmBackendState, setUtmBackendState] = useState<Record<string, unknown> | null>(null);
  const [utmLayeredStatus, setUtmLayeredStatus] = useState<Record<string, unknown> | null>(null);
  const [utmDispatchStatus, setUtmDispatchStatus] = useState<Record<string, unknown> | null>(null);
  const [lastUtmSubmitResult, setLastUtmSubmitResult] = useState<Record<string, unknown> | null>(null);
  const [lastDssMitigation, setLastDssMitigation] = useState<DssMitigationSnapshot | null>(null);
  const [missionStatusExpanded, setMissionStatusExpanded] = useState(false);
  const [plannerMapClickMode, setPlannerMapClickMode] = useState<PlannerMapClickMode>("add_wp");
  const [fleetState, setFleetState] = useState<Record<string, Record<string, unknown>>>({});
  const [latestPlannedRoutes, setLatestPlannedRoutes] = useState<Record<string, Record<string, unknown>>>({});
  const [registryUserSummary, setRegistryUserSummary] = useState<Record<string, unknown> | null>(null);
  const [registryProfileForm, setRegistryProfileForm] = useState<Record<string, string | boolean>>(() => defaultRegistryProfileForm());
  const [registryProfileDirty, setRegistryProfileDirty] = useState(false);
  const [registryProfileScopeKey, setRegistryProfileScopeKey] = useState("");
  const [registryProfileAdvanced, setRegistryProfileAdvanced] = useState(false);
  const [missionDefaultsExtraForm, setMissionDefaultsExtraForm] = useState<Record<string, string>>(() => defaultMissionDefaultsExtraForm());
  const [missionDefaultsDirty, setMissionDefaultsDirty] = useState(false);
  const [missionDefaultsScopeKey, setMissionDefaultsScopeKey] = useState("");
  const [autoFlyEnabled, setAutoFlyEnabled] = useState(false);
  const [missionActionChoice, setMissionActionChoice] = useState<MissionActionType>("photo");
  const autoFlyCycleBusyRef = useRef(false);
  const autoFlyLastWaypointRef = useRef<number>(-1);
  const uavRef = useRef<Record<string, unknown> | null>(null);

  useEffect(() => {
    autoFlyLastWaypointRef.current = -1;
  }, [simUavId, autoFlyEnabled]);

  useEffect(() => {
    setLastUtmSubmitResult(null);
    setLastDssMitigation(null);
  }, [simUavId, ownerUserId]);

  const loadState = async () => {
    setBusy(true);
    setMsg("");
    try {
      const base = normalizeBaseUrl(uavApiBase);
      const shared = getSharedPageState();
      const utmBase = normalizeBaseUrl(shared.utmApiBase);
      const utmHeaders = utmAuthHeaders(utmAuthToken);
      const stateQs = new URLSearchParams({ uav_id: simUavId, user_id: ownerUserId });
      const utmStateQs = new URLSearchParams({
        airspace_segment: simAirspace,
        operator_license_id: simOperatorLicenseId || "op-001",
      });
      const [res, uavSrcRes, utmSrcRes, utmStateRes, utmLayersRes, utmDispatchRes] = await Promise.all([
        fetch(`${base}/api/uav/live/state?${stateQs.toString()}`),
        fetch(`${base}/api/uav/live/source?uav_id=${encodeURIComponent(simUavId)}`),
        fetch(`${utmBase}/api/utm/live/source`, { headers: utmHeaders }),
        fetch(`${utmBase}/api/utm/state?${utmStateQs.toString()}`, { headers: utmHeaders }),
        fetch(`${utmBase}/api/utm/layers/status?${utmStateQs.toString()}`, { headers: utmHeaders }),
        fetch(`${utmBase}/api/utm/dss/notifications/dispatch/status`, { headers: utmHeaders }),
      ]);
      const [data, uavSrcData, utmSrcData, utmStateData, utmLayersData, utmDispatchData] = await Promise.all([
        res.json(),
        uavSrcRes.json(),
        utmSrcRes.json(),
        utmStateRes.json(),
        utmLayersRes.json().catch(() => ({})),
        utmDispatchRes.json().catch(() => ({})),
      ]);
      if (!res.ok || !isObject(data)) throw new Error(String(asRecord(data)?.detail ?? "Request failed"));
      setState(data as UavSimState);
      setRegistryUserSummary(asRecord((data as Record<string, unknown>).uav_registry_user ?? (data as Record<string, unknown>).uavRegistryUser));
      {
        const root = data as Record<string, unknown>;
        const identity = asRecord(root.identity);
        const selectedUserId = String(identity?.selected_user_id ?? ownerUserId);
        const selectedUavId = String(identity?.selected_uav_id ?? simUavId);
        const scopeKey = `${selectedUserId}:${selectedUavId}`;
        const profileSource =
          asRecord(root.uav_registry_profile)
          ?? asRecord(identity?.uav_registry_profile)
          ?? asRecord(asRecord(identity?.uav_registry)?.standardized_profile);
        if (!registryProfileDirty || registryProfileScopeKey !== scopeKey) {
          setRegistryProfileForm(registryProfileFormFromBackend(profileSource));
          setRegistryProfileDirty(false);
          setRegistryProfileScopeKey(scopeKey);
        }
        const missionDefaultsSource = asRecord(root.uav_mission_defaults);
        if (!missionDefaultsDirty || missionDefaultsScopeKey !== scopeKey) {
          setMissionDefaultsExtraForm(missionDefaultsExtraFormFromBackend(missionDefaultsSource));
          const routeIdDefault = typeof missionDefaultsSource?.route_id === "string" ? String(missionDefaultsSource.route_id) : "";
          const airspaceDefault = typeof missionDefaultsSource?.airspace_segment === "string" ? String(missionDefaultsSource.airspace_segment) : "";
          const speedDefault = typeof missionDefaultsSource?.requested_speed_mps === "number" ? String(missionDefaultsSource.requested_speed_mps) : "";
          const startDefault = typeof missionDefaultsSource?.planned_start_at === "string" ? (isoUtcToLocalInput(String(missionDefaultsSource.planned_start_at)) || String(missionDefaultsSource.planned_start_at)) : "";
          const endDefault = typeof missionDefaultsSource?.planned_end_at === "string" ? (isoUtcToLocalInput(String(missionDefaultsSource.planned_end_at)) || String(missionDefaultsSource.planned_end_at)) : "";
          const holdDefault = typeof missionDefaultsSource?.hold_reason === "string" ? String(missionDefaultsSource.hold_reason) : "";
          if (routeIdDefault) setSimRouteId(routeIdDefault);
          if (airspaceDefault) setSimAirspace(airspaceDefault);
          if (speedDefault) setSimRequestedSpeedMps(speedDefault);
          if (startDefault) setSimPlannedStartAt(startDefault);
          if (endDefault) setSimPlannedEndAt(endDefault);
          if (holdDefault) setHoldReason(holdDefault);
          setMissionDefaultsDirty(false);
          setMissionDefaultsScopeKey(scopeKey);
        }
      }
      setUavBackendSource(asRecord((uavSrcData as Record<string, unknown>)?.result));
      setUtmBackendSource(asRecord((utmSrcData as Record<string, unknown>)?.result));
      const utmStateResult = asRecord((utmStateData as Record<string, unknown>)?.result);
      const utmLayersResult = asRecord((utmLayersData as Record<string, unknown>)?.result);
      setUtmBackendState(utmStateResult);
      setUtmLayeredStatus(utmLayersResult ?? asRecord(utmStateResult?.layeredStatus));
      setUtmDispatchStatus(asRecord((utmDispatchData as Record<string, unknown>)?.result));
      const fleetRec = asRecord((data as Record<string, unknown>).fleet) ?? {};
      setFleetState(
        Object.fromEntries(
          Object.entries(fleetRec)
            .filter(([, v]) => isObject(v))
            .map(([k, v]) => [k, v as Record<string, unknown>]),
        ),
      );
      const latestRoutesRec = asRecord((data as Record<string, unknown>).latest_planned_routes ?? (data as Record<string, unknown>).latestPlannedRoutes) ?? {};
      setLatestPlannedRoutes(
        Object.fromEntries(
          Object.entries(latestRoutesRec)
            .filter(([, v]) => isObject(v))
            .map(([k, v]) => [k, v as Record<string, unknown>]),
        ),
      );
      const uavObj = asRecord((data as Record<string, unknown>).uav);
      const backendRouteId = typeof uavObj?.route_id === "string" ? uavObj.route_id : null;
      if (backendRouteId) setSimRouteId(backendRouteId);
      const backendPoints = Array.isArray(uavObj?.waypoints)
        ? (uavObj!.waypoints as unknown[])
            .filter(isObject)
            .map((w) => ({
              x: Number((w as Record<string, unknown>).x ?? 0),
              y: Number((w as Record<string, unknown>).y ?? 0),
              z: Number((w as Record<string, unknown>).z ?? 0),
              action: String((w as Record<string, unknown>).action ?? "transit") as WaypointAction,
              _wp_origin: String((w as Record<string, unknown>)._wp_origin ?? "original") === "agent_inserted" ? "agent_inserted" as const : "original" as const,
              _wp_source: typeof (w as Record<string, unknown>)._wp_source === "string" ? String((w as Record<string, unknown>)._wp_source) : undefined,
              _mapped_from_original_index:
                typeof (w as Record<string, unknown>)._mapped_from_original_index === "number"
                  ? Number((w as Record<string, unknown>)._mapped_from_original_index)
                  : undefined,
              _mapped_from_wp_source:
                typeof (w as Record<string, unknown>)._mapped_from_wp_source === "string"
                  ? String((w as Record<string, unknown>)._mapped_from_wp_source)
                  : undefined,
            }))
            .filter((w) => Number.isFinite(w.x) && Number.isFinite(w.y) && Number.isFinite(w.z))
        : [];
      const rawWpCount = Array.isArray(uavObj?.waypoints) ? (uavObj!.waypoints as unknown[]).length : -1;
      if (backendPoints.length >= 2 || rawWpCount === 0) {
        // Keep route waypoints as planned from backend; only UAV position should move during flight.
        setRouteRows(backendPoints.map(waypointToRow));
      }
      try {
        const [uavSyncRes, utmSyncRes] = await Promise.all([
          fetch(`${base}/api/uav/sync?limit_actions=16`),
          fetch(`${utmBase}/api/utm/sync?limit_actions=16`, { headers: utmHeaders }),
        ]);
        const [uavSyncData, utmSyncData] = await Promise.all([uavSyncRes.json(), utmSyncRes.json()]);
        const uavRecent = Array.isArray(asRecord(asRecord(uavSyncData)?.result)?.recentActions)
          ? (asRecord(asRecord(uavSyncData)?.result)?.recentActions as unknown[])
              .filter(isObject)
              .map((r) => ({
                id: Number((r as Record<string, unknown>).id ?? 0),
                action: String((r as Record<string, unknown>).action ?? ""),
                entity_id: (r as Record<string, unknown>).entity_id,
                payload: (r as Record<string, unknown>).payload,
                result: (r as Record<string, unknown>).result,
                created_at: String((r as Record<string, unknown>).created_at ?? ""),
                agent: "uav" as const,
              }))
          : [];
        const utmRecent = Array.isArray(asRecord(asRecord(utmSyncData)?.result)?.recentActions)
          ? (asRecord(asRecord(utmSyncData)?.result)?.recentActions as unknown[])
              .filter(isObject)
              .map((r) => ({
                id: Number((r as Record<string, unknown>).id ?? 0),
                action: String((r as Record<string, unknown>).action ?? ""),
                entity_id: (r as Record<string, unknown>).entity_id,
                payload: (r as Record<string, unknown>).payload,
                result: (r as Record<string, unknown>).result,
                created_at: String((r as Record<string, unknown>).created_at ?? ""),
                agent: "utm" as const,
              }))
          : [];
        const merged = [...uavRecent, ...utmRecent].sort((a, b) => String(b.created_at).localeCompare(String(a.created_at)));
        const filtered = backendActionLogClearedAt ? merged.filter((r) => String(r.created_at) >= backendActionLogClearedAt) : merged;
        setBackendActionLog(filtered.slice(0, 24));
      } catch {
        // optional backend action log
      }
      setMsg("Loaded UAV state");
      try {
        const netRes = await fetch(`${shared.networkApiBase.replace(/\/+$/, "")}/api/network/mission/state?airspace_segment=${encodeURIComponent(simAirspace)}&selected_uav_id=${encodeURIComponent(simUavId)}`);
        const netData = await netRes.json();
        const result = asRecord(asRecord(netData)?.result);
        const bs = Array.isArray(result?.baseStations)
          ? (result!.baseStations as unknown[]).filter(isObject).map((b) => ({
              id: String((b as Record<string, unknown>).id ?? "BS"),
              x: Number((b as Record<string, unknown>).x ?? 0),
              y: Number((b as Record<string, unknown>).y ?? 0),
              status: String((b as Record<string, unknown>).status ?? "online"),
            }))
          : [];
        const coverage = Array.isArray(result?.coverage)
          ? (result!.coverage as unknown[]).filter(isObject).map((c) => ({
              bsId: String((c as Record<string, unknown>).bsId ?? ""),
              radiusM: Number((c as Record<string, unknown>).radiusM ?? 0),
            }))
          : [];
        const tracks = Array.isArray(result?.trackingSnapshots)
          ? (result!.trackingSnapshots as unknown[]).filter(isObject).map((t) => ({
              id: String((t as Record<string, unknown>).id ?? "uav"),
              x: Number((t as Record<string, unknown>).x ?? 0),
              y: Number((t as Record<string, unknown>).y ?? 0),
              z: Number((t as Record<string, unknown>).z ?? 0),
              attachedBsId: String((t as Record<string, unknown>).attachedBsId ?? ""),
              interferenceRisk: String((t as Record<string, unknown>).interferenceRisk ?? "low") as MissionTrack["interferenceRisk"],
            }))
          : [];
        setNetworkMap({ bs, coverage, tracks });
      } catch {
        // keep page functional even when network API is unavailable
      }
    } catch (e) {
      const msgText = e instanceof Error ? e.message : String(e);
      const hint = msgText === "Failed to fetch"
        ? " (check uav_agent.api is running on the URL/port, and restart backend after CORS changes)"
        : "";
      setMsg(`Load failed: ${msgText}${hint}`);
    } finally {
      setBusy(false);
    }
  };

  const registryUserUavs = useMemo(() => {
    const rows = Array.isArray(registryUserSummary?.uavs) ? (registryUserSummary.uavs as unknown[]) : [];
    return rows.filter(isObject).map((r) => r as Record<string, unknown>);
  }, [registryUserSummary]);
  const registryUserUavIds = useMemo(() => registryUserUavs.map((r) => String(r.uav_id ?? "")).filter(Boolean), [registryUserUavs]);
  const utmLicenseCatalog = (asRecord(asRecord(state)?.utm)?.licenses ?? {}) as Record<string, unknown>;
  const selectedRegistryRow = useMemo(
    () => registryUserUavs.find((r) => String(r.uav_id ?? "") === simUavId) ?? null,
    [registryUserUavs, simUavId],
  );
  const selectedUavAssignedLicenseId = String((selectedRegistryRow?.operator_license_id ?? simOperatorLicenseId) || "op-001");
  const effectiveOperatorLicenseId = selectedUavAssignedLicenseId;
  const effectiveRequiredLicenseClass = String((asRecord(selectedRegistryRow?.operator_license)?.license_class ?? simLicenseClass) || "VLOS");

  useEffect(() => {
    void loadState();
  }, []);

  useEffect(() => {
    void loadState();
  }, [ownerUserId]);

  useEffect(() => {
    void loadState();
  }, [simUavId]);

  useEffect(() => {
    if (!registryUserUavIds.length) return;
    if (!registryUserUavIds.includes(simUavId)) {
      setSimUavId(registryUserUavIds[0]!);
    }
  }, [registryUserUavIds, simUavId]);

  useEffect(() => {
    const licId = String(selectedRegistryRow?.operator_license_id ?? "");
    if (licId && licId !== simOperatorLicenseId) setSimOperatorLicenseId(licId);
  }, [selectedRegistryRow, simOperatorLicenseId]);

  useEffect(() => {
    patchSharedPageState({ uavApiBase, utmAuthToken, uavId: simUavId, airspace: simAirspace });
  }, [uavApiBase, utmAuthToken, simUavId, simAirspace, backendActionLogClearedAt]);

  useEffect(() => {
    let lastRevision = getSharedPageState().revision;
    return subscribeSharedPageState((next) => {
      if (next.uavApiBase && next.uavApiBase !== uavApiBase) setUavApiBase(next.uavApiBase);
      if (typeof next.utmAuthToken === "string" && next.utmAuthToken !== utmAuthToken) setUtmAuthToken(next.utmAuthToken);
      if (next.uavId && next.uavId !== simUavId) setSimUavId(next.uavId);
      if (next.airspace && next.airspace !== simAirspace) setSimAirspace(next.airspace);
      if (next.revision !== lastRevision) {
        lastRevision = next.revision;
        void loadState();
      }
    });
  }, [uavApiBase, utmAuthToken, simUavId, simAirspace, registryProfileDirty, registryProfileScopeKey, missionDefaultsDirty, missionDefaultsScopeKey]);

  useEffect(() => {
    const id = window.setInterval(() => {
      void (async () => {
        if (busy) return;
        try {
          const shared = getSharedPageState();
          const [uavRes, utmRes, netRes] = await Promise.all([
            fetch(`${normalizeBaseUrl(uavApiBase)}/api/uav/sync`),
            fetch(`${normalizeBaseUrl(shared.utmApiBase)}/api/utm/sync`, { headers: utmAuthHeaders(utmAuthToken) }),
            fetch(`${normalizeBaseUrl(shared.networkApiBase)}/api/network/sync`),
          ]);
          const [uavData, utmData, netData] = await Promise.all([uavRes.json(), utmRes.json(), netRes.json()]);
          const next = {
            uav: readSyncRevision(uavData) ?? backendRevisions.uav,
            utm: readSyncRevision(utmData) ?? backendRevisions.utm,
            network: readSyncRevision(netData) ?? backendRevisions.network,
          };
          const changed = next.uav !== backendRevisions.uav || next.utm !== backendRevisions.utm || next.network !== backendRevisions.network;
          if (changed) {
            setBackendRevisions(next);
            void loadState();
          }
        } catch {
          // optional auto-refresh path
        }
      })();
    }, 1500);
    return () => window.clearInterval(id);
  }, [busy, uavApiBase, utmAuthToken, backendRevisions, simUavId, simAirspace, ownerUserId, registryProfileDirty, registryProfileScopeKey, missionDefaultsDirty, missionDefaultsScopeKey]);

  const postApi = async (path: string, body?: unknown, successMsg?: string) => {
    setBusy(true);
    setMsg("");
    try {
      const base = normalizeBaseUrl(uavApiBase);
      const res = await fetch(`${base}${path}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: body == null ? undefined : JSON.stringify(body),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(formatApiErrorDetail(asRecord(data)?.detail ?? "Request failed"));
      assertApiPayloadOk(data);
      await loadState();
      const root = asRecord(data);
      const resultObj = asRecord(root?.result);
      const warning =
        (typeof root?.warning === "string" ? root.warning : null)
        ?? (typeof resultObj?.warning === "string" ? resultObj.warning : null);
      setMsg(warning ? `Warning: ${warning}` : (successMsg ?? "OK"));
      bumpSharedRevision();
      return data;
    } catch (e) {
      const msgText = e instanceof Error ? e.message : String(e);
      const hint = msgText === "Failed to fetch"
        ? " (check uav_agent.api URL/port, CORS, and whether backend was restarted)"
        : "";
      setMsg(`Action failed: ${msgText}${hint}`);
      setBusy(false);
      return null;
    }
  };

  const postApiQuiet = async (path: string, body?: unknown) => {
    const base = normalizeBaseUrl(uavApiBase);
    const res = await fetch(`${base}${path}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: body == null ? undefined : JSON.stringify(body),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(formatApiErrorDetail(asRecord(data)?.detail ?? "Request failed"));
    assertApiPayloadOk(data);
    return asRecord(data) ?? {};
  };

  const runMissionAction = async (actionInput: string, note?: string, quiet = false) => {
    const action = normalizeMissionAction(actionInput);
    const data = await postApiQuiet("/api/uav/live/mission-action", {
      user_id: ownerUserId,
      uav_id: simUavId,
      action,
      note: note ?? "",
    });
    if (!quiet) {
      setMsg(`${MISSION_ACTION_CHOICES.find((c) => c.value === action)?.label ?? action} recorded`);
    }
    return data;
  };

  const runNetworkTickQuiet = async () => {
    await postApiQuiet("/api/network/mission/tick", { steps: 1 });
  };

  const runNetworkOptimizeForActionQuiet = async (actionInput: string) => {
    const action = normalizeMissionAction(actionInput);
    const mode = missionActionToNetworkMode(action);
    await postApiQuiet("/api/network/optimize", {
      mode,
      coverage_target_pct: mode === "coverage" ? 97 : 94,
      max_tx_cap_dbm: 41.0,
      qos_priority_weight: mode === "qos" ? 72 : 60,
    });
    return mode;
  };

  const logEvent = (action: string, detail?: string) => {
    void action;
    void detail;
  };

  const addUavAtPoint = async (point: { x: number; y: number; z?: number }) => {
    const z = Number.isFinite(point.z ?? NaN) ? Number(point.z) : 0;
    const res = await postApi("/api/uav/sim/fleet/add", { user_id: ownerUserId, operator_license_id: simOperatorLicenseId, x: point.x, y: point.y, z }, "UAV added from map");
    const resultRec = asRecord(asRecord(res)?.result);
    const uavObj = asRecord(resultRec?.uav);
    const newId = typeof uavObj?.uav_id === "string" ? uavObj.uav_id : null;
    if (newId) {
      setSimUavId(newId);
      setPlannerMapClickMode("add_wp");
    }
  };

  const deleteSelectedUav = async () => {
    const victim = simUavId.trim();
    if (!victim) return;
    const candidateIds = registryUserUavIds.filter((id) => id !== victim);
    setBusy(true);
    try {
      const res = await fetch(`${normalizeBaseUrl(uavApiBase)}/api/uav/sim/fleet/delete`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ uav_id: victim }),
      });
      const data = await res.json();
      if (!res.ok || !isObject(data)) throw new Error(formatApiErrorDetail(asRecord(data)?.detail ?? "Delete UAV failed"));
      const fallbackId = candidateIds[0] ?? "";
      setSimUavId(fallbackId);
      if (candidateIds.length === 0) {
        setRouteRows(DEFAULT_SIM_ROUTE.map(waypointToRow));
        setSimRouteId("demo-route");
      }
      setMsg(`Deleted ${uiUavLabel(victim)}`);
      bumpSharedRevision();
      if (candidateIds.length > 0) {
        await loadState();
      } else {
        setFleetState({});
        setLatestPlannedRoutes({});
      }
    } catch (e) {
      const msgText = e instanceof Error ? e.message : String(e);
      setMsg(`Action failed: ${msgText}`);
    } finally {
      setBusy(false);
    }
  };

  const routeValidation = useMemo(() => {
    const rowErrors: string[][] = routeRows.map(() => []);
    const waypoints: UavWaypoint[] = [];
    const effRegs = resolveEffectiveUtmRegulationsFromState(asRecord(state)?.utm as Record<string, unknown> | undefined, effectiveOperatorLicenseId);
    const maxAlt = typeof effRegs?.max_altitude_m === "number" ? Number(effRegs.max_altitude_m) : 120;
    routeRows.forEach((row, idx) => {
      const x = Number(row.x);
      const y = Number(row.y);
      const z = Number(row.z);
      if (!Number.isFinite(x)) rowErrors[idx].push("x");
      if (!Number.isFinite(y)) rowErrors[idx].push("y");
      if (!Number.isFinite(z)) rowErrors[idx].push("z");
      if (Number.isFinite(z) && z < 0) rowErrors[idx].push("z<0");
      if (Number.isFinite(z) && z > maxAlt) rowErrors[idx].push(`z>${maxAlt}`);
      if (rowErrors[idx].length === 0) {
        waypoints.push({
          x,
          y,
          z,
          action: row.action || "transit",
          _wp_origin: row._wp_origin ?? "original",
          _wp_source: row._wp_source,
          _mapped_from_original_index: row._mapped_from_original_index,
        });
      }
    });
    const errors: string[] = [];
    if (routeRows.length < 2) errors.push("Add at least 2 waypoints.");
    rowErrors.forEach((errs, idx) => errs.length && errors.push(`Waypoint ${idx}: ${errs.join(", ")}`));
    return { rowErrors, waypoints, errors, maxAlt };
  }, [routeRows, state, effectiveOperatorLicenseId]);

  const planRoute = async () => {
    if (routeValidation.errors.length) {
      setMsg(`Route validation failed: ${routeValidation.errors[0]}`);
      return null;
    }
    const routeId = normalizeRouteIdBase(simRouteId);
    setSimRouteId(routeId);
    return postApi("/api/uav/live/plan", { user_id: ownerUserId, uav_id: simUavId, route_id: routeId, waypoints: routeValidation.waypoints }, "Route planned");
  };

  const buildVerifyInput = () => {
    const speed = Number.parseFloat(simRequestedSpeedMps);
    const startIso = localInputToIsoUtc(simPlannedStartAt);
    const endIso = localInputToIsoUtc(simPlannedEndAt);
    if (!Number.isFinite(speed) || speed <= 0) {
      setMsg("Action failed: requested speed must be a positive number");
      return null;
    }
    if (simPlannedStartAt && !startIso) {
      setMsg("Action failed: invalid planned start");
      return null;
    }
    if (simPlannedEndAt && !endIso) {
      setMsg("Action failed: invalid planned end");
      return null;
    }
    return { requested_speed_mps: speed, planned_start_at: startIso, planned_end_at: endIso };
  };

  const requestApproval = async () => {
    const verifyInput = buildVerifyInput();
    if (!verifyInput) return;
    const submitPayload = {
      user_id: ownerUserId,
      uav_id: simUavId,
      airspace_segment: simAirspace,
      operator_license_id: effectiveOperatorLicenseId,
      required_license_class: effectiveRequiredLicenseClass,
      requested_speed_mps: verifyInput.requested_speed_mps,
      planned_start_at: verifyInput.planned_start_at,
      planned_end_at: verifyInput.planned_end_at,
    };
    const res = await postApi(
      "/api/uav/live/utm-submit-mission",
      submitPayload,
      "UTM backend workflow completed (checks + geofence + verify + approval)",
    );
    if (!res) return null;
    let resultObj = asRecord(asRecord(res)?.result);
    const initialDss = pickDssIntentResultFromAggregate(resultObj);
    const initialDssStatus = String(initialDss?.status ?? "").trim().toLowerCase();
    const initialBlockingConflicts = Array.isArray(initialDss?.blocking_conflicts) ? (initialDss.blocking_conflicts as unknown[]).length : 0;
    const mitigation: DssMitigationSnapshot = {
      started_at: new Date().toISOString(),
      conflict_detected: false,
      initial_dss_status: initialDssStatus,
      initial_blocking_conflicts: initialBlockingConflicts,
      replan_status: "skipped",
      retry_submit_status: "skipped",
      final_dss_status: initialDssStatus,
      final_blocking_conflicts: initialBlockingConflicts,
      resolved: true,
      notes: [],
    };
    const dssConflictDetected = shouldAutoReplanForDssConflict(resultObj);
    mitigation.conflict_detected = dssConflictDetected;
    if (dssConflictDetected) {
      try {
        const replanRes = await postApiQuiet("/api/uav/live/replan-via-utm-nfz", {
          user_id: ownerUserId,
          uav_id: simUavId,
          airspace_segment: simAirspace,
          operator_license_id: effectiveOperatorLicenseId,
          optimization_profile: "balanced",
          auto_utm_verify: true,
          route_category: "dss_replanned",
          replan_context: "dss_conflict_mitigation",
          user_request: "Auto detour replan to resolve DSS strategic conflict before mission launch",
        });
        const replanStatus = String(replanRes.status ?? "").trim().toLowerCase();
        if (replanStatus === "success") {
          mitigation.replan_status = "success";
          setPlannerShowDssPath(true);
          const retryRes = await postApiQuiet("/api/uav/live/utm-submit-mission", submitPayload);
          resultObj = asRecord(retryRes.result) ?? resultObj;
          mitigation.retry_submit_status = "success";
          const stillBlocked = shouldAutoReplanForDssConflict(resultObj);
          mitigation.resolved = !stillBlocked;
          if (stillBlocked) {
            mitigation.notes.push("DSS conflict still present after retry submit");
            setMsg("DSS conflict detected: auto detour replan applied, but retry submit is still blocked.");
          } else {
            mitigation.notes.push("DSS conflict cleared after auto detour replan + retry");
            setMsg("DSS conflict detected: auto detour replan applied and mission submit retried.");
          }
        } else {
          mitigation.replan_status = "failed";
          mitigation.resolved = false;
          mitigation.notes.push("Auto detour replan did not complete");
          setMsg("DSS conflict detected: auto detour replan did not complete.");
        }
        await loadState();
        bumpSharedRevision();
      } catch (e) {
        mitigation.replan_status = "failed";
        mitigation.retry_submit_status = "failed";
        mitigation.resolved = false;
        mitigation.notes.push(e instanceof Error ? e.message : String(e));
        setMsg(`DSS conflict detected, but auto detour replan failed: ${e instanceof Error ? e.message : String(e)}`);
      }
    } else {
      mitigation.notes.push("No DSS strategic conflict detected on submit");
    }
    const finalDss = pickDssIntentResultFromAggregate(resultObj);
    mitigation.final_dss_status = String(finalDss?.status ?? "").trim().toLowerCase();
    mitigation.final_blocking_conflicts = Array.isArray(finalDss?.blocking_conflicts) ? (finalDss.blocking_conflicts as unknown[]).length : 0;
    if (!dssConflictDetected) mitigation.resolved = true;
    setLastDssMitigation(mitigation);
    setLastUtmSubmitResult(resultObj ?? null);
    const approvalReq = asRecord(resultObj?.approval_request);
    const approvalReqResult = asRecord(approvalReq?.result);
    const approvalInner = asRecord(approvalReqResult?.result);
    const approvalObj = asRecord(approvalInner?.approval ?? approvalReqResult?.approval ?? resultObj?.approval);
    if (approvalObj?.approved === true) {
      setPlannerEditorSource("utm_confirmed");
      setPlannerShowUtmPath(true);
      setPlannerShowAgentPath(false);
      setPlannerShowDssPath(false);
      setPlannerShowUserPath(false);
      setPlannerMapResetSeq((v) => v + 1);
      // Wait a tick for loadState() from postApi to refresh mission_paths, then load the DB-approved path into editor.
      window.setTimeout(() => {
        focusPathRecordInPlanner("utm_confirmed");
      }, 0);
    }
    return res;
  };

  const verifyMissionPlannerWithUtm = async () => {
    if (routeValidation.errors.length) {
      setMsg(`Route validation failed: ${routeValidation.errors[0]}`);
      return;
    }
    // Preserve separation: only update User Planned DB path from explicit user-planned editing flow.
    if (plannerEditorSource === "user_planned") {
      const planRes = await planRoute();
      if (!planRes) return;
    } else {
      const sourceLabel = plannerPathSourceLabel(plannerEditorSource);
      setMsg(`Submitting current ${sourceLabel} route to UTM without overwriting User Planned path`);
    }
    await requestApproval();
  };

  const reloadPlannerWaypointsFromDb = (source: PlannerPathSourceKey) => {
    const rows = missionPathSourceRows[source] ?? [];
    if (rows.length < 2) {
      setMsg(`No ${plannerPathSourceLabel(source)} path in DB for this UAV`);
      return;
    }
    setRouteRows(rows.map((r) => ({ ...r })));
    const srcRow = asRecord(missionPaths?.[source]);
    const srcRouteId = typeof srcRow?.route_id === "string" ? srcRow.route_id : null;
    if (srcRouteId) setSimRouteId(srcRouteId);
    setMsg(`Waypoint editor reloaded from ${plannerPathSourceLabel(source)} (DB)`);
  };
  const focusPathRecordInPlanner = (source: PlannerPathSourceKey) => {
    setPlannerShowUserPath(source === "user_planned");
    setPlannerShowAgentPath(source === "agent_replanned");
    setPlannerShowDssPath(source === "dss_replanned");
    setPlannerShowUtmPath(source === "utm_confirmed");
    setPlannerEditorSource(source);
    reloadPlannerWaypointsFromDb(source);
    setPlannerMapResetSeq((v) => v + 1);
    setMsg(`Loaded ${plannerPathSourceLabel(source)} path to editor + map`);
  };
  const deletePathRecord = async (source: PlannerPathSourceKey) => {
    const res = await postApi(
      "/api/uav/path-records/delete",
      { user_id: ownerUserId, uav_id: simUavId, category: source },
      `Deleted ${plannerPathSourceLabel(source)} path record`,
    );
    if (!res) return;
    if (source === "user_planned") setPlannerShowUserPath(false);
    if (source === "agent_replanned") setPlannerShowAgentPath(false);
    if (source === "dss_replanned") setPlannerShowDssPath(false);
    if (source === "utm_confirmed") setPlannerShowUtmPath(false);
    setPlannerMapResetSeq((v) => v + 1);
  };

  const step = async () => {
    const ticks = Math.max(1, Number.parseInt(simTicks || "1", 10) || 1);
    const res = await postApi("/api/uav/live/step", { user_id: ownerUserId, uav_id: simUavId, ticks }, `Stepped ${ticks}`);
    if (!res) return;
    logEvent("step", `${ticks} tick${ticks === 1 ? "" : "s"}`);
    const out = asRecord(res);
    const snap = asRecord(out?.result);
    const phase = String(snap?.flight_phase ?? "").toUpperCase();
    const active = snap?.active === true;
    const wpIndex = Number(snap?.waypoint_index ?? NaN);
    const wpTotal = Number(snap?.waypoints_total ?? NaN);
    const reachedLastWaypoint = Number.isFinite(wpIndex) && Number.isFinite(wpTotal) && wpTotal > 0 && wpIndex >= (wpTotal - 1);
    const stepFinished = (!active && (phase === "ARRIVAL" || phase === "LOITER")) || (reachedLastWaypoint && !active);
    if (stepFinished) {
      setMsg("Step finished. Please Return Home, then End Mission.");
    }
  };
  const launchAndLog = async () => {
    const res = await postApi(`/api/uav/live/launch?uav_id=${encodeURIComponent(simUavId)}&user_id=${encodeURIComponent(ownerUserId)}`, undefined, "Launch sent");
    if (res) logEvent("launch");
  };
  const hold = async () => {
    const reason = holdReason.trim() || "operator_request";
    const res = await postApi("/api/uav/live/hold", { user_id: ownerUserId, uav_id: simUavId, reason }, "Hold command sent");
    if (res) logEvent("hold", reason);
  };
  const resume = async () => {
    const res = await postApi(`/api/uav/live/resume?uav_id=${encodeURIComponent(simUavId)}&user_id=${encodeURIComponent(ownerUserId)}`, undefined, "Resume command sent");
    if (res) logEvent("resume");
  };
  const rth = async () => {
    const res = await postApi(`/api/uav/live/rth?uav_id=${encodeURIComponent(simUavId)}&user_id=${encodeURIComponent(ownerUserId)}`, undefined, "Return-to-home sent");
    if (res) logEvent("rth");
  };
  const land = async () => {
    const res = await postApi(`/api/uav/live/land?uav_id=${encodeURIComponent(simUavId)}&user_id=${encodeURIComponent(ownerUserId)}`, undefined, "Land command sent");
    if (res) logEvent("land");
  };
  const refreshUavTelemetry = async () => {
    const payload: Record<string, unknown> = {
      uav_id: simUavId,
      battery_pct: 100,
      source: "simulated",
      source_ref: "uav_page_refresh",
      observed_at: new Date().toISOString(),
    };
    const routeId = typeof uav?.route_id === "string" ? uav.route_id.trim() : "";
    if (routeId) payload.route_id = routeId;
    const pos = asRecord(uav?.position);
    const x = Number(pos?.x);
    const y = Number(pos?.y);
    const z = Number(pos?.z);
    if (Number.isFinite(x) && Number.isFinite(y) && Number.isFinite(z)) {
      payload.position = { x, y, z };
    }
    const wpIdx = Number(uav?.waypoint_index);
    if (Number.isFinite(wpIdx)) payload.waypoint_index = Math.max(0, Math.trunc(wpIdx));
    const speed = Number(uav?.velocity_mps);
    if (Number.isFinite(speed)) payload.velocity_mps = speed;
    if (typeof uav?.flight_phase === "string" && uav.flight_phase.trim()) payload.flight_phase = uav.flight_phase;
    if (typeof uav?.armed === "boolean") payload.armed = uav.armed;
    if (typeof uav?.active === "boolean") payload.active = uav.active;
    const res = await postApi("/api/uav/live/ingest", payload, `Refreshed ${simUavId} telemetry (battery 100%)`);
    if (res) logEvent("uav_refresh", "battery + telemetry sync");
  };
  const endMissionAndCleanup = async () => {
    if (!window.confirm(`End mission for ${simUavId} and clean DSS/session state for next launch?`)) return;
    const res = await postApi(
      `/api/uav/live/end-mission?uav_id=${encodeURIComponent(simUavId)}&user_id=${encodeURIComponent(ownerUserId)}&cleanup_stale=true`,
      undefined,
      "Mission ended and cleanup completed",
    );
    if (res) logEvent("end_mission_cleanup");
  };
  const runMissionActionChoice = async () => {
    if (!simUavId.trim()) {
      setMsg("Warning: select a UAV first.");
      return;
    }
    const armed = uav?.armed === true;
    const active = uav?.active === true;
    if (!armed) {
      setMsg("Warning: launch UAV before running mission actions.");
      return;
    }
    if (!active) {
      setMsg("Warning: mission actions require UAV in active flight.");
      return;
    }
    try {
      const mode = await runNetworkOptimizeForActionQuiet(missionActionChoice);
      await runMissionAction(missionActionChoice, "manual_operator_action", true);
      await runNetworkTickQuiet();
      await loadState();
      setMsg(`Action ${missionActionChoice} applied (network mode: ${mode}).`);
    } catch (e) {
      setMsg(`Action failed: ${e instanceof Error ? e.message : String(e)}`);
    }
  };

  useEffect(() => {
    if (!autoFlyEnabled) return;
    let stopped = false;
    const intervalMs = 1200;

    const runCycle = async () => {
      if (stopped || autoFlyCycleBusyRef.current) return;
      autoFlyCycleBusyRef.current = true;
      try {
        const currentUav = uavRef.current;
        const currentArmed = currentUav?.armed === true;
        const currentActive = currentUav?.active === true;
        if (!currentArmed || !currentActive) {
          setMsg("Auto fly stopped: launch and keep UAV active first.");
          setAutoFlyEnabled(false);
          return;
        }

        const ticks = Math.max(1, Number.parseInt(simTicks || "1", 10) || 1);
        const stepOut = await postApiQuiet("/api/uav/live/step", { user_id: ownerUserId, uav_id: simUavId, ticks });
        const stepSnap = asRecord(stepOut.result);
        const wpIndex = Number(stepSnap?.waypoint_index ?? NaN);
        const waypoints = Array.isArray(stepSnap?.waypoints) ? (stepSnap!.waypoints as unknown[]) : [];
        if (Number.isFinite(wpIndex) && wpIndex !== autoFlyLastWaypointRef.current && wpIndex >= 0) {
          autoFlyLastWaypointRef.current = wpIndex;
          const row = (wpIndex >= 0 && wpIndex < waypoints.length && isObject(waypoints[wpIndex])) ? (waypoints[wpIndex] as Record<string, unknown>) : null;
          const rawWpAction = String(row?.action ?? "transit").trim().toLowerCase();
          if (rawWpAction !== "transit") {
            const wpAction = normalizeMissionAction(rawWpAction);
            await runNetworkOptimizeForActionQuiet(wpAction);
            await runMissionAction(wpAction, `auto_waypoint_${wpIndex}`, true);
          }
        }

        await runNetworkTickQuiet();
        await loadState();

        const active = stepSnap?.active === true;
        const idx = Number(stepSnap?.waypoint_index ?? NaN);
        const total = Number(stepSnap?.waypoints_total ?? NaN);
        const complete = Number.isFinite(idx) && Number.isFinite(total) && total > 0 && idx >= (total - 1) && !active;
        if (complete) {
          await postApiQuiet(
            `/api/uav/live/rth?uav_id=${encodeURIComponent(simUavId)}&user_id=${encodeURIComponent(ownerUserId)}`,
            undefined,
          );
          await postApiQuiet(
            `/api/uav/live/land?uav_id=${encodeURIComponent(simUavId)}&user_id=${encodeURIComponent(ownerUserId)}`,
            undefined,
          );
          await runNetworkTickQuiet();
          await loadState();
          setMsg("Auto fly complete: final waypoint reached, returned home, and landed.");
          setAutoFlyEnabled(false);
        }
      } catch (e) {
        setMsg(`Auto fly stopped: ${e instanceof Error ? e.message : String(e)}`);
        setAutoFlyEnabled(false);
      } finally {
        autoFlyCycleBusyRef.current = false;
      }
    };

    void runCycle();
    const timer = window.setInterval(() => {
      void runCycle();
    }, intervalMs);
    return () => {
      stopped = true;
      window.clearInterval(timer);
    };
  }, [autoFlyEnabled, ownerUserId, simTicks, simUavId, uavApiBase]);

  const runAgentCopilot = async () => {
    const rawPrompt = agentPrompt;
    const parsed = parseCopilotPromptDirectives(rawPrompt);
    const outgoingPrompt = parsed.cleanedPrompt || "Review the current UAV route, apply safe mission-aware optimization if useful, check UTM/NFZ constraints, and optimize network conditions when enabled.";
    const hasProfileDirective = /@(safe|balanced|aggressive)\b/i.test(rawPrompt);
    const hasNetworkModeDirective = /@(qos|coverage|power)\b/i.test(rawPrompt);
    const hasVerifyDirective = /@(verify|verify-on|verify-off)\b/i.test(rawPrompt);
    const hasNetworkDirective = /@(network|network-on|network-off)\b/i.test(rawPrompt);
    const effectiveProfile = hasProfileDirective ? parsed.profile : agentOptimizationProfile;
    const effectiveAutoVerify = hasVerifyDirective ? parsed.autoVerify : agentAutoVerify;
    const effectiveAutoNetwork = hasNetworkDirective ? parsed.autoNetworkOptimize : agentAutoNetworkOptimize;
    const effectiveNetworkMode = effectiveAutoNetwork
      ? (hasNetworkModeDirective ? parsed.networkMode : agentPreferredNetworkMode)
      : null;
    const looksLikeRouteAction = /\b(replan|route|path|waypoint|nfz|no[- ]?fly|detour|optimi[sz]e)\b/i.test(outgoingPrompt);
    const looksLikeNetworkAction = /\b(network|coverage|qos|latency|signal|sinr|power)\b/i.test(outgoingPrompt);
    const hasActionIntent = looksLikeRouteAction || looksLikeNetworkAction || effectiveAutoVerify || effectiveAutoNetwork;
    if (routeValidation.errors.length) {
      setAgentStatusMsg(`Route validation failed: ${routeValidation.errors[0]}`);
      return;
    }
    setAgentStatusMsg("");
    setAgentOptimizationProfile(effectiveProfile);
    setAgentBusy(true);
    const ts = new Date().toLocaleTimeString();
    const pendingId = `a-pending-${Date.now()}`;
    const userMsg: CopilotMessage = { id: `u-${Date.now()}`, role: "user", text: outgoingPrompt + (effectiveProfile !== "balanced" ? ` (${effectiveProfile})` : ""), ts };
    const pendingMsg: CopilotMessage = {
      id: pendingId,
      role: "assistant",
      lines: [hasActionIntent ? "Starting copilot workflow..." : "Thinking..."],
      toolTrace: [],
      raw: null,
      ts,
      pending: true,
    };
    setAgentConversation((prev) => [...prev, userMsg, pendingMsg].slice(-24));
    const progressSteps = hasActionIntent
      ? [
          "Analyzing mission context (route, UTM, network, UAV state)...",
          "Planning tool actions...",
          "Executing selected actions and collecting results...",
          "Summarizing outcome...",
        ]
      : [
          "Reading your message...",
          "Checking mission context...",
          "Preparing a response...",
        ];
    progressSteps.forEach((_, i) => {
      window.setTimeout(() => {
        setAgentConversation((prev) =>
          prev.map((m) =>
            m.id === pendingId && m.role === "assistant" && m.pending
              ? { ...m, lines: progressSteps.slice(0, i + 1) }
              : m,
          ),
        );
      }, 180 + i * 240);
    });
    try {
      const base = normalizeBaseUrl(uavApiBase);
      const res = await fetch(`${base}/api/uav/agent/chat`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          uav_id: simUavId,
          airspace_segment: simAirspace,
          prompt: outgoingPrompt,
          route_id: normalizeRouteIdBase(simRouteId),
          waypoints: routeValidation.waypoints,
          optimization_profile: effectiveProfile,
          operator_license_id: effectiveOperatorLicenseId,
          network_mode: effectiveNetworkMode,
          auto_verify: effectiveAutoVerify,
          auto_network_optimize: effectiveAutoNetwork,
        }),
      });
      const data = await res.json();
      if (!res.ok || !isObject(data)) throw new Error(String(asRecord(data)?.detail ?? "Agent request failed"));
      const result = asRecord((data as Record<string, unknown>).result);
      const msgs = Array.isArray(result?.messages) ? (result!.messages as unknown[]).map((m) => String(m)) : [];
      const trace = Array.isArray(result?.toolTrace)
        ? (result!.toolTrace as unknown[]).filter(isObject).map((t) => t as Record<string, unknown>)
        : [];
      const replanResult = asRecord(result?.replan);
      const replanSucceeded = String(replanResult?.status ?? "") === "success" || trace.some((t) => String(t.tool ?? "") === "uav_replan_route_via_utm_nfz" && String(t.status ?? "") === "success");
      const dssReplanDetected = trace.some((t) => {
        if (String(t.tool ?? "") !== "uav_replan_route_via_utm_nfz" || String(t.status ?? "") !== "success") return false;
        const reason = String(t.reason ?? "").toLowerCase();
        const replanContext = String(t.replan_context ?? "").toLowerCase();
        return (reason.includes("dss") && (reason.includes("conflict") || reason.includes("strategic"))) || (replanContext.includes("dss") && replanContext.includes("conflict"));
      });
      const assistantMsg: CopilotMessage = { id: `a-${Date.now()}-1`, role: "assistant", lines: msgs, toolTrace: trace, raw: result, ts };
      setAgentConversation((prev) => [...prev.filter((m) => m.id !== pendingId), assistantMsg].slice(-24));
      setAgentPrompt("");
      const uavObj = asRecord(result?.uav);
      const nextRouteId = typeof uavObj?.route_id === "string" ? uavObj.route_id : null;
      const points = Array.isArray(uavObj?.waypoints)
        ? (uavObj!.waypoints as unknown[])
            .filter(isObject)
            .map((w) => ({
              x: Number((w as Record<string, unknown>).x ?? 0),
              y: Number((w as Record<string, unknown>).y ?? 0),
              z: Number((w as Record<string, unknown>).z ?? 0),
              action: String((w as Record<string, unknown>).action ?? "transit") as WaypointAction,
            }))
        : [];
      if (nextRouteId) setSimRouteId(nextRouteId);
      if (points.length >= 2) setRouteRows(points.map(waypointToRow));
      if (replanSucceeded) {
        if (dssReplanDetected) {
          setPlannerEditorSource("dss_replanned");
          setPlannerShowDssPath(true);
        } else {
          setPlannerEditorSource("agent_replanned");
          setPlannerShowAgentPath(true);
        }
      }
      logEvent("agent_copilot", msgs.join(" | ") || "Agent workflow completed");
      await loadState();
      bumpSharedRevision();
      setAgentStatusMsg("Agent copilot completed.");
    } catch (e) {
      setAgentStatusMsg(`Agent copilot failed: ${e instanceof Error ? e.message : String(e)}`);
      const errMsg: CopilotMessage = {
        id: pendingId,
        role: "assistant",
        lines: [`Error: ${e instanceof Error ? e.message : String(e)}`],
        toolTrace: [],
        raw: null,
        ts: new Date().toLocaleTimeString(),
        pending: false,
      };
      setAgentConversation((prev) => [...prev.filter((m) => m.id !== pendingId), errMsg].slice(-24));
    } finally {
      setAgentBusy(false);
    }
  };

  const uav = asRecord(state?.uav);
  const identity = asRecord((state as Record<string, unknown> | null)?.identity);
  const sessionInfo = asRecord((state as Record<string, unknown> | null)?.session);
  const currentMission = asRecord((state as Record<string, unknown> | null)?.current_mission);
  const missionPaths = asRecord((state as Record<string, unknown> | null)?.mission_paths);
  const pathRecords = asRecord((state as Record<string, unknown> | null)?.path_records);
  const pathRecordsRows = asRecord(pathRecords?.rows);
  const uavDataSource = asRecord((state as Record<string, unknown> | null)?.dataSource);
  const currentRouteChecks = asRecord(asRecord(state?.utm)?.current_route_checks);
  const currentRouteChecksGeofence = asRecord(currentRouteChecks?.geofence);
  const currentRouteChecksTimeWindow = asRecord(currentRouteChecks?.time_window);
  const missionPathSourceRows = useMemo<Record<PlannerPathSourceKey, EditableWaypointRow[]>>(() => {
    const keys: PlannerPathSourceKey[] = ["user_planned", "agent_replanned", "dss_replanned", "utm_confirmed"];
    return Object.fromEntries(
      keys.map((key) => {
        const row = asRecord(missionPaths?.[key]);
        const wps = Array.isArray(row?.waypoints) ? (row!.waypoints as unknown[]).filter(isObject) : [];
        const rows = wps
          .map((w) => ({
            x: Number((w as Record<string, unknown>).x ?? NaN),
            y: Number((w as Record<string, unknown>).y ?? NaN),
            z: Number((w as Record<string, unknown>).z ?? NaN),
            action: String((w as Record<string, unknown>).action ?? "transit") as WaypointAction,
            _wp_origin: String((w as Record<string, unknown>)._wp_origin ?? "original") === "agent_inserted" ? "agent_inserted" as const : "original" as const,
            _wp_source: typeof (w as Record<string, unknown>)._wp_source === "string" ? String((w as Record<string, unknown>)._wp_source) : undefined,
            _mapped_from_original_index:
              typeof (w as Record<string, unknown>)._mapped_from_original_index === "number"
                ? Number((w as Record<string, unknown>)._mapped_from_original_index)
                : undefined,
          }))
          .filter((w) => [w.x, w.y, w.z].every(Number.isFinite))
          .map((w) => ({
            x: String(w.x),
            y: String(w.y),
            z: String(w.z),
            action: w.action,
            _wp_origin: w._wp_origin,
            _wp_source: w._wp_source,
            _mapped_from_original_index: w._mapped_from_original_index,
          }));
        return [key, rows];
      }),
    ) as Record<PlannerPathSourceKey, EditableWaypointRow[]>;
  }, [missionPaths]);
  const availablePlannerSources = useMemo(
    () => (Object.entries(missionPathSourceRows) as Array<[PlannerPathSourceKey, EditableWaypointRow[]]>)
      .filter(([, rows]) => rows.length >= 2)
      .map(([k]) => k),
    [missionPathSourceRows],
  );
  const plannerPathSourceLabel = (key: PlannerPathSourceKey) => (
    key === "user_planned"
      ? "User Planned"
      : key === "agent_replanned"
        ? "Agent Replanned"
        : key === "dss_replanned"
          ? "DSS Replanned"
          : "UTM Confirmed"
  );
  const pathRecordTableRows = useMemo(
    () => (["user_planned", "agent_replanned", "dss_replanned", "utm_confirmed"] as PlannerPathSourceKey[]).map((key) => {
      const summary = asRecord(pathRecordsRows?.[key]);
      const summaryMetrics = asRecord(summary?.metrics);
      const summaryOriginCounts = asRecord(summaryMetrics?.waypoint_origin_counts ?? summaryMetrics?.waypointOriginCounts);
      const summaryReplanStats = asRecord(summary?.replan_stats ?? summary?.replanStats);
      const fallbackMissionRow = asRecord(missionPaths?.[key]);
      const fallbackMeta = asRecord(fallbackMissionRow?.metadata);
      const fallbackWps = Array.isArray(fallbackMissionRow?.waypoints) ? (fallbackMissionRow!.waypoints as unknown[]).filter(isObject) : [];
      const originCounts = fallbackWps.reduce<{ original: number; inserted: number }>(
        (acc, p) => {
          const origin = String(asRecord(p)?._wp_origin ?? "original");
          if (origin === "agent_inserted") acc.inserted += 1;
          else acc.original += 1;
          return acc;
        },
        { original: 0, inserted: 0 },
      );
      const fallbackStart = fallbackWps[0];
      const fallbackEnd = fallbackWps[fallbackWps.length - 1];
      const exists = summary?.exists === true || fallbackWps.length >= 1;
      const dbPresence = asRecord(summary?.db_presence);
      return {
        key,
        label: String(summary?.label ?? plannerPathSourceLabel(key)),
        color: String(summary?.color ?? plannerPathColor(key)),
        exists,
        routeId: typeof summary?.route_id === "string" ? summary.route_id : (typeof fallbackMissionRow?.route_id === "string" ? fallbackMissionRow.route_id : "-"),
        missionId: typeof summary?.mission_id === "string" ? summary.mission_id : (typeof asRecord(fallbackMissionRow?.metadata)?.mission_id === "string" ? String(asRecord(fallbackMissionRow?.metadata)?.mission_id) : "-"),
        source: typeof summary?.source === "string" ? summary.source : (typeof fallbackMissionRow?.source === "string" ? fallbackMissionRow.source : "-"),
        userId: typeof summary?.user_id === "string" ? summary.user_id : String(identity?.selected_user_id ?? ownerUserId),
        uavId: typeof summary?.uav_id === "string" ? summary.uav_id : String(identity?.selected_uav_id ?? simUavId),
        waypointsTotal: Number(summaryMetrics?.waypoints_total ?? fallbackWps.length ?? 0),
        start: summaryMetrics?.start ?? fallbackStart ?? null,
        end: summaryMetrics?.end ?? fallbackEnd ?? null,
        estFlightSeconds: summaryMetrics?.estimated_flight_seconds ?? null,
        distanceM: summaryMetrics?.distance_m ?? null,
        originOriginalCount: typeof summaryOriginCounts?.original === "number" ? Number(summaryOriginCounts.original) : originCounts.original,
        originInsertedCount: typeof summaryOriginCounts?.agent_inserted === "number" ? Number(summaryOriginCounts.agent_inserted) : originCounts.inserted,
        losPruneDeletionsCount:
          typeof summaryReplanStats?.los_prune_deletions_count === "number"
            ? Number(summaryReplanStats.los_prune_deletions_count)
            : (typeof fallbackMeta?.los_prune_deletions_count === "number" ? Number(fallbackMeta.los_prune_deletions_count) : null),
        losPrunePassesCount:
          typeof summaryReplanStats?.los_prune_passes_count === "number"
            ? Number(summaryReplanStats.los_prune_passes_count)
            : (typeof fallbackMeta?.los_prune_passes_count === "number" ? Number(fallbackMeta.los_prune_passes_count) : null),
        insertedWaypointsCount:
          typeof summaryReplanStats?.inserted_waypoints_count === "number"
            ? Number(summaryReplanStats.inserted_waypoints_count)
            : (typeof fallbackMeta?.inserted_waypoints_count === "number" ? Number(fallbackMeta.inserted_waypoints_count) : null),
        insertedTrimDeletionsCount:
          typeof summaryReplanStats?.inserted_trim_deletions_count === "number"
            ? Number(summaryReplanStats.inserted_trim_deletions_count)
            : (typeof fallbackMeta?.inserted_trim_deletions_count === "number" ? Number(fallbackMeta.inserted_trim_deletions_count) : null),
        insertedTrimPassesCount:
          typeof summaryReplanStats?.inserted_trim_passes_count === "number"
            ? Number(summaryReplanStats.inserted_trim_passes_count)
            : (typeof fallbackMeta?.inserted_trim_passes_count === "number" ? Number(fallbackMeta.inserted_trim_passes_count) : null),
        createdAt: typeof summary?.created_at === "string" ? summary.created_at : "-",
        inUavDb: dbPresence?.uav_db === true || !!fallbackMissionRow,
        inUtmDb: dbPresence?.utm_db === true,
      };
    }),
    [pathRecordsRows, missionPaths, identity, ownerUserId, simUavId],
  );
  const currentMissionRouteOrigin = useMemo(() => {
    const currentRouteId = String(uav?.route_id ?? "");
    const entries: Array<[PlannerPathSourceKey, Record<string, unknown> | null]> = [
      ["utm_confirmed", asRecord(missionPaths?.utm_confirmed)],
      ["dss_replanned", asRecord(missionPaths?.dss_replanned)],
      ["agent_replanned", asRecord(missionPaths?.agent_replanned)],
      ["user_planned", asRecord(missionPaths?.user_planned)],
    ];
    for (const [key, row] of entries) {
      if (String(row?.route_id ?? "") && String(row?.route_id ?? "") === currentRouteId) return key;
    }
    return null;
  }, [missionPaths, uav]);
  useEffect(() => {
    if (availablePlannerSources.includes(plannerEditorSource)) return;
    if (availablePlannerSources.includes("utm_confirmed")) {
      setPlannerEditorSource("utm_confirmed");
      return;
    }
    if (availablePlannerSources.includes("dss_replanned")) {
      setPlannerEditorSource("dss_replanned");
      return;
    }
    if (availablePlannerSources.includes("agent_replanned")) {
      setPlannerEditorSource("agent_replanned");
      return;
    }
    if (availablePlannerSources.includes("user_planned")) {
      setPlannerEditorSource("user_planned");
    }
  }, [availablePlannerSources, plannerEditorSource]);
  // Do not auto-overwrite the waypoint editor whenever mission path records refresh.
  // Replan/verify actions call `loadState()`, and auto-applying a selected DB source here can
  // immediately replace the fresh replanned route with an older stored path. Use explicit
  // "Load WPs" actions from the Path Records card when the operator wants to sync editor rows.
  const pathRecordSync = asRecord(pathRecords?.sync);
  const unwrapBackendActionResult = (x: unknown) => {
    const root = asRecord(x);
    return asRecord(root?.result ?? root);
  };
  const isCurrentScopeAction = (item: AgentActionLogItem) => {
    const payload = asRecord(item.payload);
    const payloadUav = String(payload?.uav_id ?? payload?.uavId ?? "");
    const payloadUser = String(payload?.user_id ?? payload?.userId ?? "");
    const wantUav = String(simUavId ?? "");
    const wantUser = String(identity?.selected_user_id ?? ownerUserId ?? "");
    if (payloadUav && payloadUav !== wantUav) return false;
    if (payloadUser && payloadUser !== wantUser) return false;
    return true;
  };
  const scopedBackendActionLog = useMemo(
    () => backendActionLog.filter(isCurrentScopeAction),
    [backendActionLog, simUavId, identity, ownerUserId],
  );
  const latestVerifyAction = scopedBackendActionLog.find((item) => item.action.toLowerCase().includes("verify"));
  const latestGeofenceAction = scopedBackendActionLog.find((item) => item.action.toLowerCase().includes("geofence"));
  const sessionApproval = asRecord(sessionInfo?.utm_approval);
  const sessionGeofence = asRecord(sessionInfo?.utm_geofence_result);
  const approval = sessionApproval ?? asRecord(uav?.utm_approval) ?? unwrapBackendActionResult(latestVerifyAction?.result);
  const checks = asRecord(approval?.checks);
  const timeWindowCheck = asRecord(checks?.time_window) ?? currentRouteChecksTimeWindow;
  const flightTimeSummary = useMemo(() => {
    const start = String(timeWindowCheck?.planned_start_at ?? "");
    const end = String(timeWindowCheck?.planned_end_at ?? "");
    const maxMin = Number(timeWindowCheck?.max_mission_duration_min ?? NaN);
    const startMs = start ? Date.parse(start) : NaN;
    const endMs = end ? Date.parse(end) : NaN;
    const durMin = Number.isFinite(startMs) && Number.isFinite(endMs) && endMs > startMs
      ? (endMs - startMs) / 60000
      : NaN;
    if (Number.isFinite(durMin) && Number.isFinite(maxMin) && maxMin > 0) {
      return `${durMin.toFixed(1)} / ${Math.trunc(maxMin)} min`;
    }
    if (Number.isFinite(maxMin) && maxMin > 0) return `max ${Math.trunc(maxMin)} min`;
    return "-";
  }, [timeWindowCheck]);
  const geofence = sessionGeofence ?? asRecord(uav?.utm_geofence_result) ?? unwrapBackendActionResult(latestGeofenceAction?.result) ?? currentRouteChecksGeofence;
  const approvalRouteBounds = asRecord(checks?.route_bounds);
  const routeBoundsOk = approvalRouteBounds?.ok ?? approvalRouteBounds?.geofence_ok ?? approvalRouteBounds?.bounds_ok ?? geofence?.geofence_ok ?? geofence?.bounds_ok ?? geofence?.ok;
  const utmObj = asRecord(state?.utm);
  const backendFlightGate = asRecord((state as Record<string, unknown> | null)?.flight_gate);
  const stationState = asRecord(state?.uav_station_state);
  const utmBackendWeatherCheck = asRecord(utmBackendState?.weatherChecks);
  const utmBackendDss = asRecord(utmBackendState?.dss);
  const layeredStatus = asRecord(utmLayeredStatus ?? utmBackendState?.layeredStatus);
  const layeredLayers = asRecord(layeredStatus?.layers);
  const layeredUss = asRecord(layeredLayers?.uss);
  const layeredDss = asRecord(layeredLayers?.dss);
  const selectedLayeredUavCard = useMemo(() => {
    const cards = Array.isArray(layeredStatus?.uav_status_cards)
      ? (layeredStatus.uav_status_cards as unknown[]).filter(isObject).map((row) => row as Record<string, unknown>)
      : [];
    const selectedId = simUavId.trim();
    if (!selectedId) return null;
    return cards.find((row) => String(row.uav_id ?? "").trim() === selectedId) ?? null;
  }, [layeredStatus, simUavId]);
  const dispatch = asRecord(utmDispatchStatus);
  const dispatchCounts = asRecord(dispatch?.counts);
  const lastSubmitRouteChecksEnvelope = asRecord(lastUtmSubmitResult?.route_checks);
  const lastSubmitRouteChecksResult = asRecord(lastSubmitRouteChecksEnvelope?.result);
  const lastSubmitRouteChecks = asRecord(lastSubmitRouteChecksResult?.result ?? lastSubmitRouteChecksEnvelope?.result);
  const lastSubmitGeofenceEnvelope = asRecord(lastUtmSubmitResult?.geofence_submit);
  const lastSubmitGeofenceResult = asRecord(lastSubmitGeofenceEnvelope?.result);
  const lastSubmitGeofence = asRecord(lastSubmitGeofenceResult?.geofence ?? asRecord(lastSubmitGeofenceResult?.result)?.geofence);
  const lastSubmitApprovalReq = asRecord(lastUtmSubmitResult?.approval_request);
  const lastSubmitApprovalResult = asRecord(lastSubmitApprovalReq?.result);
  const lastSubmitApprovalResultInner = asRecord(lastSubmitApprovalResult?.result);
  const lastSubmitApprovalObj = asRecord(lastSubmitApprovalResultInner?.approval ?? lastSubmitApprovalResult?.approval);
  const lastSubmitVerify = asRecord(asRecord(lastUtmSubmitResult?.verify_from_uav)?.result);
  const lastSubmitDecision = asRecord(lastSubmitVerify?.decision);
  const lastSubmitSuggestions = useMemo(
    () => (Array.isArray(lastSubmitDecision?.suggestions) ? (lastSubmitDecision.suggestions as unknown[]).map(String).filter(Boolean).slice(0, 5) : []),
    [lastSubmitDecision],
  );
  const lastSubmitDssResult = useMemo(() => pickDssIntentResultFromAggregate(lastUtmSubmitResult), [lastUtmSubmitResult]);
  const lastSubmitStatusText = useMemo(() => {
    if (!lastUtmSubmitResult) return "NOT SUBMITTED";
    const approved = lastUtmSubmitResult.approved;
    if (approved === true) return "APPROVED";
    if (approved === false) return "REJECTED";
    const verifyApproved = lastSubmitVerify?.approved;
    if (verifyApproved === true) return "VERIFIED";
    if (verifyApproved === false) return "VERIFY FAILED";
    return "UNKNOWN";
  }, [lastSubmitVerify, lastUtmSubmitResult]);
  const hasProcedureData = Boolean(lastUtmSubmitResult);
  const displayedApproval = asRecord(lastSubmitApprovalObj ?? lastSubmitVerify ?? approval);
  const displayedChecks = asRecord(displayedApproval?.checks);
  const displayedAuthorization = asRecord(
    displayedApproval?.authorization
    ?? asRecord(displayedChecks?.operator_license)?.authorization,
  );
  const lastSubmitWorkflowRows = useMemo(() => {
    if (!lastUtmSubmitResult) return [];
    const rows: Array<{ label: string; ok: boolean | null; detail: string }> = [];
    const routeFlags: Array<[string, unknown]> = [
      ["geofence", asRecord(lastSubmitRouteChecks?.geofence)?.ok],
      ["no-fly-zone", asRecord(lastSubmitRouteChecks?.no_fly_zone)?.ok],
      ["regulations", asRecord(lastSubmitRouteChecks?.regulations)?.ok],
    ];
    const routeFails = routeFlags.filter(([, ok]) => ok === false).map(([name]) => name);
    const routeKnown = routeFlags.filter(([, ok]) => typeof ok === "boolean");
    const routeOk = routeFails.length > 0 ? false : (routeKnown.length > 0 && routeKnown.every(([, ok]) => ok === true) ? true : null);
    rows.push({
      label: "1) Route checks",
      ok: routeOk,
      detail: routeFails.length > 0 ? `failed: ${routeFails.join(", ")}` : (routeOk === true ? "passed" : "pending"),
    });

    const geofencePrimary = lastSubmitGeofence?.ok ?? lastSubmitGeofence?.geofence_ok ?? lastSubmitGeofence?.bounds_ok;
    const geofenceNfzOk = asRecord(lastSubmitGeofence?.no_fly_zone)?.ok;
    const outOfBoundsCount = Array.isArray(lastSubmitGeofence?.out_of_bounds) ? (lastSubmitGeofence.out_of_bounds as unknown[]).length : 0;
    let geofenceOk: boolean | null = null;
    if (typeof geofencePrimary === "boolean") geofenceOk = geofencePrimary;
    else if (typeof geofenceNfzOk === "boolean") geofenceOk = geofenceNfzOk;
    if (outOfBoundsCount > 0) geofenceOk = false;
    rows.push({
      label: "2) Geofence submit",
      ok: geofenceOk,
      detail: outOfBoundsCount > 0 ? `${outOfBoundsCount} waypoint(s) out of bounds` : (geofenceOk === true ? "passed" : geofenceOk === false ? "failed" : "pending"),
    });

    const verifyApproved = typeof lastSubmitVerify?.approved === "boolean" ? lastSubmitVerify.approved : null;
    const verifyReasons = Array.isArray(lastSubmitDecision?.reasons) ? (lastSubmitDecision.reasons as unknown[]).map(String).filter(Boolean) : [];
    rows.push({
      label: "3) UTM verify",
      ok: verifyApproved,
      detail: verifyReasons.length > 0 ? verifyReasons.join(", ") : (verifyApproved === true ? "approved" : verifyApproved === false ? "rejected" : "pending"),
    });

    const approvalReqStatus = String(lastSubmitApprovalReq?.status ?? "").trim().toLowerCase();
    const approvalReason = String(lastSubmitApprovalReq?.reason ?? "").trim();
    let approvalOk: boolean | null = null;
    if (approvalReqStatus === "error" || approvalReqStatus === "skipped") approvalOk = false;
    else if (typeof lastSubmitApprovalObj?.approved === "boolean") approvalOk = lastSubmitApprovalObj.approved === true;
    rows.push({
      label: "4) Approval request",
      ok: approvalOk,
      detail: approvalReason || (approvalReqStatus ? approvalReqStatus : (approvalOk === true ? "approved" : approvalOk === false ? "failed" : "pending")),
    });

    const selectedUssLayer = asRecord(selectedLayeredUavCard?.uss_layer);
    const ussManager = String(selectedUssLayer?.manager_uss_id ?? "").trim();
    const ussKnown = selectedUssLayer?.participant_known;
    const ussActive = selectedUssLayer?.participant_active;
    let ussOk: boolean | null = null;
    if (typeof ussKnown === "boolean" && typeof ussActive === "boolean") ussOk = ussKnown && ussActive;
    else if (typeof layeredUss?.ok === "boolean") ussOk = layeredUss.ok;
    const ussFallbackActive = displayCount(layeredUss?.active_participant_count);
    const ussFallbackTotal = displayCount(layeredUss?.participant_count ?? utmBackendDss?.participantCount);
    rows.push({
      label: "5) USS layer status",
      ok: ussOk,
      detail: ussManager
        ? `manager ${ussManager} • ${ussKnown === false ? "not registered" : ussActive === false ? "inactive" : ussActive === true ? "active" : "pending"}`
        : `active ${ussFallbackActive} / participants ${ussFallbackTotal}`,
    });

    const dssStatus = String(lastSubmitDssResult?.status ?? "").trim().toLowerCase();
    const dssBlockingConflicts = Array.isArray(lastSubmitDssResult?.blocking_conflicts) ? (lastSubmitDssResult.blocking_conflicts as unknown[]).length : 0;
    let dssOk: boolean | null = null;
    if (dssBlockingConflicts > 0) dssOk = false;
    else if (dssStatus) dssOk = !["error", "failed", "rejected"].includes(dssStatus);
    rows.push({
      label: "6) DSS publish result",
      ok: dssOk,
      detail: dssBlockingConflicts > 0 ? `${dssBlockingConflicts} blocking conflict(s)` : (dssStatus || (dssOk === true ? "ok" : dssOk === false ? "failed" : "pending")),
    });

    const selectedDssLayer = asRecord(selectedLayeredUavCard?.dss_layer);
    const selectedIntentId = String(selectedDssLayer?.intent_id ?? "").trim();
    const selectedPublishStatus = String(selectedDssLayer?.publication_status ?? "").trim().toLowerCase();
    const selectedPublishError = String(selectedDssLayer?.publication_error ?? "").trim();
    const selectedBlocking = Number(selectedDssLayer?.blocking_conflicts ?? NaN);
    const selectedPending = Number(selectedDssLayer?.pending_notifications ?? NaN);
    const dispatcherFailed = displayCount(dispatchCounts?.failed);
    const dssIntentCount = displayCount(layeredDss?.intent_count ?? utmBackendDss?.operationalIntentCount);
    const dssPendingCount = displayCount(layeredDss?.pending_notification_count ?? utmBackendDss?.pendingNotificationCount);
    let dssLayerOk: boolean | null = null;
    if (Number.isFinite(selectedBlocking) && selectedBlocking > 0) dssLayerOk = false;
    else if (selectedPublishError) dssLayerOk = false;
    else if (selectedIntentId && selectedPublishStatus) dssLayerOk = !["error", "failed", "rejected"].includes(selectedPublishStatus);
    else if (typeof layeredDss?.ok === "boolean") dssLayerOk = layeredDss.ok;
    rows.push({
      label: "7) DSS layer status",
      ok: dssLayerOk,
      detail: selectedIntentId
        ? `intent ${selectedIntentId} / blocking ${Number.isFinite(selectedBlocking) ? selectedBlocking : "-"} / pending ${Number.isFinite(selectedPending) ? selectedPending : "-"} / dispatch failed ${dispatcherFailed}`
        : `intents ${dssIntentCount} / pending ${dssPendingCount} / dispatch failed ${dispatcherFailed}`,
    });
    return rows;
  }, [dispatchCounts, lastSubmitApprovalObj, lastSubmitApprovalReq, lastSubmitDecision, lastSubmitDssResult, lastSubmitGeofence, lastSubmitRouteChecks, lastSubmitVerify, lastUtmSubmitResult, layeredDss, layeredUss, selectedLayeredUavCard, utmBackendDss]);
  const utmProcedureStepsForDisplay = useMemo(() => {
    if (lastSubmitWorkflowRows.length > 0) {
      return lastSubmitWorkflowRows.filter((row) => ["1)", "2)", "3)", "4)"].some((prefix) => row.label.startsWith(prefix)));
    }
    return [
      { label: "1) Route checks", ok: null, detail: "Pending mission submit to UTM" },
      { label: "2) Geofence submit", ok: null, detail: "Pending mission submit to UTM" },
      { label: "3) UTM verify", ok: null, detail: "Pending mission submit to UTM" },
      { label: "4) Approval request", ok: null, detail: "Pending mission submit to UTM" },
    ];
  }, [lastSubmitWorkflowRows]);
  const ussProcedureRowsForDisplay = useMemo(() => {
    if (lastSubmitWorkflowRows.length > 0) {
      return lastSubmitWorkflowRows.filter((row) => row.label.startsWith("5)"));
    }
    return [{ label: "5) USS layer status", ok: null, detail: "Waiting for post-submit layer status" }];
  }, [lastSubmitWorkflowRows]);
  const dssProcedureRowsForDisplay = useMemo(() => {
    if (lastSubmitWorkflowRows.length > 0) {
      return lastSubmitWorkflowRows.filter((row) => row.label.startsWith("6)") || row.label.startsWith("7)"));
    }
    return [
      { label: "6) DSS publish result", ok: null, detail: "Pending mission submit to UTM" },
      { label: "7) DSS layer status", ok: null, detail: "Waiting for post-submit layer status" },
    ];
  }, [lastSubmitWorkflowRows]);
  const summaryTimeWindow = asRecord(displayedChecks?.time_window) ?? timeWindowCheck;
  const summaryTimeStartRaw = String(summaryTimeWindow?.planned_start_at ?? "").trim();
  const summaryTimeEndRaw = String(summaryTimeWindow?.planned_end_at ?? "").trim();
  const summaryRequestedStartRaw = localInputToIsoUtc(simPlannedStartAt) ?? simPlannedStartAt;
  const summaryRequestedEndRaw = localInputToIsoUtc(simPlannedEndAt) ?? simPlannedEndAt;
  const summaryPathCandidates = [
    String(currentMission?.route_id ?? "").trim(),
    String(lastSubmitApprovalObj?.route_id ?? "").trim(),
    String(lastSubmitVerify?.route_id ?? "").trim(),
    String(asRecord(missionPaths?.utm_confirmed)?.route_id ?? "").trim(),
    String(asRecord(missionPaths?.dss_replanned)?.route_id ?? "").trim(),
    String(asRecord(missionPaths?.agent_replanned)?.route_id ?? "").trim(),
    String(asRecord(missionPaths?.user_planned)?.route_id ?? "").trim(),
    String(uav?.route_id ?? simRouteId).trim(),
  ].filter(Boolean);
  const summaryPathId = summaryPathCandidates[0] || "-";
  const summaryMissionCandidates = [
    String(currentMission?.mission_id ?? "").trim(),
    String(lastSubmitApprovalObj?.mission_id ?? "").trim(),
    String(asRecord(missionPaths?.utm_confirmed)?.mission_id ?? "").trim(),
  ].filter(Boolean);
  const summaryMissionId = summaryMissionCandidates[0] || "-";
  const summaryUtmSent = Boolean(lastUtmSubmitResult);
  let summaryUtmOk: boolean | null = null;
  if (summaryUtmSent) {
    if (lastSubmitStatusText === "APPROVED" || lastSubmitStatusText === "VERIFIED") summaryUtmOk = true;
    else if (lastSubmitStatusText === "REJECTED" || lastSubmitStatusText === "VERIFY FAILED") summaryUtmOk = false;
  }
  const summaryUssRow = ussProcedureRowsForDisplay[0];
  const summaryUssSent = hasProcedureData;
  const summaryUssOk = typeof summaryUssRow?.ok === "boolean" ? summaryUssRow.ok : null;
  const summaryDssRow = dssProcedureRowsForDisplay.find((row) => row.label.startsWith("6)")) ?? dssProcedureRowsForDisplay[0];
  const summaryDssSent = hasProcedureData;
  const summaryDssBlocking = Array.isArray(lastSubmitDssResult?.blocking_conflicts) ? (lastSubmitDssResult.blocking_conflicts as unknown[]).length : 0;
  const summaryDssOk = summaryDssBlocking > 0 ? false : (typeof summaryDssRow?.ok === "boolean" ? summaryDssRow.ok : null);
  const summaryUpdatedAtRaw = String(sessionInfo?.updated_at ?? currentMission?.approved_at ?? currentMission?.created_at ?? "").trim();
  const missionSummaryFields: Array<{ label: string; value: string }> = [
    { label: "Path ID", value: summaryPathId },
    { label: "Mission", value: summaryMissionId },
    { label: "Time", value: `${formatIsoSummary(summaryTimeStartRaw || summaryRequestedStartRaw)} -> ${formatIsoSummary(summaryTimeEndRaw || summaryRequestedEndRaw)}` },
    { label: "License", value: `${effectiveOperatorLicenseId} (${effectiveRequiredLicenseClass})` },
    { label: "UAV", value: `${String(identity?.selected_uav_id ?? simUavId)} / ${String(identity?.selected_user_id ?? ownerUserId)}` },
    { label: "Area", value: simAirspace || "-" },
    { label: "Path Src", value: currentMissionRouteOrigin ? plannerPathSourceLabel(currentMissionRouteOrigin) : "Current" },
    { label: "Updated", value: summaryUpdatedAtRaw ? formatIsoSummary(summaryUpdatedAtRaw) : "-" },
  ];
  const missionSummaryFlows: Array<{ name: "UTM" | "USS" | "DSS"; sent: boolean; ok: boolean | null; text: string }> = [
    {
      name: "UTM",
      sent: summaryUtmSent,
      ok: summaryUtmOk,
      text: summaryUtmSent ? lastSubmitStatusText : "NOT SENT",
    },
    {
      name: "USS",
      sent: summaryUssSent,
      ok: summaryUssOk,
      text: !summaryUssSent ? "NOT SENT" : summaryUssOk === true ? "ACTIVE" : summaryUssOk === false ? "ISSUE" : "PENDING",
    },
    {
      name: "DSS",
      sent: summaryDssSent,
      ok: summaryDssOk,
      text: !summaryDssSent ? "NOT SENT" : summaryDssBlocking > 0 ? `BLOCKED (${summaryDssBlocking})` : summaryDssOk === true ? "PUBLISHED" : summaryDssOk === false ? "ISSUE" : "PENDING",
    },
  ];
  const dssMitigationRowsForDisplay = useMemo(() => {
    if (!lastDssMitigation) {
      return [
        { label: "1) Detect conflict", ok: null, detail: "Run mission submit to evaluate DSS strategic conflicts" },
        { label: "2) Auto detour replan", ok: null, detail: "Triggered only when DSS conflict is detected" },
        { label: "3) Retry mission submit", ok: null, detail: "Triggered after successful auto detour replan" },
        { label: "4) Validate DSS clear", ok: null, detail: "Checks final DSS status + blocking conflict count" },
      ];
    }
    const conflictDetected = lastDssMitigation.conflict_detected;
    const initialStatus = lastDssMitigation.initial_dss_status || "unknown";
    const finalStatus = lastDssMitigation.final_dss_status || "unknown";
    const initialBlocking = Number(lastDssMitigation.initial_blocking_conflicts || 0);
    const finalBlocking = Number(lastDssMitigation.final_blocking_conflicts || 0);
    const replanOk = lastDssMitigation.replan_status === "success";
    const retryOk = lastDssMitigation.retry_submit_status === "success";
    return [
      {
        label: "1) Detect conflict",
        ok: true,
        detail: conflictDetected
          ? `Detected DSS strategic conflict (status ${initialStatus}, blocking ${initialBlocking})`
          : `No blocking DSS conflict on submit (status ${initialStatus}, blocking ${initialBlocking})`,
      },
      {
        label: "2) Auto detour replan",
        ok: conflictDetected ? (replanOk ? true : false) : null,
        detail: conflictDetected
          ? (replanOk ? "Replan completed via UTM/NFZ flow" : "Replan failed or skipped")
          : "Not required",
      },
      {
        label: "3) Retry mission submit",
        ok: conflictDetected ? (retryOk ? true : false) : null,
        detail: conflictDetected
          ? (retryOk ? "Retry submit request executed" : "Retry not executed")
          : "Not required",
      },
      {
        label: "4) Validate DSS clear",
        ok: lastDssMitigation.resolved,
        detail: `Final DSS status ${finalStatus}, blocking ${finalBlocking}${lastDssMitigation.notes.length ? ` • ${lastDssMitigation.notes[0]}` : ""}`,
      },
    ];
  }, [lastDssMitigation]);
  const lastSubmitIssues = useMemo(() => {
    if (!lastUtmSubmitResult) return [];
    const out: string[] = [];
    const add = (value: unknown) => {
      const text = String(value ?? "").trim();
      if (text && !out.includes(text)) out.push(text);
    };
    const submitGate = asRecord(lastUtmSubmitResult?.submit_gate);
    if (submitGate?.battery_ok === false) add("submit gate: battery check failed");
    if (Array.isArray(submitGate?.issues)) {
      (submitGate.issues as unknown[]).map(String).forEach((issue) => add(issue));
    }
    const approvalReqStatus = String(lastSubmitApprovalReq?.status ?? "").trim().toLowerCase();
    const approvalReqReason = String(lastSubmitApprovalReq?.reason ?? "").trim();
    if (approvalReqStatus && approvalReqStatus !== "success") add(approvalReqReason || `approval_request_${approvalReqStatus}`);
    const approvalReqErr = asRecord(lastSubmitApprovalReq?.result);
    if (approvalReqErr?.error) add(`approval error: ${String(approvalReqErr.error)}`);
    if (approvalReqErr?.message) add(String(approvalReqErr.message));
    if (Array.isArray(lastSubmitDecision?.reasons)) {
      (lastSubmitDecision.reasons as unknown[]).map(String).forEach((reason) => add(reason));
    }
    if (Array.isArray(lastSubmitDecision?.messages)) {
      (lastSubmitDecision.messages as unknown[]).map(String).forEach((msg) => add(msg));
    }
    const routeRegs = asRecord(lastSubmitRouteChecks?.regulations);
    const routeNfz = asRecord(lastSubmitRouteChecks?.no_fly_zone);
    const routeGeofence = asRecord(lastSubmitRouteChecks?.geofence);
    if (Array.isArray(routeRegs?.errors)) (routeRegs.errors as unknown[]).map(String).forEach((msg) => add(msg));
    if (Array.isArray(routeNfz?.waypoint_conflicts) && (routeNfz.waypoint_conflicts as unknown[]).length > 0) {
      add(`no-fly-zone waypoint conflicts: ${(routeNfz.waypoint_conflicts as unknown[]).length}`);
    }
    if (Array.isArray(routeNfz?.segment_conflicts) && (routeNfz.segment_conflicts as unknown[]).length > 0) {
      add(`no-fly-zone segment conflicts: ${(routeNfz.segment_conflicts as unknown[]).length}`);
    }
    if (Array.isArray(routeGeofence?.out_of_bounds) && (routeGeofence.out_of_bounds as unknown[]).length > 0) {
      add(`out-of-bounds waypoints: ${(routeGeofence.out_of_bounds as unknown[]).length}`);
    }
    if (Array.isArray(lastSubmitGeofence?.out_of_bounds) && (lastSubmitGeofence.out_of_bounds as unknown[]).length > 0) {
      add(`geofence submit out-of-bounds: ${(lastSubmitGeofence.out_of_bounds as unknown[]).length}`);
    }
    const verifyChecks = asRecord(lastSubmitVerify?.checks);
    const checkLabels: Array<[string, string]> = [
      ["weather", "Weather"],
      ["route_bounds", "Bounds"],
      ["regulations", "Regulation"],
      ["no_fly_zone", "NFZ"],
      ["time_window", "Time Window"],
      ["operator_license", "Operator License"],
    ];
    checkLabels.forEach(([key, label]) => {
      const row = asRecord(verifyChecks?.[key]);
      if (row?.ok === false) add(`${label} check failed`);
    });
    const dssStatus = String(lastSubmitDssResult?.status ?? "").trim().toLowerCase();
    if (["error", "failed", "rejected"].includes(dssStatus)) add(`dss status: ${dssStatus}`);
    if (Array.isArray(lastSubmitDssResult?.blocking_conflicts) && (lastSubmitDssResult.blocking_conflicts as unknown[]).length > 0) {
      add(`dss blocking conflicts: ${(lastSubmitDssResult.blocking_conflicts as unknown[]).length}`);
    }
    if (lastSubmitDssResult?.error) add(`dss error: ${String(lastSubmitDssResult.error)}`);
    return out.slice(0, 12);
  }, [lastSubmitApprovalReq, lastSubmitDecision, lastSubmitDssResult, lastSubmitGeofence, lastSubmitRouteChecks, lastSubmitVerify, lastUtmSubmitResult]);
  const utmDetailedCheckRows = useMemo(() => {
    if (!hasProcedureData) {
      return [
        { label: "Approval", ok: null, detail: "Waiting for mission submit" },
        { label: "Signature", ok: null, detail: "Waiting for mission submit" },
        { label: "Weather", ok: null, detail: "Waiting for mission submit" },
        { label: "Bounds", ok: null, detail: "Waiting for mission submit" },
        { label: "No-Fly Zone", ok: null, detail: "Waiting for mission submit" },
        { label: "Regulations", ok: null, detail: "Waiting for mission submit" },
        { label: "Time Window", ok: null, detail: "Waiting for mission submit" },
        { label: "Operator License", ok: null, detail: "Waiting for mission submit" },
        { label: "Authorization", ok: null, detail: "Waiting for mission submit" },
      ] as Array<{ label: string; ok: unknown; detail: string }>;
    }
    const rows: Array<{ label: string; ok: unknown; detail: string }> = [];
    const approvalReason = String(displayedApproval?.reason ?? "").trim();
    rows.push({
      label: "Approval",
      ok: displayedApproval?.approved,
      detail: approvalReason || "No approval reason available",
    });

    rows.push({
      label: "Signature",
      ok: displayedApproval?.signature_verified,
      detail: displayedApproval?.signature_verified === true ? "Signature verified" : "Signature missing or invalid",
    });

    const weather = asRecord(displayedChecks?.weather) ?? utmBackendWeatherCheck;
    const weatherParts: string[] = [];
    if (typeof weather?.wind_mps === "number") weatherParts.push(`wind ${weather.wind_mps.toFixed(1)} m/s`);
    if (typeof weather?.visibility_km === "number") weatherParts.push(`visibility ${weather.visibility_km.toFixed(1)} km`);
    if (weather?.storm_alert === true) weatherParts.push("storm alert");
    rows.push({
      label: "Weather",
      ok: weather?.ok,
      detail: weatherParts.join(" • ") || "No weather metrics",
    });

    const bounds = asRecord(displayedChecks?.route_bounds) ?? asRecord(lastSubmitRouteChecks?.geofence) ?? geofence;
    const outOfBounds = Array.isArray(bounds?.out_of_bounds) ? (bounds.out_of_bounds as unknown[]).length : 0;
    rows.push({
      label: "Bounds",
      ok: bounds?.ok ?? bounds?.geofence_ok ?? bounds?.bounds_ok ?? routeBoundsOk,
      detail: outOfBounds > 0 ? `${outOfBounds} waypoint(s) out of bounds` : `airspace ${String(bounds?.airspace_segment ?? simAirspace)}`,
    });

    const nfz = asRecord(displayedChecks?.no_fly_zone) ?? asRecord(lastSubmitRouteChecks?.no_fly_zone);
    const nfzWp = Array.isArray(nfz?.waypoint_conflicts) ? (nfz.waypoint_conflicts as unknown[]).length : 0;
    const nfzSeg = Array.isArray(nfz?.segment_conflicts) ? (nfz.segment_conflicts as unknown[]).length : 0;
    rows.push({
      label: "No-Fly Zone",
      ok: nfz?.ok,
      detail: nfzWp + nfzSeg > 0 ? `${nfzWp} waypoint + ${nfzSeg} segment conflicts` : "No conflicts",
    });

    const regs = asRecord(displayedChecks?.regulations) ?? asRecord(lastSubmitRouteChecks?.regulations);
    const regErrors = Array.isArray(regs?.errors) ? (regs.errors as unknown[]).map(String).filter(Boolean) : [];
    rows.push({
      label: "Regulations",
      ok: regs?.ok,
      detail: regErrors.length > 0 ? regErrors.slice(0, 2).join(" • ") : "No regulation violations",
    });

    const tw = asRecord(displayedChecks?.time_window) ?? timeWindowCheck;
    const twErrors = Array.isArray(tw?.errors) ? (tw.errors as unknown[]).map(String).filter(Boolean) : [];
    rows.push({
      label: "Time Window",
      ok: tw?.ok,
      detail: twErrors.length > 0 ? twErrors.slice(0, 2).join(" • ") : flightTimeSummary,
    });

    const license = asRecord(displayedChecks?.operator_license);
    const licenseReason = String(license?.reason ?? "").trim();
    const licClass = String(license?.required_class ?? effectiveRequiredLicenseClass);
    rows.push({
      label: "Operator License",
      ok: license?.ok,
      detail: `${licenseReason || "validated"} • required ${licClass}`,
    });

    const authReason = String(displayedAuthorization?.reason ?? "").trim();
    rows.push({
      label: "Authorization",
      ok: displayedAuthorization?.authorized,
      detail: authReason || "authorization status unavailable",
    });
    return rows;
  }, [displayedApproval, displayedAuthorization, displayedChecks, effectiveRequiredLicenseClass, flightTimeSummary, geofence, hasProcedureData, lastSubmitRouteChecks, routeBoundsOk, simAirspace, timeWindowCheck, utmBackendWeatherCheck]);
  const utmDetailPassed = utmDetailedCheckRows.filter((row) => row.ok === true).length;
  const utmDetailFailed = utmDetailedCheckRows.filter((row) => row.ok === false).length;
  const utmDetailPending = utmDetailedCheckRows.length - utmDetailPassed - utmDetailFailed;
  const fallbackFlightGateIssues = useMemo(() => {
    const issues: string[] = [];
    if (!simUavId.trim()) issues.push("No UAV selected");
    const battery = Number(uav?.battery_pct ?? NaN);
    if (Number.isFinite(battery) && battery < 15) issues.push(`Battery low (${battery.toFixed(0)}%)`);
    const wpTotal = Number(uav?.waypoints_total ?? 0);
    if (!Number.isFinite(wpTotal) || wpTotal < 2) issues.push("Planned path requires at least 2 waypoints");
    if (geofence && routeBoundsOk !== true) issues.push("Route bounds check not passed");
    if (approval?.approved !== true) issues.push("UTM approval not granted");
    const weatherOk = (utmBackendWeatherCheck?.ok ?? asRecord(checks?.weather)?.ok);
    if (weatherOk !== undefined && weatherOk !== true) issues.push("UTM weather check failed");
    const checkList: Array<[string, unknown]> = [
      ["NFZ", asRecord(checks?.no_fly_zone)?.ok],
      ["Regulations", asRecord(checks?.regulations)?.ok],
      ["Time window", asRecord(checks?.time_window)?.ok],
      ["Operator license", asRecord(checks?.operator_license)?.ok],
    ];
    checkList.forEach(([label, ok]) => {
      if (ok !== undefined && ok !== true) issues.push(`${label} check failed`);
    });
    const approvalExpiresAt = String(approval?.expires_at ?? "").trim();
    if (approval && !approvalExpiresAt) {
      issues.push("UTM approval expiry not available");
    } else if (approvalExpiresAt) {
      const expiresAtMs = Date.parse(approvalExpiresAt);
      if (!Number.isFinite(expiresAtMs)) {
        issues.push(`UTM approval expiry invalid (${approvalExpiresAt})`);
      } else if (expiresAtMs <= Date.now()) {
        issues.push(`UTM approval expired (${approvalExpiresAt})`);
      }
    }
    const dssResult = asRecord(sessionInfo?.utm_dss_result ?? uav?.utm_dss_result);
    if (dssResult) {
      const dssIntent = asRecord(dssResult.intent);
      const ovn = String(dssIntent?.ovn ?? dssResult.ovn ?? "").trim();
      if (!ovn) issues.push("DSS OVN missing in intent");
    }
    if (approval && approval.signature_verified === false) issues.push("UTM approval signature not verified");
    return issues;
  }, [approval, checks, geofence, routeBoundsOk, sessionInfo, simUavId, uav, utmBackendWeatherCheck]);
  const backendLaunchIssues = useMemo(
    () => (
      Array.isArray(backendFlightGate?.launch_issues)
        ? (backendFlightGate.launch_issues as unknown[]).map(String).filter(Boolean)
        : []
    ),
    [backendFlightGate],
  );
  const backendLaunchReady = typeof backendFlightGate?.launch_ready === "boolean" ? (backendFlightGate.launch_ready as boolean) : null;
  const flightGateIssues = backendLaunchIssues.length ? backendLaunchIssues : fallbackFlightGateIssues;
  const flightControlReady = backendLaunchReady ?? (flightGateIssues.length === 0);
  const visibleUtmFailureIssues = useMemo(
    () => (lastSubmitIssues.length > 0 ? lastSubmitIssues : flightGateIssues.slice(0, 12)),
    [flightGateIssues, lastSubmitIssues],
  );
  useEffect(() => {
    uavRef.current = uav;
  }, [uav]);
  const msgTone: "neutral" | "warning" | "error" = useMemo(() => {
    const m = String(msg || "").toLowerCase();
    if (m.startsWith("warning:")) return "warning";
    if (m.includes("failed") || m.includes("error")) return "error";
    return "neutral";
  }, [msg]);
  type FlightAction = "launch" | "step" | "hold" | "resume" | "rth" | "land";
  const flightControlBtnStyle = (blocked: boolean): React.CSSProperties => (blocked
    ? { ...chipStyle(false), background: "#f2f4f7", color: "#98a2b3", borderColor: "#e4e7ec", cursor: "not-allowed" }
    : chipStyle(false));
  const actionNeedsPreflightGate = (action: FlightAction) => (
    action === "launch"
  );
  const hasSelectedUav = Boolean(simUavId.trim());
  const isArmed = uav?.armed === true;
  const isActive = uav?.active === true;
  const isLaunched = isArmed;
  const actionUiBlocked = useMemo<Record<FlightAction, boolean>>(() => {
    // UX policy: Launch is preflight-gated; once launched (armed), all in-flight controls become available.
    return {
      launch: !hasSelectedUav || !flightControlReady,
      step: !hasSelectedUav || !isArmed,
      hold: !hasSelectedUav || !isArmed,
      resume: !hasSelectedUav || !isArmed,
      rth: !hasSelectedUav || !isArmed,
      land: !hasSelectedUav || !isArmed,
    };
  }, [flightControlReady, hasSelectedUav, isArmed]);
  const missionActionBlocked = !hasSelectedUav || !isLaunched || !isActive;
  const autoFlyBlocked = !hasSelectedUav || !isArmed || !isActive;
  const postLaunchControlsLocked = actionUiBlocked.step || actionUiBlocked.hold || actionUiBlocked.resume || actionUiBlocked.rth || actionUiBlocked.land;
  const waypointProgress = useMemo(() => {
    const idx = Number(uav?.waypoint_index ?? NaN);
    const total = Number(uav?.waypoints_total ?? NaN);
    const routePct = Number(uav?.route_progress_pct ?? NaN);
    if (!Number.isFinite(idx) || !Number.isFinite(total) || total <= 0) {
      return { label: "- / -", hint: "", pct: Number.isFinite(routePct) ? `${routePct.toFixed(1)}%` : "-" };
    }
    const i = Math.max(0, Math.trunc(idx));
    const t = Math.max(0, Math.trunc(total));
    const label = `${Math.min(i + 1, t)} / ${t}`;
    const pct = Number.isFinite(routePct) ? `${routePct.toFixed(1)}%` : "-";
    if (t > 0 && i >= t - 1) {
      const armed = uav?.armed === true;
      const active = uav?.active === true;
      const hint = active
        ? "Final waypoint reached"
        : armed
          ? "Mission complete: RTH/Land or Launch to restart"
          : "Mission complete: Launch to restart";
      return { label, hint, pct };
    }
    return { label, hint: "", pct };
  }, [uav]);
  const briefMsg = useMemo(() => {
    const raw = String(msg || "").trim();
    if (!raw) return "";
    if (raw.toLowerCase().includes("flight control blocked")) {
      return `Blocked: ${flightGateIssues[0] ?? "preflight checks not passed"}`;
    }
    const normalized = raw
      .replace(/^warning:\s*/i, "Warn: ")
      .replace(/^action failed:\s*/i, "Failed: ");
    return normalized.length > 120 ? `${normalized.slice(0, 117)}...` : normalized;
  }, [flightGateIssues, msg]);
  const runFlightControlGuarded = (action: FlightAction, fn: () => void) => {
    if (busy || agentBusy) return;
    const actionLabel = action === "rth" ? "return to home" : action;
    const armed = uav?.armed === true;
    const active = uav?.active === true;
    const wpIndex = Number(uav?.waypoint_index ?? NaN);
    const wpTotal = Number(uav?.waypoints_total ?? NaN);
    const missionComplete = Number.isFinite(wpIndex) && Number.isFinite(wpTotal) && wpTotal > 0 && wpIndex >= (wpTotal - 1) && !active;
    if (action === "launch" && armed && !missionComplete) {
      setMsg(`Warning: ${simUavId} is already launched. No need to launch again.`);
      return;
    }
    if (action !== "launch" && !armed) {
      setMsg(`Warning: ${simUavId} is not launched (armed=false). Please launch this UAV before ${actionLabel}.`);
      return;
    }
    if (action === "step" && missionComplete) {
      setMsg("Warning: mission complete. Use RTH/Land/End Mission, or Launch to restart.");
      return;
    }
    if (actionNeedsPreflightGate(action) && !flightControlReady) {
      setMsg(`Warning: ${flightGateIssues[0] ?? "Flight control blocked. Resolve mission checks first."}`);
      return;
    }
    fn();
  };
  const sourceBadge = (src: Record<string, unknown> | null) => {
    const active = String(src?.active ?? "unknown");
    const mode = String(src?.mode ?? "-");
    const live = active.includes("live");
    return (
      <span
        style={{
          display: "inline-flex",
          alignItems: "center",
          gap: 6,
          borderRadius: 999,
          padding: "2px 8px",
          fontSize: 11,
          fontWeight: 700,
          background: live ? "#ecfdf3" : "#f2f4f7",
          color: live ? "#027a48" : "#475467",
          border: `1px solid ${live ? "#abefc6" : "#d0d5dd"}`,
        }}
      >
        {active}
        <span style={{ fontWeight: 500, color: "#667085" }}>({mode})</span>
      </span>
    );
  };

  const assignSelectedUavLicense = async (operatorLicenseId: string) => {
    const uavId = simUavId.trim();
    if (!uavId) return;
    const res = await postApi("/api/uav/registry/assign", { user_id: ownerUserId, uav_id: uavId, operator_license_id: operatorLicenseId }, `Assigned ${uavId} to ${operatorLicenseId}`);
    if (res) setSimOperatorLicenseId(operatorLicenseId);
  };
  const setRegistryProfileField = (key: string, value: string | boolean) => {
    setRegistryProfileForm((prev) => ({ ...prev, [key]: value }));
    setRegistryProfileDirty(true);
  };
  const setMissionDefaultsExtraField = (key: string, value: string) => {
    setMissionDefaultsExtraForm((prev) => ({ ...prev, [key]: value }));
    setMissionDefaultsDirty(true);
  };
  const saveRegistryProfile = async () => {
    const uavId = simUavId.trim();
    if (!uavId) {
      setMsg("Action failed: select a UAV first");
      return;
    }
    const res = await postApi(
      "/api/uav/registry/profile",
      { user_id: ownerUserId, uav_id: uavId, ...registryProfilePayloadFromForm(registryProfileForm) },
      "Saved standardized UAV profile",
    );
    if (res) {
      setRegistryProfileDirty(false);
      await loadState();
    }
  };
  const saveMissionDefaults = async () => {
    const uavId = simUavId.trim();
    if (!uavId) {
      setMsg("Action failed: select a UAV first");
      return;
    }
    const speed = Number.parseFloat(simRequestedSpeedMps);
    if (!Number.isFinite(speed) || speed <= 0) {
      setMsg("Action failed: requested speed must be a positive number");
      return;
    }
    const startIso = simPlannedStartAt ? localInputToIsoUtc(simPlannedStartAt) : null;
    const endIso = simPlannedEndAt ? localInputToIsoUtc(simPlannedEndAt) : null;
    if (simPlannedStartAt && !startIso) {
      setMsg("Action failed: invalid start time");
      return;
    }
    if (simPlannedEndAt && !endIso) {
      setMsg("Action failed: invalid end time");
      return;
    }
    const res = await postApi(
      "/api/uav/mission-defaults",
      {
        user_id: ownerUserId,
        uav_id: uavId,
        route_id: simRouteId.trim() || null,
        airspace_segment: simAirspace.trim() || null,
        requested_speed_mps: speed,
        planned_start_at: startIso,
        planned_end_at: endIso,
        hold_reason: holdReason.trim() || null,
        ...missionDefaultsExtraPayloadFromForm(missionDefaultsExtraForm),
      },
      "Saved mission defaults",
    );
    if (res) {
      setMissionDefaultsDirty(false);
      await loadState();
    }
  };
  const renderDynamicField = (
    field: DynamicFieldDef,
    form: Record<string, string | boolean>,
    onChange: (key: string, value: string | boolean) => void,
  ) => {
    const baseLabelStyle: React.CSSProperties = { fontSize: 12, minWidth: 0, display: "grid", gap: 4 };
    if (field.kind === "checkbox") {
      return (
        <label key={field.key} style={{ ...baseLabelStyle, alignContent: "start" }}>
          <span>{field.label}</span>
          <label style={{ display: "inline-flex", alignItems: "center", gap: 8, color: "#344054", fontSize: 12 }}>
            <input
              type="checkbox"
              checked={form[field.key] === true}
              onChange={(e) => onChange(field.key, e.target.checked)}
              disabled={busy || agentBusy}
            />
            Enabled
          </label>
        </label>
      );
    }
    if (field.kind === "textarea") {
      return (
        <label key={field.key} style={baseLabelStyle}>
          <span>{field.label}</span>
          <textarea
            style={{ ...inputStyle, minHeight: (field.rows ?? 3) * 18 + 16, resize: "vertical" }}
            rows={field.rows ?? 3}
            value={String(form[field.key] ?? "")}
            onChange={(e) => onChange(field.key, e.target.value)}
            disabled={busy || agentBusy}
            placeholder={field.placeholder}
          />
        </label>
      );
    }
    if (field.kind === "select") {
      return (
        <label key={field.key} style={baseLabelStyle}>
          <span>{field.label}</span>
          <select
            style={inputStyle}
            value={String(form[field.key] ?? "")}
            onChange={(e) => onChange(field.key, e.target.value)}
            disabled={busy || agentBusy}
          >
            {(field.options ?? []).map((opt) => (
              <option key={`${field.key}-${opt.value}`} value={opt.value}>{opt.label}</option>
            ))}
          </select>
        </label>
      );
    }
    return (
      <label key={field.key} style={baseLabelStyle}>
        <span>{field.label}</span>
        <input
          type={field.kind}
          step={field.step}
          style={inputStyle}
          value={String(form[field.key] ?? "")}
          onChange={(e) => onChange(field.key, e.target.value)}
          disabled={busy || agentBusy}
          placeholder={field.placeholder}
        />
      </label>
    );
  };
  const nfzZones: MissionNfz[] = Array.isArray(utmObj?.no_fly_zones)
    ? (utmObj!.no_fly_zones as unknown[])
        .filter(isObject)
        .map((z) => ({
          zone_id: String((z as Record<string, unknown>).zone_id ?? "nfz"),
          cx: Number((z as Record<string, unknown>).cx ?? 0),
          cy: Number((z as Record<string, unknown>).cy ?? 0),
          radius_m: Number((z as Record<string, unknown>).radius_m ?? 0),
          z_min: Number((z as Record<string, unknown>).z_min ?? 0),
          z_max: Number((z as Record<string, unknown>).z_max ?? 120),
          reason: String((z as Record<string, unknown>).reason ?? ""),
        }))
    : [];
  const allFleetTracksForMap: MissionTrack[] = Object.entries(fleetState).map(([id, snap]) => {
    const pos = asRecord(snap.position);
    return {
      id,
      x: Number(pos?.x ?? 0),
      y: Number(pos?.y ?? 0),
      z: Number(pos?.z ?? 0),
      attachedBsId: "",
      interferenceRisk: "low",
    };
  });
  // Planner map uses editor waypoints; live map should use backend-authoritative route for cross-page synchronization.
  const routePointsForMap = routeValidation.waypoints.map((w) => ({ x: w.x, y: w.y, z: w.z }));
  const backendRoutePointsForMap = Array.isArray(uav?.waypoints)
    ? (uav.waypoints as unknown[])
        .filter(isObject)
        .map((w) => ({
          x: Number((w as Record<string, unknown>).x ?? 0),
          y: Number((w as Record<string, unknown>).y ?? 0),
          z: Number((w as Record<string, unknown>).z ?? 0),
        }))
        .filter((p) => Number.isFinite(p.x) && Number.isFinite(p.y) && Number.isFinite(p.z))
    : [];
  const allPlannedRouteOverlays = useMemo<MissionRouteOverlay[]>(() => {
    return Object.entries(latestPlannedRoutes).reduce<MissionRouteOverlay[]>((acc, [uavId, row]) => {
      const wps = Array.isArray(row.waypoints) ? (row.waypoints as unknown[]).filter(isObject) : [];
      const route = wps.map((w) => ({ x: Number((w as Record<string, unknown>).x ?? 0), y: Number((w as Record<string, unknown>).y ?? 0), z: Number((w as Record<string, unknown>).z ?? 0) }));
        if (route.length >= 2) acc.push({ id: uavId, route, color: "#98a2b3" });
        return acc;
      }, []);
  }, [latestPlannedRoutes]);
  const selectedMissionRouteOverlays = useMemo<MissionRouteOverlay[]>(() => {
    const defs: Array<[PlannerPathSourceKey, string, string, boolean]> = [
      ["user_planned", "mission-user-planned", "#2563eb", plannerShowUserPath],
      ["agent_replanned", "mission-agent-replanned", "#f79009", plannerShowAgentPath],
      ["dss_replanned", "mission-dss-replanned", "#f04438", plannerShowDssPath],
      ["utm_confirmed", "mission-utm-confirmed", "#12b76a", plannerShowUtmPath],
    ];
    return defs.reduce<MissionRouteOverlay[]>((acc, [key, id, color, enabled]) => {
      if (!enabled) return acc;
      const row = asRecord(missionPaths?.[key]);
      const wps = Array.isArray(row?.waypoints) ? (row!.waypoints as unknown[]).filter(isObject) : [];
      const route = wps.map((w) => ({
        x: Number((w as Record<string, unknown>).x ?? 0),
        y: Number((w as Record<string, unknown>).y ?? 0),
        z: Number((w as Record<string, unknown>).z ?? 0),
      }));
      if (route.length >= 2) acc.push({ id, route, color });
      return acc;
    }, []);
  }, [missionPaths, plannerShowAgentPath, plannerShowDssPath, plannerShowUserPath, plannerShowUtmPath]);
  const extraRoutesForMap = [...allPlannedRouteOverlays.filter((r) => r.id !== simUavId), ...selectedMissionRouteOverlays];
  const missionTableAltitudeColWidth = 104;
  const missionTableActionColWidth = 210;
  const missionTableGap = 6;
  const missionTableActionStickyRight = 0;
  const missionTableVisibleRows = 6;
  const missionTableRowViewportHeight = missionTableVisibleRows * 46 + 10;
  const visibleRouteRows = useMemo(() => {
    const base = routeRows.map((r) => ({ ...r }));
    while (base.length < MIN_VISIBLE_WAYPOINT_ROWS) base.push({ ...PLACEHOLDER_WAYPOINT_ROW });
    return base;
  }, [routeRows]);
  const upsertRouteRow = (idx: number, update: (row: EditableWaypointRow) => EditableWaypointRow) => {
    setRouteRows((rows) => {
      const next = rows.slice();
      while (next.length <= idx) next.push({ ...PLACEHOLDER_WAYPOINT_ROW });
      next[idx] = update(next[idx] ?? { ...PLACEHOLDER_WAYPOINT_ROW });
      return next;
    });
  };
  const plannedPosForMap = routeValidation.waypoints.length > 0
    ? { x: routeValidation.waypoints[0]!.x, y: routeValidation.waypoints[0]!.y, z: routeValidation.waypoints[0]!.z }
    : null;
  const backendPlannedPosForMap = backendRoutePointsForMap.length > 0
    ? backendRoutePointsForMap[0]!
    : null;
  const filteredBackendActionLog = useMemo(() => {
    if (backendLogFilter === "all") return backendActionLog;
    return backendActionLog.filter((item) => {
      const a = item.action.toLowerCase();
      if (backendLogFilter === "copilot") return a.includes("agent_chat") || a.includes("copilot");
      if (backendLogFilter === "utm_verify") return a.includes("verify");
      if (backendLogFilter === "flight") return ["launch", "step", "hold", "resume", "rth", "land", "mission_action"].some((k) => a === k || a.includes(k));
      if (backendLogFilter === "utm_config") return a.includes("utm_") && (a.includes("weather") || a.includes("nfz") || a.includes("license"));
      if (backendLogFilter === "live_data") return a.includes("live_ingest");
      return true;
    });
  }, [backendActionLog, backendLogFilter]);
  const missionDefaultsInlineValidation = useMemo(() => {
    const errors: string[] = [];
    const warnings: string[] = [];

    const parseNum = (v: unknown): number | null => {
      const n = Number(typeof v === "string" ? v.trim() : v);
      return Number.isFinite(n) ? n : null;
    };

    const requestedSpeed = parseNum(simRequestedSpeedMps);
    const maxSpeedCapability = parseNum(registryProfileForm.max_speed_mps_capability);
    const maxAltitudeCapability = parseNum(registryProfileForm.max_altitude_m);

    if (simRequestedSpeedMps.trim() && requestedSpeed == null) {
      errors.push("Requested speed must be a valid number.");
    } else if (requestedSpeed != null && requestedSpeed <= 0) {
      errors.push("Requested speed must be greater than 0.");
    }
    if (requestedSpeed != null && maxSpeedCapability != null && maxSpeedCapability > 0 && requestedSpeed > maxSpeedCapability) {
      errors.push(`Requested speed (${requestedSpeed} m/s) exceeds UAV capability (${maxSpeedCapability} m/s).`);
    }

    const routeMaxAltitude =
      routeValidation.waypoints.length > 0
        ? routeValidation.waypoints.reduce((m, w) => Math.max(m, Number(w.z ?? 0)), Number.NEGATIVE_INFINITY)
        : null;
    if (
      routeMaxAltitude != null
      && Number.isFinite(routeMaxAltitude)
      && maxAltitudeCapability != null
      && maxAltitudeCapability > 0
      && routeMaxAltitude > maxAltitudeCapability
    ) {
      errors.push(`Route max altitude (${routeMaxAltitude.toFixed(1)} m) exceeds UAV capability (${maxAltitudeCapability} m).`);
    }

    const startIso = simPlannedStartAt ? localInputToIsoUtc(simPlannedStartAt) : null;
    const endIso = simPlannedEndAt ? localInputToIsoUtc(simPlannedEndAt) : null;
    if (simPlannedStartAt && !startIso) errors.push("Start time is invalid.");
    if (simPlannedEndAt && !endIso) errors.push("End time is invalid.");
    if (startIso && endIso && new Date(endIso).getTime() <= new Date(startIso).getTime()) {
      errors.push("End time must be later than start time.");
    }

    const fleetStatus = String(registryProfileForm.status ?? "").trim();
    if (fleetStatus && ["maintenance", "grounded", "retired"].includes(fleetStatus)) {
      warnings.push(`UAV status is '${fleetStatus}' in registry profile.`);
    }
    const airworthinessStatus = String(registryProfileForm.airworthiness_status ?? "").trim();
    if (airworthinessStatus && airworthinessStatus !== "airworthy") {
      warnings.push(`Airworthiness status is '${airworthinessStatus}'.`);
    }

    return { errors, warnings };
  }, [
    simRequestedSpeedMps,
    simPlannedStartAt,
    simPlannedEndAt,
    registryProfileForm,
    routeValidation,
  ]);
  const sharedBases = getSharedPageState();
  const quickAuthUtmBase = normalizeBaseUrl(sharedBases.utmApiBase || "http://127.0.0.1:8021");
  const quickAuthUavBase = normalizeBaseUrl(uavApiBase || "http://127.0.0.1:8020");
  const quickAuthToken = utmAuthToken.trim() || "local-dev-token";
  const quickAuthIntentId = `${ownerUserId}:${simUavId}:${simRouteId || "route-1"}`;
  const visibleRegistryProfileSections = registryProfileAdvanced ? REGISTRY_PROFILE_SECTIONS : REGISTRY_PROFILE_SECTIONS.slice(0, 1);

  return (
    <div style={{ display: "grid", gap: 12, padding: 14, maxWidth: 1280, margin: "0 auto" }}>
      <div style={{ display: "grid", gap: 12, gridTemplateColumns: "minmax(0, 1.15fr) minmax(0, 1fr)", alignItems: "start" }}>
        <div style={{ display: "grid", gap: 12, alignContent: "start" }}>
          <div style={{ ...cardStyle, background: "#fff", padding: 10, display: "grid", gap: 8 }}>
            <div style={{ display: "grid", gridTemplateColumns: "1fr auto", gap: 8, alignItems: "center" }}>
              <div>
                <div style={{ fontWeight: 700, color: "#101828" }}>Mission Defaults (Per User + UAV)</div>
                <div style={{ fontSize: 11, color: "#667085" }}>Persisted planning defaults used by this page and UTM workflow inputs.</div>
              </div>
              <div style={{ display: "flex", gap: 6, alignItems: "center" }}>
                <button type="button" style={chipStyle(false)} onClick={() => void loadState()} disabled={busy || agentBusy}>Refresh</button>
                <button type="button" style={{ ...chipStyle(missionDefaultsDirty), fontWeight: 700 }} onClick={() => void saveMissionDefaults()} disabled={busy || agentBusy || !simUavId}>Save Defaults</button>
              </div>
            </div>
            <div style={{ border: "1px solid #eaecf0", borderRadius: 10, background: "#fcfcfd", padding: 8, display: "grid", gap: 8 }}>
              <div style={{ fontSize: 12, fontWeight: 700, color: "#101828" }}>UAV Registration & Assignment</div>
              <div style={{ display: "grid", gridTemplateColumns: "minmax(120px,0.8fr) minmax(160px,1fr) minmax(180px,1fr)", gap: 6, alignItems: "end" }}>
                <label style={{ fontSize: 12, minWidth: 0, display: "grid", gap: 4 }}>User ID
                  <input style={{ ...inputStyle, maxWidth: 140 }} value={ownerUserId} onChange={(e) => setOwnerUserId(e.target.value)} />
                </label>
                <label style={{ fontSize: 12, minWidth: 0, display: "grid", gap: 4 }}>User UAV
                  <select style={{ ...inputStyle, maxWidth: 220 }} value={simUavId} onChange={(e) => setSimUavId(e.target.value)}>
                    {registryUserUavIds.length
                      ? registryUserUavIds.map((id) => <option key={id} value={id}>{uiUavLabel(id)}</option>)
                      : <option value="">No UAV</option>}
                  </select>
                </label>
                <label style={{ fontSize: 12, minWidth: 0, display: "grid", gap: 4 }}>UAV Operator License
                  <select
                    style={{ ...inputStyle, maxWidth: 240 }}
                    value={selectedUavAssignedLicenseId}
                    onChange={(e) => void assignSelectedUavLicense(e.target.value)}
                    disabled={busy || agentBusy || !simUavId}
                  >
                    {Object.keys(utmLicenseCatalog).length
                      ? Object.keys(utmLicenseCatalog).map((licId) => (
                          <option key={licId} value={licId}>
                            {licId} ({String(asRecord(utmLicenseCatalog[licId])?.license_class ?? "-")})
                          </option>
                        ))
                      : <option value={selectedUavAssignedLicenseId}>{selectedUavAssignedLicenseId}</option>}
                  </select>
                </label>
              </div>
              <div style={{ display: "flex", gap: 6, flexWrap: "wrap", alignItems: "center" }}>
                <span style={{ fontSize: 11, color: "#667085" }}>Map Click</span>
                <button type="button" style={chipStyle(plannerMapClickMode === "add_wp")} onClick={() => setPlannerMapClickMode("add_wp")}>Add WP</button>
                <button type="button" style={chipStyle(plannerMapClickMode === "add_uav")} onClick={() => setPlannerMapClickMode("add_uav")}>Add UAV</button>
                <button type="button" style={{ ...chipStyle(false), borderColor: "#fda29b", color: "#b42318" }} onClick={() => void deleteSelectedUav()} disabled={busy || agentBusy}>Delete UAV</button>
                <div style={{ fontSize: 11, color: "#667085" }}>
                  User {String(registryUserSummary?.user_id ?? ownerUserId)} has <b>{String(registryUserSummary?.uav_count ?? registryUserUavIds.length)}</b> UAV(s)
                </div>
              </div>
              {!registryUserUavs.length ? (
                <div style={{ fontSize: 11, color: "#667085" }}>No UAV registered for this user yet. Use map click mode `Add UAV`.</div>
              ) : null}
            </div>
            <div style={{ display: "grid", gridTemplateColumns: "minmax(160px,0.9fr) minmax(150px,0.8fr) minmax(120px,0.6fr)", gap: 6 }}>
              <label style={{ fontSize: 12, minWidth: 0, display: "grid", gap: 4 }}>Route ID
                <input style={{ ...inputStyle, maxWidth: 170 }} value={simRouteId} onChange={(e) => { setSimRouteId(e.target.value); setMissionDefaultsDirty(true); }} />
              </label>
              <label style={{ fontSize: 12, minWidth: 0, display: "grid", gap: 4 }}>Airspace ID
                <input style={{ ...inputStyle, maxWidth: 150 }} value={simAirspace} onChange={(e) => { setSimAirspace(e.target.value); setMissionDefaultsDirty(true); }} />
              </label>
              <label style={{ fontSize: 12, minWidth: 0, display: "grid", gap: 4 }}>Requested Speed (m/s)
                <input style={{ ...inputStyle, maxWidth: 120 }} value={simRequestedSpeedMps} onChange={(e) => { setSimRequestedSpeedMps(e.target.value); setMissionDefaultsDirty(true); }} />
              </label>
            </div>
            <div style={{ display: "grid", gridTemplateColumns: "minmax(220px, 1fr)", gap: 6 }}>
              <label style={{ fontSize: 12, minWidth: 0, display: "grid", gap: 4 }}>UTM Bearer Token
                <input
                  style={{ ...inputStyle, maxWidth: 340 }}
                  value={utmAuthToken}
                  onChange={(e) => setUtmAuthToken(e.target.value)}
                  placeholder="local-dev-token"
                />
              </label>
            </div>
            <div style={{ display: "grid", gridTemplateColumns: "minmax(180px,1fr) minmax(180px,1fr) minmax(180px,1fr)", gap: 6 }}>
              <label style={{ fontSize: 12, minWidth: 0, display: "grid", gap: 4 }}>Start Time
                <input type="datetime-local" style={inputStyle} value={simPlannedStartAt} onChange={(e) => { setSimPlannedStartAt(e.target.value); setMissionDefaultsDirty(true); }} />
              </label>
              <label style={{ fontSize: 12, minWidth: 0, display: "grid", gap: 4 }}>End Time
                <input type="datetime-local" style={inputStyle} value={simPlannedEndAt} onChange={(e) => { setSimPlannedEndAt(e.target.value); setMissionDefaultsDirty(true); }} />
              </label>
              <label style={{ fontSize: 12, minWidth: 0, display: "grid", gap: 4 }}>Hold Reason
                <input style={inputStyle} value={holdReason} onChange={(e) => { setHoldReason(e.target.value); setMissionDefaultsDirty(true); }} />
              </label>
            </div>
            <div style={{ display: "grid", gridTemplateColumns: "repeat(3, minmax(0, 1fr))", gap: 6 }}>
              {MISSION_DEFAULT_EXTRA_FIELDS.map((field) => renderDynamicField(field, missionDefaultsExtraForm, (k, v) => setMissionDefaultsExtraField(k, String(v))))}
            </div>
            <div style={{ fontSize: 11, color: "#667085" }}>
              Advanced/debug settings (API URL and step ticks) stay internal. These defaults are persisted in the UAV agent DB for the selected user/UAV scope.
            </div>
            {(missionDefaultsInlineValidation.errors.length > 0 || missionDefaultsInlineValidation.warnings.length > 0) ? (
              <div
                style={{
                  border: "1px solid #eaecf0",
                  borderRadius: 10,
                  background: "#f8fafc",
                  padding: "8px 10px",
                  display: "grid",
                  gap: 6,
                }}
              >
                <div style={{ fontSize: 11, fontWeight: 700, color: "#344054" }}>Inline Validation Hints</div>
                {missionDefaultsInlineValidation.errors.map((issue, idx) => (
                  <div key={`mission-validation-error-${idx}`} style={{ fontSize: 11, color: "#b42318" }}>
                    {`Error: ${issue}`}
                  </div>
                ))}
                {missionDefaultsInlineValidation.warnings.map((issue, idx) => (
                  <div key={`mission-validation-warning-${idx}`} style={{ fontSize: 11, color: "#b54708" }}>
                    {`Warning: ${issue}`}
                  </div>
                ))}
                <div style={{ fontSize: 10, color: "#667085" }}>
                  Route checks / UTM verify / approval can fail on these conditions. Fix them before submit.
                </div>
              </div>
            ) : (
              <div style={{ fontSize: 11, color: "#027a48" }}>
                Inline validation: no mission-defaults capability/time issues detected for the selected UAV profile.
              </div>
            )}
          </div>

        </div>

        <div style={{ ...cardStyle, padding: 10, display: "grid", gap: 8, alignContent: "start", minWidth: 0 }}>
          <div style={{ display: "grid", gridTemplateColumns: "1fr auto", gap: 8, alignItems: "center" }}>
            <div>
              <div style={{ fontWeight: 700, color: "#101828" }}>Agent Copilot</div>
              <div style={{ fontSize: 11, color: "#667085" }}>Route + UTM/NFZ + network optimization</div>
            </div>
            <div style={{ display: "flex", gap: 6, alignItems: "center" }}>
              <button type="button" style={chipStyle(false)} onClick={() => setAgentConversation([])} disabled={agentBusy}>Clear</button>
            </div>
          </div>
            <div style={{ border: "1px solid #eaecf0", borderRadius: 10, background: "#fff", display: "grid", gridTemplateRows: "minmax(80px, 110px) auto auto", overflow: "hidden", minWidth: 0 }}>
            <div style={{ overflow: "auto", maxHeight: 110, padding: 8, display: "grid", gap: 8, alignContent: "start" }}>
              {agentConversation.length === 0 ? (
                <div style={{ border: "1px dashed #d0d5dd", borderRadius: 10, background: "#fcfcfd", padding: 10, display: "grid", gap: 6 }}>
                  <div style={{ fontSize: 12, color: "#344054", fontWeight: 700 }}>Run Copilot</div>
                  <div style={{ fontSize: 12, color: "#667085" }}>
                    Runs backend route planning with UTM checks and optional network optimization.
                  </div>
                  <div style={{ fontSize: 11, color: "#667085" }}>
                    `@` directives still work and override the UI selections.
                  </div>
                </div>
              ) : (
                agentConversation.map((m) =>
                  m.role === "user" ? (
                    <div key={m.id} style={{ justifySelf: "end", maxWidth: "94%", background: "#eef4ff", border: "1px solid #c7d7fe", borderRadius: 10, padding: "8px 10px" }}>
                      <div style={{ fontSize: 10, color: "#155eef", fontWeight: 700, marginBottom: 4 }}>You • {m.ts}</div>
                      <div style={{ fontSize: 12, color: "#1d2939", whiteSpace: "pre-wrap", wordBreak: "break-word" }}>{m.text}</div>
                    </div>
                  ) : (
                    <div key={m.id} style={{ justifySelf: "stretch", border: m.pending ? "1px solid #c7d7fe" : "1px solid #e4e7ec", background: m.pending ? "#f5f9ff" : "#f8fafc", borderRadius: 10, padding: "8px 10px" }}>
                      <div style={{ fontSize: 10, color: "#667085", fontWeight: 700, marginBottom: 6 }}>Agent • {m.ts}</div>
                      <div style={{ display: "grid", gap: 4 }}>
                        {m.lines.map((line, i) => <div key={`${m.id}-line-${i}`} style={{ fontSize: 12, color: m.pending ? "#155eef" : "#344054", fontStyle: m.pending ? "italic" : "normal" }}>{line}</div>)}
                      </div>
                      {!m.pending && m.toolTrace.length ? (
                        <details style={{ marginTop: 8 }}>
                          <summary style={{ cursor: "pointer", fontSize: 11, color: "#155eef", fontWeight: 700 }}>Tools & Actions</summary>
                          <div style={{ display: "grid", gap: 4, marginTop: 6 }}>
                            {m.toolTrace.map((t, i) => (
                              <div key={`${m.id}-trace-${i}`} style={{ border: "1px solid #eaecf0", borderRadius: 8, background: "#fff", padding: "6px 8px", fontSize: 11, color: "#344054" }}>
                                <code>{String(t.tool ?? "step")}</code>
                                <span style={{ marginLeft: 6, color: String(t.status ?? "") === "success" ? "#027a48" : "#b42318" }}>{String(t.status ?? "")}</span>
                                {"profile" in t ? <span style={{ marginLeft: 6, color: "#667085" }}>profile={String(t.profile)}</span> : null}
                                {"mode" in t ? <span style={{ marginLeft: 6, color: "#667085" }}>mode={String(t.mode)}</span> : null}
                                {"approved" in t ? <span style={{ marginLeft: 6, color: "#667085" }}>approved={String(t.approved)}</span> : null}
                                {isObject(t.utm_decision) ? (
                                  <div style={{ marginTop: 4, display: "grid", gap: 3 }}>
                                    <div style={{ color: asRecord(t.utm_decision)?.status === "approved" ? "#027a48" : "#b42318" }}>
                                      UTM decision: <b>{String(asRecord(t.utm_decision)?.status ?? "-")}</b>
                                      {Array.isArray(asRecord(t.utm_decision)?.reasons) && (asRecord(t.utm_decision)?.reasons as unknown[]).length > 0 ? (
                                        <span style={{ color: "#667085" }}> ({(asRecord(t.utm_decision)?.reasons as unknown[]).map(String).join(", ")})</span>
                                      ) : null}
                                    </div>
                                    {(() => {
                                      const summary = asRecord(asRecord(t.utm_decision)?.nfz_conflict_summary);
                                      const wps = Array.isArray(summary?.waypoints) ? (summary!.waypoints as unknown[]) : [];
                                      const segs = Array.isArray(summary?.segments) ? (summary!.segments as unknown[]) : [];
                                      if (!wps.length && !segs.length) return null;
                                      return (
                                        <div style={{ color: "#b42318" }}>
                                          Conflict locations:
                                          {wps.length ? ` waypoints ${wps.map(String).join(", ")}` : ""}
                                          {wps.length && segs.length ? ";" : ""}
                                          {segs.length ? ` segments ${segs.map(String).join(", ")}` : ""}
                                        </div>
                                      );
                                    })()}
                                    {Array.isArray(asRecord(t.utm_decision)?.messages) ? (
                                      <div style={{ display: "grid", gap: 2 }}>
                                        {(asRecord(t.utm_decision)?.messages as unknown[]).slice(0, 4).map((msgItem, j) => (
                                          <div key={`${m.id}-trace-${i}-utm-msg-${j}`} style={{ color: "#475467" }}>UTM: {String(msgItem)}</div>
                                        ))}
                                      </div>
                                    ) : null}
                                    {Array.isArray(asRecord(t.utm_decision)?.suggestions) && (asRecord(t.utm_decision)?.suggestions as unknown[]).length > 0 ? (
                                      <div style={{ display: "grid", gap: 2 }}>
                                        {(asRecord(t.utm_decision)?.suggestions as unknown[]).slice(0, 4).map((sItem, j) => (
                                          <div key={`${m.id}-trace-${i}-utm-sug-${j}`} style={{ color: "#b54708" }}>Suggestion: {String(sItem)}</div>
                                        ))}
                                      </div>
                                    ) : null}
                                  </div>
                                ) : null}
                                {isObject(t.nfz_conflict_feedback) && String(asRecord(t.nfz_conflict_feedback)?.summary ?? "").trim() ? (
                                  <div style={{ marginTop: 4, color: "#b42318", whiteSpace: "pre-wrap" }}>
                                    NFZ conflicts: {String(asRecord(t.nfz_conflict_feedback)?.summary)}
                                  </div>
                                ) : null}
                              </div>
                            ))}
                          </div>
                        </details>
                      ) : null}
                      {!m.pending && m.raw ? (
                        <details style={{ marginTop: 8 }}>
                          <summary style={{ cursor: "pointer", fontSize: 11, color: "#155eef", fontWeight: 700 }}>Raw Agent JSON</summary>
                          <pre style={{ margin: "6px 0 0", whiteSpace: "pre-wrap", wordBreak: "break-word", fontSize: 11 }}>{JSON.stringify(m.raw, null, 2)}</pre>
                        </details>
                      ) : null}
                    </div>
                  ),
                )
              )}
            </div>
            <div style={{ borderTop: "1px solid #eaecf0", padding: 8, display: "grid", gap: 6 }}>
              <div style={{ display: "grid", gap: 8, padding: 8, borderRadius: 10, border: "1px solid #eaecf0", background: "#fcfcfd" }}>
                <div style={{ display: "grid", gap: 6, gridTemplateColumns: "minmax(0,1.05fr) minmax(0,0.95fr)" }}>
                  <div style={{ display: "grid", gap: 4, minWidth: 0 }}>
                    <div style={{ fontSize: 11, color: "#475467", fontWeight: 700 }}>Optimization Profile</div>
                    <div style={segmentedGroupStyle()}>
                      <button type="button" style={segmentedOptionStyle(agentOptimizationProfile === "safe", "good")} onClick={() => setAgentOptimizationProfile("safe")} disabled={busy || agentBusy}>Safe</button>
                      <button type="button" style={segmentedOptionStyle(agentOptimizationProfile === "balanced")} onClick={() => setAgentOptimizationProfile("balanced")} disabled={busy || agentBusy}>Balanced</button>
                      <button type="button" style={segmentedOptionStyle(agentOptimizationProfile === "aggressive", "warn")} onClick={() => setAgentOptimizationProfile("aggressive")} disabled={busy || agentBusy}>Aggressive</button>
                    </div>
                    <div style={{ fontSize: 10, color: "#667085" }}>Route behavior.</div>
                  </div>
                  <div style={{ display: "grid", gap: 4, minWidth: 0 }}>
                    <div style={{ fontSize: 11, color: "#475467", fontWeight: 700 }}>Auto UTM Verify</div>
                    <div style={segmentedGroupStyle()}>
                      <button type="button" style={segmentedOptionStyle(agentAutoVerify, "good")} onClick={() => setAgentAutoVerify(true)} disabled={busy || agentBusy}>On</button>
                      <button type="button" style={segmentedOptionStyle(!agentAutoVerify)} onClick={() => setAgentAutoVerify(false)} disabled={busy || agentBusy}>Off</button>
                    </div>
                    <div style={{ fontSize: 10, color: "#667085" }}>Verify after run.</div>
                  </div>
                </div>
                <div style={{ display: "grid", gap: 6 }}>
                  <div style={{ display: "grid", gridTemplateColumns: "auto 1fr", gap: 10, alignItems: "center" }}>
                    <div style={{ fontSize: 11, color: "#475467", fontWeight: 700 }}>Auto Network Strategy</div>
                    <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
                      <div style={segmentedGroupStyle()}>
                        <button type="button" style={segmentedOptionStyle(agentAutoNetworkOptimize, "good")} onClick={() => setAgentAutoNetworkOptimize(true)} disabled={busy || agentBusy}>On</button>
                        <button type="button" style={segmentedOptionStyle(!agentAutoNetworkOptimize)} onClick={() => setAgentAutoNetworkOptimize(false)} disabled={busy || agentBusy}>Off</button>
                      </div>
                      <div style={{ fontSize: 10, color: "#667085" }}>Optimize for network goals.</div>
                    </div>
                  </div>
                  <div style={{ display: "flex", flexWrap: "wrap", gap: 6, alignItems: "center" }}>
                    <div style={{ fontSize: 11, color: "#475467", fontWeight: 700 }}>Network Priority</div>
                    <div style={{ ...segmentedGroupStyle(), opacity: agentAutoNetworkOptimize ? 1 : 0.55 }}>
                      <button type="button" style={segmentedOptionStyle(agentPreferredNetworkMode === "coverage")} onClick={() => setAgentPreferredNetworkMode("coverage")} disabled={busy || agentBusy || !agentAutoNetworkOptimize}>Coverage</button>
                      <button type="button" style={segmentedOptionStyle(agentPreferredNetworkMode === "qos")} onClick={() => setAgentPreferredNetworkMode("qos")} disabled={busy || agentBusy || !agentAutoNetworkOptimize}>QoS / Latency</button>
                      <button type="button" style={segmentedOptionStyle(agentPreferredNetworkMode === "power")} onClick={() => setAgentPreferredNetworkMode("power")} disabled={busy || agentBusy || !agentAutoNetworkOptimize}>Power</button>
                    </div>
                    <div style={{ fontSize: 10, color: "#667085" }}>`@` directives override.</div>
                  </div>
                </div>
              </div>
              <div style={{ display: "flex", justifyContent: "space-between", gap: 8, alignItems: "center", fontSize: 12 }}>
                <div style={{ color: agentStatusMsg ? (agentStatusMsg.toLowerCase().includes("failed") || agentStatusMsg.toLowerCase().includes("validation") ? "#b42318" : "#155eef") : (agentBusy ? "#155eef" : "#667085") }}>
                  {agentStatusMsg || `${agentBusy ? "Working..." : "Ready"} • Profile: ${agentOptimizationProfile} • Verify: ${agentAutoVerify ? "on" : "off"} • Network: ${agentAutoNetworkOptimize ? `${agentPreferredNetworkMode}` : "off"}`}
                </div>
                <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                  <div style={{ color: "#667085" }}>One-click run</div>
                  <button
                    type="button"
                    style={{ ...chipStyle(false), borderColor: "#155eef", background: "#eef4ff", color: "#155eef", fontWeight: 700 }}
                    onClick={() => void runAgentCopilot()}
                    disabled={busy || agentBusy}
                  >
                    Run Copilot
                  </button>
                </div>
              </div>
            </div>
          </div>
        </div>

      </div>

      <div style={{ display: "grid", gap: 12, gridTemplateColumns: "minmax(0, 1.15fr) minmax(0, 1fr)", alignItems: "start" }}>
        <div style={{ display: "grid", gap: 12, alignContent: "start" }}>
          <div style={{ ...cardStyle, padding: 10, display: "grid", gap: 8 }}>
            <div>
              <div style={{ fontWeight: 700, color: "#101828" }}>Mission Planner</div>
            </div>
            <div style={{ display: "flex", justifyContent: "space-between", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
              <div style={{ fontSize: 11, color: "#667085" }}>
                Selected UAV is highlighted. Other UAVs and their latest paths are shown in gray for scheduling context.
              </div>
            </div>
            <div style={{ border: "1px solid #eaecf0", borderRadius: 10, background: "#fff", padding: 6 }}>
          <MissionSyncMap
            title=""
            route={backendRoutePointsForMap.length ? backendRoutePointsForMap : routePointsForMap}
            plannedPosition={backendPlannedPosForMap ?? plannedPosForMap}
            trackedPositions={allFleetTracksForMap}
            selectedUavId={simUavId}
            noFlyZones={nfzZones}
                  baseStations={networkMap.bs}
                  coverage={networkMap.coverage}
                  routeOverlays={extraRoutesForMap}
                  externalResetSeq={plannerMapResetSeq}
                  focusSelectedTrack
                  clickable
                  onAddWaypoint={(p) => {
                    if (plannerMapClickMode === "add_uav") {
                      void addUavAtPoint(p);
                      return;
                    }
                    const currentPos = asRecord(uav?.position);
                    const currentWp = currentPos
                      ? {
                          x: Number(currentPos.x ?? 0),
                          y: Number(currentPos.y ?? 0),
                          z: Number(currentPos.z ?? 0),
                        }
                      : null;
                    const z = routeRows.length ? Number(routeRows[routeRows.length - 1]?.z || "40") : Number(currentWp?.z ?? 40);
                    setRouteRows((rows) => {
                      const next = rows.slice();
                      const routeHomeWpRaw = Array.isArray(uav?.waypoints) ? (uav.waypoints as unknown[]).find((w) => isObject(w)) : null;
                      const routeHomeWp = isObject(routeHomeWpRaw)
                        ? {
                            x: Number((routeHomeWpRaw as Record<string, unknown>).x ?? NaN),
                            y: Number((routeHomeWpRaw as Record<string, unknown>).y ?? NaN),
                            z: Number((routeHomeWpRaw as Record<string, unknown>).z ?? NaN),
                          }
                        : null;
                      const profileHome = {
                        x: Number(registryProfileForm.home_x ?? NaN),
                        y: Number(registryProfileForm.home_y ?? NaN),
                        z: Number(registryProfileForm.home_z ?? NaN),
                      };
                      const stableHome =
                        (routeHomeWp && [routeHomeWp.x, routeHomeWp.y, routeHomeWp.z].every(Number.isFinite) ? routeHomeWp : null)
                        ?? ([profileHome.x, profileHome.y, profileHome.z].every(Number.isFinite) ? profileHome : null)
                        ?? (currentWp && [currentWp.x, currentWp.y, currentWp.z].every(Number.isFinite) ? currentWp : null);
                      if (next.length === 0 && stableHome) {
                        next.push({
                          x: String(stableHome.x),
                          y: String(stableHome.y),
                          z: String(stableHome.z),
                          action: "transit",
                        });
                      }
                      next.push({ x: String(p.x), y: String(p.y), z: String(Number.isFinite(z) ? z : 40), action: "transit" });
                      return next;
                    });
                  }}
                />
            </div>
            <div style={{ fontSize: 11, color: "#667085" }}>
              Choosing a UAV (and license) refreshes backend state so the mission planner map and waypoint editor update automatically for that UAV. Map click can add waypoints for the selected UAV or add a new UAV (based on `Map Click` mode).
            </div>
            <div style={{ border: "1px solid #eaecf0", borderRadius: 10, background: "#fff", overflowX: "auto" }}>
              <div style={{ minWidth: 740 }}>
              <div style={{ display: "grid", gridTemplateColumns: `38px 38px 34px 92px 92px ${missionTableAltitudeColWidth}px ${missionTableActionColWidth}px`, gap: missionTableGap, alignItems: "center", padding: "6px 8px", background: "#f8fafc", borderBottom: "1px solid #eaecf0", fontSize: 11, color: "#667085", fontWeight: 700 }}>
                <div>Seq</div>
                <div>Type</div>
                <div>Src</div>
                <div>X</div>
                <div>Y</div>
                <div>Z (m)</div>
                <div style={{ position: "sticky", right: missionTableActionStickyRight, background: "#f8fafc", zIndex: 1, paddingLeft: 4 }}>Action</div>
              </div>
              <div style={{ display: "grid", gap: 5, height: missionTableRowViewportHeight, overflowY: "auto", padding: 6 }}>
              {visibleRouteRows.map((row, idx) => {
                const errs = routeValidation.rowErrors[idx] ?? [];
                const wpType = plannerWaypointTypeAbbrev(idx, routeRows.length, row.action);
                const isPlaceholderRow = idx >= routeRows.length;
                return (
                  <div key={`row-${idx}`} style={{ display: "grid", gridTemplateColumns: `38px 38px 34px 92px 92px ${missionTableAltitudeColWidth}px ${missionTableActionColWidth}px`, gap: missionTableGap, alignItems: "center", border: "1px solid #eaecf0", borderRadius: 8, padding: "6px 8px", background: "#fcfcfd" }}>
                    <div style={{ fontSize: 11, color: "#667085", textAlign: "center" }}>{idx}</div>
                    <div title={isPlaceholderRow ? "Placeholder" : wpType.title} style={{ fontSize: 10, fontWeight: 700, color: isPlaceholderRow ? "#98a2b3" : wpType.color, textAlign: "center" }}>
                      {isPlaceholderRow ? "--" : wpType.label}
                    </div>
                    <div style={{ textAlign: "center" }}>
                      {isPlaceholderRow ? (
                        <span style={{ fontSize: 10, color: "#98a2b3" }}>-</span>
                      ) : (() => {
                        const tag = wpOriginTag(row);
                        return (
                          <span
                            title={`${tag.title}${typeof row._mapped_from_original_index === "number" ? ` • mapped from original WP #${row._mapped_from_original_index}` : ""}${row._wp_source ? ` • ${row._wp_source}` : ""}`}
                            style={{
                              display: "inline-block",
                              minWidth: 18,
                              padding: "1px 4px",
                              borderRadius: 999,
                              fontSize: 10,
                              fontWeight: 700,
                              color: tag.color,
                              background: tag.bg,
                              border: `1px solid ${tag.border}`,
                            }}
                          >
                            {tag.label}
                          </span>
                        );
                      })()}
                    </div>
                    <input style={{ ...inputStyle, padding: "5px 6px", borderColor: errs.some((e) => e.startsWith("x")) ? "#f04438" : "#d0d5dd" }} value={row.x} onChange={(e) => upsertRouteRow(idx, (r) => ({ ...r, x: e.target.value }))} placeholder="x" />
                    <input style={{ ...inputStyle, padding: "5px 6px", borderColor: errs.some((e) => e.startsWith("y")) ? "#f04438" : "#d0d5dd" }} value={row.y} onChange={(e) => upsertRouteRow(idx, (r) => ({ ...r, y: e.target.value }))} placeholder="y" />
                    <input style={{ ...inputStyle, padding: "5px 6px", borderColor: errs.some((e) => e.startsWith("z")) ? "#f04438" : "#d0d5dd" }} value={row.z} onChange={(e) => upsertRouteRow(idx, (r) => ({ ...r, z: e.target.value }))} placeholder="z" />
                    <div style={{ position: "sticky", right: missionTableActionStickyRight, background: "#fcfcfd", zIndex: 1, paddingLeft: 4, display: "grid", gridTemplateColumns: "1fr auto", gap: 6, alignItems: "center" }}>
                      <select style={{ ...inputStyle, padding: "5px 6px", minWidth: 0 }} value={row.action} onChange={(e) => upsertRouteRow(idx, (r) => ({ ...r, action: e.target.value as WaypointAction }))}>
                        {WAYPOINT_ACTIONS.map((a) => <option key={a.value} value={a.value}>{a.label}</option>)}
                      </select>
                      <button
                        type="button"
                        style={{ ...chipStyle(false), padding: "4px 8px", borderColor: "#fda29b", color: "#b42318", background: "#fff5f4", fontWeight: 700 }}
                        onClick={() => {
                          setRouteRows((rows) => rows.filter((_, i) => i !== idx));
                        }}
                        disabled={isPlaceholderRow || idx >= routeRows.length}
                        title="Delete waypoint"
                      >
                        Del
                      </button>
                    </div>
                  </div>
                );
              })}
              </div>
              </div>
            </div>
            <div style={{ display: "flex", justifyContent: "space-between", gap: 8, fontSize: 11, color: "#667085", alignItems: "center" }}>
              <div>Waypoints: {routeRows.length}. Scroll to view all.</div>
              <div style={{ display: "flex", gap: 6, alignItems: "center", flexWrap: "wrap" }}>
                <button
                  type="button"
                  style={{ ...chipStyle(false), borderColor: "#155eef", background: "#eef4ff", color: "#155eef", fontWeight: 700 }}
                  onClick={() => { void planRoute(); }}
                  disabled={busy || agentBusy}
                  title="Confirm and save the current user-input waypoint path to DB as User Planned"
                >
                  Confirm User Path
                </button>
                <button
                  type="button"
                  style={{ ...chipStyle(false), borderColor: "#155eef", background: "#eef4ff", color: "#155eef", fontWeight: 700 }}
                  onClick={() => { void verifyMissionPlannerWithUtm(); }}
                  disabled={busy || agentBusy}
                  title="Plan current waypoints to backend, then run backend UTM auto workflow (checks, geofence, verify, approval)"
                >
                  Submit to UTM (Auto)
                </button>
                <button
                  type="button"
                  style={chipStyle(false)}
                  onClick={() => {
                    setRouteRows([]);
                    setMsg("Mission planner waypoints cleared");
                  }}
                >
                  Clear All WP
                </button>
              </div>
            </div>
            <div style={{ display: "flex", gap: 6, flexWrap: "wrap", alignItems: "center" }}>
              <div style={{ fontSize: 12, color: routeValidation.errors.length ? "#b42318" : "#475467" }}>
                {routeValidation.errors[0] ?? `Route valid. Max altitude: ${routeValidation.maxAlt}m`}
              </div>
              {(() => {
                const effRegs = resolveEffectiveUtmRegulationsFromState(asRecord(state)?.utm as Record<string, unknown> | undefined, effectiveOperatorLicenseId);
                if (!effRegs) return null;
                return (
                  <div style={{ fontSize: 11, color: "#667085" }}>
                    UTM license profile: <b>{String(effRegs.uav_size_class ?? "middle")}</b> • Max span {String(effRegs.max_route_span_m ?? "-")} m • Max wind {String(effRegs.max_wind_mps ?? "-")} m/s
                  </div>
                );
              })()}
            </div>
          </div>

          <div style={{ ...cardStyle, background: "#fff", padding: 10, display: "grid", gap: 10 }}>
            <div style={{ display: "grid", gridTemplateColumns: "1fr auto auto", gap: 8, alignItems: "center" }}>
              <div>
                <div style={{ fontWeight: 700, color: "#101828" }}>Standardized UAV Profile</div>
                <div style={{ fontSize: 11, color: "#667085" }}>
                  Backend validation enforces enum values and capability limits (max speed / max altitude) during route checks and UTM verify/approval.
                </div>
              </div>
              <div style={segmentedGroupStyle()}>
                <button type="button" style={segmentedOptionStyle(!registryProfileAdvanced, "good")} onClick={() => setRegistryProfileAdvanced(false)} disabled={busy || agentBusy}>Compact</button>
                <button type="button" style={segmentedOptionStyle(registryProfileAdvanced)} onClick={() => setRegistryProfileAdvanced(true)} disabled={busy || agentBusy}>Advanced</button>
              </div>
              <button type="button" style={{ ...chipStyle(registryProfileDirty), fontWeight: 700 }} onClick={() => void saveRegistryProfile()} disabled={busy || agentBusy || !simUavId}>Save Profile</button>
            </div>
            {!registryProfileAdvanced ? (
              <div style={{ fontSize: 11, color: "#667085" }}>
                Compact mode shows Identity & Classification only. Switch to Advanced to edit all standardized metadata sections in this card.
              </div>
            ) : null}
            <div style={{ display: "grid", gap: 10 }}>
              {visibleRegistryProfileSections.map((section) => (
                <div key={`registry-profile-merged-${section.title}`} style={{ borderTop: "1px solid #eaecf0", paddingTop: 10, display: "grid", gap: 8 }}>
                  <div>
                    <div style={{ fontWeight: 700, color: "#101828" }}>Standardized UAV Profile • {section.title}</div>
                    <div style={{ fontSize: 11, color: "#667085" }}>Persisted aircraft metadata in UAV registry DB state.</div>
                  </div>
                  <div style={{ display: "grid", gridTemplateColumns: section.columns, gap: 6, alignItems: "start" }}>
                    {section.fields.map((field) => renderDynamicField(field, registryProfileForm, setRegistryProfileField))}
                  </div>
                </div>
              ))}
            </div>
          </div>

          <div style={{ ...cardStyle, background: "#fff", padding: 10, display: "grid", gap: 8 }}>
            <div style={{ fontSize: 12, fontWeight: 700, color: "#101828" }}>UTM + DSS API Auth Quick Card</div>
            <div style={{ fontSize: 11, color: "#667085" }}>
              One bearer token is used for UTM/DSS/security/certification APIs. This card is for auth scopes only; runtime UTM/USS/DSS status is shown separately in Mission status.
            </div>
            <div style={{ display: "grid", gridTemplateColumns: "minmax(0,1fr) minmax(0,0.7fr)", gap: 6 }}>
              {[
                ["Read state/health", "read"],
                ["UTM updates (weather, NFZ, license)", "utm_write"],
                ["DSS upsert/delete (intents, subs, participants)", "dss_write"],
                ["Security auth config", "security_admin"],
                ["Certification packs/exports", "compliance_admin"],
              ].map(([scope, role]) => (
                <React.Fragment key={`auth-role-${scope}`}>
                  <div style={{ fontSize: 11, color: "#344054" }}>{scope}</div>
                  <div style={{ fontSize: 11, color: "#155eef", fontWeight: 700 }}>{role}</div>
                </React.Fragment>
              ))}
            </div>
            <pre style={codeSnippetStyle}>
{[
`export UTM_BASE=${quickAuthUtmBase}`,
`export UTM_TOKEN=${quickAuthToken}`,
`export AUTH="Authorization: Bearer \${UTM_TOKEN}"`,
`curl -sS "\${UTM_BASE}/api/utm/security/status" -H "\${AUTH}"`,
].join("\n")}
            </pre>
            <details>
              <summary style={{ cursor: "pointer", fontSize: 11, color: "#155eef", fontWeight: 700 }}>UAV Use + UTM Update</summary>
              <pre style={{ ...codeSnippetStyle, marginTop: 6 }}>
{[
`curl -sS -X POST "${quickAuthUavBase}/api/uav/live/utm-submit-mission" -H "Content-Type: application/json" \\`,
`  -d '{"user_id":"${ownerUserId}","uav_id":"${simUavId}","airspace_segment":"${simAirspace}","operator_license_id":"${effectiveOperatorLicenseId}","required_license_class":"${effectiveRequiredLicenseClass}","requested_speed_mps":${Number(simRequestedSpeedMps || "12") || 12}}'`,
`curl -sS -X POST "\${UTM_BASE}/api/utm/weather" -H "\${AUTH}" -H "Content-Type: application/json" \\`,
`  -d '{"airspace_segment":"${simAirspace}","wind_mps":7,"visibility_km":10,"precip_mmph":0,"storm_alert":false}'`,
`curl -sS -X POST "\${UTM_BASE}/api/utm/nfz" -H "\${AUTH}" -H "Content-Type: application/json" \\`,
`  -d '{"zone_id":"nfz-temp","cx":150,"cy":110,"radius_m":35,"z_min":0,"z_max":120,"reason":"hospital_helipad"}'`,
].join("\n")}
              </pre>
            </details>
            <details>
              <summary style={{ cursor: "pointer", fontSize: 11, color: "#155eef", fontWeight: 700 }}>DSS Upsert + Delete</summary>
              <pre style={{ ...codeSnippetStyle, marginTop: 6 }}>
{[
`curl -sS -X POST "\${UTM_BASE}/api/utm/dss/participants" -H "\${AUTH}" -H "Content-Type: application/json" \\`,
`  -d '{"participant_id":"uss-local-user-1","uss_base_url":"http://127.0.0.1:9000","roles":["uss"],"status":"active","metadata":{}}'`,
`curl -sS -X POST "\${UTM_BASE}/api/utm/dss/operational-intents" -H "\${AUTH}" -H "Content-Type: application/json" \\`,
`  -d '{"intent_id":"${quickAuthIntentId}","manager_uss_id":"uss-local-user-1","state":"contingent","priority":"normal","conflict_policy":"conditional_approve","volume4d":{"x":[57.4,222.4],"y":[150.8,210.8],"z":[40,50],"time_start":"2026-02-27T13:17:00Z","time_end":"2026-02-27T13:37:00Z"}}'`,
`curl -sS -X DELETE "\${UTM_BASE}/api/utm/dss/operational-intents/${quickAuthIntentId}" -H "\${AUTH}"`,
].join("\n")}
              </pre>
            </details>
            <details>
              <summary style={{ cursor: "pointer", fontSize: 11, color: "#155eef", fontWeight: 700 }}>Certification + Auth Administration</summary>
              <pre style={{ ...codeSnippetStyle, marginTop: 6 }}>
{[
`curl -sS -X POST "\${UTM_BASE}/api/utm/certification/pack/generate" -H "\${AUTH}" -H "Content-Type: application/json" \\`,
`  -d '{"profile_id":"local-dev","generated_by":"uav-page"}'`,
`curl -sS -X POST "\${UTM_BASE}/api/utm/security/trust-store/peers" -H "\${AUTH}" -H "Content-Type: application/json" \\`,
`  -d '{"issuer":"uss-peer-1","key_id":"key-peer-1","secret":"change-me","status":"active"}'`,
`curl -sS -X POST "\${UTM_BASE}/api/utm/security/service-tokens" -H "\${AUTH}" -H "Content-Type: application/json" \\`,
`  -d '{"token":"uav-ops-token","roles":["read","utm_write","dss_write","compliance_admin"]}'`,
].join("\n")}
              </pre>
            </details>
          </div>
        </div>

        <div style={{ display: "grid", gap: 12, alignContent: "start" }}>
          <div style={{ ...cardStyle, padding: 10, display: "grid", gap: 8, minHeight: 0 }}>
            <div style={{ display: "grid", gridTemplateColumns: "1fr auto auto", gap: 8, alignItems: "center" }}>
              <div style={{ fontWeight: 700, color: "#101828" }}>Mission status</div>
              <span
                style={{
                  display: "inline-block",
                  borderRadius: 999,
                  padding: "2px 8px",
                  fontSize: 10,
                  fontWeight: 700,
                  background: lastSubmitStatusText === "APPROVED" ? "#ecfdf3" : lastSubmitStatusText === "REJECTED" ? "#fef3f2" : "#f2f4f7",
                  color: lastSubmitStatusText === "APPROVED" ? "#027a48" : lastSubmitStatusText === "REJECTED" ? "#b42318" : "#475467",
                  border: `1px solid ${lastSubmitStatusText === "APPROVED" ? "#abefc6" : lastSubmitStatusText === "REJECTED" ? "#fecdca" : "#d0d5dd"}`,
                }}
              >
                {lastSubmitStatusText}
              </span>
              <button
                type="button"
                style={chipStyle(missionStatusExpanded)}
                onClick={() => setMissionStatusExpanded((v) => !v)}
              >
                {missionStatusExpanded ? "Collapse" : "Expand"}
              </button>
            </div>
            <div style={{ border: "1px solid #eaecf0", borderRadius: 8, background: "#fcfcfd", padding: "8px 10px", display: "grid", gap: 7 }}>
              <div style={{ fontSize: 11, color: "#667085" }}>Mission summary</div>
              <div style={{ display: "grid", gridTemplateColumns: "repeat(2, minmax(0,1fr))", gap: 6 }}>
                {missionSummaryFields.map((row) => (
                  <div key={`mission-summary-${row.label}`} style={{ border: "1px solid #f2f4f7", borderRadius: 8, background: "#fff", padding: "5px 7px", display: "grid", gap: 2 }}>
                    <div style={{ fontSize: 10, color: "#667085" }}>{row.label}</div>
                    <div style={{ fontSize: 11, color: "#101828", fontWeight: 600, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }} title={row.value}>
                      {row.value}
                    </div>
                  </div>
                ))}
              </div>
              <div style={{ display: "flex", gap: 6, flexWrap: "wrap", alignItems: "center" }}>
                {missionSummaryFlows.map((flow) => {
                  const pass = flow.ok === true;
                  const fail = flow.ok === false;
                  const waiting = !flow.sent || flow.ok === null;
                  return (
                    <span
                      key={`mission-summary-flow-${flow.name}`}
                      style={{
                        borderRadius: 999,
                        border: `1px solid ${pass ? "#abefc6" : fail ? "#fecdca" : "#d0d5dd"}`,
                        background: pass ? "#ecfdf3" : fail ? "#fef3f2" : "#f2f4f7",
                        color: pass ? "#027a48" : fail ? "#b42318" : "#475467",
                        padding: "2px 8px",
                        fontSize: 10,
                        fontWeight: 700,
                      }}
                      title={waiting ? `${flow.name} status pending or not sent` : `${flow.name} status ready`}
                    >
                      {flow.name}: {flow.text}
                    </span>
                  );
                })}
              </div>
            </div>
            {!missionStatusExpanded ? (
              <div style={{ fontSize: 11, color: "#667085" }}>
                Showing compact summary. Expand to view full UTM/USS/DSS mission status details.
              </div>
            ) : (
              <>
                <div style={{ maxHeight: 340, overflowY: "auto", paddingRight: 4, display: "grid", gap: 6 }}>
                  <div style={{ fontSize: 11, color: "#667085" }}>
                    Backend UTM state source: {sourceBadge(utmBackendSource)}
                  </div>
                  <div style={{ fontSize: 11, color: "#475467" }}>
                    Proposed UTM request context: user <code>{String(identity?.selected_user_id ?? ownerUserId)}</code> • UAV <code>{simUavId}</code> • route <code>{String(uav?.route_id ?? simRouteId)}</code> • license <code>{effectiveOperatorLicenseId}</code> • airspace <code>{simAirspace}</code>
                  </div>
                  <div style={{ border: "1px solid #eaecf0", borderRadius: 8, background: "#fff", padding: "8px 10px", display: "grid", gap: 8 }}>
                    <div style={{ display: "flex", justifyContent: "space-between", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
                      <div style={{ fontSize: 11, color: "#667085" }}>UTM approval workflow</div>
                      <span
                        style={{
                          display: "inline-block",
                          borderRadius: 999,
                          padding: "2px 8px",
                          fontSize: 11,
                          fontWeight: 700,
                          background: lastSubmitStatusText === "APPROVED" ? "#ecfdf3" : lastSubmitStatusText === "REJECTED" ? "#fef3f2" : "#f2f4f7",
                          color: lastSubmitStatusText === "APPROVED" ? "#027a48" : lastSubmitStatusText === "REJECTED" ? "#b42318" : "#475467",
                          border: `1px solid ${lastSubmitStatusText === "APPROVED" ? "#abefc6" : lastSubmitStatusText === "REJECTED" ? "#fecdca" : "#d0d5dd"}`,
                        }}
                      >
                        {lastSubmitStatusText}
                      </span>
                    </div>
                    <div style={{ fontSize: 11, color: "#667085" }}>
                      Backend submit order: route checks {"->"} geofence submit {"->"} UTM verify {"->"} approval request.
                    </div>
                    <div style={{ display: "grid", gridTemplateColumns: "repeat(2, minmax(0,1fr))", gap: 8 }}>
                      {[
                        ["Procedure Status", lastSubmitStatusText],
                        ["UTM Check Pass/Fail/Pending", `${utmDetailPassed}/${utmDetailFailed}/${utmDetailPending}`],
                      ].map(([label, value]) => (
                        <div key={`workflow-summary-${String(label)}`} style={{ border: "1px solid #f2f4f7", borderRadius: 8, background: "#fcfcfd", padding: "6px 8px", display: "grid", gap: 4 }}>
                          <div style={{ fontSize: 10, color: "#667085" }}>{String(label)}</div>
                          <div style={{ fontSize: 12, fontWeight: 700, color: "#101828" }}>{String(value)}</div>
                        </div>
                      ))}
                    </div>
                    <div style={{ display: "grid", gap: 6 }}>
                      {utmProcedureStepsForDisplay.map((row) => (
                        <div key={`submit-procedure-utm-${row.label}`} style={{ display: "grid", gap: 6 }}>
                          <div style={{ display: "grid", gridTemplateColumns: "160px auto 1fr", gap: 8, alignItems: "center" }}>
                            <div style={{ fontSize: 11, color: "#475467", fontWeight: 600 }}>{row.label}</div>
                            <div>{row.ok === true ? badge(true) : row.ok === false ? badge(false) : badge("WAIT")}</div>
                            <div style={{ fontSize: 11, color: row.ok === false ? "#b42318" : "#667085" }}>{row.detail}</div>
                          </div>
                          {row.label.startsWith("3) UTM verify") ? (
                            <details style={{ marginLeft: 12, borderLeft: "2px solid #eaecf0", paddingLeft: 10 }}>
                              <summary style={{ cursor: "pointer", fontSize: 11, color: "#475467", fontWeight: 700 }}>
                                UTM verify detail checks ({utmDetailPassed}/{utmDetailedCheckRows.length} pass)
                              </summary>
                              <div style={{ marginTop: 6, display: "grid", gap: 6 }}>
                                {utmDetailedCheckRows.map((checkRow) => (
                                  <div key={`submit-procedure-verify-${checkRow.label}`} style={{ display: "grid", gridTemplateColumns: "130px auto 1fr", gap: 8, alignItems: "center" }}>
                                    <div style={{ fontSize: 11, color: "#667085", fontWeight: 600 }}>{checkRow.label}</div>
                                    <div>{checkRow.ok === true ? badge(true) : checkRow.ok === false ? badge(false) : badge("WAIT")}</div>
                                    <div style={{ fontSize: 11, color: checkRow.ok === false ? "#b42318" : "#475467" }}>{checkRow.detail}</div>
                                  </div>
                                ))}
                              </div>
                            </details>
                          ) : null}
                        </div>
                      ))}
                    </div>
                  </div>
                  <div style={{ border: "1px solid #eaecf0", borderRadius: 8, background: "#fff", padding: "8px 10px", display: "grid", gap: 8 }}>
                    <div style={{ fontSize: 11, color: "#667085" }}>USS layer status (participant/manager side)</div>
                    <div style={{ display: "grid", gap: 6 }}>
                      {ussProcedureRowsForDisplay.map((row) => (
                        <div key={`submit-procedure-uss-${row.label}`} style={{ display: "grid", gridTemplateColumns: "160px auto 1fr", gap: 8, alignItems: "center" }}>
                          <div style={{ fontSize: 11, color: "#475467", fontWeight: 600 }}>{row.label}</div>
                          <div>{row.ok === true ? badge(true) : row.ok === false ? badge(false) : badge("WAIT")}</div>
                          <div style={{ fontSize: 11, color: row.ok === false ? "#b42318" : "#667085" }}>{row.detail}</div>
                        </div>
                      ))}
                    </div>
                  </div>
                  <div style={{ border: "1px solid #eaecf0", borderRadius: 8, background: "#fff", padding: "8px 10px", display: "grid", gap: 8 }}>
                    <div style={{ display: "flex", justifyContent: "space-between", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
                      <div style={{ fontSize: 11, color: "#667085" }}>DSS publication + mitigation (UTM side)</div>
                      <span
                        style={{
                          display: "inline-block",
                          borderRadius: 999,
                          padding: "2px 8px",
                          fontSize: 10,
                          fontWeight: 700,
                          background: !lastDssMitigation ? "#f2f4f7" : (lastDssMitigation.resolved ? "#ecfdf3" : "#fef3f2"),
                          color: !lastDssMitigation ? "#475467" : (lastDssMitigation.resolved ? "#027a48" : "#b42318"),
                          border: `1px solid ${!lastDssMitigation ? "#d0d5dd" : (lastDssMitigation.resolved ? "#abefc6" : "#fecdca")}`,
                        }}
                      >
                        {!lastDssMitigation ? "NOT RUN" : (lastDssMitigation.resolved ? "MITIGATED" : "BLOCKED")}
                      </span>
                    </div>
                    <div style={{ fontSize: 11, color: "#667085" }}>
                      Layer and publish status are kept separate from USS participant health.
                    </div>
                    <div style={{ display: "grid", gap: 6 }}>
                      {dssProcedureRowsForDisplay.map((row) => (
                        <div key={`submit-procedure-dss-${row.label}`} style={{ display: "grid", gridTemplateColumns: "160px auto 1fr", gap: 8, alignItems: "center" }}>
                          <div style={{ fontSize: 11, color: "#475467", fontWeight: 600 }}>{row.label}</div>
                          <div>{row.ok === true ? badge(true) : row.ok === false ? badge(false) : badge("WAIT")}</div>
                          <div style={{ fontSize: 11, color: row.ok === false ? "#b42318" : "#667085" }}>{row.detail}</div>
                        </div>
                      ))}
                    </div>
                    <div style={{ marginTop: 2, paddingTop: 8, borderTop: "1px solid #f2f4f7", display: "grid", gap: 6 }}>
                      <div style={{ fontSize: 11, color: "#667085" }}>DSS mitigation procedure (auto detour + retry)</div>
                      <details>
                        <summary style={{ cursor: "pointer", fontSize: 11, color: "#475467", fontWeight: 700 }}>
                          Show DSS mitigation steps
                        </summary>
                        <div style={{ marginTop: 6, display: "grid", gap: 6 }}>
                          {dssMitigationRowsForDisplay.map((row) => (
                            <div key={`submit-procedure-dss-mitigation-${row.label}`} style={{ display: "grid", gridTemplateColumns: "160px auto 1fr", gap: 8, alignItems: "center" }}>
                              <div style={{ fontSize: 11, color: "#475467", fontWeight: 600 }}>{row.label}</div>
                              <div>{row.ok === true ? badge(true) : row.ok === false ? badge(false) : badge("WAIT")}</div>
                              <div style={{ fontSize: 11, color: row.ok === false ? "#b42318" : "#667085" }}>{row.detail}</div>
                            </div>
                          ))}
                        </div>
                      </details>
                    </div>
                  </div>
                </div>
                <div style={{ border: "1px solid #fecdca", borderRadius: 8, background: "#fff5f4", padding: "8px 10px", display: "grid", gap: 6 }}>
                  <div style={{ fontSize: 11, color: "#b42318", fontWeight: 700 }}>Important alerts ({visibleUtmFailureIssues.length})</div>
                  {visibleUtmFailureIssues.length > 0 ? (
                    <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
                      {visibleUtmFailureIssues.map((issue, idx) => (
                        <span
                          key={`last-submit-issue-${idx}`}
                          style={{
                            fontSize: 10,
                            color: "#b42318",
                            border: "1px solid #fecdca",
                            borderRadius: 999,
                            padding: "2px 8px",
                            background: "#fff",
                          }}
                        >
                          {issue}
                        </span>
                      ))}
                    </div>
                  ) : (
                    <div style={{ fontSize: 11, color: "#667085" }}>
                      {lastUtmSubmitResult ? "No blocking issue reported by UTM workflow." : "Submit mission to UTM to run all procedure steps."}
                    </div>
                  )}
                  {lastSubmitSuggestions.length > 0 ? <div style={{ fontSize: 11, color: "#b54708" }}>Next action: {lastSubmitSuggestions.join(" • ")}</div> : null}
                </div>
              </>
            )}
          </div>

          <div style={{ ...cardStyle, padding: 10, display: "grid", gap: 8 }}>
            <div style={{ display: "grid", gridTemplateColumns: "1fr auto auto", gap: 8, alignItems: "center" }}>
              <div style={{ fontWeight: 700, color: "#101828" }}>UAV Status</div>
              <button
                type="button"
                style={{ ...chipStyle(false), borderColor: "#155eef", background: "#eef4ff", color: "#155eef", fontWeight: 700, minWidth: 122 }}
                onClick={() => void loadState()}
                disabled={busy}
              >
                Load UAV State
              </button>
              <button
                type="button"
                style={{ ...chipStyle(false), borderColor: "#079455", background: "#ecfdf3", color: "#027a48", fontWeight: 700, minWidth: 122 }}
                onClick={() => void refreshUavTelemetry()}
                disabled={busy || !simUavId.trim()}
                title="Refresh UAV telemetry and restore battery to 100%"
              >
                Refresh UAV
              </button>
            </div>
            <div style={{ fontSize: 11, color: "#667085" }}>
              User <code>{String(identity?.selected_user_id ?? ownerUserId)}</code> • UAV <code>{String(identity?.selected_uav_id ?? simUavId)}</code> • Source {sourceBadge(uavBackendSource ?? uavDataSource)}
            </div>
            <div style={{ display: "grid", gridTemplateColumns: "repeat(4, minmax(0,1fr))", gap: 8 }}>
              {[
                ["Phase", <code>{String(uav?.flight_phase ?? "-")}</code>],
                ["Waypoint", <span><b>{waypointProgress.label}</b> • {waypointProgress.pct}{waypointProgress.hint ? <span style={{ marginLeft: 6, color: "#b54708" }}>({waypointProgress.hint})</span> : null}</span>],
                ["Armed / Active", `${uav?.armed === true ? "Yes" : "No"} / ${uav?.active === true ? "Yes" : "No"}`],
                ["Battery / Speed", `${String(uav?.battery_pct ?? "-")}% / ${Number.isFinite(Number(uav?.velocity_mps)) ? Number(uav?.velocity_mps).toFixed(1) : "-"} m/s`],
              ].map(([label, value]) => (
                <div key={String(label)} style={{ border: "1px solid #eaecf0", borderRadius: 8, background: "#fff", padding: "6px 8px", display: "grid", gap: 4 }}>
                  <div style={{ color: "#667085", fontSize: 11 }}>{label}</div>
                  <div style={{ fontSize: 12, color: "#101828", fontWeight: 600, minHeight: 18 }}>{value as React.ReactNode}</div>
                </div>
              ))}
            </div>
            <div style={{ display: "flex", gap: 6, flexWrap: "wrap", alignItems: "center" }}>
              <span style={{ ...chipStyle(false), cursor: "default", ...(actionUiBlocked.launch ? { background: "#fef3f2", color: "#b42318", borderColor: "#fecdca" } : { background: "#ecfdf3", color: "#027a48", borderColor: "#abefc6" }) }}>
                Launch Gate {actionUiBlocked.launch ? "Blocked" : "Ready"}
              </span>
              <span style={{ ...chipStyle(false), cursor: "default", ...(postLaunchControlsLocked ? { background: "#fef3f2", color: "#b42318", borderColor: "#fecdca" } : { background: "#ecfdf3", color: "#027a48", borderColor: "#abefc6" }) }}>
                Controls {postLaunchControlsLocked ? "Locked" : "Available"}
              </span>
            </div>
            <div style={{ fontSize: 12, color: msgTone === "error" ? "#b42318" : msgTone === "warning" ? "#b54708" : "#475467", minHeight: 18 }}>
              {briefMsg || "Load state, then launch (UTM/DSS pass) to enable mission controls."}
            </div>
            {actionUiBlocked.launch && flightGateIssues.length > 0 ? (
              <div style={{ display: "flex", flexWrap: "wrap", gap: 5 }}>
                {flightGateIssues.map((issue, idx) => (
                  <span
                    key={`launch-issue-${idx}`}
                    style={{ fontSize: 10, color: "#b42318", border: "1px solid #fecdca", background: "#fef3f2", borderRadius: 999, padding: "2px 7px", whiteSpace: "nowrap" }}
                  >
                    {issue}
                  </span>
                ))}
              </div>
            ) : null}
            <div style={{ display: "grid", gap: 6 }}>
              <div style={{ display: "flex", gap: 6, flexWrap: "wrap", alignItems: "center" }}>
                <button type="button" style={flightControlBtnStyle(actionUiBlocked.launch)} onClick={() => runFlightControlGuarded("launch", () => { void launchAndLog(); })} disabled={busy || actionUiBlocked.launch}>Launch</button>
                <button type="button" style={flightControlBtnStyle(actionUiBlocked.step)} onClick={() => runFlightControlGuarded("step", () => { void step(); })} disabled={busy || actionUiBlocked.step}>Step</button>
                <button type="button" style={flightControlBtnStyle(actionUiBlocked.hold)} onClick={() => runFlightControlGuarded("hold", () => { void hold(); })} disabled={busy || actionUiBlocked.hold}>Hold</button>
                <button type="button" style={flightControlBtnStyle(actionUiBlocked.resume)} onClick={() => runFlightControlGuarded("resume", () => { void resume(); })} disabled={busy || actionUiBlocked.resume}>Resume</button>
                <button type="button" style={flightControlBtnStyle(actionUiBlocked.rth)} onClick={() => runFlightControlGuarded("rth", () => { void rth(); })} disabled={busy || actionUiBlocked.rth}>RTH</button>
                <button type="button" style={flightControlBtnStyle(actionUiBlocked.land)} onClick={() => runFlightControlGuarded("land", () => { void land(); })} disabled={busy || actionUiBlocked.land}>Land</button>
                <button type="button" style={flightControlBtnStyle(actionUiBlocked.land)} onClick={() => { void endMissionAndCleanup(); }} disabled={busy || actionUiBlocked.land}>End mission</button>
                <button
                  type="button"
                  style={autoFlyEnabled
                    ? { ...chipStyle(false), borderColor: "#fda29b", background: "#fef3f2", color: "#b42318", fontWeight: 700 }
                    : (autoFlyBlocked
                      ? flightControlBtnStyle(true)
                      : { ...chipStyle(false), borderColor: "#079455", background: "#ecfdf3", color: "#027a48", fontWeight: 700 })}
                  onClick={() => setAutoFlyEnabled((v) => !v)}
                  disabled={busy || agentBusy || autoFlyBlocked}
                >
                  {autoFlyEnabled ? "Stop Auto Fly" : "Auto Fly"}
                </button>
              </div>
              <div style={{ display: "flex", gap: 6, flexWrap: "wrap", alignItems: "center" }}>
                <span style={{ fontSize: 11, color: "#667085" }}>Mission actions</span>
                {MISSION_ACTION_CHOICES.map((opt) => {
                  const actionDisabled = busy || agentBusy || missionActionBlocked;
                  return (
                  <button
                    key={`mission-action-chip-${opt.value}`}
                    type="button"
                    style={actionDisabled ? flightControlBtnStyle(true) : chipStyle(missionActionChoice === opt.value)}
                    onClick={() => setMissionActionChoice(opt.value)}
                    disabled={actionDisabled}
                  >
                    {opt.label}
                  </button>
                  );
                })}
                <button
                  type="button"
                  style={flightControlBtnStyle(missionActionBlocked)}
                  onClick={() => { void runMissionActionChoice(); }}
                  disabled={busy || agentBusy || missionActionBlocked}
                >
                  Run Action
                </button>
              </div>
              {!isLaunched ? (
                <div style={{ fontSize: 11, color: "#b54708" }}>Mission actions are disabled until UAV is launched.</div>
              ) : null}
              <div style={{ fontSize: 11, color: "#667085" }}>
                {sessionInfo?.updated_at ? `Upd ${String(sessionInfo.updated_at)}` : "Upd -"}
                {stationState ? ` • DB ${String(stationState.last_control_action ?? "-")}/${String(stationState.last_control_status ?? "-")}` : ""}
              </div>
            </div>
          </div>

          <div style={{ ...cardStyle, padding: 10, display: "grid", gap: 8 }}>
            <div style={{ fontWeight: 700, color: "#101828" }}>Path Records</div>
            <div style={{ fontSize: 11, color: "#667085" }}>
              Recorded path summaries for the selected user/UAV session. Choose which paths are shown on the map and reload waypoint actions from DB.
            </div>
            <div style={{ fontSize: 11, color: "#475467" }}>
              Backend current route source: <b>{currentMissionRouteOrigin ? plannerPathSourceLabel(currentMissionRouteOrigin) : "Current / Unspecified"}</b>
            </div>
            <div style={{ fontSize: 11, color: "#475467" }}>
              WP Editor Source: <b>{plannerPathSourceLabel(plannerEditorSource)}</b>
              {pathRecordSync ? ` • DB sync UAV ${String(pathRecordSync.uav_db ?? "-")} / UTM ${String(pathRecordSync.utm_db ?? "-")}` : ""}
            </div>
            <div style={{ overflowX: "auto", border: "1px solid #eaecf0", borderRadius: 10, background: "#fff" }}>
              <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 11, minWidth: 780 }}>
                <thead>
                  <tr style={{ background: "#f9fafb", color: "#475467" }}>
                    {["Type", "Show", "WP", "O/I", "Start", "End", "Flight", "User", "UAV", "DB", "Action"].map((h) => (
                      <th key={`path-rec-col-${h}`} style={{ textAlign: "left", padding: "6px 8px", borderBottom: "1px solid #eaecf0", fontWeight: 700, whiteSpace: "nowrap" }}>{h}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {pathRecordTableRows.map((row) => {
                    const showOnMap = row.key === "user_planned"
                      ? plannerShowUserPath
                      : row.key === "agent_replanned"
                        ? plannerShowAgentPath
                        : row.key === "dss_replanned"
                          ? plannerShowDssPath
                          : plannerShowUtmPath;
                    const setShowOnMap = () => {
                      if (row.key === "user_planned") setPlannerShowUserPath((v) => !v);
                      else if (row.key === "agent_replanned") setPlannerShowAgentPath((v) => !v);
                      else if (row.key === "dss_replanned") setPlannerShowDssPath((v) => !v);
                      else setPlannerShowUtmPath((v) => !v);
                    };
                    const isReplanRow = row.key === "agent_replanned" || row.key === "dss_replanned";
                    return (
                      <tr key={`path-record-row-${row.key}`} style={{ borderTop: "1px solid #f2f4f7" }}>
                        <td style={{ padding: "6px 8px", verticalAlign: "top" }}>
                          <div style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
                            <span style={{ width: 10, height: 10, borderRadius: 999, background: row.color, border: "1px solid rgba(0,0,0,0.08)" }} />
                            <span style={{ fontWeight: 600, color: "#101828" }}>{row.label}</span>
                          </div>
                          <div style={{ color: "#667085" }}>{row.exists ? String(row.routeId || "-") : "No record"}</div>
                        </td>
                        <td style={{ padding: "6px 8px", verticalAlign: "top" }}>
                          <button type="button" style={chipStyle(showOnMap)} onClick={setShowOnMap} disabled={!row.exists}>
                            {showOnMap ? "Shown" : "Hidden"}
                          </button>
                        </td>
                        <td style={{ padding: "6px 8px", verticalAlign: "top", color: "#475467" }}>{row.exists ? String(row.waypointsTotal) : "-"}</td>
                        <td style={{ padding: "6px 8px", verticalAlign: "top", color: "#475467", whiteSpace: "nowrap" }}>
                          {row.exists ? `${row.originOriginalCount}/${row.originInsertedCount}` : "-"}
                          {isReplanRow && row.exists && (row.losPruneDeletionsCount != null || row.losPrunePassesCount != null) ? (
                            <div style={{ marginTop: 2, fontSize: 10, color: "#667085", whiteSpace: "nowrap" }}>
                              LoS: {String(row.losPruneDeletionsCount ?? 0)} del / {String(row.losPrunePassesCount ?? 0)} passes
                              {row.insertedWaypointsCount != null ? ` • I=${String(row.insertedWaypointsCount)}` : ""}
                            </div>
                          ) : null}
                          {isReplanRow && row.exists && (row.insertedTrimDeletionsCount != null || row.insertedTrimPassesCount != null) ? (
                            <div style={{ marginTop: 2, fontSize: 10, color: "#667085", whiteSpace: "nowrap" }}>
                              Trim: {String(row.insertedTrimDeletionsCount ?? 0)} del / {String(row.insertedTrimPassesCount ?? 0)} passes
                            </div>
                          ) : null}
                        </td>
                        <td style={{ padding: "6px 8px", verticalAlign: "top", color: "#475467", whiteSpace: "nowrap" }}>{row.exists ? formatPathPointBrief(row.start) : "-"}</td>
                        <td style={{ padding: "6px 8px", verticalAlign: "top", color: "#475467", whiteSpace: "nowrap" }}>{row.exists ? formatPathPointBrief(row.end) : "-"}</td>
                        <td style={{ padding: "6px 8px", verticalAlign: "top", color: "#475467", whiteSpace: "nowrap" }}>
                          {row.exists ? formatFlightTimeBrief(row.estFlightSeconds) : "-"}
                          {typeof row.distanceM === "number" && Number.isFinite(row.distanceM) && row.distanceM > 0 ? ` • ${row.distanceM.toFixed(0)}m` : ""}
                        </td>
                        <td style={{ padding: "6px 8px", verticalAlign: "top", color: "#475467" }}><code>{row.userId}</code></td>
                        <td style={{ padding: "6px 8px", verticalAlign: "top", color: "#475467" }}><code>{row.uavId}</code></td>
                        <td style={{ padding: "6px 8px", verticalAlign: "top" }}>
                          <div style={{ display: "flex", gap: 4, flexWrap: "wrap" }}>
                            <span style={{ ...chipStyle(row.inUavDb), padding: "3px 8px", fontSize: 10, cursor: "default" }}>UAV</span>
                            <span style={{ ...chipStyle(row.inUtmDb), padding: "3px 8px", fontSize: 10, cursor: "default" }}>UTM</span>
                          </div>
                        </td>
                        <td style={{ padding: "6px 8px", verticalAlign: "top" }}>
                          <div style={{ display: "flex", gap: 4, flexWrap: "wrap" }}>
                            <button
                              type="button"
                              style={chipStyle(plannerEditorSource === row.key)}
                              disabled={busy || agentBusy || !row.exists || !availablePlannerSources.includes(row.key)}
                              onClick={() => {
                                focusPathRecordInPlanner(row.key);
                              }}
                            >
                              Load WPs
                            </button>
                            <button
                              type="button"
                              style={{ ...chipStyle(false), borderColor: "#fda29b", color: "#b42318", background: "#fff5f4", fontWeight: 700 }}
                              disabled={busy || agentBusy || !row.exists}
                              onClick={() => { void deletePathRecord(row.key); }}
                              title={`Delete ${row.label} from DB`}
                            >
                              Delete
                            </button>
                          </div>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
            <div style={{ display: "flex", gap: 10, flexWrap: "wrap", alignItems: "center", fontSize: 11, color: "#475467" }}>
              <span style={{ color: "#667085" }}>Legend</span>
              {[
                ["#2563eb", "User Planned"],
                ["#f79009", "Agent Replanned"],
                ["#12b76a", "UTM Approved"],
              ].map(([color, label]) => (
                <span key={`path-record-legend-${String(label)}`} style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
                  <span style={{ width: 10, height: 10, borderRadius: 999, background: String(color), border: "1px solid rgba(0,0,0,0.08)" }} />
                  <span>{label}</span>
                </span>
              ))}
            </div>
          </div>

          <div style={{ ...cardStyle, padding: 10, display: "grid", gap: 8, alignContent: "start", minWidth: 0 }}>
            <div style={{ display: "flex", justifyContent: "space-between", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
              <div>
                <div style={{ fontWeight: 700, color: "#101828" }}>Backend Actions (UAV ↔ UTM)</div>
                <div style={{ fontSize: 11, color: "#667085" }}>Real backend action log for copilot/tool execution and UTM interactions</div>
              </div>
              <div style={{ display: "flex", gap: 6, alignItems: "center", flexWrap: "wrap" }}>
                <button type="button" style={chipStyle(backendLogFilter === "all")} onClick={() => setBackendLogFilter("all")} disabled={busy || agentBusy}>All</button>
                <button type="button" style={chipStyle(backendLogFilter === "copilot")} onClick={() => setBackendLogFilter("copilot")} disabled={busy || agentBusy}>Copilot</button>
                <button type="button" style={chipStyle(backendLogFilter === "utm_verify")} onClick={() => setBackendLogFilter("utm_verify")} disabled={busy || agentBusy}>UTM Verify</button>
                <button type="button" style={chipStyle(backendLogFilter === "flight")} onClick={() => setBackendLogFilter("flight")} disabled={busy || agentBusy}>Flight</button>
                <button type="button" style={chipStyle(backendLogFilter === "utm_config")} onClick={() => setBackendLogFilter("utm_config")} disabled={busy || agentBusy}>UTM Config</button>
                <button type="button" style={chipStyle(backendLogFilter === "live_data")} onClick={() => setBackendLogFilter("live_data")} disabled={busy || agentBusy}>Live Data</button>
                <button
                  type="button"
                  style={chipStyle(false)}
                  onClick={() => {
                    setBackendActionLog([]);
                    setBackendActionLogClearedAt(new Date().toISOString());
                    void loadState();
                  }}
                  disabled={busy || agentBusy}
                >
                  Clear + Refresh
                </button>
              </div>
            </div>
            <div style={{ border: "1px solid #eaecf0", borderRadius: 10, background: "#fff", maxHeight: 320, overflow: "auto", display: "grid", gap: 6, padding: 8 }}>
              {filteredBackendActionLog.length === 0 ? (
                <div style={{ fontSize: 12, color: "#667085" }}>
                  No backend actions for this filter yet. Run the copilot or use UAV/UTM actions to populate this log.
                </div>
              ) : (
                filteredBackendActionLog.map((item) => {
                  const resultRec = asRecord(item.result);
                  const decision = asRecord(asRecord(resultRec?.result ?? resultRec)?.decision);
                  return (
                    <div key={`${item.agent}-${item.id}-${item.created_at}`} style={{ border: "1px solid #eaecf0", borderRadius: 8, background: "#fcfcfd", padding: 8, display: "grid", gap: 4 }}>
                      <div style={{ display: "flex", justifyContent: "space-between", gap: 8, flexWrap: "wrap" }}>
                        <div style={{ fontSize: 11, color: item.agent === "utm" ? "#b54708" : "#155eef", fontWeight: 700 }}>
                          {item.agent.toUpperCase()} • <code>{item.action}</code>
                        </div>
                        <div style={{ fontSize: 10, color: "#667085" }}>{new Date(item.created_at).toLocaleString()}</div>
                      </div>
                      <div style={{ fontSize: 12, color: "#344054" }}>{summarizeBackendAction(item)}</div>
                      {item.entity_id != null ? <div style={{ fontSize: 11, color: "#667085" }}>entity: <code>{String(item.entity_id)}</code></div> : null}
                      {decision ? (
                        <div style={{ fontSize: 11, color: decision.status === "approved" ? "#027a48" : "#b42318" }}>
                          UTM decision: <b>{String(decision.status ?? "-")}</b>
                          {Array.isArray(decision.reasons) && (decision.reasons as unknown[]).length > 0 ? ` (${(decision.reasons as unknown[]).map(String).join(", ")})` : ""}
                        </div>
                      ) : null}
                      <details>
                        <summary style={{ cursor: "pointer", fontSize: 11, color: "#667085" }}>Payload / Result</summary>
                        <pre style={{ margin: "6px 0 0", whiteSpace: "pre-wrap", wordBreak: "break-word", fontSize: 11 }}>
{JSON.stringify({ payload: item.payload, result: item.result }, null, 2)}
                        </pre>
                      </details>
                    </div>
                  );
                })
              )}
            </div>
          </div>

        </div>
      </div>
    </div>
  );
}
