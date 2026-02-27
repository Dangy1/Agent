import React, { useEffect, useMemo, useState } from "react";
import { MissionSyncMap, type MissionBs, type MissionCoverage, type MissionNfz, type MissionTrack as SyncTrack } from "./MissionSyncMap";
import { bumpSharedRevision, getSharedPageState, patchSharedPageState, subscribeSharedPageState } from "./pageSync";

type Point = { x: number; y: number };
type RoutePoint = Point & { z: number };
type OptimizationMode = "coverage" | "power" | "qos";

type BaseStation = {
  id: string;
  x: number;
  y: number;
  band: string;
  freqMHz: number;
  bandwidthMHz: number;
  txPowerDbm: number;
  heightM: number;
  tiltDeg: number;
  loadPct: number;
  status: "online" | "degraded" | "maintenance";
};

type UavTrack = {
  id: string;
  mission: string;
  route: RoutePoint[];
  routeIndex: number;
  t: number;
  speedMps: number;
  altitudeM: number;
  qosClass: "telemetry" | "video" | "control";
};

type TrackingSnapshot = {
  id: string;
  x: number;
  y: number;
  z: number;
  headingDeg: number;
  speedMps: number;
  attachedBsId: string;
  rsrpDbm: number;
  sinrDb: number;
  latencyMs: number;
  packetLossPct: number;
  trackingConfidencePct: number;
  interferenceRisk: "low" | "medium" | "high";
};

type NetworkKpis = {
  coverageScorePct: number;
  avgSinrDb: number;
  avgLatencyMs: number;
  highInterferenceRiskCount: number;
  utmTrackingHealthPct: number;
};
type TrafficSourceInfo = { mode?: string; active?: string; config?: Record<string, unknown> | null; liveTimestamp?: string | null; liveReceivedAt?: string | null };

function isObject(x: unknown): x is Record<string, unknown> {
  return typeof x === "object" && x !== null;
}

function asRecord(x: unknown): Record<string, unknown> | null {
  return isObject(x) ? x : null;
}

function readSyncRevision(data: unknown): number | null {
  const root = asRecord(data);
  const result = asRecord(root?.result);
  const sync = asRecord(result?.sync ?? root?.sync);
  return typeof sync?.revision === "number" ? sync.revision : null;
}

function normalizeBaseUrl(url: string): string {
  return url.trim().replace(/\/+$/, "");
}

function formatTickTs(iso?: string): string {
  if (!iso) return new Date().toLocaleTimeString();
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleTimeString();
}

function chipStyle(active = false): React.CSSProperties {
  return {
    borderRadius: 999,
    border: active ? "1px solid #155eef" : "1px solid #d0d5dd",
    background: active ? "#eef4ff" : "#fff",
    color: active ? "#155eef" : "#344054",
    padding: "6px 10px",
    fontSize: 12,
    fontWeight: 600,
    cursor: "pointer",
  };
}

const cardStyle: React.CSSProperties = {
  background: "#fff",
  border: "1px solid #eaecf0",
  borderRadius: 14,
  padding: 12,
  boxShadow: "0 1px 2px rgba(16, 24, 40, 0.04)",
};

const fieldStyle: React.CSSProperties = {
  width: "100%",
  borderRadius: 8,
  border: "1px solid #d0d5dd",
  padding: "6px 8px",
  fontSize: 12,
};

const INITIAL_BS: BaseStation[] = [
  { id: "BS-A", x: 60, y: 55, band: "n78", freqMHz: 3500, bandwidthMHz: 100, txPowerDbm: 39, heightM: 32, tiltDeg: 6, loadPct: 62, status: "online" },
  { id: "BS-B", x: 205, y: 48, band: "n78", freqMHz: 3500, bandwidthMHz: 80, txPowerDbm: 37, heightM: 28, tiltDeg: 5, loadPct: 74, status: "degraded" },
  { id: "BS-C", x: 330, y: 95, band: "n41", freqMHz: 2600, bandwidthMHz: 60, txPowerDbm: 36, heightM: 24, tiltDeg: 4, loadPct: 48, status: "online" },
  { id: "BS-D", x: 110, y: 210, band: "n28", freqMHz: 700, bandwidthMHz: 20, txPowerDbm: 43, heightM: 36, tiltDeg: 7, loadPct: 41, status: "online" },
  { id: "BS-E", x: 290, y: 225, band: "n78", freqMHz: 3500, bandwidthMHz: 100, txPowerDbm: 40, heightM: 34, tiltDeg: 6, loadPct: 69, status: "online" },
];

function clamp(n: number, min: number, max: number): number {
  return Math.max(min, Math.min(max, n));
}

function lerp(a: number, b: number, t: number): number {
  return a + (b - a) * t;
}

function distance(a: Point, b: Point): number {
  const dx = a.x - b.x;
  const dy = a.y - b.y;
  return Math.sqrt(dx * dx + dy * dy);
}

function currentUavPosition(u: UavTrack): RoutePoint {
  const a = u.route[u.routeIndex];
  const b = u.route[(u.routeIndex + 1) % u.route.length];
  return {
    x: lerp(a.x, b.x, u.t),
    y: lerp(a.y, b.y, u.t),
    z: lerp(a.z, b.z, u.t),
  };
}

