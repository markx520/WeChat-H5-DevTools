import os
import sys
import time
from pathlib import Path
from typing import Optional

try:
    import frida
except ImportError:
    frida = None

from ..utils.logger import log_info, log_warn, log_error, log_step
from .wechat_finder import WeChatFinder

class ProcessHooker:
    """微信进程注入管理器"""

    def __init__(self):
        self.finder = WeChatFinder()

    def launch_and_hook(
        self,
        custom_wechat_path: Optional[str] = None,
        cdp_port: int = 9222,
        backend_port: int = 62000,
        enable_proxy: bool = True,
        proxy_port: int = 8899
    ) -> bool:
        if not frida:
            log_error("未检测到 frida 依赖，请先执行: pip install frida")
            return False

        hook_js_path = Path(__file__).parent / "scripts" / "hook_inapp.js"
        with open(hook_js_path, "r", encoding="utf-8") as f:
            hook_template = f.read()

        # 动态参数矩阵渲染
        hook_code = hook_template.replace("{{CDP_PORT}}", str(cdp_port)) \
                                 .replace("{{BACKEND_PORT}}", str(backend_port)) \
                                 .replace("{{PROXY_PORT}}", str(proxy_port)) \
                                 .replace("{{ENABLE_PROXY}}", "true" if enable_proxy else "false")

        import json
        import psutil
        import threading
        import urllib.request

        cdp_engine_instance = None

        # 步骤 0.1: 检查并自动拉起后台 CDP 协议解析引擎
        def ensure_cdp_backend_daemon():
            nonlocal cdp_engine_instance
            import socket
            s = socket.socket()
            try:
                s.connect(("127.0.0.1", backend_port))
                s.close()
            except Exception:
                try:
                    from .kernel_cdp import KernelCDPEngine
                    import asyncio
                    cdp_engine_instance = KernelCDPEngine(
                        debug_port=9421,
                        cdp_port=backend_port,
                        public_cdp_port=cdp_port,
                        auto_hook=False
                    )
                    def run_cdp():
                        asyncio.run(cdp_engine_instance.run())
                    cdp_th = threading.Thread(target=run_cdp, daemon=True)
                    cdp_th.start()
                    time.sleep(0.5)
                    log_info(f"已在后台激活 CDP 协议解析引擎 (http://127.0.0.1:{backend_port})！")
                except Exception as e:
                    log_warn(f"后台 CDP 协议引擎拉起告警: {e}")

        ensure_cdp_backend_daemon()

        # 步骤 0.2: 检查并自动拉起后台透明注入网关 (若开启)
        def ensure_proxy_daemon():
            import socket
            s = socket.socket()
            try:
                s.connect(("127.0.0.1", proxy_port))
                s.close()
            except Exception:
                try:
                    from .proxy_injector import ProxyInjector
                    proxy = ProxyInjector(port=proxy_port)
                    pth = threading.Thread(target=proxy.start_proxy, daemon=True)
                    pth.start()
                    time.sleep(0.5)
                    log_info(f"已在后台激活透明注入网关 (http://127.0.0.1:{proxy_port})！")
                except Exception as e:
                    log_warn(f"后台注入网关拉起告警: {e}")

        if enable_proxy:
            ensure_proxy_daemon()

        # CDP 端口异步自动探活器
        probed_lock = threading.Lock()
        probed_state = {"done": False, "running": True}

        def start_cdp_probe():
            def probe_worker():
                probe_url = f"http://127.0.0.1:{cdp_port}/json/version"
                for _ in range(40):
                    with probed_lock:
                        if probed_state["done"] or not probed_state["running"]:
                            return
                    try:
                        req = urllib.request.Request(probe_url, headers={"User-Agent": "WeChatDevTools/1.0"})
                        with urllib.request.urlopen(req, timeout=1.0) as resp:
                            if resp.status == 200:
                                res_data = json.loads(resp.read().decode("utf-8"))
                                with probed_lock:
                                    if not probed_state["done"] and probed_state["running"]:
                                        probed_state["done"] = True
                                        b_name = res_data.get("Browser", "Chromium")
                                        w_ver = res_data.get("WebKit-Version", "")
                                        log_step("=" * 60)
                                        log_step(f"[CDP 激活成功] 微信内置网页远程调试端口已就绪: http://127.0.0.1:{cdp_port}")
                                        log_info(f"  • 内核引擎: {b_name} {w_ver}")
                                        log_info(f"  • 直连调试: 打开 Edge/Chrome 输入 edge://inspect (已自动发现 localhost:{cdp_port})")
                                        log_step("=" * 60)
                                return
                    except Exception:
                        pass
                    for _ in range(10):
                        if not probed_state["running"]:
                            return
                        time.sleep(0.1)

            th = threading.Thread(target=probe_worker, daemon=True)
            th.start()

        def handle_frida_message(message, pid, name):
            if message.get("type") == "send":
                payload = message.get("payload", "")
                try:
                    data = json.loads(payload)
                    evt = data.get("event")
                    if evt == "process_injected":
                        is_master = data.get("is_browser_master", False)
                        p_num = data.get("cdp_port")
                        if is_master:
                            log_step(f"[{name}:{pid}] 成功拦截 Browser 管理进程，已注入远程 CDP 端口 -> http://127.0.0.1:{p_num}")
                            start_cdp_probe()
                        else:
                            log_info(f"[{name}:{pid}] 拦截渲染子进程 (Renderer)，已放行并注入沙箱隔离参数")
                    elif evt == "cdp_server_started":
                        p_num = data.get("port")
                        b_port = data.get("backend_port")
                        log_step(f"[{name}:{pid}] Frida 进程内自建 CDP 服务端已激活！")
                        log_info(f"  • 系统端口监听: http://127.0.0.1:{p_num} (WeChatAppEx.exe PID: {pid})")
                        log_info(f"  • 双向流中继端: 127.0.0.1:{b_port} (CDP Gateway Engine)")
                        start_cdp_probe()
                    elif evt == "cdp_server_error":
                        log_warn(f"[{name}:{pid}] 进程内 CDP 服务端监听告警: {data.get('error')}")
                    elif evt == "hook_installed":
                        log_info(f"[{name}:{pid}] 挂载 CreateProcessW 拦截引擎 ({data.get('arch')}) @ {data.get('address')}")
                    elif evt == "hook_error":
                        log_error(f"[{name}:{pid}] Hook 错误: {data.get('message')}")
                    else:
                        log_info(f"[{name}:{pid}] {payload}")
                except Exception:
                    log_info(f"[{name}:{pid}] {payload}")
            elif message.get("type") == "error":
                log_warn(f"[{name}:{pid}] Frida 告警: {message.get('description', '')}")

        running_weixin = [p for p in psutil.process_iter(['pid', 'name']) if p.info['name'] in ['WeChat.exe', 'Weixin.exe']]

        # 模式 1：热附加模式 (微信已在运行，免重新登录/无需杀主进程)
        if running_weixin:
            log_step(f"检测到微信主程序已在运行中 (发现 {len(running_weixin)} 个微信进程)！")
            log_info("正在执行高可靠多级级联热附加 (Weixin -> WeChatAppEx Master -> Renderers)...")

            sessions = {}
            lock = threading.RLock()

            def attach_target(pid: int, name: str) -> bool:
                with lock:
                    if pid in sessions:
                        return True
                try:
                    s = frida.attach(pid)
                    sc = s.create_script(hook_code)

                    def on_message(message, data, current_pid=pid, current_name=name):
                        handle_frida_message(message, current_pid, current_name)

                    sc.on("message", on_message)
                    sc.load()
                    with lock:
                        sessions[pid] = (s, sc, name)
                    log_info(f"成功注入调试通道 -> {name} (PID: {pid})")
                    return True
                except Exception as e:
                    return False

            # 步骤 1：必须先附加到 Weixin.exe 主进程！
            log_step("[1/3] 挂载微信主进程 CreateProcessW 拦截引擎...")
            attached_weixin = 0
            for p in running_weixin:
                if attach_target(p.info['pid'], p.info['name']):
                    attached_weixin += 1

            if attached_weixin == 0:
                log_error("热附加微信主程序失败，请尝试以管理员身份运行终端！")
                return False

            # 步骤 2：优雅终止旧的 WeChatAppEx 渲染器与 Broker，促使微信以新参数全新拉起
            running_renderers = [
                p for p in psutil.process_iter(['pid', 'name'])
                if (p.info.get('name') or '').lower() in ['wechatappex.exe', 'weixinext.exe']
            ]
            if running_renderers:
                log_step(f"[2/3] 正在重置 {len(running_renderers)} 个旧渲染器进程以装载最新调试参数...")
                for p in running_renderers:
                    try:
                        p.kill()
                    except Exception:
                        pass
                time.sleep(1.0)

            # 步骤 3：启动后台动态巡检线程，级联捕获新拉起的 WeChatAppEx Master 与 Renderers
            log_step("[3/3] 启动多级级联守护线程，自动挂载所有新衍生进程...")
            watcher_running = True

            def watcher_loop():
                while watcher_running:
                    try:
                        for p in psutil.process_iter(['pid', 'name']):
                            if not watcher_running:
                                break
                            p_name = p.info.get('name') or ''
                            if p_name.lower() in ['wechatappex.exe', 'weixinext.exe']:
                                pid = p.info['pid']
                                with lock:
                                    already_attached = (pid in sessions)
                                if not already_attached and watcher_running:
                                    attach_target(pid, p_name)
                    except Exception:
                        pass
                    for _ in range(10):
                        if not watcher_running:
                            break
                        time.sleep(0.1)

            watcher_th = threading.Thread(target=watcher_loop, daemon=True)
            watcher_th.start()

            # 初次扫描新生成的 WeChatAppEx
            time.sleep(1.5)

            log_step("[PASS] [微信全局推文与内置浏览器 Hook 级联挂载完毕！]")
            log_info(f"调试指引 (微信 4.x / 3.x 深度注入模式 | CDP 端口: {cdp_port}):")
            if enable_proxy:
                log_info(f"  • 【全自动公众号推文/H5注入】透明网关 (:{proxy_port}) 已激活，点击任意微信公众号文章右下角自动挂载绿色 vConsole！")
                log_info("  • 【内置浏览器真·物理 F12】在微信文章窗口直接按下键盘 F12 或 Ctrl+Shift+I 即可展开/收起控制台！")
            log_info(f"  • 【远程 CDP 协议直连】打开 Chrome / Edge 访问 chrome://inspect 或 edge://inspect (添加 localhost:{cdp_port})")
            log_info(f"  • 【CDP 端点探针】微信打开文章后可访问 http://127.0.0.1:{cdp_port}/json/version 获取当前 Targets")
            log_info("  • 【独立沙箱调试】运行 wx-h5 open <网址>，在自带满血 F12 + JSSDK Mock 的桌面沙箱中秒开调试。")
            log_info("提示: 保持本终端运行即可持续生效，按 Ctrl + C 可随时卸载退出。")

            try:
                while True:
                    time.sleep(1)
            except KeyboardInterrupt:
                watcher_running = False
                with probed_lock:
                    probed_state["running"] = False
                log_warn("已收到中断信号，正在卸载 Hook 并退出...")
                try:
                    watcher_th.join(timeout=0.5)
                except Exception:
                    pass
                if cdp_engine_instance:
                    try:
                        cdp_engine_instance.stop()
                    except Exception:
                        pass
                with lock:
                    for pid, (s, sc, n) in list(sessions.items()):
                        try:
                            s.detach()
                        except Exception:
                            pass
                    sessions.clear()
                return True

        # 模式 2：冷启动模式 (微信未运行，自动拉起并注入)
        if custom_wechat_path:
            p = Path(custom_wechat_path)
            if p.is_dir():
                found = None
                for exe in ["Weixin.exe", "WeChat.exe"]:
                    if (p / exe).is_file():
                        found = p / exe
                        break
                    if (p / "Weixin" / exe).is_file():
                        found = p / "Weixin" / exe
                        break
                wechat_exe = found
            elif p.is_file():
                wechat_exe = p
            else:
                wechat_exe = None
        else:
            wechat_exe = self.finder.get_wechat_path()
        if not wechat_exe or not wechat_exe.exists():
            log_error("未能自动定位到微信可执行文件，请使用 --path 参数指定微信路径 (如: Weixin.exe)")
            return False

        log_info(f"匹配到微信主执行文件: {wechat_exe}")
        log_step("正在通过 Frida 调试引擎 Spawn 启动微信主进程...")

        try:
            device = frida.get_local_device()
            pid = device.spawn(str(wechat_exe))
            log_info(f"已创建微信挂起进程 PID: {pid}")

            session = device.attach(pid)
            script = session.create_script(hook_code)

            def on_message(message, data):
                handle_frida_message(message, pid, "Weixin.exe")

            script.on("message", on_message)
            script.load()

            log_step("恢复微信进程执行...")
            device.resume(pid)

            log_step("[PASS] [微信全局推文与内置浏览器 Hook 挂载完毕！]")
            log_info(f"调试指引 (微信 4.x / 3.x 深度注入模式 | CDP 端口: {cdp_port}):")
            if enable_proxy:
                log_info(f"  • 【全自动公众号推文/H5注入】透明网关 (:{proxy_port}) 已激活，微信内打开文章自动注入绿色 vConsole！")
                log_info("  • 【内置浏览器真·物理 F12】在微信文章窗口直接按下键盘 F12 或 Ctrl+Shift+I 即可展开/收起控制台！")
            log_info(f"  • 【远程 CDP 协议直连】打开 Chrome / Edge 访问 chrome://inspect 或 edge://inspect (添加 localhost:{cdp_port})")
            log_info("提示: 保持本终端运行即可生效，按 Ctrl + C 可随时退出挂载。")

            try:
                while True:
                    time.sleep(1)
            except KeyboardInterrupt:
                log_warn("已收到中断信号，正在卸载 Hook 并退出...")
                if cdp_engine_instance:
                    try:
                        cdp_engine_instance.stop()
                    except Exception:
                        pass
                session.detach()
                return True

        except Exception as e:
            log_error(f"注入过程中发生异常: {e}")
            return False
