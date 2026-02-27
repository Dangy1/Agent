Yes. As of **February 27, 2026**, there are solid open-source UTM/USSP codebases aligned with recognized standards.

**Best fit to evaluate first**
- **InterUSS Platform (DSS + Monitoring)**: `interuss/dss` implements the ASTM DSS concept and targets ASTM F3411/F3548; `interuss/monitoring` provides automated conformance/interoperability testing (including ASTM and EU 2021/664 references).  
- Another strong option: **openutm/flight-blender** (open-source USS/USSP backend with Remote ID, flight authorization, geofence, traffic info, DSS connectivity, conformance monitoring modules).

**How your current UTM flow works (from code)**
- Mission submit path is backend-orchestrated at [`api_routes_uav.py:851`](/home/dang/agent_test/backend/uav_agent/api_routes_uav.py:851): route checks -> geofence submit -> verify-from-uav -> approval request.
- UTM verification is policy-check based in [`service.py:636`](/home/dang/agent_test/backend/utm_agent/service.py:636), evaluating:
  - route bounds, weather, NFZ conflicts, regulations, time window, operator license.
- Approval objects include scope/permissions/expiry and are persisted for gating.
- Flight controls are blocked unless safety/approval checks pass in [`api_shared.py:1331`](/home/dang/agent_test/backend/uav_agent/api_shared.py:1331).
- There is optional mirror sync from a “real” UTM endpoint (`:8021`) before checks in [`api_shared.py:117`](/home/dang/agent_test/backend/uav_agent/api_shared.py:117).
- Mission supervisor orchestrates cross-domain execution, approvals, policy checks, locks, rollback in [`mission_supervisor_agent/graph.py:29`](/home/dang/agent_test/backend/mission_supervisor_agent/graph.py:29).

**What is missing vs certification-oriented UTM/U-space systems**
- **Certified USSP process artifacts**: no formal compliance package flow (organizational capability, safety case, cyber assurance, continuity, oversight evidence) like EU USSP certification expects.
- **Federated USS interoperability**: no full DSS-based multi-USSP operation exchange; current design is mostly local service + optional mirror pull.
- **Dynamic airspace constraint integration**: limited real-time feeds (NOTAM/TFR/authoritative AIS constraints not deeply integrated end-to-end).
- **Strategic/tactical deconfliction at ecosystem scale**: checks are route-centric; no robust multi-operator traffic negotiation/conflict resolution pipeline.
- **Corridor reservation is a stub** in [`utm_agent/api.py:290`](/home/dang/agent_test/backend/utm_agent/api.py:290).
- **Approval assurance is simulator-grade**: `signature_verified` is logic-derived, not strong cryptographic/non-repudiation trust chain.
- **Hardcoded airspace geometry** in checks (`sector-A3` bounds) rather than full geo/airspace service abstraction.

Inference from sources + your code: your current implementation is a good **UTM simulation and workflow gate**, but not yet a **certification-ready USSP stack**.

Sources:
- https://github.com/interuss/dss
- https://github.com/interuss/monitoring
- https://github.com/openutm/flight-blender
- https://www.flightblender.com/
- https://www.faa.gov/uas/advanced_operations/traffic_management
- https://www.oig.dot.gov/library-item/40195
- https://www.easa.europa.eu/en/u-space
- https://www.easa.europa.eu/en/newsroom-and-events/press-releases/easa-certifies-anra-technologies-first-u-space-service-provider
- https://www.icao.int/Meetings/anconf14/Documents/WorkingPapers/wp_056_en.pdf

If you want, I can next produce a concrete gap-closure roadmap (MVP -> pre-cert -> cert) mapped to your current modules.