#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
ENGINE_DIR="$SCRIPT_DIR/engine"

PROJECT_OPS_ENGINE="$ENGINE_DIR/project_ops_tool.py"
TASK_FLOW_ENGINE="$ENGINE_DIR/task_flow_tool.py"
GUARDRAILS_ENGINE="$ENGINE_DIR/guardrails_tool.py"
SPEC_STATE_ENGINE="$ENGINE_DIR/spec_state_tool.py"
AUTOMATION_ENGINE="$ENGINE_DIR/automation_tool.py"

SESSION_START_CORE="$ENGINE_DIR/session_start_core.py"
USER_PROMPT_SUBMIT_CORE="$ENGINE_DIR/user_prompt_submit_core.py"
PRE_TOOL_USE_CORE="$ENGINE_DIR/pre_tool_use_core.py"
POST_TOOL_USE_CORE="$ENGINE_DIR/post_tool_use_core.py"
STOP_GATE_CORE="$ENGINE_DIR/stop_gate_core.py"
PYTHON_BIN=""

usage() {
  cat <<'HELP'
Usage: bash .claude/workflow/rpi.sh <subcommand> [args]

Primary Subcommands:
  init [setup "<idea>" [platform] | deepen [idea] [platform] | bootstrap [--force] [<idea>] [platform]]
  task <start|pause|resume|abort|close|phase|status> [args...]
  check <env|doctor|precode|bootstrap|discovery|contract|scope|ux|linkage|skeleton|skeleton-init|theory|entry|artifact|architecture|risk|full> [args...]
  spec <build|verify|sync|link|expand> [args...]
  gates <preview|setup|run> [args...]
  mode <show|harness|profile|on|off|strict-regulated|balanced-enterprise|auto-lab> [args...]
  observe <logs|trace|evals|audit-pack|audit-report|recover> [args...]
  auto <run|review|memory|entropy> [args...]
  help

Hook Subcommands:
  hook-session-start
  hook-user-prompt-submit
  hook-pre-tool-use
  hook-post-tool-use
  hook-stop
HELP
}

emit_pretool_deny() {
  local reason="$1"
  local escaped="$reason"
  escaped="${escaped//\\/\\\\}"
  escaped="${escaped//\"/\\\"}"
  printf '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"deny","permissionDecisionReason":"%s"}}\n' "$escaped"
}

emit_stop_block() {
  local reason="$1"
  local escaped="$reason"
  escaped="${escaped//\\/\\\\}"
  escaped="${escaped//\"/\\\"}"
  printf '{"decision":"block","reason":"%s"}\n' "$escaped"
}

resolve_python_bin() {
  if command -v python3 >/dev/null 2>&1; then
    PYTHON_BIN="python3"
    return 0
  fi
  if command -v python >/dev/null 2>&1; then
    PYTHON_BIN="python"
    return 0
  fi
  PYTHON_BIN=""
  return 1
}

require_python_or_exit() {
  local context="$1"
  if ! resolve_python_bin; then
    echo "python3 or python is required for ${context}." >&2
    exit 1
  fi
}

run_python_engine() {
  local engine="$1"
  shift || true
  PYTHONIOENCODING="utf-8" PYTHONUTF8="1" "$PYTHON_BIN" "$engine" --project-dir "$PROJECT_DIR" "$@"
}

run_project_ops() {
  require_python_or_exit "project ops"
  if [[ ! -f "$PROJECT_OPS_ENGINE" ]]; then
    echo "Missing engine script: $PROJECT_OPS_ENGINE" >&2
    exit 1
  fi
  run_python_engine "$PROJECT_OPS_ENGINE" "$@"
}

run_task_flow() {
  require_python_or_exit "task flow"
  if [[ ! -f "$TASK_FLOW_ENGINE" ]]; then
    echo "Missing engine script: $TASK_FLOW_ENGINE" >&2
    exit 1
  fi
  run_python_engine "$TASK_FLOW_ENGINE" "$@"
}

run_spec_state() {
  require_python_or_exit "spec state"
  if [[ ! -f "$SPEC_STATE_ENGINE" ]]; then
    echo "Missing engine script: $SPEC_STATE_ENGINE" >&2
    exit 1
  fi
  run_python_engine "$SPEC_STATE_ENGINE" "$@"
}

run_guardrails() {
  require_python_or_exit "guardrails"
  if [[ ! -f "$GUARDRAILS_ENGINE" ]]; then
    echo "Missing engine script: $GUARDRAILS_ENGINE" >&2
    exit 1
  fi
  local subcmd="$1"
  shift || true
  PYTHONIOENCODING="utf-8" PYTHONUTF8="1" "$PYTHON_BIN" "$GUARDRAILS_ENGINE" "$subcmd" --project-dir "$PROJECT_DIR" "$@"
}

