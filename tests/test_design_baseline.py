import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class DesignBaselineTests(unittest.TestCase):
    def test_prd_tracks_current_governance_architecture(self) -> None:
        prd = (ROOT / "prd.md").read_text(encoding="utf-8")
        required = [
            "Change Intelligence",
            "Capability / Invariant Registry",
            "设计与实现对账",
            "Schema 与增量索引",
            "Skills 执行架构",
            "11 个命令组",
            ".rpi-outfile/state/transactions/",
        ]
        for marker in required:
            with self.subTest(marker=marker):
                self.assertIn(marker, prd)
        self.assertNotIn("压缩后仅 8 个入口", prd)

    def test_blueprint_carries_traceability_and_safety_contract(self) -> None:
        spec = (ROOT / ".rpi-blueprint/specs/l0/spec.md").read_text(encoding="utf-8")
        tasks = (ROOT / ".rpi-blueprint/specs/l0/tasks.md").read_text(encoding="utf-8")
        guards = (ROOT / ".rpi-blueprint/specs/l2/engineering-guardrails.md").read_text(encoding="utf-8")
        self.assertIn("关联 Change", spec)
        self.assertIn("Capability/Invariant", spec)
        self.assertIn("change_refs", tasks)
        self.assertIn("reconciliation", tasks)
        self.assertIn("Schema 校验", guards)
        self.assertIn("原子写入", guards)
        self.assertIn("平台边界", guards)

    def test_ux_blueprint_matches_current_skill_quality_layers(self) -> None:
        ux = (ROOT / ".rpi-blueprint/specs/l0/ux-spec.template.md").read_text(encoding="utf-8")
        for marker in ["视觉方向", "可访问性", "性能预算", "状态矩阵", "设计维护"]:
            with self.subTest(marker=marker):
                self.assertIn(marker, ux)
        self.assertNotIn("字段 ≤ 8 个", ux)


if __name__ == "__main__":
    unittest.main()
