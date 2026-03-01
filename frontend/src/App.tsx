import React, { useEffect, useMemo, useState } from "react";
import { AgentSkillGraphPage } from "./AgentSkillGraphPage";
import { Chat } from "./Chat";
import { NetworkPage } from "./NetworkPage";
import { UavPage } from "./UavPage";
import { UtmPage } from "./UtmPage";

type AgentPage = "oran" | "uav" | "utm" | "network" | "graph";

function readHashPage(): AgentPage {
  const raw = (window.location.hash || "#/oran").replace(/^#\/?/, "").toLowerCase();
  if (raw === "uav") return "uav";
  if (raw === "utm") return "utm";
  if (raw === "network") return "network";
  if (raw === "graph") return "graph";
  return "oran";
}

function setHashPage(page: AgentPage) {
  const next = `#/${page}`;
  if (window.location.hash !== next) window.location.hash = next;
}

function navButtonStyle(active: boolean): React.CSSProperties {
  return {
    borderRadius: 999,
    border: active ? "1px solid #155eef" : "1px solid #d0d5dd",
    background: active ? "#eef4ff" : "#fff",
    color: active ? "#155eef" : "#344054",
    padding: "8px 12px",
    fontSize: 13,
    fontWeight: 600,
    cursor: "pointer",
  };
}

export default function App() {
  const [page, setPage] = useState<AgentPage>(() => readHashPage());

  useEffect(() => {
    const onHashChange = () => setPage(readHashPage());
    window.addEventListener("hashchange", onHashChange);
    return () => window.removeEventListener("hashchange", onHashChange);
  }, []);

  const title = useMemo(() => {
    if (page === "uav") return "UAV Agent";
    if (page === "utm") return "UTM Agent";
    if (page === "network") return "Network Mission";
    if (page === "graph") return "Agent + Skill Graph";
    return "O-RAN Agent";
  }, [page]);

  return (
    <div style={{ minHeight: "100vh", background: "linear-gradient(180deg, #f8fbff 0%, #f9fafb 55%, #ffffff 100%)" }}>
      <header style={{ position: "sticky", top: 0, zIndex: 20, borderBottom: "1px solid #eaecf0", background: "rgba(255,255,255,0.92)", backdropFilter: "blur(8px)" }}>
        <div style={{ maxWidth: 1240, margin: "0 auto", padding: "12px 16px", display: "flex", gap: 10, alignItems: "center", justifyContent: "space-between", flexWrap: "wrap" }}>
          <div>
            <div style={{ fontSize: 11, color: "#667085", fontWeight: 700, letterSpacing: 0.4, textTransform: "uppercase" }}>Multi-Agent Console</div>
            <div style={{ fontSize: 18, color: "#101828", fontWeight: 700 }}>{title}</div>
          </div>
          <nav style={{ display: "flex", gap: 8, flexWrap: "wrap" }} aria-label="Agent pages">
            <button type="button" style={navButtonStyle(page === "oran")} onClick={() => setHashPage("oran")}>O-RAN Page</button>
            <button type="button" style={navButtonStyle(page === "uav")} onClick={() => setHashPage("uav")}>UAV Page</button>
            <button type="button" style={navButtonStyle(page === "utm")} onClick={() => setHashPage("utm")}>UTM Page</button>
            <button type="button" style={navButtonStyle(page === "network")} onClick={() => setHashPage("network")}>Network Page</button>
            <button type="button" style={navButtonStyle(page === "graph")} onClick={() => setHashPage("graph")}>Graph Page</button>
          </nav>
        </div>
      </header>

      {page === "oran" ? <Chat showSimulatorPanel={false} compactOranUi /> : null}
      {page === "uav" ? <UavPage /> : null}
      {page === "utm" ? <UtmPage /> : null}
      {page === "network" ? <NetworkPage /> : null}
      {page === "graph" ? <AgentSkillGraphPage /> : null}
    </div>
  );
}
