import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace


ENGINE_DIR = Path(__file__).resolve().parents[1] / ".claude" / "workflow" / "engine"
if str(ENGINE_DIR) not in sys.path:
    sys.path.insert(0, str(ENGINE_DIR))

import automation_tool  # noqa: E402
import post_tool_use_core  # noqa: E402


class PostToolUseExitCodeTests(unittest.TestCase):
    def test_extract_exit_code_accepts_success_status_payload(self) -> None:
        payload = {
            "tool_name": "Bash",
            "tool_response": {
                "status": "success",
            },
        }
        exit_code, source, raw = post_tool_use_core.extract_exit_code(payload)
        self.assertEqual(exit_code, "0")
        self.assertEqual(source, "hook_payload_status:tool_response.status")
        self.assertEqual(raw, "success")

    def test_extract_exit_code_handles_camel_case_exit_code(self) -> None:
        payload = {
            "tool_name": "Bash",
            "result": {
                "exitCode": 0,
            },
        }
        exit_code, source, raw = post_tool_use_core.extract_exit_code(payload)
        self.assertEqual(exit_code, "0")
        self.assertEqual(source, "hook_payload:result.exitCode")
        self.assertEqual(raw, "0")

    def test_extract_exit_code_reads_transcript_tool_result(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            transcript = Path(tmp) / "transcript.jsonl"
            transcript.write_text(
                '\n'.join(
                    [
                        '{"type":"user","message":{"role":"user","content":[{"tool_use_id":"tooluse_demo","type":"tool_result","content":"ok","is_error":false}]}}',
                    ]
                )
                + '\n',
                encoding="utf-8",
            )
            payload = {
                "tool_name": "Bash",
                "transcript_path": str(transcript),
                "tool_use_id": "tooluse_demo",
                "tool_response": {
                    "stdout": "ok",
                    "stderr": "",
                    "interrupted": False,
                },
            }
            exit_code, source, raw = post_tool_use_core.extract_exit_code(payload)
            self.assertEqual(exit_code, "0")
            self.assertEqual(source, "transcript_tool_result:tooluse_demo")
            self.assertEqual(raw, "false")

    def test_extract_exit_code_falls_back_to_non_interrupted_tool_response(self) -> None:
        payload = {
            "tool_name": "Bash",
            "tool_input": {"command": "echo ok"},
            "tool_response": {
                "stdout": "ok",
                "stderr": "",
                "interrupted": False,
            },
        }
        exit_code, source, raw = post_tool_use_core.extract_exit_code(payload)
        self.assertEqual(exit_code, "0")
        self.assertEqual(source, "hook_tool_response:non_interrupted")
        self.assertEqual(raw, "ok")


class DiscoveryMaterializationTests(unittest.TestCase):
    def test_materialize_l0_docs_rewrites_discovery_with_domain_content(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            spec_l0_dir = Path(tmp) / ".rpi-outfile" / "specs" / "l0"
            spec_l0_dir.mkdir(parents=True, exist_ok=True)
            paths = SimpleNamespace(spec_l0_dir=spec_l0_dir)
            idea = (
                "基于next.js+node.js+sqlite开发一个网课系统，目前mvp阶段，只做网课短视频和音频播放、"
                "课程订阅收藏、学习历史、vip系统（暂时只支持激活码激活），暂不做第三方支付。"
            )
            profile = automation_tool.infer_business_profile(idea)
            automation_tool.materialize_l0_docs(
                paths=paths,
                idea=idea,
                platform="Web",
                profile=profile,
                direction="A",
                must_ids=["L1", "L2", "L3"],
                wont_ids=["L4", "第三方支付", "社区互动"],
                coverage_target="P0 >= 40%",
                weighted_target="40%",
            )
            discovery = (spec_l0_dir / "discovery.md").read_text(encoding="utf-8")
            tasks = (spec_l0_dir / "tasks.md").read_text(encoding="utf-8")

            self.assertIn("学员", discovery)
            self.assertIn("课程播放", discovery)
            self.assertIn("VIP 权益", discovery)
            self.assertNotIn("一线业务操作人员", discovery)
            self.assertNotIn("用户登录并创建核心记录", discovery)
            self.assertIn("短视频/音频播放", tasks)
            self.assertNotIn("创建核心记录", tasks)

    def test_mvp_placeholder_replacements_follow_profile_direction_maps(self) -> None:
        idea = (
            "基于next.js+node.js+sqlite开发一个网课系统，目前mvp阶段，只做网课短视频和音频播放、"
            "课程订阅收藏、学习历史、vip系统（暂时只支持激活码激活），暂不做第三方支付。"
        )
        profile = automation_tool.infer_business_profile(idea)
        replacements = automation_tool.build_mvp_placeholder_replacements(
            profile=profile,
            cov_a=40,
            cov_b=80,
            cov_c=100,
            is_frontend=True,
            is_headless_cli=False,
        )
        self.assertEqual(replacements["选定链路 IDs（示例：L1,L2）"], "L1, L2, L3")
        self.assertIn("第三方支付", replacements["未入选链路 + 非核心扩展能力"])


if __name__ == "__main__":
    unittest.main()