run_automation() {
  require_python_or_exit "automation"
  if [[ ! -f "$AUTOMATION_ENGINE" ]]; then
    echo "Missing engine script: $AUTOMATION_ENGINE" >&2
    exit 1
  fi
  run_python_engine "$AUTOMATION_ENGINE" "$@"
}

run_hook_core() {
  local core_file="$1"
  local hook_name="$2"
  local fallback_type="${3:-none}"

  if [[ ! -f "$core_file" ]]; then
    case "$fallback_type" in
      pretool) emit_pretool_deny "${hook_name} core engine missing: ${core_file#$PROJECT_DIR/}" ;;
      stop) emit_stop_block "${hook_name} core engine missing: ${core_file#$PROJECT_DIR/}" ;;
      *) ;;
    esac
    exit 0
  fi

  if ! resolve_python_bin; then
    case "$fallback_type" in
      pretool) emit_pretool_deny "python3 or python is required for ${hook_name} core. Install Python 3 and retry." ;;
      stop) emit_stop_block "python3 or python is required for ${hook_name} core. Install Python 3 and retry." ;;
      *) ;;
    esac
    exit 0
  fi

  exec env PYTHONIOENCODING="utf-8" PYTHONUTF8="1" "$PYTHON_BIN" "$core_file" --project-dir "$PROJECT_DIR"
}

ensure_env_ready() {
  local output rc
  set +e
  output="$(run_project_ops check-environment --require-jq --auto-fix --include-recommended 2>&1)"
  rc=$?
  set -e

  if [[ -n "$output" ]]; then
    printf '%s\n' "$output" >&2
  fi
  if [[ "$rc" -eq 3 ]]; then
    echo "Environment not ready on current platform (manual_action_required=true)." >&2
  fi
  return "$rc"
}

show_active_task_status() {
  if [[ -f "$PROJECT_DIR/.rpi-outfile/state/current_task.json" ]]; then
    task_id="$(jq -r '.task_id // ""' "$PROJECT_DIR/.rpi-outfile/state/current_task.json" 2>/dev/null || true)"
    status="$(jq -r '.status // "idle"' "$PROJECT_DIR/.rpi-outfile/state/current_task.json" 2>/dev/null || true)"
    if [[ -n "$task_id" && "$status" != "idle" ]]; then
      echo "Active task: $task_id (status: $status)"
      return 0
    fi
  fi
  echo "No active task"
  return 1
}

run_init_group() {
  local action="${1:-setup}"
  local raw_action="$action"
  local arg1=""
  if [[ "$action" == "--help" || "$action" == "-h" || "$action" == "help" ]]; then
    cat <<'HELP'
Usage: bash .claude/workflow/rpi.sh init [setup "<idea>" [platform] | deepen [idea] [platform] | bootstrap [--force] [<idea>] [platform]]

Actions:
  setup      初始化并生成 M0 基线（默认动作）
  deepen     基于当前设想深化 MVP 候选方向
  bootstrap  仅覆盖/重建基线规范文件
HELP
    return 0
  fi
  case "$action" in
    setup|deepen|bootstrap)
      shift || true
      arg1="${1:-}"
      ;;
    *)
      action="setup"
      arg1="$raw_action"
      ;;
  esac

  if [[ "$arg1" == "--help" || "$arg1" == "-h" || "$arg1" == "help" ]]; then
    case "$action" in
      setup)
        echo 'Usage: bash .claude/workflow/rpi.sh init [setup] "<idea>" [platform]'
        ;;
      deepen)
        echo 'Usage: bash .claude/workflow/rpi.sh init deepen [idea] [platform]'
        ;;
      bootstrap)
        echo 'Usage: bash .claude/workflow/rpi.sh init bootstrap [--force] [<idea>] [platform]'
        ;;
    esac
    return 0
  fi

  case "$action" in
    setup)
      local idea platform eval_output eval_rc eval_status
      idea="${1:-}"
      platform="${2:-Web}"
      if [[ -z "$idea" ]]; then
        echo 'Usage: bash .claude/workflow/rpi.sh init [setup] "<idea>" [platform]' >&2
        return 1
      fi
      ensure_env_ready
      run_project_ops init-state
      set +e
      eval_output="$(run_automation evaluate-requirement "$idea" 2>&1)"
      eval_rc=$?
      set -e
      if [[ -n "$eval_output" ]]; then
        printf '%s\n' "$eval_output"
      fi
      if [[ "$eval_rc" -ne 0 ]]; then
        return "$eval_rc"
      fi
      eval_status="$(printf '%s' "$eval_output" | jq -r '.status // ""' 2>/dev/null || true)"
      if [[ "$eval_status" == "rejected" || "$eval_status" == "clarify" ]]; then
        echo "init setup halted: requirement status=$eval_status" >&2
        return 2
      fi
      run_project_ops bootstrap "$idea" "$platform"
      run_automation create-mvp "$idea" "$platform"
      ;;
    deepen)
      run_automation deepen-mvp "$@"
      ;;
    bootstrap)
      ensure_env_ready
      run_project_ops bootstrap "$@"
      ;;
  esac
}

