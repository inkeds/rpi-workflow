import json
import contextlib
import io
import subprocess
import shutil
import sys
import tempfile
import unittest
from unittest import mock
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CORE_DIR = ROOT / ".rpi" / "core"
if str(CORE_DIR) not in sys.path:
    sys.path.insert(0, str(CORE_DIR))

import adapter_tool  # noqa: E402
import change_intelligence as ci  # noqa: E402
import eval_tool  # noqa: E402
import product_intelligence as pi  # noqa: E402
import project_governance as pg  # noqa: E402
import reconciliation as rec  # noqa: E402
import state_store  # noqa: E402
import state_migrations as sm  # noqa: E402
import schema_validation  # noqa: E402

ENGINE_DIR = ROOT / ".claude" / "workflow" / "engine"
if str(ENGINE_DIR) not in sys.path:
    sys.path.insert(0, str(ENGINE_DIR))
import task_flow_tool  # noqa: E402
import pre_tool_use_core  # noqa: E402


class ProductIntelligenceTests(unittest.TestCase):
    def test_analysis_detects_web_system_conflict_and_marketing_language(self) -> None:
        analysis = pi.analyze_text(
            "做一个无需安装的 Web 应用，实时监控所有应用流量并修改请求。",
            "SRC-test",
        )
        self.assertEqual(analysis["uncertainty"], "high")
        self.assertFalse(analysis["formal_spec_ready"])
        self.assertIn("无需安装", {item["term"] for item in analysis["marketing_language"]})
        self.assertTrue(any(item["left"] == "web" for item in analysis["conflicts"]))

    def test_capture_preserves_raw_source_and_creates_inferred_claims(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            with contextlib.redirect_stdout(io.StringIO()):
                rc = pi.cmd_capture(project, "支持网页管理；Windows 托盘运行。", "copied_description")
            self.assertEqual(rc, 0)
            sources = json.loads((project / ".rpi-outfile/product/sources.json").read_text(encoding="utf-8"))
            claims = json.loads((project / ".rpi-outfile/product/claims.json").read_text(encoding="utf-8"))
            self.assertEqual(sources["sources"][0]["text"], "支持网页管理；Windows 托盘运行。")
            self.assertTrue(sources["sources"][0]["immutable"])
            self.assertTrue(all(claim["state"] == "inferred" for claim in claims["claims"]))

    def test_fact_transition_requires_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            with contextlib.redirect_stdout(io.StringIO()):
                pi.cmd_capture(project, "用户可以创建记录。", "user_idea")
            claims_path = project / ".rpi-outfile/product/claims.json"
            claims = json.loads(claims_path.read_text(encoding="utf-8"))
            claim_id = claims["claims"][0]["id"]
            with contextlib.redirect_stdout(io.StringIO()):
                pi.cmd_transition(project, claim_id, "selected", "用户选择首版实现", [])
            with self.assertRaisesRegex(ValueError, "requires at least one evidence"):
                pi.cmd_transition(project, claim_id, "fact", "成为当前事实", [])
            with contextlib.redirect_stdout(io.StringIO()):
                pi.cmd_transition(project, claim_id, "fact", "原型验收通过", ["evidence://prototype-001"])
            facts = json.loads((project / ".rpi-outfile/product/current_facts.json").read_text(encoding="utf-8"))
            self.assertEqual(facts["facts"][0]["id"], claim_id)

    def test_exploration_gate_requires_at_least_one_source(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(pi.cmd_status(project, require_source=True), 2)
                pi.cmd_capture(project, "验证一个本地工具想法。", "user_idea")
                self.assertEqual(pi.cmd_status(project, require_source=True), 0)

    def test_directions_explain_tradeoffs_without_auto_promoting_fact(self) -> None:
        analysis = pi.analyze_text(
            "无需安装的 Web 管理页，监控 Windows 所有应用流量，并支持团队云端协作。",
            "SRC-direction",
        )
        directions = pi.direction_candidates(analysis, "demo")
        self.assertGreaterEqual(len(directions), 2)
        self.assertLessEqual(len(directions), 3)
        self.assertTrue(any(item["recommendation"]["objections"] for item in directions))
        self.assertTrue(all(item["status"] == "candidate" for item in directions))

    def test_select_direction_creates_selected_claim_not_fact(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            with contextlib.redirect_stdout(io.StringIO()):
                pi.cmd_capture(project, "Web 管理页结合 Windows 桌面流量监控。", "user_idea")
                pi.cmd_directions(project, "")
            directions = json.loads((project / ".rpi-outfile/product/directions.json").read_text(encoding="utf-8"))["directions"]
            with contextlib.redirect_stdout(io.StringIO()):
                pi.cmd_select_direction(project, directions[0]["id"], "用户接受该取舍")
            claims = json.loads((project / ".rpi-outfile/product/claims.json").read_text(encoding="utf-8"))["claims"]
            direction_claims = [item for item in claims if item.get("applicable_scope") == "product-direction"]
            self.assertEqual(direction_claims[0]["state"], "selected")
            self.assertFalse((project / ".rpi-outfile/product/current_facts.json").exists())


class ChangeIntelligenceTests(unittest.TestCase):
    def test_change_captures_authority_baseline_and_flags_invariant_conflict(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            invariants = project / ".rpi-outfile/product/invariants.json"
            invariants.parent.mkdir(parents=True)
            invariants.write_text(
                json.dumps(
                    {
                        "schema_version": 2,
                        "invariants": [
                            {
                                "id": "INV-ASSET-001",
                                "title": "资产所有权和可见性遵循主体边界",
                                "status": "candidate",
                                "selected_option": "owner_scoped",
                            }
                        ],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            result = ci.analyze_change("允许查看所有文件")
            ci.persist_analysis(project, result)
            latest = json.loads((project / ".rpi-outfile/state/changes/latest.json").read_text(encoding="utf-8"))
            self.assertTrue(latest["baseline"]["authority_fingerprint"])
            self.assertEqual(latest["status"], "pending_decision")
            self.assertEqual(latest["conflicts"][0]["kind"], "invariant_change")
            self.assertEqual(latest["conflicts"][0]["status"], "pending")

    def test_change_flags_conflict_against_existing_spec_without_registry_entry(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            spec = project / ".rpi-outfile/specs/l0/spec.md"
            spec.parent.mkdir(parents=True)
            spec.write_text("资产仅所有者可见，本阶段不支持管理员查看全部文件。", encoding="utf-8")
            result = ci.analyze_change("允许管理员查看所有文件")
            ci.persist_analysis(project, result)
            self.assertTrue(any(item["kind"] == "design_semantics_review" for item in result["conflicts"]))

    def test_conflict_resolution_and_explicit_rebase_close_the_governance_loop(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            invariants = project / ".rpi-outfile/product/invariants.json"
            invariants.parent.mkdir(parents=True)
            invariants.write_text(
                json.dumps(
                    {"schema_version": 2, "invariants": [{"id": "INV-ASSET-001", "title": "资产可见性", "status": "candidate", "selected_option": "owner_scoped"}]},
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            result = ci.analyze_change("允许查看所有文件")
            ci.persist_analysis(project, result)
            conflict_id = result["conflicts"][0]["conflict_id"]
            resolved = ci.resolve_conflict(project, result["change_id"], conflict_id, "coexist", "user://explicit-scope")
            for decision in result["decisions_required"]:
                resolved = ci.confirm_change(
                    project,
                    result["change_id"],
                    "user://decision-confirmed",
                    decision["decision_id"],
                    decision["recommended_option"],
                )
            self.assertEqual(resolved["status"], "spec_update_required")
            rebased = ci.rebase_change(project, result["change_id"], "design://updated-spec-and-invariant")
            self.assertEqual(rebased["baseline_history"][0]["evidence"], "design://updated-spec-and-invariant")
            self.assertEqual(ci.compare_baseline(project, rebased["baseline"])["status"], "current")

    def test_stale_authority_baseline_blocks_linked_task_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            result = ci.analyze_change("修复导出乱码")
            ci.persist_analysis(project, result)
            inv = project / ".rpi-outfile/product/invariants.json"
            inv.parent.mkdir(parents=True, exist_ok=True)
            inv.write_text(json.dumps({"schema_version": 2, "invariants": [{"id": "INV-1", "title": "新增约束"}]}), encoding="utf-8")
            paths = pre_tool_use_core.build_paths(project)
            paths.state_dir.mkdir(parents=True, exist_ok=True)
            paths.current_task_file.write_text(
                json.dumps({"task_id": "TASK-STALE", "status": "in_progress", "change_refs": [result["change_id"]]}),
                encoding="utf-8",
            )
            core = pre_tool_use_core.PreToolUseCore(paths, {"tool_name": "Edit", "tool_input": {"file_path": "src/export.py"}})
            decision = core.enforce_change_gate_if_needed()
            self.assertEqual(decision[0], "deny")
            self.assertIn("stale product-governance baseline", decision[1])

    def test_local_bug_fix_can_proceed_with_lightweight_governance(self) -> None:
        result = ci.analyze_change("修复导出 CSV 时中文乱码的问题")
        self.assertEqual(result["change_type"], "local_fix")
        self.assertTrue(result["implementation_allowed"])
        self.assertIn("data", result["affected_domains"])

    def test_cross_domain_feature_requires_impact_analysis(self) -> None:
        result = ci.analyze_change("增加团队共享，允许管理员查看并删除成员文件")
        self.assertEqual(result["change_type"], "cross_domain_change")
        self.assertFalse(result["implementation_allowed"])
        self.assertIn("authorization", result["affected_domains"])
        self.assertIn("assets", result["affected_domains"])
        self.assertTrue(result["decisions_required"])

    def test_product_model_change_never_auto_promotes_to_implementation(self) -> None:
        result = ci.analyze_change("改成多租户 SaaS，由组织统一付费并邀请成员")
        self.assertEqual(result["change_type"], "product_model_change")
        self.assertFalse(result["implementation_allowed"])
        self.assertEqual(result["governance_level"], "decision_required")

    def test_question_does_not_create_a_change(self) -> None:
        result = ci.analyze_change("为什么导出功能会失败？")
        self.assertEqual(result["change_type"], "question")
        self.assertFalse(result["repository_change_requested"])

    def test_change_analysis_can_be_persisted_for_task_linkage(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            result = ci.analyze_change("新增批量导出功能")
            path = ci.persist_analysis(project, result)
            latest = json.loads((project / ".rpi-outfile/state/changes/latest.json").read_text(encoding="utf-8"))
            self.assertTrue(path.exists())
            self.assertEqual(latest["change_id"], result["change_id"])

    def test_natural_language_hook_injects_change_governance_without_command(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            payload = json.dumps({"prompt": "增加团队共享，允许管理员查看成员文件"}, ensure_ascii=False)
            completed = subprocess.run(
                [sys.executable, str(ROOT / ".claude/workflow/engine/user_prompt_submit_core.py"), "--project-dir", str(project)],
                input=payload,
                text=True,
                capture_output=True,
                check=True,
            )
            output = json.loads(completed.stdout)
            context = output["hookSpecificOutput"]["additionalContext"]
            latest = json.loads((project / ".rpi-outfile/state/changes/latest.json").read_text(encoding="utf-8"))
            self.assertIn("[RPI Change Analysis]", context)
            self.assertIn("change_type: cross_domain_change", context)
            self.assertIn("implementation_allowed: false", context)
            self.assertEqual(latest["status"], "pending_decision")

    def test_natural_language_hook_surfaces_existing_design_conflict(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            spec = project / ".rpi-outfile/specs/l0/spec.md"
            spec.parent.mkdir(parents=True)
            spec.write_text("资产仅所有者可见。", encoding="utf-8")
            payload = json.dumps({"prompt": "允许管理员查看所有文件"}, ensure_ascii=False)
            completed = subprocess.run(
                [sys.executable, str(ROOT / ".claude/workflow/engine/user_prompt_submit_core.py"), "--project-dir", str(project)],
                input=payload,
                text=True,
                capture_output=True,
                check=True,
            )
            context = json.loads(completed.stdout)["hookSpecificOutput"]["additionalContext"]
            self.assertIn("conflicts_detected: CNF-", context)
            self.assertIn("design_semantics_review", context)

    def test_confirmed_change_moves_to_spec_update_instead_of_direct_implementation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            result = ci.analyze_change("改成多租户 SaaS，由组织统一付费")
            ci.persist_analysis(project, result)
            confirmed = ci.confirm_change(project, result["change_id"], "user-confirmed://decision-1")
            self.assertEqual(confirmed["status"], "spec_update_required")
            self.assertFalse(confirmed["implementation_allowed"])

    def test_decisions_can_be_confirmed_individually(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            result = ci.analyze_change("增加团队共享，允许管理员删除成员文件")
            ci.persist_analysis(project, result)
            first = result["decisions_required"][0]
            partial = ci.confirm_change(
                project,
                result["change_id"],
                "user-confirmed://decision-1",
                first["decision_id"],
                first["recommended_option"],
            )
            self.assertEqual(partial["status"], "pending_decision")
            self.assertEqual(partial["decisions_required"][0]["status"], "confirmed")
            self.assertTrue(any(item["status"] == "pending" for item in partial["decisions_required"]))

    def test_ambiguous_continue_is_not_a_decision_confirmation(self) -> None:
        self.assertFalse(ci.is_explicit_confirmation("继续"))
        self.assertFalse(ci.is_explicit_confirmation("可以，往下做"))
        self.assertTrue(ci.is_explicit_confirmation("确认以上 P0 决策"))
        self.assertTrue(ci.is_explicit_confirmation("按以上推荐方案继续"))

    def test_explicit_natural_language_confirmation_updates_pending_change(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            pending = ci.analyze_change("改成多租户 SaaS，由组织统一付费")
            ci.persist_analysis(project, pending)
            payload = json.dumps({"prompt": "确认以上 P0 决策"}, ensure_ascii=False)
            completed = subprocess.run(
                [sys.executable, str(ROOT / ".claude/workflow/engine/user_prompt_submit_core.py"), "--project-dir", str(project)],
                input=payload,
                text=True,
                capture_output=True,
                check=True,
            )
            output = json.loads(completed.stdout)
            latest = json.loads((project / ".rpi-outfile/state/changes/latest.json").read_text(encoding="utf-8"))
            self.assertIn("[RPI Decision Confirmation]", output["hookSpecificOutput"]["additionalContext"])
            self.assertEqual(latest["status"], "spec_update_required")

    def test_pending_decision_blocks_code_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            paths = pre_tool_use_core.build_paths(project)
            paths.state_dir.mkdir(parents=True)
            result = ci.analyze_change("增加团队共享，允许管理员删除成员文件")
            ci.persist_analysis(project, result)
            (paths.current_task_file).write_text(
                json.dumps({"task_id": "TASK-001", "status": "in_progress", "change": {"change_id": result["change_id"]}}),
                encoding="utf-8",
            )
            core = pre_tool_use_core.PreToolUseCore(
                paths,
                {"tool_name": "Edit", "tool_input": {"file_path": "src/service.py"}},
            )
            decision = core.enforce_change_gate_if_needed()
            self.assertEqual(decision[0], "deny")
            self.assertIn(result["change_id"], decision[1])

    def test_unrelated_pending_change_does_not_block_linked_active_task(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            paths = pre_tool_use_core.build_paths(project)
            paths.state_dir.mkdir(parents=True)
            active = ci.analyze_change("修复导出乱码")
            ci.persist_analysis(project, active)
            pending = ci.analyze_change("改成多租户 SaaS，由组织统一付费")
            ci.persist_analysis(project, pending)
            paths.current_task_file.write_text(
                json.dumps({"task_id": "TASK-001", "status": "in_progress", "change": {"change_id": active["change_id"]}}),
                encoding="utf-8",
            )
            core = pre_tool_use_core.PreToolUseCore(
                paths,
                {"tool_name": "Edit", "tool_input": {"file_path": "src/export.py"}},
            )
            self.assertIsNone(core.enforce_change_gate_if_needed())

    def test_explicit_current_task_addition_is_linked_and_blocks_when_pending(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            state = project / ".rpi-outfile/state"
            state.mkdir(parents=True)
            (state / "current_task.json").write_text(
                json.dumps({"task_id": "TASK-123", "status": "in_progress", "change": {}, "change_refs": []}),
                encoding="utf-8",
            )
            payload = json.dumps({"prompt": "在当前任务中再加团队共享，允许管理员删除成员文件"}, ensure_ascii=False)
            completed = subprocess.run(
                [sys.executable, str(ROOT / ".claude/workflow/engine/user_prompt_submit_core.py"), "--project-dir", str(project)],
                input=payload,
                text=True,
                capture_output=True,
                check=True,
            )
            output = json.loads(completed.stdout)
            current = json.loads((state / "current_task.json").read_text(encoding="utf-8"))
            latest = json.loads((state / "changes/latest.json").read_text(encoding="utf-8"))
            self.assertIn(latest["change_id"], current["change_refs"])
            self.assertEqual(latest["task_id"], "TASK-123")
            self.assertIn("linked_active_task: TASK-123", output["hookSpecificOutput"]["additionalContext"])
            paths = pre_tool_use_core.build_paths(project)
            core = pre_tool_use_core.PreToolUseCore(paths, {"tool_name": "Edit", "tool_input": {"file_path": "src/x.py"}})
            self.assertEqual(core.enforce_change_gate_if_needed()[0], "deny")

    def test_current_task_billing_addition_phrase_is_recognized(self) -> None:
        result = ci.analyze_change("在当前任务中再加组织统一付费")
        self.assertTrue(result["repository_change_requested"])
        self.assertEqual(result["change_type"], "product_model_change")
        self.assertEqual(result["status"], "pending_decision")


class ProjectGovernanceTests(unittest.TestCase):
    def test_schema_validation_rejects_invalid_change_before_write(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            invalid = ci.analyze_change("新增批量导出功能")
            invalid["change_id"] = "invalid"
            with self.assertRaises(schema_validation.SchemaValidationError):
                ci.persist_analysis(project, invalid)
            self.assertFalse((project / ".rpi-outfile/state/changes/latest.json").exists())

    def test_build_preserves_user_agents_content_and_adds_project_routing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            (project / "AGENTS.md").write_text("# AGENTS.md\n\n## Custom Rules\n- Keep this rule.\n", encoding="utf-8")
            (project / ".rpi-outfile/specs/l0").mkdir(parents=True)
            (project / ".rpi-outfile/specs/l0/spec.md").write_text("用户可以共享文件，管理员按权限查看。", encoding="utf-8")
            result = pg.build_governance(project)
            agents = (project / "AGENTS.md").read_text(encoding="utf-8")
            self.assertIn("Keep this rule", agents)
            self.assertIn(pg.MANAGED_START, agents)
            self.assertIn("Authorization", agents)
            self.assertIn("Assets", agents)
            self.assertTrue(Path(result["capability_registry"]).exists())
            self.assertTrue(Path(result["invariant_registry"]).exists())

    def test_verify_rejects_unknown_capability_dependencies(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            pg.ensure_layout(project)
            capabilities = {
                "schema_version": 1,
                "capabilities": [
                    {"id": "CAP-001", "name": "Export", "classification": "core", "dependencies": ["CAP-999"], "invariants": []}
                ],
            }
            pg.write_json(pg.capability_registry_path(project), capabilities)
            report = pg.verify_governance(project)
            self.assertEqual(report["status"], "fail")
            self.assertTrue(any("CAP-999" in item for item in report["errors"]))

    def test_confirmed_change_generates_candidate_capability_invariant_and_real_routes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            (project / "src/auth").mkdir(parents=True)
            (project / "tests/auth").mkdir(parents=True)
            (project / "src/auth/policy.py").write_text("# 管理员权限与文件删除策略", encoding="utf-8")
            (project / "tests/auth/test_policy.py").write_text("# 权限测试", encoding="utf-8")
            change = ci.analyze_change("增加团队共享，允许管理员删除成员文件")
            ci.persist_analysis(project, change)
            confirmed = ci.confirm_change(project, change["change_id"], "user://confirmed")
            self.assertEqual(confirmed["status"], "spec_update_required")
            pg.build_governance(project)
            capabilities = json.loads(pg.capability_registry_path(project).read_text(encoding="utf-8"))["capabilities"]
            invariants = json.loads(pg.invariant_registry_path(project).read_text(encoding="utf-8"))["invariants"]
            agents = (project / "AGENTS.md").read_text(encoding="utf-8")
            audit = json.loads((project / ".rpi-outfile/product/material-audit.json").read_text(encoding="utf-8"))
            self.assertEqual(len(capabilities), 1)
            self.assertGreaterEqual(len(invariants), 2)
            self.assertIn("src/auth/policy.py", agents)
            self.assertIn("tests/auth/test_policy.py", agents)
            self.assertEqual(audit["project_state"], "existing_code")

    def test_verify_detects_capability_dependency_cycles(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            pg.ensure_layout(project)
            pg.write_json(
                pg.capability_registry_path(project),
                {
                    "schema_version": 1,
                    "capabilities": [
                        {"id": "CAP-001", "name": "A", "classification": "core", "dependencies": ["CAP-002"], "invariants": []},
                        {"id": "CAP-002", "name": "B", "classification": "supporting", "dependencies": ["CAP-001"], "invariants": []},
                    ],
                },
            )
            report = pg.verify_governance(project)
            self.assertTrue(any("cycle" in item for item in report["errors"]))

    def test_similar_accepted_changes_reuse_candidate_capability(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            first = ci.analyze_change("新增批量导出功能")
            ci.persist_analysis(project, first)
            pg.build_governance(project)
            second = ci.analyze_change("增加批量导出 CSV 文件")
            ci.persist_analysis(project, second)
            pg.build_governance(project)
            capabilities = json.loads(pg.capability_registry_path(project).read_text(encoding="utf-8"))["capabilities"]
            self.assertEqual(len(capabilities), 1)
            self.assertEqual(set(capabilities[0]["source_changes"]), {first["change_id"], second["change_id"]})
            self.assertTrue(capabilities[0]["merge_history"])

    def test_governance_rebuild_does_not_duplicate_capability_merge_history(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            first = ci.analyze_change("新增批量导出功能")
            ci.persist_analysis(project, first)
            pg.build_governance(project)
            second = ci.analyze_change("增加批量导出 CSV 文件")
            ci.persist_analysis(project, second)
            pg.build_governance(project)
            pg.build_governance(project)
            capability = json.loads(pg.capability_registry_path(project).read_text(encoding="utf-8"))["capabilities"][0]
            history = [item for item in capability["merge_history"] if item["change_id"] == second["change_id"]]
            self.assertEqual(len(history), 1)
            self.assertNotIn(first["request_text"], capability["aliases"])

    def test_broad_cross_domain_candidate_is_flagged_for_decomposition_review(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            change = ci.analyze_change("同时增加团队共享、管理员删除文件和组织统一付费")
            ci.persist_analysis(project, change)
            ci.confirm_change(project, change["change_id"], "user://confirmed")
            pg.build_governance(project)
            capability = json.loads(pg.capability_registry_path(project).read_text(encoding="utf-8"))["capabilities"][0]
            self.assertTrue(capability["decomposition_review"])
            self.assertEqual(capability["decomposition"]["status"], "required")
            self.assertIn("crosses_three_or_more_high_impact_domains", capability["decomposition"]["reasons"])
            self.assertGreaterEqual(len(capability["decomposition"]["suggested_slices"]), 3)

    def test_manual_capability_merge_migrates_references(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            pg.ensure_layout(project)
            capabilities = [
                {"id": "CAP-target", "name": "Export", "classification": "core", "dependencies": [], "invariants": []},
                {"id": "CAP-source", "name": "CSV Export", "classification": "supporting", "dependencies": [], "invariants": []},
            ]
            pg.write_json(pg.capability_registry_path(project), {"schema_version": 2, "capabilities": capabilities})
            pg.write_json(pg.invariant_registry_path(project), {"schema_version": 2, "invariants": [{"id": "DATA-1", "title": "Data", "scope": ["CAP-source"], "change_policy": "spec_update_required"}]})
            change = ci.analyze_change("新增 CSV 导出功能")
            change["affected_capabilities"] = ["CAP-source"]
            ci.persist_analysis(project, change)
            report = pg.merge_capabilities(project, "CAP-target", ["CAP-source"], "user://merge-approved")
            cap_doc = json.loads(pg.capability_registry_path(project).read_text(encoding="utf-8"))
            inv_doc = json.loads(pg.invariant_registry_path(project).read_text(encoding="utf-8"))
            latest = json.loads((project / ".rpi-outfile/state/changes/latest.json").read_text(encoding="utf-8"))
            by_id = {item["id"]: item for item in cap_doc["capabilities"]}
            self.assertEqual(report["status"], "merged")
            self.assertEqual(by_id["CAP-source"]["superseded_by"], "CAP-target")
            self.assertEqual(inv_doc["invariants"][0]["scope"], ["CAP-target"])
            self.assertEqual(latest["affected_capabilities"], ["CAP-target"])

    def test_manual_capability_split_creates_children_and_migrates_references(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            pg.ensure_layout(project)
            parent = {"id": "CAP-broad", "name": "Team platform", "classification": "core", "dependencies": [], "invariants": [], "decomposition_review": True}
            pg.write_json(pg.capability_registry_path(project), {"schema_version": 2, "capabilities": [parent]})
            report = pg.split_capability(project, "CAP-broad", ["Team sharing", "Organization billing"], "user://split-approved")
            cap_doc = json.loads(pg.capability_registry_path(project).read_text(encoding="utf-8"))
            by_id = {item["id"]: item for item in cap_doc["capabilities"]}
            self.assertEqual(report["status"], "split")
            self.assertEqual(by_id["CAP-broad"]["status"], "retired")
            self.assertEqual(set(by_id["CAP-broad"]["split_into"]), set(report["children"]))
            self.assertTrue(all(by_id[child]["split_from"] == "CAP-broad" for child in report["children"]))

    def test_project_route_index_reuses_unchanged_content_and_invalidates_changes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            (project / "src").mkdir()
            source = project / "src/export.py"
            source.write_text("# 数据导出功能\n", encoding="utf-8")
            change = ci.analyze_change("新增数据导出功能")
            ci.persist_analysis(project, change)
            first = pg.build_governance(project)
            second = pg.build_governance(project)
            self.assertGreater(first["index"]["cache_misses"], 0)
            self.assertGreater(second["index"]["cache_hits"], 0)
            source.write_text("# 数据导出和备份功能\n", encoding="utf-8")
            third = pg.build_governance(project)
            self.assertGreater(third["index"]["cache_misses"], 0)

    def test_governance_build_uses_one_project_tree_walk(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            (project / "src").mkdir()
            (project / "src/export.py").write_text("# 数据导出", encoding="utf-8")
            ci.persist_analysis(project, ci.analyze_change("新增数据导出功能"))
            real_walk = pg.os.walk
            calls = 0

            def counted_walk(*args, **kwargs):
                nonlocal calls
                calls += 1
                return real_walk(*args, **kwargs)

            with mock.patch.object(pg.os, "walk", side_effect=counted_walk):
                report = pg.build_governance(project)
            self.assertEqual(calls, 1)
            self.assertEqual(report["index"]["walks"], 1)

    def test_invalid_project_index_is_rebuilt(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            (project / "src").mkdir()
            (project / "src/export.py").write_text("# 数据导出", encoding="utf-8")
            ci.persist_analysis(project, ci.analyze_change("新增数据导出功能"))
            index_path = pg.project_index_path(project)
            index_path.parent.mkdir(parents=True)
            index_path.write_text(json.dumps({"schema_version": 1, "updated_at": "now", "entries": {"broken.py": {"size": "bad"}}}), encoding="utf-8")
            report = pg.build_governance(project)
            rebuilt = json.loads(index_path.read_text(encoding="utf-8"))
            schema_validation.validate(rebuilt, "project-index.schema.json", project)
            self.assertTrue(report["index"]["cache_rebuilt"])

    def test_project_route_scan_skips_symlinked_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as outside:
            project = Path(tmp)
            target = Path(outside) / "secret.py"
            target.write_text("# 管理员读取全部敏感资产", encoding="utf-8")
            link = project / "linked_secret.py"
            try:
                link.symlink_to(target)
            except OSError:
                self.skipTest("symlinks are unavailable")
            routes, stats = pg._discover_routes(project, ["authorization", "privacy"])
            self.assertGreaterEqual(stats["skipped"], 1)
            self.assertFalse(any("linked_secret.py" in path for route in routes.values() for paths in route.values() for path in paths))

    def test_oversized_text_is_audited_but_not_content_scanned(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            (project / "src").mkdir()
            large = project / "src/large.py"
            large.write_text("# 数据导出\n" + "x" * 513_000, encoding="utf-8")
            audit, routes, stats = pg._scan_project(project, ["data"])
            self.assertIn("src/large.py", audit["inventory"]["code"])
            self.assertGreaterEqual(stats["skipped"], 1)
            self.assertFalse(any("src/large.py" in path for paths in routes["data"].values() for path in paths))


class ReconciliationTests(unittest.TestCase):
    def test_feature_code_change_without_test_or_design_update_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            state = project / ".rpi-outfile/state"
            logs = project / ".rpi-outfile/logs"
            state.mkdir(parents=True)
            logs.mkdir(parents=True)
            task = {
                "task_id": "TASK-001",
                "created_at": "2026-01-01T00:00:00+00:00",
                "spec_refs": [".rpi-outfile/specs/l0/spec.md"],
                "change": {"change_id": "CHG-001", "change_type": "feature_change", "affected_domains": ["assets"]},
                "tdd": {"latest_test_status": "unknown"},
            }
            (state / "current_task.json").write_text(json.dumps(task), encoding="utf-8")
            (logs / "events.jsonl").write_text(
                json.dumps({"ts": "2026-01-02T00:00:00+00:00", "event": "post_tool_use", "path": "src/export.py", "targets_code": True}) + "\n",
                encoding="utf-8",
            )
            report = rec.reconcile(project, "TASK-001")
            self.assertEqual(report["status"], "fail")
            categories = {item["category"] for item in report["issues"]}
            self.assertIn("test_evidence_missing", categories)
            self.assertIn("test_execution_missing", categories)
            self.assertIn("design_update_missing", categories)

    def test_feature_with_code_test_and_spec_updates_reconciles(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            state = project / ".rpi-outfile/state"
            logs = project / ".rpi-outfile/logs"
            state.mkdir(parents=True)
            logs.mkdir(parents=True)
            task = {
                "task_id": "TASK-002",
                "created_at": "2026-01-01T00:00:00+00:00",
                "spec_refs": [".rpi-outfile/specs/l0/spec.md"],
                "change": {"change_id": "CHG-002", "change_type": "feature_change", "affected_domains": ["data"]},
                "tdd": {"latest_test_status": "pass"},
            }
            (state / "current_task.json").write_text(json.dumps(task), encoding="utf-8")
            rows = [
                {"ts": "2026-01-02T00:00:00+00:00", "event": "post_tool_use", "path": "src/export.py", "targets_code": True},
                {"ts": "2026-01-02T00:01:00+00:00", "event": "post_tool_use", "path": "tests/test_export.py", "targets_code": True},
                {"ts": "2026-01-02T00:02:00+00:00", "event": "post_tool_use", "path": ".rpi-outfile/specs/l0/spec.md", "targets_specs": True},
            ]
            (logs / "events.jsonl").write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")
            report = rec.reconcile(project, "TASK-002")
            self.assertEqual(report["status"], "pass")


class CoreConcurrencyTests(unittest.TestCase):
    def test_json_write_size_limit_rejects_oversized_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            original_limit = state_store.MAX_JSON_BYTES
            state_store.MAX_JSON_BYTES = 64
            try:
                with self.assertRaisesRegex(ValueError, "JSON state exceeds"):
                    state_store.write_json(Path(tmp) / "large.json", {"value": "x" * 128})
            finally:
                state_store.MAX_JSON_BYTES = original_limit

    def test_schema_validation_depth_is_bounded(self) -> None:
        schema: dict[str, object] = {"type": "string"}
        value: object = "leaf"
        for _ in range(schema_validation.MAX_VALIDATION_DEPTH + 2):
            schema = {"type": "array", "items": schema}
            value = [value]
        errors = schema_validation._validate(value, schema, ROOT / ".rpi/schemas", "$")
        self.assertTrue(any("maximum Schema validation depth" in error for error in errors))

    def test_atomic_write_rejects_symlink_target(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "target.json"
            target.write_text('{"safe":true}', encoding="utf-8")
            link = root / "state.json"
            try:
                link.symlink_to(target)
            except OSError:
                self.skipTest("symlinks are unavailable")
            with self.assertRaisesRegex(RuntimeError, "symlinked state"):
                state_store.write_json(link, {"safe": False})
            self.assertTrue(json.loads(target.read_text(encoding="utf-8"))["safe"])

    def test_lock_timeout_is_enforced(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            lock = Path(tmp) / "held"
            holder = (
                "import sys,time; from pathlib import Path; "
                f"sys.path.insert(0, {str(CORE_DIR)!r}); import state_store; "
                f"p=Path({str(lock)!r}); "
                "\nwith state_store.exclusive_lock(p):\n print('locked', flush=True); time.sleep(1)"
            )
            process = subprocess.Popen([sys.executable, "-c", holder], stdout=subprocess.PIPE, text=True)
            self.assertEqual(process.stdout.readline().strip(), "locked")
            with self.assertRaises(TimeoutError):
                with state_store.exclusive_lock(lock, timeout_seconds=0.1, poll_seconds=0.01):
                    pass
            process.stdout.close()
            process.wait(timeout=3)

    def test_atomic_update_preserves_concurrent_increments(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "counter.json"
            worker = (
                "import sys; from pathlib import Path; "
                f"sys.path.insert(0, {str(CORE_DIR)!r}); import state_store; "
                f"p=Path({str(path)!r}); "
                "state_store.update_json(p, {'value': 0}, lambda d: {'value': int(d.get('value', 0)) + 1})"
            )
            processes = [subprocess.Popen([sys.executable, "-c", worker]) for _ in range(12)]
            self.assertTrue(all(process.wait() == 0 for process in processes))
            self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["value"], 12)

    def test_concurrent_decision_confirmations_do_not_lose_updates(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            change = ci.analyze_change("增加团队共享，允许管理员删除成员文件")
            ci.persist_analysis(project, change)
            commands = []
            for decision in change["decisions_required"]:
                commands.append(
                    [
                        sys.executable,
                        str(ROOT / ".rpi/core/change_intelligence.py"),
                        "--project-dir",
                        str(project),
                        "confirm",
                        change["change_id"],
                        "--decision",
                        decision["decision_id"],
                        "--option",
                        decision["recommended_option"],
                        "--evidence",
                        f"concurrency://{decision['decision_id']}",
                    ]
                )
            processes = [subprocess.Popen(command, stdout=subprocess.DEVNULL) for command in commands]
            self.assertTrue(all(process.wait() == 0 for process in processes))
            final = json.loads((project / ".rpi-outfile/state/changes/latest.json").read_text(encoding="utf-8"))
            self.assertEqual(final["status"], "spec_update_required")
            self.assertTrue(all(item["status"] == "confirmed" for item in final["decisions_required"]))

    def test_parallel_governance_builds_leave_valid_single_managed_section(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            (project / "AGENTS.md").write_text("# AGENTS.md\n\n## User Rule\n- preserve\n", encoding="utf-8")
            change = ci.analyze_change("新增批量导出功能")
            ci.persist_analysis(project, change)
            command = [
                sys.executable,
                str(ROOT / ".rpi/core/project_governance.py"),
                "--project-dir",
                str(project),
                "build",
            ]
            processes = [subprocess.Popen(command, stdout=subprocess.DEVNULL) for _ in range(8)]
            self.assertTrue(all(process.wait() == 0 for process in processes))
            agents = (project / "AGENTS.md").read_text(encoding="utf-8")
            self.assertEqual(agents.count(pg.MANAGED_START), 1)
            self.assertIn("preserve", agents)
            json.loads(pg.capability_registry_path(project).read_text(encoding="utf-8"))
            json.loads(pg.invariant_registry_path(project).read_text(encoding="utf-8"))

    def test_interrupted_multi_file_transaction_is_recovered(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first = root / "first.json"
            second = root / "second.json"
            journal = root / "transaction.json"
            first.write_text('{"value":"before-first"}\n', encoding="utf-8")
            second.write_text('{"value":"before-second"}\n', encoding="utf-8")
            worker = (
                "import os,sys; from pathlib import Path; "
                f"sys.path.insert(0, {str(CORE_DIR)!r}); import state_store; "
                f"a=Path({str(first)!r}); b=Path({str(second)!r}); j=Path({str(journal)!r}); "
                f"\nwith state_store.atomic_file_transaction(j, [a,b], root=Path({str(root)!r})):\n"
                " state_store.write_json(a, {'value':'after-first'})\n"
                " state_store.write_json(b, {'value':'after-second'})\n"
                " os._exit(17)"
            )
            process = subprocess.run([sys.executable, "-c", worker], check=False)
            self.assertEqual(process.returncode, 17)
            self.assertTrue(journal.exists())
            self.assertTrue(state_store.recover_transaction(journal, allowed_root=root))
            self.assertEqual(json.loads(first.read_text(encoding="utf-8"))["value"], "before-first")
            self.assertEqual(json.loads(second.read_text(encoding="utf-8"))["value"], "before-second")
            self.assertFalse(journal.exists())

    def test_transaction_recovery_rejects_workspace_escape(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            journal = root / "transaction.json"
            journal.write_text(
                json.dumps({"status": "prepared", "root": str(root), "files": [{"path": "../escape", "existed": False, "content_base64": ""}]}),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(RuntimeError, "Unsafe transaction path"):
                state_store.recover_transaction(journal, allowed_root=root)

    def test_migration_and_governance_build_share_transaction_boundary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            changes = project / ".rpi-outfile/state/changes"
            product = project / ".rpi-outfile/product"
            changes.mkdir(parents=True)
            product.mkdir(parents=True)
            change = ci.analyze_change("新增批量导出功能")
            change["schema_version"] = 1
            (changes / f"{change['change_id']}.json").write_text(json.dumps(change), encoding="utf-8")
            (changes / "latest.json").write_text(json.dumps(change), encoding="utf-8")
            (product / "capabilities.json").write_text(json.dumps({"schema_version": 1, "capabilities": []}), encoding="utf-8")
            build = [sys.executable, str(ROOT / ".rpi/core/project_governance.py"), "--project-dir", str(project), "build"]
            migrate = [sys.executable, str(ROOT / ".rpi/core/state_migrations.py"), "--project-dir", str(project)]
            processes = [subprocess.Popen(build if index % 2 else migrate, stdout=subprocess.DEVNULL) for index in range(8)]
            self.assertTrue(all(process.wait() == 0 for process in processes))
            capability_doc = json.loads(pg.capability_registry_path(project).read_text(encoding="utf-8"))
            latest = json.loads((changes / "latest.json").read_text(encoding="utf-8"))
            self.assertEqual(capability_doc["schema_version"], 2)
            self.assertEqual(latest["schema_version"], 2)
            self.assertEqual(len(capability_doc["capabilities"]), 1)


class StateMigrationTests(unittest.TestCase):
    def test_v1_governance_state_migrates_idempotently_without_losing_content(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            changes = project / ".rpi-outfile/state/changes"
            product = project / ".rpi-outfile/product"
            changes.mkdir(parents=True)
            product.mkdir(parents=True)
            legacy_change = {
                "schema_version": 1,
                "change_id": "CHG-old",
                "request_text": "增加团队共享",
                "status": "pending_decision",
                "decisions_required": [{"topic": "authorization_scope", "reason": "legacy"}],
            }
            (changes / "CHG-old.json").write_text(json.dumps(legacy_change), encoding="utf-8")
            (changes / "latest.json").write_text(json.dumps(legacy_change), encoding="utf-8")
            (product / "capabilities.json").write_text(
                json.dumps({"schema_version": 1, "capabilities": [{"id": "CAP-old", "name": "Share", "classification": "core"}]}),
                encoding="utf-8",
            )
            dry = sm.migrate_project(project, dry_run=True)
            self.assertGreater(dry["changed_count"], 0)
            self.assertEqual(json.loads((changes / "CHG-old.json").read_text())["schema_version"], 1)
            first = sm.migrate_project(project)
            migrated = json.loads((changes / "CHG-old.json").read_text(encoding="utf-8"))
            capability = json.loads((product / "capabilities.json").read_text(encoding="utf-8"))["capabilities"][0]
            self.assertGreater(first["changed_count"], 0)
            self.assertEqual(migrated["schema_version"], 2)
            self.assertTrue(migrated["decisions_required"][0]["decision_id"].startswith("DEC-"))
            self.assertEqual(migrated["decisions_required"][0]["reason"], "legacy")
            self.assertEqual(capability["name"], "Share")
            self.assertIn("dependencies", capability)
            second = sm.migrate_project(project)
            self.assertEqual(second["changed_count"], 0)

    def test_migration_preserves_invalid_json_and_reports_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            product = project / ".rpi-outfile/product"
            product.mkdir(parents=True)
            path = product / "capabilities.json"
            original = '{"schema_version": 1, broken'
            path.write_text(original, encoding="utf-8")
            report = sm.migrate_project(project)
            self.assertEqual(report["error_count"], 1)
            self.assertEqual(report["changed_count"], 0)
            self.assertEqual(path.read_text(encoding="utf-8"), original)

    def test_migration_does_not_downgrade_future_schema(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            product = project / ".rpi-outfile/product"
            product.mkdir(parents=True)
            path = product / "capabilities.json"
            future = {"schema_version": 99, "future_field": {"preserve": True}, "capabilities": []}
            path.write_text(json.dumps(future), encoding="utf-8")
            report = sm.migrate_project(project)
            self.assertEqual(report["skipped_future_count"], 1)
            self.assertEqual(json.loads(path.read_text(encoding="utf-8")), future)
            with self.assertRaisesRegex(RuntimeError, "newer Schema"):
                pg.build_governance(project)


class ReconciliationIntegrationTests(unittest.TestCase):
    def test_reconciliation_detects_undeclared_high_impact_implementation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            state = project / ".rpi-outfile/state"
            logs = project / ".rpi-outfile/logs"
            (project / "src").mkdir(parents=True)
            state.mkdir(parents=True)
            logs.mkdir(parents=True)
            (project / "src/admin_delete.py").write_text("# 管理员删除用户文件", encoding="utf-8")
            task = {
                "task_id": "TASK-003",
                "created_at": "2026-01-01T00:00:00+00:00",
                "spec_refs": [".rpi-outfile/specs/l0/spec.md"],
                "change": {"change_id": "", "change_type": "feature_change", "affected_domains": ["data"]},
                "tdd": {"latest_test_status": "pass"},
            }
            (state / "current_task.json").write_text(json.dumps(task), encoding="utf-8")
            rows = [
                {"ts": "2026-01-02T00:00:00+00:00", "event": "post_tool_use", "path": "src/admin_delete.py", "targets_code": True},
                {"ts": "2026-01-02T00:01:00+00:00", "event": "post_tool_use", "path": "tests/test_admin_delete.py", "targets_code": True},
                {"ts": "2026-01-02T00:02:00+00:00", "event": "post_tool_use", "path": ".rpi-outfile/specs/l0/spec.md", "targets_specs": True},
            ]
            (logs / "events.jsonl").write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")
            report = rec.reconcile(project, "TASK-003")
            self.assertEqual(report["status"], "fail")
            self.assertTrue(any(item["category"] == "implementation_scope_untracked" for item in report["issues"]))

    def _prepare_close_project(self, project: Path, test_status: str) -> task_flow_tool.Paths:
        paths = task_flow_tool.build_paths(project)
        task_flow_tool.ensure_layout(paths)
        task_flow_tool.write_json_atomic(
            paths.runtime_file,
            {"strict_mode": True, "close_require_spec_sync": True, "agent_memory_auto_update": False},
        )
        core_dir = project / ".rpi/core"
        core_dir.mkdir(parents=True)
        shutil.copy(ROOT / ".rpi/core/reconciliation.py", core_dir / "reconciliation.py")
        shutil.copy(ROOT / ".rpi/core/change_intelligence.py", core_dir / "change_intelligence.py")
        shutil.copy(ROOT / ".rpi/core/state_store.py", core_dir / "state_store.py")
        shutil.copy(ROOT / ".rpi/core/schema_validation.py", core_dir / "schema_validation.py")
        schemas_dir = project / ".rpi/schemas"
        schemas_dir.mkdir(parents=True)
        for name in ("reconciliation.schema.json", "change-impact.schema.json", "decision.schema.json"):
            shutil.copy(ROOT / ".rpi/schemas" / name, schemas_dir / name)
        task = {
            "task_id": "TASK-010",
            "phase": "M0",
            "status": "in_progress",
            "created_at": "2026-01-01T00:00:00+00:00",
            "spec_refs": [".rpi-outfile/specs/l0/spec.md"],
            "change": {"change_id": "CHG-010", "change_type": "feature_change", "affected_domains": ["data"]},
            "tdd": {"latest_test_status": test_status},
        }
        task_flow_tool.write_json_atomic(paths.current_task_file, task)
        rows = [
            {"ts": "2026-01-02T00:00:00+00:00", "event": "post_tool_use", "path": "src/export.py", "targets_code": True},
            {"ts": "2026-01-02T00:01:00+00:00", "event": "post_tool_use", "path": "tests/test_export.py", "targets_code": True},
            {"ts": "2026-01-02T00:02:00+00:00", "event": "post_tool_use", "path": ".rpi-outfile/specs/l0/spec.md", "targets_specs": True},
        ]
        paths.event_log.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")
        return paths

    def test_task_close_is_blocked_when_reconciliation_lacks_test_execution(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            paths = self._prepare_close_project(Path(tmp), "unknown")
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                rc = task_flow_tool.cmd_close(paths, ["pass", "auto", "done"])
            self.assertEqual(rc, 1)

    def test_task_close_passes_after_design_test_and_execution_reconcile(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            paths = self._prepare_close_project(Path(tmp), "pass")
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                rc = task_flow_tool.cmd_close(paths, ["pass", "auto", "done"])
            self.assertEqual(rc, 0)
            report = json.loads((paths.state_dir / "reconciliation/TASK-010.json").read_text(encoding="utf-8"))
            self.assertEqual(report["status"], "pass")


class PhaseModelTests(unittest.TestCase):
    def test_m_minus_one_is_a_supported_phase(self) -> None:
        self.assertEqual(task_flow_tool.normalize_phase("m-1"), "M-1")
        self.assertEqual(task_flow_tool.extract_phase_from_text("开始 M-1 探索任务"), "M-1")
        self.assertEqual(task_flow_tool.phase_ratio("M-1"), "8:2")

    def test_new_layout_starts_in_explore(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            paths = task_flow_tool.build_paths(Path(tmp))
            task_flow_tool.ensure_layout(paths)
            phase = json.loads(paths.phase_file.read_text(encoding="utf-8"))
            self.assertEqual(phase["phase"], "M-1")

    def test_explore_gate_uses_product_material_instead_of_formal_spec(self) -> None:
        gates = json.loads((ROOT / ".claude/workflow/config/gates.json").read_text(encoding="utf-8"))
        self.assertEqual(gates["phase_gates"]["M-1"], ["exploration_material_captured"])
        self.assertIn("idea status --require-source", gates["commands"]["exploration_material_captured"])


class AdapterTests(unittest.TestCase):
    def test_all_generated_skill_trees_match_canonical_sources(self) -> None:
        canonical_root = ROOT / ".rpi/skills"
        canonical_names = {path.name for path in canonical_root.iterdir() if path.is_dir()}
        for adapter_root in (ROOT / ".agents/skills", ROOT / ".claude/skills"):
            self.assertEqual({path.name for path in adapter_root.iterdir() if path.is_dir()}, canonical_names)
            for name in canonical_names:
                canonical = canonical_root / name
                adapter = adapter_root / name
                canonical_files = {path.relative_to(canonical): path.read_bytes() for path in canonical.rglob("*") if path.is_file()}
                adapter_files = {path.relative_to(adapter): path.read_bytes() for path in adapter.rglob("*") if path.is_file()}
                self.assertEqual(adapter_files, canonical_files, name)

    def test_debugging_and_review_skills_preserve_source_and_rpi_boundaries(self) -> None:
        debugging = (ROOT / ".rpi/skills/systematic-debugging/SKILL.md").read_text(encoding="utf-8")
        review = (ROOT / ".rpi/skills/code-reviewing/SKILL.md").read_text(encoding="utf-8")
        self.assertIn("d884ae04edebef577e82ff7c4e143debd0bbec99", debugging)
        self.assertIn("根因", debugging)
        self.assertIn("Change/Decision/Spec", debugging)
        self.assertIn("auto review", review)
        self.assertIn("manual_review_required", review)
        self.assertTrue((ROOT / ".rpi/skills/systematic-debugging/references/MIT.txt").exists())
        self.assertTrue((ROOT / ".rpi/skills/code-reviewing/references/MIT.txt").exists())

    def test_ux_skill_fuses_design_react_and_rpi_validation_layers(self) -> None:
        skill_dir = ROOT / ".rpi/skills/ux-compliance-checking"
        skill = (skill_dir / "SKILL.md").read_text(encoding="utf-8")
        visual = (skill_dir / "references/visual-design.md").read_text(encoding="utf-8")
        react = (skill_dir / "references/react-performance.md").read_text(encoding="utf-8")
        self.assertIn("product-ux | visual-design | accessibility", skill)
        self.assertIn("references/visual-design.md", skill)
        self.assertIn("references/react-performance.md", skill)
        self.assertIn("9d2f1ae187231d8199c64b5b762e1bdf2244733d", visual)
        self.assertIn("f8a72b9603728bb92a217a879b7e62e43ad76c81", react)
        self.assertTrue((skill_dir / "references/APACHE-2.0.txt").exists())
        self.assertTrue((skill_dir / "references/MIT.txt").exists())

    def test_generated_ux_skills_match_canonical_source(self) -> None:
        canonical = ROOT / ".rpi/skills/ux-compliance-checking"
        for adapter in (ROOT / ".agents/skills/ux-compliance-checking", ROOT / ".claude/skills/ux-compliance-checking"):
            canonical_files = {path.relative_to(canonical): path.read_bytes() for path in canonical.rglob("*") if path.is_file()}
            adapter_files = {path.relative_to(adapter): path.read_bytes() for path in adapter.rglob("*") if path.is_file()}
            self.assertEqual(adapter_files, canonical_files)

    def test_codex_config_explains_intentional_project_override_boundary(self) -> None:
        config = adapter_tool.render_codex_config()
        self.assertIn("Intentionally contains no model", config)
        self.assertIn(".codex/hooks.json", config)
        self.assertIn("../AGENTS.md", config)

    def test_compat_setup_upgrades_only_legacy_placeholder_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            (project / ".codex").mkdir()
            config = project / ".codex/config.toml"
            config.write_text(adapter_tool.LEGACY_CODEX_CONFIG, encoding="utf-8")
            with contextlib.redirect_stdout(io.StringIO()):
                adapter_tool.cmd_setup(project)
            self.assertEqual(config.read_text(encoding="utf-8"), adapter_tool.render_codex_config())
            config.write_text('model = "custom"\n', encoding="utf-8")
            with contextlib.redirect_stdout(io.StringIO()):
                adapter_tool.cmd_setup(project)
            self.assertEqual(config.read_text(encoding="utf-8"), 'model = "custom"\n')

    def test_codex_hooks_cover_rpi_lifecycle(self) -> None:
        hooks = adapter_tool.codex_hooks()["hooks"]
        self.assertEqual(set(hooks), set(adapter_tool.HOOK_EVENTS))
        self.assertIn("apply_patch", hooks["PreToolUse"][0]["matcher"])

    def test_codex_payload_normalization_is_covered_by_bridge_contract(self) -> None:
        bridge_dir = ROOT / ".rpi" / "adapters"
        if str(bridge_dir) not in sys.path:
            sys.path.insert(0, str(bridge_dir))
        import hook_bridge

        normalized = hook_bridge.normalize_codex_payload(
            "PreToolUse",
            {"tool_name": "exec_command", "tool_input": {"cmd": "pytest -q"}},
        )
        self.assertEqual(normalized["tool_name"], "Bash")
        self.assertEqual(normalized["tool_input"]["command"], "pytest -q")

    def test_verified_capability_becomes_stale_when_adapter_changes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            (project / ".codex").mkdir()
            (project / ".agents/skills/demo").mkdir(parents=True)
            (project / ".rpi/adapters").mkdir(parents=True)
            (project / "AGENTS.md").write_text("rules", encoding="utf-8")
            (project / ".codex/hooks.json").write_text("{}", encoding="utf-8")
            (project / ".agents/skills/demo/SKILL.md").write_text("skill", encoding="utf-8")
            (project / ".rpi/adapters/hook_bridge.py").write_text("bridge", encoding="utf-8")
            installed = {"installed": True, "version": "codex-test"}
            files = {"config": True, "hooks": True, "skills": True, "instructions": True}
            fingerprint = adapter_tool.platform_fingerprint(project, "codex", "codex-test")
            adapter_tool.write_json(
                adapter_tool.verification_path(project),
                {
                    "platforms": {
                        "codex": {
                            "fingerprint": fingerprint,
                            "capabilities": {"skills": {"status": "verified", "evidence": "manual check"}},
                        }
                    }
                },
            )
            state = adapter_tool.capability_states(project, "codex", installed, files)
            self.assertEqual(state["capabilities"]["skills"]["status"], "verified")
            (project / ".agents/skills/demo/SKILL.md").write_text("changed", encoding="utf-8")
            state = adapter_tool.capability_states(project, "codex", installed, files)
            self.assertEqual(state["capabilities"]["skills"]["status"], "stale")


class EvalToolTests(unittest.TestCase):
    def test_critical_metric_regression_requires_manual_review(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            baseline = project / "baseline.json"
            candidate = project / "candidate.json"
            baseline.write_text(
                json.dumps({"model": "old", "metrics": {"unsupported_claim_rate": {"value": 0.02, "higher_is_better": False, "critical": True}}}),
                encoding="utf-8",
            )
            candidate.write_text(
                json.dumps({"model": "new", "metrics": {"unsupported_claim_rate": {"value": 0.05, "higher_is_better": False, "critical": True}}}),
                encoding="utf-8",
            )
            with contextlib.redirect_stdout(io.StringIO()):
                rc = eval_tool.cmd_compare(project, baseline, candidate, None)
            self.assertEqual(rc, 2)

    def test_eval_templates_cover_three_supported_scenarios(self) -> None:
        self.assertEqual(
            set(eval_tool.TEMPLATE_NAMES),
            {"structured-extraction", "grounded-generation", "agent-tool-use"},
        )


if __name__ == "__main__":
    unittest.main()
