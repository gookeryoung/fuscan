"""启动期导入隔离测试：验证重型依赖不在 import 期被加载。

用 subprocess 隔离每个断言（同一进程内一旦加载无法卸载），确保 CLI/GUI 启动期
不加载非必需重型依赖（lxml / orjson / extractors / scanner 链），对应
``docs/load-performance-plan.md`` 的 P1-P5 优化项。

标记 ``slow``：subprocess 启动开销较大，不纳入默认测试集，由 ``make cov`` 与
``-m slow`` 显式触发。
"""

from __future__ import annotations

import subprocess
import sys
import textwrap

import pytest


def _assert_modules_absent(import_stmt: str, absent_modules: tuple[str, ...]) -> None:
    """在子进程执行 ``import_stmt``，断言 ``absent_modules`` 均未被加载。

    :param import_stmt: 待执行的 import 语句（传给 ``python -c``）
    :param absent_modules: 不应被加载的模块名（精确匹配 ``sys.modules`` 键）
    """
    code = textwrap.dedent(
        f"""
        {import_stmt}
        import sys
        loaded = [m for m in {absent_modules!r} if m in sys.modules]
        if loaded:
            print(f"意外加载: {{loaded}}", file=sys.stderr)
            sys.exit(1)
        """
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, (
        f"断言失败：{import_stmt} 不应触发 {absent_modules}\nstdout: {result.stdout}\nstderr: {result.stderr}"
    )


@pytest.mark.slow
def test_import_scanner_no_heavy_deps() -> None:
    """``import fuscan.scanner`` 不触发 lxml / orjson / fuscan.extractors。

    对应 P1（_odf_xml.py lxml 懒加载）+ P2（_helpers.py extractor 延迟导入）+
    P3（manifest.py orjson 惰性）：scanner 子包顶层不应拉起提取器注册链与 C 扩展。
    """
    _assert_modules_absent(
        "import fuscan.scanner",
        ("lxml", "orjson", "fuscan.extractors"),
    )


@pytest.mark.slow
def test_import_cli_no_heavy_deps() -> None:
    """``import fuscan.cli`` 不触发 lxml / orjson / scanner 链 / extractors。

    对应 P3（orjson 惰性）+ P4（CLI 子命令级延迟导入）：``fuscan version`` /
    ``fuscan gui`` / ``fuscan cache`` 等子命令无需加载 scanner 链与提取器注册链。
    """
    _assert_modules_absent(
        "import fuscan.cli",
        ("lxml", "orjson", "fuscan.scanner.scanner", "fuscan.extractors"),
    )


@pytest.mark.slow
def test_import_manifest_no_orjson() -> None:
    """``import fuscan.scanner.manifest`` 不触发 orjson。

    对应 P3：``_get_orjson`` 惰性加载，模块级不执行 ``import orjson``。
    """
    _assert_modules_absent("import fuscan.scanner.manifest", ("orjson",))


@pytest.mark.slow
def test_import_whitelist_no_orjson() -> None:
    """``import fuscan.rules.whitelist`` 不触发 orjson。

    对应 P3：whitelist 模块级不执行 ``import orjson``，首次序列化时才加载。
    """
    _assert_modules_absent("import fuscan.rules.whitelist", ("orjson",))


@pytest.mark.slow
def test_import_controllers_app_no_scanner_chain() -> None:
    """``from fuscan.gui.controllers import AppController`` 不触发 scanner 链。

    对应 P5：``controllers/__init__.py`` 惰性 ``__getattr__`` +
    ``app_controller.py`` 延迟 ``FileMonitorController`` 导入，使 ``AppController``
    类定义不拉起 ``fuscan.scanner.scanner``（file_monitor_controller 顶层依赖）。
    """
    _assert_modules_absent(
        "from fuscan.gui.controllers import AppController",
        ("fuscan.scanner.scanner", "fuscan.extractors"),
    )


@pytest.mark.slow
def test_import_app_no_orjson() -> None:
    """``import fuscan.app`` 不触发 orjson（GUI 入口启动期不加载 C 扩展）。

    对应 P3：app.py 顶层导入链不经过 manifest/whitelist 的 orjson 顶层导入。
    """
    _assert_modules_absent("import fuscan.app", ("orjson",))


@pytest.mark.slow
def test_fuscan_version_runs_without_scanner() -> None:
    """``fuscan version`` 子命令不加载 scanner 链（端到端验证 P4）。

    用 ``-c "from fuscan.cli import main; main(['version'])"`` 模拟 ``fuscan version``，
    断言执行后 ``fuscan.scanner.scanner`` 未被加载。
    """
    code = textwrap.dedent(
        """
        import sys
        from fuscan.cli import main
        try:
            main(["version"])
        except SystemExit:
            pass
        loaded = [m for m in ("fuscan.scanner.scanner", "fuscan.extractors", "lxml", "orjson") if m in sys.modules]
        if loaded:
            print(f"意外加载: {loaded}", file=sys.stderr)
            sys.exit(1)
        """
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, f"fuscan version 不应触发重型依赖\nstdout: {result.stdout}\nstderr: {result.stderr}"
