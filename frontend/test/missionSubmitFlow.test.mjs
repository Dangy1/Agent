import test from "node:test";
import assert from "node:assert/strict";

import { shouldAutoReplanForDssConflict } from "../src/missionSubmitFlow.js";

test("detects blocking conflicts from DSS intent result", () => {
  const aggregate = {
    approval_request: {
      dss_intent_result: {
        status: "accepted",
        blocking_conflicts: [{ intent_id: "peer-1" }],
      },
    },
  };
  assert.equal(shouldAutoReplanForDssConflict(aggregate), true);
});

test("detects DSS rejected status", () => {
  const aggregate = {
    geofence_submit: {
      dss_intent_result: {
        status: "rejected",
      },
    },
  };
  assert.equal(shouldAutoReplanForDssConflict(aggregate), true);
});

test("detects explicit dss_strategic_conflict error", () => {
  const aggregate = {
    approval_request: {
      result: {
        error: "dss_strategic_conflict",
      },
    },
  };
  assert.equal(shouldAutoReplanForDssConflict(aggregate), true);
});

test("returns false when no conflict signals are present", () => {
  const aggregate = {
    approval_request: {
      dss_intent_result: {
        status: "success",
        blocking_conflicts: [],
      },
      result: {
        status: "success",
      },
    },
  };
  assert.equal(shouldAutoReplanForDssConflict(aggregate), false);
});

test("returns false for non-object payloads", () => {
  assert.equal(shouldAutoReplanForDssConflict(null), false);
  assert.equal(shouldAutoReplanForDssConflict(undefined), false);
  assert.equal(shouldAutoReplanForDssConflict("bad-payload"), false);
});