function coverageRadius(bs: BaseStation): number {
  const lowBandBoost = bs.freqMHz < 1000 ? 55 : bs.freqMHz < 3000 ? 35 : 18;
  return clamp(45 + (bs.txPowerDbm - 34) * 5 + bs.bandwidthMHz * 0.18 + lowBandBoost - bs.loadPct * 0.12, 36, 160);
}

function signalAtPoint(bs: BaseStation, p: RoutePoint): number {
  const d2d = Math.max(2, distance(bs, p));
  const d3d = Math.sqrt(d2d * d2d + Math.pow(Math.max(1, bs.heightM - p.z), 2));
  const pathLoss = 32.4 + 20 * Math.log10(d3d / 100) + 20 * Math.log10(Math.max(700, bs.freqMHz) / 1000);
  const loadPenalty = bs.loadPct * 0.07;
  const tiltPenalty = Math.abs(bs.tiltDeg - 6) * 0.4;
  return bs.txPowerDbm - pathLoss - loadPenalty - tiltPenalty + 28;
}

function computeSnapshot(u: UavTrack, baseStations: BaseStation[]): TrackingSnapshot {
  const pos = currentUavPosition(u);
  const scored = baseStations.map((bs) => ({ bs, rsrp: signalAtPoint(bs, pos) })).sort((a, b) => b.rsrp - a.rsrp);
  const serving = scored[0];
  const interferer = scored[1];
  const rsrp = serving?.rsrp ?? -120;
  const sinr = clamp(rsrp - ((interferer?.rsrp ?? -130) + 3), -8, 35);
  const headingTarget = u.route[(u.routeIndex + 1) % u.route.length];
  const headingDeg = ((Math.atan2(headingTarget.y - pos.y, headingTarget.x - pos.x) * 180) / Math.PI + 360) % 360;
  const latencyMs = clamp(14 + (u.qosClass === "video" ? 12 : 4) + (serving?.bs.loadPct ?? 50) * 0.24 - sinr * 0.2, 8, 85);
  const packetLossPct = clamp((u.qosClass === "video" ? 0.8 : 0.25) + (sinr < 6 ? (6 - sinr) * 0.22 : 0) + ((interferer?.rsrp ?? -150) > -82 ? 0.5 : 0), 0.05, 9);
  const trackingConfidencePct = clamp(98 - packetLossPct * 4.2 - Math.max(0, 20 - sinr) * 0.7, 62, 99.5);
  const interferenceRisk: TrackingSnapshot["interferenceRisk"] = sinr < 6 ? "high" : sinr < 14 ? "medium" : "low";
  return {
    id: u.id,
    x: pos.x,
    y: pos.y,
    z: pos.z,
    headingDeg,
    speedMps: u.speedMps,
    attachedBsId: serving?.bs.id ?? "N/A",
    rsrpDbm: Number(rsrp.toFixed(1)),
    sinrDb: Number(sinr.toFixed(1)),
    latencyMs: Number(latencyMs.toFixed(1)),
    packetLossPct: Number(packetLossPct.toFixed(2)),
    trackingConfidencePct: Number(trackingConfidencePct.toFixed(1)),
    interferenceRisk,
  };
}

function statusColor(status: BaseStation["status"]): string {
  if (status === "online") return "#12b76a";
  if (status === "degraded") return "#f79009";
  return "#98a2b3";
}

function riskColor(risk: TrackingSnapshot["interferenceRisk"]): string {
  if (risk === "high") return "#f04438";
  if (risk === "medium") return "#f79009";
  return "#12b76a";
}

function Metric({ label, value, tone = "#101828" }: { label: string; value: React.ReactNode; tone?: string }) {
  return (
    <div style={{ display: "grid", gap: 3 }}>
      <div style={{ fontSize: 11, color: "#667085" }}>{label}</div>
      <div style={{ fontSize: 14, fontWeight: 700, color: tone }}>{value}</div>
    </div>
  );
}

