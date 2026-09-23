# -*- coding: utf-8 -*-
"""Quote Agent 桌面版入口：把内嵌的 FastAPI 服务包成一个原生窗口。

同一个应用，两种启动方式（不产生第二套代码）：
    python server.py    纯 Web 模式 —— 浏览器访问 http://127.0.0.1:8623
    python main.py      桌面模式   —— 原生窗口；无 GUI 环境时自动回落默认浏览器

启动顺序（用户反馈"启动好慢"，改成先出界面再做重活）：
    1. 起服务线程 —— app 传「模块:属性」字符串，让 uvicorn 在**它自己的线程**里
       完成 server 的重量级导入（langchain / fastapi / 记忆库…）；
    2. 主线程立刻开窗，先显示内联的加载页（不依赖服务，纯 HTML）；
    3. 后台线程等服务真正监听后，把窗口切到主界面 URL。
    —— 这样"什么都没显示"的时间只剩 PyInstaller 单文件解包（那一步在 Python 起来之前，
       任何代码都插不进去；想根治要改成 onedir 打包）。

打包：python build.py  →  dist/Quote.exe（Windows）/ dist/Quote（macOS、Linux）
"""
from __future__ import annotations

import socket
import sys
import threading
import time
import webbrowser

import config

WINDOW_TITLE = "Quote Agent · 跨境选品与上架工作台"
WINDOW_SIZE = (1320, 860)
WINDOW_MIN = (960, 640)
READY_TIMEOUT_S = 120.0     # 重量级导入现在在服务线程里，给足时间

_STARTED_AT = time.monotonic()


def _say(message: str) -> None:
    """窗口模式下 stdout 可能为 None，打印失败不应影响启动。"""
    try:
        print(message, flush=True)
    except Exception:  # noqa: BLE001 - 无控制台时静默
        pass


def _stamp(message: str) -> None:
    """启动日志带耗时前缀，方便下次追查"到底慢在哪一段"。"""
    _say(f"[{time.monotonic() - _STARTED_AT:5.2f}s] {message}")


def _redirect_output_when_headless() -> None:
    """打包成窗口程序后没有控制台：把输出落到 exe 旁的日志文件，便于排查。"""
    if not getattr(sys, "frozen", False) or sys.stdout is not None:
        return
    try:
        stream = open(config.BASE_DIR / "quote-agent.log", "a", encoding="utf-8", buffering=1)
        sys.stdout = stream
        sys.stderr = stream
    except Exception:  # noqa: BLE001 - 日志不可写则放弃，不影响运行
        pass


def _pick_port(preferred: int) -> int:
    """优先用配置端口；被占用时让系统分配一个空闲端口（避免与已开实例打架）。"""
    with socket.socket() as probe:
        try:
            probe.bind((config.HOST, preferred))
            return preferred
        except OSError:
            pass
    with socket.socket() as probe:
        probe.bind((config.HOST, 0))
        return int(probe.getsockname()[1])


def _wait_until_ready(port: int, timeout_s: float = READY_TIMEOUT_S) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        with socket.socket() as probe:
            probe.settimeout(0.3)
            if probe.connect_ex((config.HOST, port)) == 0:
                return True
        time.sleep(0.05)
    return False


def _serve(app_spec: str, host: str, port: int) -> None:
    """服务线程：uvicorn 与 app 的导入都发生在这里，不占用主线程的开窗时间。"""
    import uvicorn
    uvicorn.run(app_spec, host=host, port=port, log_level="warning")


