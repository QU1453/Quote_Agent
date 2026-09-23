# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置：把 Quote 打成单文件可执行程序。

请通过 `python build.py` 调用（它会做环境检查与产物提示）。

说明：
- PyInstaller 不支持交叉编译 —— Windows 的 .exe 必须在 Windows 上执行本配置生成；
  在 macOS / Linux 上执行则得到对应平台的可执行文件。
- config.RESOURCE_DIR 在单文件模式下指向解包临时目录，因此 ui/ 会随包释放；
  config.BASE_DIR 指向可执行文件所在目录，.env 与日志都落在程序旁边。
"""
from pathlib import Path
import sys

from PyInstaller.utils.hooks import collect_submodules

ROOT = Path(SPECPATH)

# conda / conda-venv 下 OpenSSL 的两个 DLL 只躺在 <base>\Library\bin，该目录**不一定在 PATH 上**；
# PyInstaller 靠 PATH 找依赖 DLL，收不到就会在冻结后抛
#   ImportError: DLL load failed while importing _ssl: 找不到指定的模块
# 于是显式带上（找不到就跳过，纯 pip 环境不受影响）。
_SSL_DLLS = ("libssl-3-x64.dll", "libcrypto-3-x64.dll")
binaries = []
for _name in _SSL_DLLS:
    for _dir in (Path(sys.prefix), Path(sys.base_prefix) / "Library" / "bin",
                 Path(sys.base_prefix) / "DLLs", Path(sys.base_prefix)):
        _path = _dir / _name
        if _path.exists():
            binaries.append((str(_path), "."))
            break

hiddenimports = [
    "dotenv",
    # main.py 现在把 app 以「"server:app"」字符串交给 uvicorn（为了把重量级导入挪到服务线程，
    # 让窗口能先开出来），静态分析看不到 server 了 —— 必须显式声明，否则冻结后
    # 会 ModuleNotFoundError: server。server.py 里对 agents/memory/... 的导入是静态的，跟着它走即可。
    "server",
    # uvicorn 的循环 / 协议 / 生命周期实现都是按字符串动态挑选的，静态分析扫不到
    "uvicorn.logging",
    "uvicorn.loops.auto",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.lifespan.on",
    "webview",
]

# 这些库大量使用动态导入，显式收集子模块；未安装时跳过
# （缺少 langgraph 时应用会退化为「本地规则兜底模式」，仍然可用）
for _pkg in ("langchain_openai", "langgraph", "langchain_core"):
    try:
        hiddenimports += collect_submodules(_pkg)
    except Exception:  # noqa: BLE001 - 收集失败不影响其它模块打包
        pass

a = Analysis(
    [str(ROOT / "main.py")],
    pathex=[str(ROOT)],
    binaries=binaries,
    datas=[
        (str(ROOT / "ui"), "ui"),                 # 前端资源（只读，从 _MEIPASS 读取）
        (str(ROOT / ".env.example"), "."),        # 配置模板，首次运行可照抄
    ],
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "pytest", "_pytest", "matplotlib"],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="Quote",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,                 # 未安装 UPX 时不压缩，避免构建告警
    # 单文件模式默认把内容解包到 %TEMP%（在 C 盘，每次启动约 35 MB）——与本项目
    # "绝不向 C 盘写入"的铁律冲突。改成相对路径 "."：解包到「启动时的工作目录」，
    # 双击启动时 CWD 就是 exe 所在目录（dist/，在 D 盘）。
    runtime_tmpdir=".",
    console=False,             # 窗口程序：无控制台，运行日志写到程序旁的 quote-agent.log
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
