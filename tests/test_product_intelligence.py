import json
import contextlib
import io
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CORE_DIR = ROOT / ".rpi" / "core"
if str(CORE_DIR) not in sys.path:
    sys.path.insert(0, str(CORE_DIR))

import adapter_tool  # noqa: E402
import product_intelligence as pi  # noqa: E402

ENGINE_DIR = ROOT / ".claude" / "workflow" / "engine"
if str(ENGINE_DIR) not in sys.path:
    sys.path.insert(0, str(ENGINE_DIR))
import task_flow_tool  # noqa: E402


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


if __name__ == "__main__":
    unittest.main()