_LOADING_HTML = """<!doctype html><html lang="zh"><head><meta charset="utf-8">
<title>Quote Agent · 启动中</title><style>
  :root{--mist:#e9edee;--ink:#0d1215;--ink2:#5c6669;--ink3:#8b9599;--pine:#2c6b60;--hair:rgba(13,18,21,.09)}
  *{margin:0;padding:0;box-sizing:border-box}
  html,body{height:100%}
  body{
    background:
      radial-gradient(760px 520px at 78% -8%,rgba(40,64,110,.16),transparent 66%),
      radial-gradient(640px 460px at 4% 12%,rgba(44,107,96,.13),transparent 64%),
      var(--mist);
    color:var(--ink);display:grid;place-items:center;
    font-family:"Instrument Sans","PingFang SC","Microsoft YaHei",system-ui,-apple-system,sans-serif;
    -webkit-user-select:none;user-select:none;
  }
  .wrap{text-align:center;animation:rise .62s cubic-bezier(.2,.7,.2,1) both}
  /* 切页前先整页淡出（见下方 leave()）：旧页面的字若残留一帧，会叠在新界面上 */
  body{transition:opacity .2s ease}
  body.is-out{opacity:0}
  .mark{
    width:54px;height:54px;margin:0 auto 24px;display:grid;place-items:center;
    border:1px solid var(--hair);border-radius:16px;background:rgba(255,255,255,.74);
    box-shadow:inset 0 1px 0 rgba(255,255,255,.9),0 18px 40px -22px rgba(13,18,21,.45);
  }
  .mark svg{width:20px;height:20px;stroke:var(--pine);fill:none;stroke-width:1.1}
  h1{font-size:14px;font-weight:500;letter-spacing:.2em;text-transform:uppercase}
  .sub{margin-top:12px;font-size:12px;line-height:1.95;color:var(--ink2);font-weight:300}
  .sub em{font-style:normal;color:var(--ink3)}
  .bar{margin:26px auto 0;width:196px;height:2px;border-radius:2px;background:rgba(13,18,21,.08);overflow:hidden}
  .bar i{display:block;height:100%;width:38%;border-radius:2px;
    background:linear-gradient(90deg,transparent,var(--pine),transparent);
    animation:sweep 1.15s cubic-bezier(.65,0,.35,1) infinite}
  .url{margin-top:15px;font-family:"DM Mono",ui-monospace,Consolas,monospace;font-size:10.5px;color:#a9b3b6}
  @keyframes sweep{0%{transform:translateX(-100%)}100%{transform:translateX(363%)}}
  @keyframes rise{from{opacity:0;transform:translateY(8px)}to{opacity:1;transform:none}}
</style></head><body><div class="wrap">
  <div class="mark"><svg viewBox="0 0 12 12"><path d="M6 1l5 5-5 5-5-5z"/></svg></div>
  <h1>Quote Agent</h1>
  <p class="sub">正在启动本地服务…<br><em>首次启动需要解包，请稍候</em></p>
  <div class="bar"><i></i></div>
  <p class="url" id="u">__URL__</p>
</div><script>
  // 兜底：万一主线程的 load_url 没生效，加载页自己轮询到服务起来就跳过去。
  // 用 mode:"no-cors" —— 加载页来自内联 HTML（源为 opaque），普通跨源 fetch 会被 CORS 拦下；
  // no-cors 下拿到的是 opaque 响应，但"能拿到"本身就说明服务已就绪。
  var target = "__URL__", n = 0, gone = false;
  // 先淡出再跳转：WebView2 从内联页切到真实 URL 时，旧文档可能有一帧还在合成里，
  // 看起来像"加载页的字和主界面的字叠在一起"。淡出后这一帧是空的，就不会看到重叠。
  function leave() {
    if (gone) return; gone = true;
    document.body.classList.add("is-out");
    setTimeout(function () { location.replace(target); }, 220);
  }
  var t = setInterval(function () {
    n++;
    fetch(target, { mode: "no-cors", cache: "no-store" }).then(leave).catch(function () {});
    if (n > 1200) clearInterval(t);
  }, 400);
</script></body></html>"""

