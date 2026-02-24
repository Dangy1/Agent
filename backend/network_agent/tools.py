from oran_agent.core import (
    mcp_health,
    mcp_kpm_monitor_check,
    mcp_kpm_rc_start,
    mcp_run_log_tail,
    mcp_run_status,
    mcp_runs_list,
    mcp_slice_apply_profile_and_verify,
    mcp_slice_monitor_check,
    mcp_slice_start,
    mcp_tc_start,
)

TOOLS = [
    mcp_health,
    mcp_slice_start,
    mcp_slice_monitor_check,
    mcp_slice_apply_profile_and_verify,
    mcp_tc_start,
    mcp_kpm_rc_start,
    mcp_kpm_monitor_check,
    mcp_runs_list,
    mcp_run_status,
    mcp_run_log_tail,
]

