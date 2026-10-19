#!/usr/bin/env python
"""可选的 Python 编译保护构建（补充规范 C06）。

只处理 ``scripts/protected_modules.json`` 里登记的少量纯计算模块，**不**编译
路由、认证流程、数据库层与任务队列——这些模块需要动态导入、反射与调试能力，
编译它们只会增加维护成本，不构成有效保护。

支持两种工具，二选一，不叠加：
- ``--tool nuitka``：开源，编译为原生扩展（需要 C 编译器；Windows 上 Nuitka 可自动下载 MinGW）。
- ``--tool pyarmor``：需要可用的 PyArmor 授权；未授权时明确失败，不静默降级。

默认行为：
- 不修改仓库源码，产物只写 ``backend/build/protected*/``；
- 不把编译产物提交进 Git；
- 生成 ``build/protection-report.json`` 记录工具版本、Python ABI、平台与产物哈希；
- 任何一步失败都保留源码可用，部署回退为「直接使用源码」，不需要回滚代码。

用法：
    python scripts/build_protected_modules.py --check
    python scripts/build_protected_modules.py --tool nuitka
    python scripts/build_protected_modules.py --verify --tool nuitka
"""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
CONFIG = Path(__file__).resolve().parent / "protected_modules.json"
BUILD_DIR = BACKEND / "build"


def _load_targets() -> list[str]:
    data = json.loads(CONFIG.read_text(encoding="utf-8"))
    return [item["module"] for item in data["targets"]]


def _tool_version(tool: str) -> str | None:
    """返回工具版本；不可用时返回 None（不抛异常，便于 --check 汇总）。"""
    try:
        if tool == "nuitka":
            out = subprocess.run(
                [sys.executable, "-m", "nuitka", "--version"],
                capture_output=True,
                text=True,
                timeout=60,
            )
        else:
            out = subprocess.run(
                [sys.executable, "-m", "pyarmor", "--version"],
                capture_output=True,
                text=True,
                timeout=60,
            )
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return None
    text = (out.stdout or out.stderr).strip().splitlines()
    return text[0] if text else "unknown"


def check() -> int:
    targets = _load_targets()
    print(f"Python {platform.python_version()} / {platform.platform()}")
    print(f"目标模块（{len(targets)}）：{', '.join(targets)}")
    available = False
    for tool in ("nuitka", "pyarmor"):
        version = _tool_version(tool)
        print(f"- {tool}: {version or '不可用（未安装或未授权）'}")
        available = available or version is not None
    if not available:
        print("没有可用的保护工具：请先安装 Nuitka，或配置 PyArmor 授权。")
        print("未安装时保持源码部署即可，不影响功能与安全边界。")
        return 1
    return 0


def _module_to_path(module: str) -> Path:
    return BACKEND / (module.replace(".", "/") + ".py")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build(tool: str) -> int:
    targets = _load_targets()
    version = _tool_version(tool)
    if version is None:
        print(f"{tool} 不可用：未安装或未授权。编译保护是可选项，源码部署仍然可用。")
        return 2

    output_dir = BUILD_DIR / f"protected-{tool}"
    output_dir.mkdir(parents=True, exist_ok=True)

    built: list[dict[str, object]] = []
    for module in targets:
        source = _module_to_path(module)
        if not source.exists():
            print(f"跳过不存在的模块：{module}")
            continue
        if tool == "nuitka":
            cmd = [
                sys.executable,
                "-m",
                "nuitka",
                "--module",
                str(source),
                f"--output-dir={output_dir}",
                "--no-pyi-file",
                "--remove-output",
                "--assume-yes-for-downloads",
            ]
        else:
            cmd = [
                sys.executable,
                "-m",
                "pyarmor",
                "gen",
                "-O",
                str(output_dir),
                str(source),
            ]
        print(f"[{tool}] {module} -> {output_dir}")
        result = subprocess.run(cmd, cwd=BACKEND)
        if result.returncode != 0:
            print(f"编译失败：{module}（退出码 {result.returncode}）。源码未被修改，可继续使用源码部署。")
            return result.returncode
        built.append(
            {
                "module": module,
                "source": str(source.relative_to(BACKEND)),
                "source_sha256": _sha256(source),
            }
        )

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "tool": tool,
        "tool_version": version,
        "python": platform.python_version(),
        "python_abi": getattr(sys, "abiflags", ""),
        "platform": platform.platform(),
        "machine": platform.machine(),
        "output_dir": str(output_dir.relative_to(BACKEND)),
        "targets": built,
        "rollback": "删除 build/protected-* 即可恢复源码运行；应用代码与迁移不依赖编译产物。",
    }
    BUILD_DIR.mkdir(parents=True, exist_ok=True)
    report_path = BUILD_DIR / "protection-report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"完成：{len(built)} 个模块；报告 {report_path}")
    return 0


def _probe_script() -> str:
    """在子进程里对目标模块做固定输入输出对照，避免同名模块污染当前进程。"""
    return r"""
import json, sys
from app.core import pagination, security

out = {
    "digest": security.token_digest("abc"),
    "equal": security.constant_time_equal("abc", "abc"),
    "equal_diff": security.constant_time_equal("abc", "abd"),
    "password_short": security.password_problem("short"),
    "password_ok": security.password_problem("a" * 12),
    "hash_verify": security.verify_password("pw-123456789", security.hash_password("pw-123456789")),
    "cursor_roundtrip": pagination.decode_cursor(pagination.encode_cursor({"id": 7})),
    "page_size": pagination.normalize_page_size(10_000),
}
print(json.dumps(out, ensure_ascii=False, sort_keys=True))
"""


def verify(tool: str) -> int:
    output_dir = BUILD_DIR / f"protected-{tool}"
    if not output_dir.exists():
        print(f"未找到编译产物：{output_dir}。请先执行 --tool {tool}。")
        return 2

    def run(path_prefix: str) -> dict:
        env_path = f"{path_prefix};{BACKEND}" if platform.system() == "Windows" else f"{path_prefix}:{BACKEND}"
        result = subprocess.run(
            [sys.executable, "-c", _probe_script()],
            cwd=BACKEND,
            env={"PYTHONPATH": env_path, "PATH": __import__("os").environ.get("PATH", "")},
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip()[-800:])
        return json.loads(result.stdout.strip().splitlines()[-1])

    try:
        source_result = run(str(BACKEND))
        built_result = run(str(output_dir))
    except (RuntimeError, ValueError) as exc:
        print(f"对照失败：{exc}")
        return 1

    if source_result != built_result:
        print("源码与编译产物行为不一致：")
        for key in sorted(set(source_result) | set(built_result)):
            if source_result.get(key) != built_result.get(key):
                print(f"  {key}: 源码={source_result.get(key)!r} 编译={built_result.get(key)!r}")
        return 1
    print(f"对照通过：{len(source_result)} 项输入输出与源码一致（{tool}）。")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="可选的 Python 编译保护构建")
    parser.add_argument("--check", action="store_true", help="检查工具可用性")
    parser.add_argument("--tool", choices=["nuitka", "pyarmor"], default="nuitka")
    parser.add_argument("--verify", action="store_true", help="对照编译产物与源码行为")
    args = parser.parse_args()

    if args.check:
        return check()
    if args.verify:
        return verify(args.tool)
    return build(args.tool)


if __name__ == "__main__":
    raise SystemExit(main())
