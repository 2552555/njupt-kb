"""0.0.3 代码保护构建的契约测试。

只验证本地可确定的部分：
- 受保护模块清单存在，且目标模块可导入、排除项覆盖敏感链路；
- 目标模块的输入输出契约稳定（编译前后对照的基准值）；
- 构建脚本在缺少工具时明确失败，不静默「成功但未保护」。

真实编译产物的一致性对照需要 C 编译器与目标平台，见
``python scripts/build_protected_modules.py --tool nuitka --verify``，属于环境相关验收。
"""

import json
import subprocess
import sys
import unittest
from pathlib import Path

from app.core import pagination, security

BACKEND = Path(__file__).resolve().parent.parent
CONFIG = BACKEND / "scripts" / "protected_modules.json"
SCRIPT = BACKEND / "scripts" / "build_protected_modules.py"


class ProtectedModulesConfigTests(unittest.TestCase):
    def setUp(self):
        self.data = json.loads(CONFIG.read_text(encoding="utf-8"))

    def test_targets_are_importable_and_limited(self):
        targets = [item["module"] for item in self.data["targets"]]
        self.assertGreaterEqual(len(targets), 1)
        # 只选少量纯计算模块：不允许把路由/主程序/数据库层放进编译清单。
        for module in targets:
            self.assertFalse(module.endswith(".router"), module)
            self.assertNotIn(module, {"app.main", "app.db"})
            __import__(module)

    def test_sensitive_layers_are_explicitly_excluded(self):
        excluded = " ".join(self.data["excluded"])
        for needle in ("app.main", "router", "identity.service", "app.db", "workers"):
            self.assertIn(needle, excluded)

    def test_each_target_declares_verification_points(self):
        for item in self.data["targets"]:
            self.assertTrue(item.get("reason"))
            self.assertTrue(item.get("verify"))


class ProtectionBaselineTests(unittest.TestCase):
    """编译前后对照的基准值：这些行为不能在「保护」过程中改变。"""

    def test_token_digest_is_stable(self):
        self.assertEqual(
            security.token_digest("abc"),
            "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad",
        )

    def test_constant_time_equal(self):
        self.assertTrue(security.constant_time_equal("abc", "abc"))
        self.assertFalse(security.constant_time_equal("abc", "abd"))

    def test_password_policy_boundaries(self):
        self.assertIsNotNone(security.password_problem("short"))
        self.assertIsNone(security.password_problem("a" * 12))

    def test_cursor_roundtrip(self):
        cursor = pagination.encode_cursor({"id": 7})
        self.assertEqual(pagination.decode_cursor(cursor), {"id": 7})

    def test_page_size_is_clamped(self):
        self.assertEqual(pagination.normalize_page_size(10_000), 100)
        self.assertEqual(pagination.normalize_page_size(None), 20)


class ProtectionScriptTests(unittest.TestCase):
    def test_script_exists_and_compiles(self):
        self.assertTrue(SCRIPT.exists())
        source = SCRIPT.read_text(encoding="utf-8")
        compile(source, str(SCRIPT), "exec")

    def test_check_mode_reports_tool_availability(self):
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "--check"],
            cwd=BACKEND,
            capture_output=True,
            text=True,
            timeout=180,
        )
        # 0 表示至少一个工具可用；1 表示都不可用——两者都是明确结论。
        self.assertIn(result.returncode, (0, 1), result.stderr)
        self.assertIn("nuitka", result.stdout.lower())
        self.assertIn("pyarmor", result.stdout.lower())

    def test_missing_tool_fails_explicitly(self):
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "--tool", "nuitka", "--verify"],
            cwd=BACKEND,
            capture_output=True,
            text=True,
            timeout=120,
        )
        # 未构建产物时必须返回非 0 并说明原因，不能假装验证通过。
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("未找到编译产物", result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
