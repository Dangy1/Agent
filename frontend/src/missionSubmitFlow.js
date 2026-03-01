function asRecord(value) {
  return value !== null && typeof value === "object" && !Array.isArray(value) ? value : null;
}

export function shouldAutoReplanForDssConflict(aggregate) {
  const agg = asRecord(aggregate);
  if (!agg) return false;
  const approvalReq = asRecord(agg.approval_request);
  const approvalReqResult = asRecord(approvalReq?.result);
  const approvalReqError = String(approvalReqResult?.error ?? "").trim().toLowerCase();
  const dssCandidates = [
    asRecord(approvalReq?.dss_intent_result),
    asRecord(approvalReqResult?.dss_intent_result),
    asRecord(asRecord(agg.verify_from_uav)?.dss_intent_result),
    asRecord(asRecord(agg.geofence_submit)?.dss_intent_result),
  ];
  const dss = dssCandidates.find((row) => !!row) ?? null;
  const status = String(dss?.status ?? "").trim().toLowerCase();
  const blocking = Array.isArray(dss?.blocking_conflicts) ? dss.blocking_conflicts.length : 0;
  return blocking > 0 || status === "rejected" || approvalReqError === "dss_strategic_conflict";
}
