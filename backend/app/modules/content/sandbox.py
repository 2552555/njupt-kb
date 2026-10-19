"""解析与文档转换子进程的沙箱计划（补充规范 S04；wenjian.md §解析隔离）。

威胁：恶意 PDF/Office 文档在转换器里触发解析漏洞、读取宿主文件、访问内网服务
（SSRF/横向移动）或耗尽 CPU/内存/磁盘。

控制分三层，按环境能力逐级降级，但**降级必须可见**：

1. ``bubblewrap``：独立挂载命名空间（根只读、仅工作目录可写）+ 网络命名空间隔离
   + PID 隔离 + 父进程退出即清理。Linux 上首选。
2. ``unshare``：网络与 PID 命名空间隔离（挂载空间不隔离，需 root 或可用 user namespace）。
3. 资源限制：``setrlimit`` 约束 CPU 时间、地址空间、单文件大小与进程数；
   **不做网络隔离**，``SandboxPlan.network_isolated=False`` 会如实记录。

``PARSE_SANDBOX_MODE``：
- ``off``：不包装（仅本地调试；生产不应使用）；
- ``best-effort``（默认）：能隔离就隔离，不能则记录 ``backend`` 与原因，继续转换；
- ``required``：无法实现网络隔离时**拒绝转换**，由调用方记录派生失败。

本模块不假装在 Windows 上完成了网络隔离：那里只能设置超时与输出上限，
``network_isolated`` 始终为 False，需要网络隔离的部署应使用 Linux 容器 + bubblewrap。
"""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from app.config import settings

__all__ = [
    "SandboxPlan",
    "SandboxUnavailable",
    "plan_command",
    "run_sandboxed",
]


class SandboxUnavailable(RuntimeError):
    """``required`` 模式下无法满足隔离要求。"""


@dataclass(frozen=True)
class SandboxPlan:
    argv: list[str]
    env: dict[str, str]
    network_isolated: bool
    filesystem_isolated: bool
    mode: str
    backend: str
    reason: str | None = None
    # 资源上限（仅在 POSIX 上通过 setrlimit 生效）。
    cpu_seconds: int = 0
    memory_bytes: int = 0
    max_file_bytes: int = 0
    extra: dict = field(default_factory=dict)


def _base_env(workdir: Path) -> dict[str, str]:
    """最小环境：不给转换器继承令牌、代理或数据库连接串。"""
    return {
        "HOME": str(workdir),
        "TMPDIR": str(workdir),
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "LANG": "C.UTF-8",
        # 禁用可能触发外部访问的代理变量；即使未隔离网络也不主动引导出网。
        "http_proxy": "",
        "https_proxy": "",
        "HTTP_PROXY": "",
        "HTTPS_PROXY": "",
        "no_proxy": "*",
    }


