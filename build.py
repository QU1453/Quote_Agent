# -*- coding: utf-8 -*-
"""一键打包 Quote 单文件可执行程序。

用法：
    python -m pip install -r requirements.txt pyinstaller
    python build.py

产物（在当前平台构建，PyInstaller 不支持交叉编译）：
    Windows  dist/Quote.exe
    macOS    dist/Quote
    Linux    dist/Quote

打包后仍是同一个应用：双击即启动内嵌服务并打开原生窗口，
设置面板里保存的密钥 / 模型 / 预算会写到可执行文件旁边的 .env。
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SPEC = ROOT / "Quote.spec"

# PyInstaller 默认把缓存/解包临时文件写到 %LOCALAPPDATA%\pyinstaller；
# 项目铁律禁止读写 C 盘，这里统一重定向到项目内 .tmp\（已被 .gitignore 拦截）。
_TMP = ROOT / ".tmp"
(_TMP / "temp").mkdir(parents=True, exist_ok=True)
os.environ.setdefault("PYINSTALLER_CONFIG_DIR", str(_TMP / "pyinstaller"))
os.environ.setdefault("TEMP", str(_TMP / "temp"))
os.environ.setdefault("TMP", str(_TMP / "temp"))


def _human(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} GB"


def main() -> int:
    if shutil.which("pyinstaller") is None and not _has_module("PyInstaller"):
        print("缺少 PyInstaller。请先执行：python -m pip install pyinstaller")
        return 1

    print(f"开始打包（{sys.platform}）→ {SPEC.name}")
    result = subprocess.run(
        [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", str(SPEC)],
        cwd=str(ROOT),
    )
    if result.returncode != 0:
        print(f"\n打包失败（退出码 {result.returncode}）。")
        print("排查建议：先结束所有残留进程，再执行 python build.py --clean 重试。")
        return result.returncode

    artifact = ROOT / "dist" / ("Quote.exe" if sys.platform.startswith("win") else "Quote")
    if not artifact.exists():
        print(f"\n构建完成但未找到产物：{artifact}")
        return 1

    print(f"\n打包完成 → {artifact}")
    print(f"文件大小：{_human(artifact.stat().st_size)}")
    print("双击即可运行；密钥等设置会保存在可执行文件同目录的 .env。")
    return 0


def _has_module(name: str) -> bool:
    try:
        __import__(name)
        return True
    except ImportError:
        return False


if __name__ == "__main__":
    raise SystemExit(main())