run_task_group() {
  local action="${1:-}"
  if [[ "$action" == "--help" || "$action" == "-h" || "$action" == "help" ]]; then
    echo "Usage: bash .claude/workflow/rpi.sh task <start|pause|resume|abort|close|phase|status> [args...]"
    return 0
  fi
  if [[ -z "$action" ]]; then
    echo "Usage: bash .claude/workflow/rpi.sh task <start|pause|resume|abort|close|phase|status> [args...]" >&2
    return 1
  fi
  shift || true
  case "$action" in
    start)
      ensure_env_ready
      run_project_ops init-state
      run_task_flow start "$@"
      ;;
    pause)
      run_automation pause-task "$@"
      ;;
    resume)
      run_automation resume-task "$@"
      ;;
    abort)
      run_automation abort-task "$@"
      ;;
    close)
      ensure_env_ready
      run_task_flow gates-auto
      run_task_flow close "$@"
      ;;
    phase)
      ensure_env_ready
      run_automation switch-phase "$@"
      ;;
    status)
      show_active_task_status
      ;;
    *)
      echo "Unknown task action: $action" >&2
      return 1
      ;;
  esac
}

run_check_group() {
  local action="${1:-full}"
  if [[ "$action" == "--help" || "$action" == "-h" || "$action" == "help" ]]; then
    echo "Usage: bash .claude/workflow/rpi.sh check <env|doctor|precode|bootstrap|discovery|contract|scope|ux|linkage|skeleton|skeleton-init|theory|entry|artifact|architecture|risk|full> [args...]"
    return 0
  fi
  if [[ $# -ge 1 ]]; then
    shift || true
  fi
  case "$action" in
    env)
      run_project_ops check-environment --require-jq --include-recommended "$@"
      ;;
    doctor)
      run_project_ops doctor
      ;;
    precode)
      run_guardrails check-discovery
      run_guardrails check-contract
      run_guardrails check-scope
      run_spec_state verify --scope all
      ;;
    bootstrap)
      run_automation bootstrap-gate "$@"
      ;;
    discovery)
      run_guardrails check-discovery "$@"
      ;;
    contract)
      run_guardrails check-contract "$@"
      ;;
    scope)
      run_guardrails check-scope "$@"
      ;;
    ux)
      run_automation ux-check "$@"
      ;;
    linkage)
      run_guardrails linkage-check "$@"
      ;;
    skeleton)
      run_automation check-skeleton "$@"
      ;;
    skeleton-init)
      run_automation skeleton-init "$@"
      ;;
    theory)
      run_automation check-theory "$@"
      ;;
    entry)
      run_automation check-entry "$@"
      ;;
    artifact)
      run_task_flow artifact-status "$@"
      ;;
    architecture)
      run_guardrails architecture-check "$@"
      ;;
    risk)
      run_guardrails risk-assess "$@"
      ;;
    full)
      run_project_ops doctor
      run_guardrails check-discovery
      run_guardrails check-contract
      run_guardrails check-scope
      run_spec_state verify --scope all
      run_automation check-theory
      run_automation check-entry
      ;;
    *)
      echo "Unknown check action: $action" >&2
      return 1
      ;;
  esac
}

run_spec_group() {
  local action="${1:-}"
  if [[ "$action" == "--help" || "$action" == "-h" || "$action" == "help" ]]; then
    echo "Usage: bash .claude/workflow/rpi.sh spec <build|verify|sync|link|expand> [args...]"
    return 0
  fi
  if [[ -z "$action" ]]; then
    echo "Usage: bash .claude/workflow/rpi.sh spec <build|verify|sync|link|expand> [args...]" >&2
    return 1
  fi
  shift || true
  case "$action" in
    build)
      run_spec_state build "$@"
      ;;
    verify)
      run_spec_state verify "$@"
      ;;
    sync)
      run_spec_state sync-source "$@"
      ;;
    link)
      run_guardrails spec-link "$@"
      ;;
    expand)
      run_automation spec-expand "$@"
      ;;
    *)
      echo "Unknown spec action: $action" >&2
      return 1
      ;;
  esac
}