@lru_cache(maxsize=4)
def _probe(argv: tuple[str, ...]) -> bool:
    """探测沙箱能力；结果缓存，避免每次转换都付出一次进程启动成本。"""
    try:
        completed = subprocess.run(
            list(argv),
            capture_output=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return completed.returncode == 0


def _bwrap_plan(bwrap: str, argv: list[str], workdir: Path) -> SandboxPlan:
    wrapped = [
        bwrap,
        "--unshare-net",
        "--unshare-pid",
        "--die-with-parent",
        "--new-session",
        "--ro-bind",
        "/",
        "/",
        "--dev",
        "/dev",
        "--proc",
        "/proc",
        "--tmpfs",
        "/tmp",
        "--bind",
        str(workdir),
        str(workdir),
        "--chdir",
        str(workdir),
        "--",
        *argv,
    ]
    return SandboxPlan(
        argv=wrapped,
        env=_base_env(workdir),
        network_isolated=True,
        filesystem_isolated=True,
        mode=settings.PARSE_SANDBOX_MODE,
        backend="bwrap",
    )


def _unshare_plan(unshare: str, argv: list[str], workdir: Path) -> SandboxPlan:
    wrapped = [unshare, "--net", "--pid", "--fork", "--", *argv]
    return SandboxPlan(
        argv=wrapped,
        env=_base_env(workdir),
        network_isolated=True,
        filesystem_isolated=False,
        mode=settings.PARSE_SANDBOX_MODE,
        backend="unshare",
        reason="挂载空间未隔离：转换器仍可读取宿主文件，仅阻断网络与新建进程可见性",
    )


def plan_command(argv: list[str], *, workdir: Path) -> SandboxPlan:
    """按环境能力生成沙箱命令；``required`` 模式无法隔离时抛 `SandboxUnavailable`。"""
    mode = settings.PARSE_SANDBOX_MODE
    limits = {
        "cpu_seconds": int(settings.DERIVATIVE_TIMEOUT_SECONDS) + 15,
        "memory_bytes": int(settings.PARSE_SANDBOX_MEMORY_MB) * 1024 * 1024,
        "max_file_bytes": int(settings.PARSE_SANDBOX_MAX_OUTPUT_MB) * 1024 * 1024,
    }

    if mode == "off":
        return SandboxPlan(
            argv=list(argv),
            env=_base_env(workdir),
            network_isolated=False,
            filesystem_isolated=False,
            mode=mode,
            backend="none",
            reason="沙箱已关闭（仅用于本地调试）",
            **limits,
        )

    if platform.system() == "Linux":
        bwrap = shutil.which("bwrap")
        if bwrap and _probe((bwrap, "--unshare-net", "--ro-bind", "/", "/", "true")):
            plan = _bwrap_plan(bwrap, argv, workdir)
            return SandboxPlan(**{**plan.__dict__, **limits})
        unshare = shutil.which("unshare")
        if unshare and _probe((unshare, "--net", "true")):
            plan = _unshare_plan(unshare, argv, workdir)
            return SandboxPlan(**{**plan.__dict__, **limits})

    reason = (
        "当前平台没有可用的网络隔离（bubblewrap/unshare 均不可用）"
        if platform.system() != "Linux"
        else "bubblewrap 与 unshare 均不可用或缺少权限"
    )
    if mode == "required":
        raise SandboxUnavailable(f"解析沙箱不可用：{reason}")
    return SandboxPlan(
        argv=list(argv),
        env=_base_env(workdir),
        network_isolated=False,
        filesystem_isolated=False,
        mode=mode,
        backend="rlimit" if os.name == "posix" else "none",
        reason=reason,
        **limits,
    )


def _rlimit_preexec(plan: SandboxPlan):
    """POSIX 资源限制；Windows 返回 None（由超时与输出上限兜底）。"""
    if os.name != "posix":
        return None
    import resource

    def _apply() -> None:  # pragma: no cover - 子进程内执行
        if plan.cpu_seconds:
            resource.setrlimit(resource.RLIMIT_CPU, (plan.cpu_seconds, plan.cpu_seconds + 5))
        if plan.memory_bytes:
            resource.setrlimit(resource.RLIMIT_AS, (plan.memory_bytes, plan.memory_bytes))
        if plan.max_file_bytes:
            resource.setrlimit(
                resource.RLIMIT_FSIZE, (plan.max_file_bytes, plan.max_file_bytes)
            )
        try:
            resource.setrlimit(resource.RLIMIT_NPROC, (64, 64))
        except (ValueError, OSError):  # 某些容器不允许收紧
            pass

    return _apply


def run_sandboxed(
    argv: list[str], *, workdir: Path, timeout: int | None = None
) -> tuple[subprocess.CompletedProcess, SandboxPlan]:
    """在沙箱计划下运行命令；返回结果与实际生效的计划（便于记录隔离程度）。"""
    plan = plan_command(argv, workdir=workdir)
    completed = subprocess.run(
        plan.argv,
        capture_output=True,
        timeout=timeout or settings.DERIVATIVE_TIMEOUT_SECONDS,
        env=plan.env,
        cwd=str(workdir),
        preexec_fn=_rlimit_preexec(plan),
        check=False,
    )
    return completed, plan