export function NetworkPage() {
  const sharedInit = getSharedPageState();
  const [networkApiBase, setNetworkApiBase] = useState(sharedInit.networkApiBase || "http://127.0.0.1:8022");
  const [baseStations, setBaseStations] = useState<BaseStation[]>(INITIAL_BS);
  const [uavs, setUavs] = useState<UavTrack[]>([]);
  const [remoteSnapshots, setRemoteSnapshots] = useState<TrackingSnapshot[] | null>(null);
  const [remoteSelectedSnapshot, setRemoteSelectedSnapshot] = useState<TrackingSnapshot | null>(null);
  const [remoteKpis, setRemoteKpis] = useState<NetworkKpis | null>(null);
  const [remoteCoverage, setRemoteCoverage] = useState<MissionCoverage[]>([]);
  const [remoteNfz, setRemoteNfz] = useState<MissionNfz[]>([]);
  const [utmEffectiveRegs, setUtmEffectiveRegs] = useState<Record<string, unknown> | null>(null);
  const [selectedUavId, setSelectedUavId] = useState(sharedInit.uavId || "uav-1");
  const [airspace, setAirspace] = useState(sharedInit.airspace || "sector-A3");
  const [optimizationMode, setOptimizationMode] = useState<OptimizationMode>("coverage");
  const [coverageTargetPct, setCoverageTargetPct] = useState(96);
  const [maxTxCapDbm, setMaxTxCapDbm] = useState(41);
  const [qosPriorityWeight, setQosPriorityWeight] = useState(68);
  const [showCoverage, setShowCoverage] = useState(true);
  const [showInterference, setShowInterference] = useState(true);
  const [liveTracking, setLiveTracking] = useState(true);
  const [refreshMs, setRefreshMs] = useState(1200);
  const [statusMsg, setStatusMsg] = useState("Live network mission view ready");
  const [lastTickAt, setLastTickAt] = useState<string>(new Date().toLocaleTimeString());
  const [apiBusy, setApiBusy] = useState(false);
  const [backendRevisions, setBackendRevisions] = useState<{ uav: number; utm: number; network: number }>({ uav: -1, utm: -1, network: -1 });
  const [trafficSource, setTrafficSource] = useState<TrafficSourceInfo | null>(null);
  const [networkLiveJson, setNetworkLiveJson] = useState(
    JSON.stringify(
      {
        payload: {
          source: "ric-kpi-feed",
          timestamp: "2026-02-24T20:00:00Z",
          trackingSnapshots: [
            {
              id: "uav-1",
              x: 120,
              y: 90,
              z: 60,
              headingDeg: 45,
              speedMps: 12,
              attachedBsId: "BS-A",
              rsrpDbm: -78,
              sinrDb: 18,
              latencyMs: 22,
              packetLossPct: 0.3,
              trackingConfidencePct: 97,
              interferenceRisk: "low",
            },
          ],
          networkKpis: {
            coverageScorePct: 96.5,
            avgSinrDb: 17.8,
            avgLatencyMs: 24.1,
            highInterferenceRiskCount: 0,
            utmTrackingHealthPct: 98.2,
          },
        },
      },
      null,
      2,
    ),
  );

  const loadNetworkState = async (selectedId?: string) => {
    try {
      const q = new URLSearchParams();
      q.set("airspace_segment", airspace);
      q.set("selected_uav_id", selectedId ?? selectedUavId);
      const res = await fetch(`${normalizeBaseUrl(networkApiBase)}/api/network/mission/state?${q.toString()}`);
      const data = await res.json();
      if (!res.ok || !isObject(data)) throw new Error(String(asRecord(data)?.detail ?? "Network state request failed"));
      const result = asRecord((data as Record<string, unknown>).result);
      if (!result) throw new Error("Network state result missing");
      setTrafficSource(asRecord(result.trafficSource) as TrafficSourceInfo | null);

      const bsList = Array.isArray(result.baseStations) ? result.baseStations.filter(isObject) : [];
      setBaseStations(
        bsList.map((b) => ({
          id: String(b.id ?? "BS"),
          x: Number(b.x ?? 0),
          y: Number(b.y ?? 0),
          band: String(b.band ?? "n78"),
          freqMHz: Number(b.freqMHz ?? 3500),
          bandwidthMHz: Number(b.bandwidthMHz ?? 100),
          txPowerDbm: Number(b.txPowerDbm ?? 35),
          heightM: Number(b.heightM ?? 30),
          tiltDeg: Number(b.tiltDeg ?? 6),
          loadPct: Number(b.loadPct ?? 50),
          status: (String(b.status ?? "online") as BaseStation["status"]),
        })),
      );

      const uavList = Array.isArray(result.uavs) ? result.uavs.filter(isObject) : [];
      setUavs(
        uavList.map((u) => {
          const route = Array.isArray(u.route)
            ? u.route.filter(isObject).map((p) => ({ x: Number(p.x ?? 0), y: Number(p.y ?? 0), z: Number(p.z ?? 0) }))
            : [];
          const pos = asRecord(u.position);
          return {
            id: String(u.id ?? "uav"),
            mission: String(u.mission ?? "mission"),
            route: route.length ? route : [{ x: 0, y: 0, z: 0 }, { x: 1, y: 1, z: 1 }],
            routeIndex: Number(u.routeIndex ?? 0),
            t: Number(u.t ?? 0),
            speedMps: Number(u.speedMps ?? 12),
            altitudeM: Number(pos?.z ?? route[0]?.z ?? 0),
            qosClass: (String(u.qosClass ?? "telemetry") as UavTrack["qosClass"]),
          };
        }),
      );

      const snapshots = Array.isArray(result.trackingSnapshots) ? result.trackingSnapshots.filter(isObject) : [];
      setRemoteSnapshots(
        snapshots.map((s) => ({
          id: String(s.id ?? "uav"),
          x: Number(s.x ?? 0),
          y: Number(s.y ?? 0),
          z: Number(s.z ?? 0),
          headingDeg: Number(s.headingDeg ?? 0),
          speedMps: Number(s.speedMps ?? 0),
          attachedBsId: String(s.attachedBsId ?? "N/A"),
          rsrpDbm: Number(s.rsrpDbm ?? -120),
          sinrDb: Number(s.sinrDb ?? 0),
          latencyMs: Number(s.latencyMs ?? 0),
          packetLossPct: Number(s.packetLossPct ?? 0),
          trackingConfidencePct: Number(s.trackingConfidencePct ?? 0),
          interferenceRisk: (String(s.interferenceRisk ?? "low") as TrackingSnapshot["interferenceRisk"]),
        })),
      );
      const sel = asRecord(result.selectedTracking);
      setRemoteSelectedSnapshot(
        sel
          ? {
              id: String(sel.id ?? "uav"),
              x: Number(sel.x ?? 0),
              y: Number(sel.y ?? 0),
              z: Number(sel.z ?? 0),
              headingDeg: Number(sel.headingDeg ?? 0),
              speedMps: Number(sel.speedMps ?? 0),
              attachedBsId: String(sel.attachedBsId ?? "N/A"),
              rsrpDbm: Number(sel.rsrpDbm ?? -120),
              sinrDb: Number(sel.sinrDb ?? 0),
              latencyMs: Number(sel.latencyMs ?? 0),
              packetLossPct: Number(sel.packetLossPct ?? 0),
              trackingConfidencePct: Number(sel.trackingConfidencePct ?? 0),
              interferenceRisk: (String(sel.interferenceRisk ?? "low") as TrackingSnapshot["interferenceRisk"]),
            }
          : null,
      );
      const kpis = asRecord(result.networkKpis);
      setRemoteKpis(
        kpis
          ? {
              coverageScorePct: Number(kpis.coverageScorePct ?? 0),
              avgSinrDb: Number(kpis.avgSinrDb ?? 0),
              avgLatencyMs: Number(kpis.avgLatencyMs ?? 0),
              highInterferenceRiskCount: Number(kpis.highInterferenceRiskCount ?? 0),
              utmTrackingHealthPct: Number(kpis.utmTrackingHealthPct ?? 0),
            }
          : null,
      );
      const covRows = Array.isArray(result.coverage) ? result.coverage.filter(isObject) : [];
      setRemoteCoverage(
        covRows.map((c) => ({
          bsId: String(c.bsId ?? ""),
          radiusM: Number(c.radiusM ?? 0),
        })),
      );
      const utm = asRecord(result.utm);
      setUtmEffectiveRegs(asRecord(utm?.effectiveRegulations ?? utm?.effective_regulations));
      const nfzRows = Array.isArray(utm?.noFlyZones) ? (utm.noFlyZones as unknown[]).filter(isObject) : [];
      setRemoteNfz(
        nfzRows.map((z) => ({
          zone_id: String(z.zone_id ?? "nfz"),
          cx: Number(z.cx ?? 0),
          cy: Number(z.cy ?? 0),
          radius_m: Number(z.radius_m ?? 0),
          z_min: Number(z.z_min ?? 0),
          z_max: Number(z.z_max ?? 120),
          reason: String(z.reason ?? ""),
        })),
      );
      setLastTickAt(formatTickTs(typeof result.lastTickTs === "string" ? result.lastTickTs : undefined));
    } catch (e) {
      setStatusMsg(`Network backend load failed: ${e instanceof Error ? e.message : String(e)}`);
    }
  };

  const postNetwork = async (path: string, body: unknown) => {
    setApiBusy(true);
    try {
      const res = await fetch(`${normalizeBaseUrl(networkApiBase)}${path}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      const data = await res.json();
      if (!res.ok || !isObject(data)) throw new Error(String(asRecord(data)?.detail ?? "Network request failed"));
      return data as Record<string, unknown>;
    } finally {
      setApiBusy(false);
    }
  };

  const ingestNetworkTelemetry = async () => {
    try {
      const parsed = JSON.parse(networkLiveJson);
      if (!isObject(parsed)) throw new Error("JSON payload must be an object");
      const parsedRec = parsed as Record<string, unknown>;
      const body = isObject(parsedRec.payload) ? parsedRec : { payload: parsedRec };
      await postNetwork("/api/network/telemetry/ingest", body);
      setStatusMsg("Live network telemetry ingested");
      await loadNetworkState(selectedUavId);
      bumpSharedRevision();
    } catch (e) {
      setStatusMsg(`Telemetry ingest failed: ${e instanceof Error ? e.message : String(e)}`);
    }
  };

  useEffect(() => {
    void loadNetworkState(selectedUavId);
  }, []);

  useEffect(() => {
    void loadNetworkState(selectedUavId);
  }, [selectedUavId, airspace, networkApiBase]);

  useEffect(() => {
    patchSharedPageState({ networkApiBase, uavId: selectedUavId, airspace });
  }, [networkApiBase, selectedUavId, airspace]);

  useEffect(() => {
    let lastRevision = getSharedPageState().revision;
    return subscribeSharedPageState((next) => {
      if (next.networkApiBase && next.networkApiBase !== networkApiBase) setNetworkApiBase(next.networkApiBase);
      if (next.uavId && next.uavId !== selectedUavId) setSelectedUavId(next.uavId);
      if (next.airspace && next.airspace !== airspace) setAirspace(next.airspace);
      if (next.revision !== lastRevision) {
        lastRevision = next.revision;
        void loadNetworkState(next.uavId || selectedUavId);
      }
    });
  }, [networkApiBase, selectedUavId, airspace]);

  useEffect(() => {
    if (!liveTracking) return;
    const id = window.setInterval(() => {
      void (async () => {
        try {
          await postNetwork("/api/network/mission/tick", { steps: 1 });
          await loadNetworkState(selectedUavId);
        } catch (e) {
          setStatusMsg(`Live tick failed: ${e instanceof Error ? e.message : String(e)}`);
        }
      })();
    }, clamp(refreshMs, 400, 5000));
    return () => window.clearInterval(id);
  }, [liveTracking, refreshMs, selectedUavId]);

  useEffect(() => {
    const id = window.setInterval(() => {
      void (async () => {
        if (apiBusy) return;
        try {
          const shared = getSharedPageState();
          const [uavRes, utmRes, netRes] = await Promise.all([
            fetch(`${normalizeBaseUrl(shared.uavApiBase)}/api/uav/sync`),
            fetch(`${normalizeBaseUrl(shared.utmApiBase)}/api/utm/sync`),
            fetch(`${normalizeBaseUrl(networkApiBase)}/api/network/sync`),
          ]);
          const [uavData, utmData, netData] = await Promise.all([uavRes.json(), utmRes.json(), netRes.json()]);
          const next = {
            uav: readSyncRevision(uavData) ?? backendRevisions.uav,
            utm: readSyncRevision(utmData) ?? backendRevisions.utm,
            network: readSyncRevision(netData) ?? backendRevisions.network,
          };
          const changedExternal = next.uav !== backendRevisions.uav || next.utm !== backendRevisions.utm;
          const changedNetwork = next.network !== backendRevisions.network;
          if (changedExternal || (!liveTracking && changedNetwork)) {
            setBackendRevisions(next);
            if (backendRevisions.uav >= 0 || backendRevisions.utm >= 0 || backendRevisions.network >= 0) {
              void loadNetworkState(selectedUavId);
            }
          } else if (changedNetwork) {
            setBackendRevisions(next);
          }
        } catch {
          // optional auto-refresh path
        }
      })();
    }, 1500);
    return () => window.clearInterval(id);
  }, [apiBusy, networkApiBase, backendRevisions, liveTracking, selectedUavId, airspace]);

  const computedSnapshots = useMemo(() => uavs.map((u) => computeSnapshot(u, baseStations)), [uavs, baseStations]);
  const snapshots = remoteSnapshots ?? computedSnapshots;
  const selectedSnapshot = remoteSelectedSnapshot ?? snapshots.find((s) => s.id === selectedUavId) ?? snapshots[0] ?? null;

  const computedNetworkSummary = useMemo(() => {
    const avgSinr = snapshots.length ? snapshots.reduce((sum, s) => sum + s.sinrDb, 0) / snapshots.length : 0;
    const avgLatency = snapshots.length ? snapshots.reduce((sum, s) => sum + s.latencyMs, 0) / snapshots.length : 0;
    const highRisk = snapshots.filter((s) => s.interferenceRisk === "high").length;
    const coverageScore = clamp(
      93 + baseStations.filter((b) => coverageRadius(b) > 85).length * 1.1 - baseStations.filter((b) => b.status !== "online").length * 2.4 - highRisk * 1.8,
      72,
      99,
    );
    return {
      avgSinr: Number(avgSinr.toFixed(1)),
      avgLatency: Number(avgLatency.toFixed(1)),
      highRisk,
      coverageScore: Number(coverageScore.toFixed(1)),
      utmTrackingHealth: Number(clamp(99 - highRisk * 6 - Math.max(0, 12 - avgSinr) * 1.2, 70, 99.4).toFixed(1)),
    };
  }, [snapshots, baseStations]);

  const networkSummary = remoteKpis
    ? {
        coverageScore: remoteKpis.coverageScorePct,
        avgSinr: remoteKpis.avgSinrDb,
        avgLatency: remoteKpis.avgLatencyMs,
        highRisk: remoteKpis.highInterferenceRiskCount,
        utmTrackingHealth: remoteKpis.utmTrackingHealthPct,
      }
    : computedNetworkSummary;

  const applyOptimization = async () => {
    try {
      await postNetwork("/api/network/optimize", {
        mode: optimizationMode,
        coverage_target_pct: coverageTargetPct,
        max_tx_cap_dbm: maxTxCapDbm,
        qos_priority_weight: qosPriorityWeight,
      });
      const modeLabel = optimizationMode === "coverage" ? "Coverage optimization" : optimizationMode === "power" ? "Power optimization" : "QoS optimization";
      setStatusMsg(`${modeLabel} applied via Network API`);
      await loadNetworkState(selectedUavId);
      bumpSharedRevision();
    } catch (e) {
      setStatusMsg(`Optimization failed: ${e instanceof Error ? e.message : String(e)}`);
    }
  };

  const updateBaseStationTxPower = async (bsId: string, txPowerDbm: number) => {
    try {
      await postNetwork("/api/network/base-station/update", { bs_id: bsId, txPowerDbm });
      await loadNetworkState(selectedUavId);
      bumpSharedRevision();
    } catch (e) {
      setStatusMsg(`BS update failed: ${e instanceof Error ? e.message : String(e)}`);
    }
  };

  const selectedUav = uavs.find((u) => u.id === selectedUavId) ?? uavs[0] ?? null;
  const utmSizeClass = String(utmEffectiveRegs?.uav_size_class ?? "middle");
  const routeForSyncMap = selectedUav?.route ?? [];
  const plannedPosForSyncMap = routeForSyncMap.length > 0 ? routeForSyncMap[0] : null;
  const bsForSyncMap: MissionBs[] = baseStations.map((b) => ({ id: b.id, x: b.x, y: b.y, status: b.status }));
  const tracksForSyncMap: SyncTrack[] = snapshots.map((s) => ({
    id: s.id,
    x: s.x,
    y: s.y,
    z: s.z,
    attachedBsId: s.attachedBsId,
    interferenceRisk: s.interferenceRisk,
  }));

  return (
    <div style={{ maxWidth: 1280, margin: "0 auto", padding: 16, display: "grid", gap: 14 }}>
      <div style={{ ...cardStyle, background: "linear-gradient(135deg, #f8fbff 0%, #ffffff 55%, #f5faff 100%)" }}>
        <div style={{ display: "flex", justifyContent: "space-between", gap: 12, flexWrap: "wrap", alignItems: "center" }}>
          <div>
            <div style={{ fontSize: 12, fontWeight: 700, color: "#155eef", letterSpacing: 0.4, textTransform: "uppercase" }}>Network Mission Page</div>
            <div style={{ fontSize: 20, fontWeight: 700, color: "#101828" }}>BS Coverage + UAV/UTM Interference and Tracking</div>
            <div style={{ fontSize: 12, color: "#667085", marginTop: 2 }}>
              UAV network optimization (coverage / power / QoS) and UTM live tracking service monitoring on one mission overlay map.
            </div>
          </div>
          <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
            <button type="button" style={chipStyle(showCoverage)} onClick={() => setShowCoverage((v) => !v)}>{showCoverage ? "Hide Coverage" : "Show Coverage"}</button>
            <button type="button" style={chipStyle(showInterference)} onClick={() => setShowInterference((v) => !v)}>{showInterference ? "Hide Interference" : "Show Interference"}</button>
            <button type="button" style={chipStyle(liveTracking)} onClick={() => setLiveTracking((v) => !v)}>{liveTracking ? "Pause Live" : "Resume Live"}</button>
          </div>
        </div>
        <div style={{ marginTop: 10, fontSize: 12, color: "#344054" }}>{statusMsg}</div>
        <div style={{ marginTop: 8, display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center", fontSize: 11 }}>
          <span style={{ color: "#667085" }}>Traffic source:</span>
          <span style={{ fontWeight: 700, color: String(trafficSource?.active ?? "").includes("live") ? "#027a48" : "#667085" }}>
            {String(trafficSource?.active ?? "unknown")}
          </span>
          <span style={{ color: "#667085" }}>mode={String(trafficSource?.mode ?? "-")}</span>
          {trafficSource?.liveTimestamp ? <span style={{ color: "#667085" }}>live ts {String(trafficSource.liveTimestamp)}</span> : null}
        </div>
      </div>

      <div style={{ display: "grid", gap: 14, gridTemplateColumns: "minmax(0, 1.8fr) minmax(320px, 1fr)" }}>
        <div style={{ display: "grid", gap: 14 }}>
          <div style={cardStyle}>
            <MissionSyncMap
              title="Network Synchronized Map"
              route={routeForSyncMap}
              plannedPosition={plannedPosForSyncMap}
              trackedPositions={tracksForSyncMap}
              selectedUavId={selectedUavId}
              noFlyZones={remoteNfz}
              baseStations={bsForSyncMap}
              coverage={remoteCoverage}
              showCoverage={showCoverage}
              showInterferenceHints={showInterference}
            />
          </div>
        </div>

        <div style={{ display: "grid", gap: 14, alignContent: "start" }}>
          <div style={cardStyle}>
            <div style={{ fontSize: 13, fontWeight: 700, color: "#101828", marginBottom: 8 }}>Mission Network KPIs</div>
            <div style={{ display: "grid", gridTemplateColumns: "repeat(2, minmax(0,1fr))", gap: 10 }}>
              <Metric label="Coverage Score" value={`${networkSummary.coverageScore}%`} tone="#155eef" />
              <Metric label="Avg SINR" value={`${networkSummary.avgSinr} dB`} tone={networkSummary.avgSinr < 10 ? "#b42318" : "#027a48"} />
              <Metric label="Avg Latency" value={`${networkSummary.avgLatency} ms`} tone={networkSummary.avgLatency > 35 ? "#b42318" : "#101828"} />
              <Metric label="UTM Tracking Health" value={`${networkSummary.utmTrackingHealth}%`} tone="#0f766e" />
            </div>
            <div style={{ marginTop: 10, fontSize: 11, color: "#667085" }}>
              High interference-risk UAVs: <b style={{ color: networkSummary.highRisk ? "#b42318" : "#027a48" }}>{networkSummary.highRisk}</b> / {snapshots.length}
            </div>
          </div>

          <div style={cardStyle}>
            <div style={{ fontSize: 13, fontWeight: 700, color: "#101828", marginBottom: 8 }}>UTM UAV Capability Profile</div>
            <div style={{ fontSize: 11, color: "#667085", marginBottom: 8 }}>
              License-derived weather/mission limits used by UTM verification and exposed to network mission monitoring.
            </div>
            <div style={{ display: "grid", gridTemplateColumns: "repeat(2, minmax(0,1fr))", gap: 8 }}>
              <Metric label="UAV Size Class" value={utmSizeClass} tone="#155eef" />
              <Metric label="Max Altitude" value={`${Number(utmEffectiveRegs?.max_altitude_m ?? 0)} m`} />
              <Metric label="Max Route Span" value={`${Number(utmEffectiveRegs?.max_route_span_m ?? 0)} m`} />
              <Metric label="Max Wind" value={`${Number(utmEffectiveRegs?.max_wind_mps ?? 0)} m/s`} />
              <Metric label="Min Visibility" value={`${Number(utmEffectiveRegs?.min_visibility_km ?? 0)} km`} />
              <Metric label="Max Mission Duration" value={`${Number(utmEffectiveRegs?.max_mission_duration_min ?? 0)} min`} />
            </div>
          </div>

          <div style={cardStyle}>
            <div style={{ fontSize: 13, fontWeight: 700, color: "#101828", marginBottom: 8 }}>Live Traffic Telemetry (Backend Ingest)</div>
            <div style={{ fontSize: 11, color: "#667085", marginBottom: 8 }}>
              Push real traffic/KPI snapshots to the Network backend and plot them on the map immediately.
            </div>
            <textarea
              style={{ ...fieldStyle, minHeight: 120, fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace", fontSize: 11 }}
              value={networkLiveJson}
              onChange={(e) => setNetworkLiveJson(e.target.value)}
              spellCheck={false}
            />
            <div style={{ marginTop: 8, display: "flex", justifyContent: "space-between", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
              <div style={{ fontSize: 11, color: "#667085" }}>
                Active: {String(trafficSource?.active ?? "-")} {trafficSource?.liveReceivedAt ? `• received ${String(trafficSource.liveReceivedAt)}` : ""}
              </div>
              <button type="button" style={chipStyle(false)} onClick={() => void ingestNetworkTelemetry()} disabled={apiBusy}>Ingest Live Traffic</button>
            </div>
          </div>

          <div style={cardStyle}>
            <div style={{ fontSize: 13, fontWeight: 700, color: "#101828", marginBottom: 8 }}>UTM Live Tracking Service</div>
            <div style={{ display: "grid", gap: 8 }}>
              <label style={{ fontSize: 12, color: "#344054" }}>
                Tracked UAV
                <select style={fieldStyle} value={selectedUavId} onChange={(e) => setSelectedUavId(e.target.value)}>
                  {uavs.map((u) => (
                    <option key={u.id} value={u.id}>{u.id} ({u.mission})</option>
                  ))}
                </select>
              </label>
              <label style={{ fontSize: 12, color: "#344054" }}>
                Refresh Interval (ms)
                <input style={fieldStyle} type="number" min={400} max={5000} step={100} value={refreshMs} onChange={(e) => setRefreshMs(Number(e.target.value || 1200))} />
              </label>
              <div style={{ display: "grid", gridTemplateColumns: "repeat(2, minmax(0,1fr))", gap: 8 }}>
                <Metric label="Live Monitor" value={liveTracking ? "Running" : "Paused"} tone={liveTracking ? "#027a48" : "#b42318"} />
                <Metric label="Last Tracking Tick" value={lastTickAt} />
              </div>
            </div>
            {selectedSnapshot ? (
              <div style={{ marginTop: 10, padding: 10, borderRadius: 10, border: "1px solid #eaecf0", background: "#fcfcfd", display: "grid", gap: 8 }}>
                <div style={{ display: "grid", gridTemplateColumns: "repeat(2, minmax(0,1fr))", gap: 8 }}>
                  <Metric label="Position" value={`(${selectedSnapshot.x.toFixed(1)}, ${selectedSnapshot.y.toFixed(1)}, ${selectedSnapshot.z.toFixed(1)})`} />
                  <Metric label="Heading / Speed" value={`${selectedSnapshot.headingDeg.toFixed(0)}° / ${selectedSnapshot.speedMps} m/s`} />
                  <Metric label="Serving BS" value={selectedSnapshot.attachedBsId} />
                  <Metric label="Confidence" value={`${selectedSnapshot.trackingConfidencePct}%`} tone="#0f766e" />
                </div>
                <div style={{ fontSize: 11, color: "#344054" }}>
                  UTM tracking interference risk:
                  <span style={{ marginLeft: 6, color: riskColor(selectedSnapshot.interferenceRisk), fontWeight: 700, textTransform: "uppercase" }}>{selectedSnapshot.interferenceRisk}</span>
                </div>
              </div>
            ) : null}
          </div>
        </div>
      </div>

      <div style={{ display: "grid", gap: 14, gridTemplateColumns: "minmax(0, 1.2fr) minmax(0, 1fr)" }}>
        <div style={cardStyle}>
          <div style={{ display: "flex", justifyContent: "space-between", gap: 8, alignItems: "center", marginBottom: 8, flexWrap: "wrap" }}>
            <div style={{ fontSize: 13, fontWeight: 700, color: "#101828" }}>BS Coverage & Network Parameters</div>
            <div style={{ fontSize: 11, color: "#667085" }}>Mission-zone base stations on shared UAV overlay</div>
          </div>
          <div style={{ overflowX: "auto" }}>
            <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12 }}>
              <thead>
                <tr style={{ background: "#f9fafb", color: "#475467" }}>
                  {["BS", "Band", "Freq", "BW", "Tx Power", "Tilt", "Load", "Status"].map((h) => (
                    <th key={h} style={{ textAlign: "left", padding: "8px 6px", borderBottom: "1px solid #eaecf0", whiteSpace: "nowrap" }}>{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {baseStations.map((bs, idx) => (
                  <tr key={bs.id}>
                    <td style={{ padding: "8px 6px", borderBottom: "1px solid #f2f4f7", fontWeight: 700 }}>{bs.id}</td>
                    <td style={{ padding: "8px 6px", borderBottom: "1px solid #f2f4f7" }}>{bs.band}</td>
                    <td style={{ padding: "8px 6px", borderBottom: "1px solid #f2f4f7" }}>{bs.freqMHz} MHz</td>
                    <td style={{ padding: "8px 6px", borderBottom: "1px solid #f2f4f7" }}>{bs.bandwidthMHz} MHz</td>
                    <td style={{ padding: "8px 6px", borderBottom: "1px solid #f2f4f7", minWidth: 120 }}>
                      <input
                        type="range"
                        min={30}
                        max={46}
                        step={0.5}
                        value={bs.txPowerDbm}
                        onChange={(e) => {
                          const nextVal = Number(e.target.value);
                          setBaseStations((prev) => prev.map((row, i) => (i === idx ? { ...row, txPowerDbm: nextVal } : row)));
                          void updateBaseStationTxPower(bs.id, nextVal);
                        }}
                        style={{ width: "100%" }}
                      />
                      <div style={{ fontSize: 11, color: "#667085" }}>{bs.txPowerDbm} dBm</div>
                    </td>
                    <td style={{ padding: "8px 6px", borderBottom: "1px solid #f2f4f7" }}>{bs.tiltDeg}°</td>
                    <td style={{ padding: "8px 6px", borderBottom: "1px solid #f2f4f7" }}>{bs.loadPct}%</td>
                    <td style={{ padding: "8px 6px", borderBottom: "1px solid #f2f4f7" }}>
                      <span style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
                        <span style={{ width: 8, height: 8, borderRadius: 999, background: statusColor(bs.status), display: "inline-block" }} />
                        <span style={{ textTransform: "capitalize" }}>{bs.status}</span>
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>

        <div style={{ display: "grid", gap: 14, alignContent: "start" }}>
          <div style={cardStyle}>
            <div style={{ fontSize: 13, fontWeight: 700, color: "#101828", marginBottom: 8 }}>UAV Network Optimization</div>
            <div style={{ display: "flex", gap: 6, flexWrap: "wrap", marginBottom: 10 }}>
              <button type="button" style={chipStyle(optimizationMode === "coverage")} onClick={() => setOptimizationMode("coverage")}>Coverage</button>
              <button type="button" style={chipStyle(optimizationMode === "power")} onClick={() => setOptimizationMode("power")}>Power</button>
              <button type="button" style={chipStyle(optimizationMode === "qos")} onClick={() => setOptimizationMode("qos")}>QoS</button>
            </div>
            <div style={{ display: "grid", gap: 10 }}>
              <label style={{ fontSize: 12, color: "#344054" }}>
                Coverage Target
                <input type="range" min={80} max={99} value={coverageTargetPct} onChange={(e) => setCoverageTargetPct(Number(e.target.value))} style={{ width: "100%" }} />
                <div style={{ fontSize: 11, color: "#667085" }}>{coverageTargetPct}% mission-zone radio coverage for UAV flight corridor</div>
              </label>
              <label style={{ fontSize: 12, color: "#344054" }}>
                Max BS Tx Power Cap
                <input type="range" min={34} max={46} value={maxTxCapDbm} onChange={(e) => setMaxTxCapDbm(Number(e.target.value))} style={{ width: "100%" }} />
                <div style={{ fontSize: 11, color: "#667085" }}>{maxTxCapDbm} dBm cap for energy/interference control</div>
              </label>
              <label style={{ fontSize: 12, color: "#344054" }}>
                QoS Priority Weight (video/control)
                <input type="range" min={0} max={100} value={qosPriorityWeight} onChange={(e) => setQosPriorityWeight(Number(e.target.value))} style={{ width: "100%" }} />
                <div style={{ fontSize: 11, color: "#667085" }}>{qosPriorityWeight}% bias toward low latency and tracking continuity</div>
              </label>
              <button type="button" style={{ ...chipStyle(true), justifySelf: "start" }} onClick={() => void applyOptimization()} disabled={apiBusy}>
                Apply {optimizationMode === "coverage" ? "Coverage" : optimizationMode === "power" ? "Power" : "QoS"} Optimization
              </button>
            </div>
          </div>

          <div style={cardStyle}>
            <div style={{ fontSize: 13, fontWeight: 700, color: "#101828", marginBottom: 8 }}>Interference & Service Metrics (Selected UAV)</div>
            {selectedSnapshot && selectedUav ? (
              <div style={{ display: "grid", gap: 8 }}>
                <div style={{ fontSize: 12, color: "#344054" }}>
                  <b>{selectedUav.id}</b> on mission <b>{selectedUav.mission}</b> ({selectedUav.qosClass} QoS)
                </div>
                <div style={{ display: "grid", gridTemplateColumns: "repeat(2, minmax(0,1fr))", gap: 8 }}>
                  <Metric label="RSRP" value={`${selectedSnapshot.rsrpDbm} dBm`} tone={selectedSnapshot.rsrpDbm < -90 ? "#b42318" : "#027a48"} />
                  <Metric label="SINR" value={`${selectedSnapshot.sinrDb} dB`} tone={selectedSnapshot.sinrDb < 8 ? "#b42318" : "#027a48"} />
                  <Metric label="Latency" value={`${selectedSnapshot.latencyMs} ms`} tone={selectedSnapshot.latencyMs > 35 ? "#b42318" : "#101828"} />
                  <Metric label="Packet Loss" value={`${selectedSnapshot.packetLossPct}%`} tone={selectedSnapshot.packetLossPct > 2 ? "#b42318" : "#101828"} />
                </div>
                <div style={{ fontSize: 11, color: "#667085", lineHeight: 1.35 }}>
                  UTM tracking service monitors live UAV position using the same network links. Interference hotspots reduce SINR and can degrade tracking confidence and latency.
                </div>
              </div>
            ) : (
              <div style={{ fontSize: 12, color: "#667085" }}>No UAV selected.</div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