run_gates_group() {
  local action="${1:-run}"
  if [[ "$action" == "--help" || "$action" == "-h" || "$action" == "help" ]]; then
    echo "Usage: bash .claude/workflow/rpi.sh gates <preview|setup|run> [args...]"
    return 0
  fi
  case "$action" in
    preview|setup|run)
      shift || true
      ;;
    *)
      action="run"
      ;;
  esac
  case "$action" in
    preview)
      ensure_env_ready
      profile="${1:-standard}"
      run_automation suggest-gates --profile "$profile" --explain
      ;;
    setup)
      ensure_env_ready
      profile="${1:-standard}"
      run_automation suggest-gates --profile "$profile" --write
      ;;
    run)
      ensure_env_ready
      run_task_flow gates-auto "$@"
      ;;
  esac
}

run_mode_group() {
  local action="${1:-show}"
  if [[ "$action" == "--help" || "$action" == "-h" || "$action" == "help" ]]; then
    echo "Usage: bash .claude/workflow/rpi.sh mode <show|harness|profile|on|off|strict-regulated|balanced-enterprise|auto-lab> [args...]"
    return 0
  fi
  shift || true
  case "$action" in
    show)
      run_task_flow profile show
      run_automation harness show
      ;;
    harness)
      run_automation harness "${1:-show}"
      ;;
    profile)
      run_task_flow profile "$@"
      ;;
    on|off)
      run_automation harness "$action"
      ;;
    strict-regulated|balanced-enterprise|auto-lab)
      run_task_flow profile apply "$action"
      ;;
    *)
      echo "Unknown mode action: $action" >&2
      return 1
      ;;
  esac
}

run_observe_group() {
  local action="${1:-}"
  if [[ "$action" == "--help" || "$action" == "-h" || "$action" == "help" ]]; then
    echo "Usage: bash .claude/workflow/rpi.sh observe <logs|trace|evals|audit-pack|audit-report|recover> [args...]"
    return 0
  fi
  if [[ -z "$action" ]]; then
    echo "Usage: bash .claude/workflow/rpi.sh observe <logs|trace|evals|audit-pack|audit-report|recover> [args...]" >&2
    return 1
  fi
  shift || true
  case "$action" in
    logs)
      run_automation query-logs "$@"
      ;;
    trace)
      run_automation trace-grade "$@"
      ;;
    evals)
      run_automation run-evals "$@"
      ;;
    audit-pack)
      run_automation build-audit-pack "$@"
      ;;
    audit-report)
      run_automation audit-report "$@"
      ;;
    recover)
      run_automation recover "$@"
      ;;
    *)
      echo "Unknown observe action: $action" >&2
      return 1
      ;;
  esac
}

run_auto_group() {
  local action="${1:-run}"
  if [[ "$action" == "--help" || "$action" == "-h" || "$action" == "help" ]]; then
    echo "Usage: bash .claude/workflow/rpi.sh auto <run|review|memory|entropy> [args...]"
    return 0
  fi
  case "$action" in
    run|review|memory|entropy)
      shift || true
      ;;
    *)
      action="run"
      ;;
  esac
  case "$action" in
    run)
      run_automation auto-rpi "$@"
      ;;
    review)
      run_automation a2a-review "$@"
      ;;
    memory)
      run_automation agent-memory-update "$@"
      ;;
    entropy)
      run_automation anti-entropy "$@"
      ;;
  esac
}

subcommand="${1:-}"
if [[ -z "$subcommand" ]]; then
  usage
  exit 1
fi
shift || true

case "$subcommand" in
  init)
    run_init_group "$@"
    ;;
  task)
    run_task_group "$@"
    ;;
  check)
    run_check_group "$@"
    ;;
  spec)
    run_spec_group "$@"
    ;;
  gates)
    run_gates_group "$@"
    ;;
  mode)
    run_mode_group "$@"
    ;;
  observe)
    run_observe_group "$@"
    ;;
  auto)
    run_auto_group "$@"
    ;;
  hook-session-start)
    run_hook_core "$SESSION_START_CORE" "SessionStart"
    ;;
  hook-user-prompt-submit)
    run_hook_core "$USER_PROMPT_SUBMIT_CORE" "UserPromptSubmit"
    ;;
  hook-pre-tool-use)
    run_hook_core "$PRE_TOOL_USE_CORE" "PreToolUse" "pretool"
    ;;
  hook-post-tool-use)
    run_hook_core "$POST_TOOL_USE_CORE" "PostToolUse"
    ;;
  hook-stop)
    run_hook_core "$STOP_GATE_CORE" "Stop" "stop"
    ;;
  help|--help|-h)
    usage
    ;;
  *)
    cmd_file="$PROJECT_DIR/.claude/commands/rpi-${subcommand}.md"
    if [[ -f "$cmd_file" ]]; then
      echo "「/rpi-${subcommand}」是提示词命令，由 AI 直接执行，不通过 rpi.sh 调用。" >&2
      echo "请在对话中输入 /rpi-${subcommand} 触发。" >&2
      exit 0
    fi
    echo "Unknown subcommand: $subcommand" >&2
    usage
    exit 1
    ;;
esac