_ERROR_HTML = """<!doctype html><html lang="zh"><head><meta charset="utf-8">
<title>Quote Agent · 启动失败</title><style>
  body{margin:0;height:100vh;display:grid;place-items:center;background:#e9edee;color:#2b3437;
    font-family:"PingFang SC","Microsoft YaHei",system-ui,sans-serif;text-align:center}
  h1{font-size:15px;font-weight:500;letter-spacing:.04em}
  p{margin-top:12px;font-size:12.5px;line-height:1.9;color:#5c6669;font-weight:300}
  code{font-family:Consolas,monospace;font-size:12px;color:#2c6b60}
</style></head><body><div>
  <h1>本地服务没有起来</h1>
  <p>请查看程序目录下的 <code>quote-agent.log</code>，或换一个端口重试。<br>服务地址：<code>__URL__</code></p>
</div></body></html>"""


def _loading_html(url: str) -> str:
    return _LOADING_HTML.replace("__URL__", url)


def _open_browser(url: str) -> int:
    """没有可用 GUI 后端时的回落：等服务起来再交给默认浏览器。"""
    if not _wait_until_ready(_port_of(url)):
        _stamp("服务启动超时，请检查端口是否被占用。")
        return 1
    _stamp(f"Quote Agent 就绪 → {url}")
    webbrowser.open(url)
    _hold()
    return 0


def _port_of(url: str) -> int:
    return int(url.rstrip("/").rsplit(":", 1)[1])


def _hold() -> None:
    """浏览器模式下服务需常驻，直到进程被结束。"""
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        pass


def main() -> int:
    _redirect_output_when_headless()
    _stamp("进程已启动")

    port = _pick_port(int(config.PORT))
    url = f"http://{config.HOST}:{port}/"

    server = threading.Thread(
        target=_serve,
        args=("server:app", config.HOST, port),   # 字符串形式：让重量级导入留在服务线程里
        daemon=True,
    )
    server.start()
    _stamp(f"服务线程已启动（端口 {port}），等待就绪…")

    try:
        import webview
    except Exception as exc:  # noqa: BLE001 - 缺 GUI 依赖 → 回落浏览器
        _say(f"未能加载窗口组件（{exc.__class__.__name__}: {exc}），改用默认浏览器打开。")
        return _open_browser(url)

    try:
        window = webview.create_window(
            WINDOW_TITLE, html=_loading_html(url),
            width=WINDOW_SIZE[0], height=WINDOW_SIZE[1], min_size=WINDOW_MIN,
        )
    except Exception as exc:  # noqa: BLE001 - 建窗失败 → 回落浏览器
        _say(f"未能创建原生窗口（{exc.__class__.__name__}: {exc}），改用默认浏览器打开。")
        return _open_browser(url)

    _stamp("窗口已打开（加载页），后台等待服务就绪")

    def _swap() -> None:
        ok = _wait_until_ready(port)
        _stamp(("Quote Agent 就绪 → " + url) if ok else "服务启动超时，请查看 quote-agent.log。")
        try:
            if not ok:
                window.load_html(_ERROR_HTML.replace("__URL__", url))
                return
            # 让加载页先淡出，再切主界面：避免旧文档残留一帧、和主界面的字叠在一起。
            try:
                window.evaluate_js("document.body.classList.add('is-out')")
                time.sleep(0.24)
            except Exception:  # noqa: BLE001 - 淡出只是观感，失败就直接切
                pass
            window.load_url(url)
        except Exception as exc:  # noqa: BLE001 - 切页失败不应让进程倒下
            _say(f"切换到主界面失败（{exc.__class__.__name__}: {exc}）。")

    threading.Thread(target=_swap, daemon=True).start()

    try:
        # private_mode=False + 项目内的 storage_path：让 WebView2 的 HTTP 缓存**跨启动保留**。
        # 默认的临时 profile 每次启动都是空的，外网字体/静态资源要重新下一个来回 → 这正是
        # "冷启动慢"的一部分。缓存目录落在项目目录（D 盘），不写 C 盘。
        webview.start(
            private_mode=False,
            storage_path=str(config.BASE_DIR / ".webview"),
        )      # 阻塞至窗口关闭
    except Exception as exc:  # noqa: BLE001 - 窗口运行期异常 → 回落浏览器
        _say(f"窗口运行失败（{exc.__class__.__name__}: {exc}），改用默认浏览器打开。")
        webbrowser.open(url)
        _hold()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
