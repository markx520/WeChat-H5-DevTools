#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
微信 4.x 公众号文章独立 DevTools 大屏审查引擎 (Article DevTools Engine)
用途: 自动化监听微信公众号文章/内嵌 H5 渲染进程，执行主线程多 Tab 内存拓扑感知并桥接至 Chii DevTools
"""

import os
import sys
import time
import json
import shutil
import subprocess
import urllib.request
import webbrowser
from typing import Optional, Set, Dict, List, Any
import psutil

from ..utils.logger import log_info, log_warn, log_error, log_step, print_banner

JS_MULTI_TAB_INJECT = """
var flue = Process.findModuleByName("flue.dll");
if (flue) {
    // 适配 RadiumWMPF Build 25799 与 Build 25510 双版本
    var fromUtf8Ptr = flue.base.add(0x4aea160);
    var executeScriptPtr = flue.base.add(0x9192440);
    var fnObserver = flue.base.add(0x3799e40);

    var chiiCode = "(function(){try{if(window.__CHII_INJECTED__)return;window.__CHII_INJECTED__=true;window.ChiiServerUrl='http://127.0.0.1:8080/';var s=document.createElement('script');s.src='http://127.0.0.1:8080/target.js';(document.head||document.documentElement||document.body).appendChild(s);console.log('[DevTools] Attached: ' + window.location.href);}catch(e){console.error(e);}})();";
    var payloadBuf = Memory.allocUtf8String(chiiCode);
    var payloadLen = chiiCode.length;

    // 1. 动态挂钩 WebString::FromUTF8 拦截注入
    try {
        Interceptor.attach(fromUtf8Ptr, {
            onEnter: function(args) {
                var piecePtr = args[1];
                if (!piecePtr.isNull()) {
                    try {
                        var strPtr = piecePtr.readPointer();
                        var len = piecePtr.add(8).readU64().toInt32();
                        if (len > 300) {
                            var head = strPtr.readUtf8String(Math.min(len, 60));
                            if (head.indexOf("createAndAddFloatWindow") !== -1 || len === 6166) {
                                var orig = strPtr.readUtf8String(len);
                                var combined = chiiCode + "\\n" + orig;
                                var combBuf = Memory.allocUtf8String(combined);
                                piecePtr.writePointer(combBuf);
                                piecePtr.add(8).writeU64(combined.length);
                                send({event: "frames_injected", count: 1});
                            }
                        }
                    } catch(e) {}
                }
            }
        });
    } catch(e) {}

    // 2. 挂钩 RenderFrameObserver 页面生命周期
    try {
        Interceptor.attach(fnObserver, {
            onEnter: function(args) {
                send({event: "observer_event_called"});
            }
        });
    } catch(e) {}

    // 3. 扫描定位已加载的页面进行主动注入
    // 适配 Build 25799: 精准定位 Observer 虚表 0xbd7e250，Frame 链条为 obj -> +8(rf) -> +0x78(frame)
    var mainThreadId = null;
    Process.enumerateThreads().forEach(function(t) {
        if (t.name === "CrRendererMain") {
            mainThreadId = t.id;
        }
    });

    var targetFrames = [];
    var vtable250 = flue.base.add(0xbd7e250);
    Process.enumerateRanges('rw-').forEach(function(r) {
        if (targetFrames.length > 5 || r.size > 1024 * 1024 * 64) return;
        try {
            var res = Memory.scanSync(r.base, r.size, vtable250.toMatchPattern());
            for (var i = 0; i < res.length; i++) {
                var obj = res[i].address;
                try {
                    var rf = obj.add(8).readPointer();
                    if (!rf.isNull()) {
                        var frame = rf.add(0x78).readPointer();
                        if (!frame.isNull()) {
                            targetFrames.push(frame);
                        }
                    }
                } catch(e) {}
            }
        } catch(e) {}
    });

    // 兼容回退旧版本 vtable
    if (targetFrames.length === 0) {
        var legacy_vtables = [0xbd7e230, 0xbd88a70, 0xbdcc870, 0xbdcfb88, 0xbdcab88];
        legacy_vtables.forEach(function(vtOff) {
            var vtAddr = flue.base.add(vtOff);
            Process.enumerateRanges('rw-').forEach(function(r) {
                if (targetFrames.length > 5 || r.size > 1024 * 1024 * 64) return;
                try {
                    var res = Memory.scanSync(r.base, r.size, vtAddr.toMatchPattern());
                    for (var i = 0; i < res.length; i++) targetFrames.push(res[i].address);
                } catch(e) {}
            });
        });
    }

    send({event: "frames_detected", frames: targetFrames.map(function(f){return f.toString();})});

    if (mainThreadId && targetFrames.length > 0) {
        var executeScript = new NativeFunction(executeScriptPtr, 'pointer', ['pointer', 'pointer']);
        var fromUtf8 = new NativeFunction(fromUtf8Ptr, 'pointer', ['pointer', 'pointer']);

        var piece = Memory.alloc(16);
        piece.writePointer(payloadBuf);
        piece.add(8).writeU64(payloadLen);

        var webString = Memory.alloc(8);
        webString.writePointer(ptr(0));
        fromUtf8(webString, piece);

        var scriptSource = Memory.alloc(0x100);
        scriptSource.writeByteArray(new Array(0x100).fill(0));
        scriptSource.writePointer(webString.readPointer());

        Process.runOnThread(mainThreadId, function() {
            var injectedCount = 0;
            for (var i = 0; i < targetFrames.length; i++) {
                try {
                    executeScript(targetFrames[i], scriptSource);
                    injectedCount++;
                } catch(e) {}
            }
            if (injectedCount > 0) {
                send({event: "frames_injected", count: injectedCount});
            }
        });
    }
}
"""

HOOK_MASTER_JS = """
var cpsPtr = Process.getModuleByName("kernel32.dll").getExportByName("CreateProcessW");
var allocated = [];

if (cpsPtr) {
    Interceptor.attach(cpsPtr, {
        onEnter: function(args) {
            this.cmdPtr = args[1];
            if (!this.cmdPtr.isNull()) {
                var cmd = this.cmdPtr.readUtf16String();
                if (cmd && (cmd.toLowerCase().indexOf("wechatappex.exe") !== -1 || cmd.toLowerCase().indexOf("weixinext.exe") !== -1 || cmd.indexOf("--type=renderer") !== -1)) {
                    if (cmd.indexOf("--no-sandbox") === -1) {
                        var newCmd = cmd + " --no-sandbox";
                        var buf = Memory.allocUtf16String(newCmd);
                        allocated.push(buf);
                        args[1] = buf;
                        if (Process.arch === "x64" && this.context.rdx !== undefined) {
                            this.context.rdx = buf;
                        }
                    }
                }
            }
        }
    });
}
"""

class ArticleDevToolsEngine:
    """微信 4.x 公众号文章独立 DevTools 大屏审查守护中枢"""

    def __init__(self, port: int = 8080, auto_open: bool = True):
        self.port = port
        self.auto_open = auto_open
        self.injected_frames_map: Dict[int, Set[str]] = {}
        self.opened_target_ids: Set[str] = set()
        self.chii_process: Optional[subprocess.Popen] = None
        self.master_sessions: Dict[int, Any] = {}
        self.renderer_sessions: Dict[int, Any] = {}

    def ensure_chii_server(self):
        """确保本地 Chii DevTools 服务端处于运行状态"""
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/targets", timeout=0.5) as resp:
                if resp.status == 200:
                    log_info(f"本地 DevTools 服务端已在端口 {self.port} 就绪")
                    return
        except Exception:
            pass

        log_step(f"正在尝试启动本地 DevTools 服务 (端口 {self.port})...")
        node_exe = shutil.which("node")
        npx_exe = shutil.which("npx")

        root_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        local_chii = os.path.join(root_dir, "node_modules", "chii", "bin", "chii.js")

        cmd = None
        if node_exe and os.path.exists(local_chii):
            cmd = [node_exe, local_chii, "start", "-p", str(self.port)]
        elif npx_exe:
            cmd = [npx_exe, "chii", "start", "-p", str(self.port)]

        if cmd:
            try:
                self.chii_process = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    cwd=root_dir if os.path.exists(root_dir) else None
                )
                time.sleep(1.2)
                if self.chii_process.poll() is None:
                    log_info(f"DevTools 服务端已成功在后台启动 (PID: {self.chii_process.pid})")
                else:
                    log_warn(f"本地 Chii 服务启动退出，请在项目根目录运行: npm install，或手动执行: npx chii start -p {self.port}")
            except Exception as e:
                log_warn(f"无法自动启动 Chii 服务端 ({e})，请手动在终端运行: npx chii start -p {self.port}")
        else:
            log_warn(f"未检测到 Node.js 运行时。如需独立大屏 DevTools，请安装 Node.js 并运行 npm install；或直接使用纯 Python 方案: wx-h5 proxy")

    def hook_master_processes(self):
        """挂载所有微信宿主与管理主进程的 CreateProcessW，强制新拉起的文章渲染器带 --no-sandbox 参数"""
        try:
            import frida
        except ImportError:
            return

        for p in psutil.process_iter(['pid', 'name', 'cmdline']):
            try:
                name = (p.info['name'] or '').lower()
                cmd = ' '.join(p.info['cmdline'] or [])
                is_weixin = 'weixin.exe' in name or 'wechat.exe' in name
                is_broker = 'wechatappex.exe' in name and '--type=renderer' not in cmd
                if is_weixin or is_broker:
                    pid = p.info['pid']
                    if pid not in self.master_sessions:
                        try:
                            s = frida.attach(pid)
                            sc = s.create_script(HOOK_MASTER_JS)
                            sc.load()
                            self.master_sessions[pid] = (s, sc)
                            log_info(f"已为微信宿主主进程解除子进程沙箱隔离 (PID: {pid}, {name})")
                        except Exception:
                            pass
            except Exception:
                pass

    def run(self):
        """启动文章多 Tab 捕获与注入循环"""
        try:
            import frida
        except ImportError:
            log_error("未检测到 frida 模块，请先运行: pip install frida")
            return

        wechat_running = False
        for p in psutil.process_iter(['name']):
            try:
                pname = (p.info['name'] or '').lower()
                if pname in ('weixin.exe', 'wechat.exe', 'wechatappex.exe'):
                    wechat_running = True
                    break
            except Exception:
                pass

        if not wechat_running:
            log_warn("当前未检测到正在运行的微信主程序，请先启动电脑端微信并登录。")

        self.ensure_chii_server()

        # 挂载宿主进程沙箱拦截
        self.hook_master_processes()

        # 检查并自动重置受沙箱保护的旧进程
        sandboxed_renderers = []
        for p in psutil.process_iter(['pid', 'name', 'cmdline']):
            try:
                name = (p.info['name'] or '').lower()
                cmd = ' '.join(p.info['cmdline'] or [])
                if 'wechatappex' in name and '--no-sandbox' not in cmd:
                    sandboxed_renderers.append(p)
            except Exception:
                pass

        if sandboxed_renderers:
            log_step(f"检测到 {len(sandboxed_renderers)} 个受沙箱限制的旧文章渲染器，正在自动重置...")
            for p in sandboxed_renderers:
                try:
                    p.kill()
                except Exception:
                    pass
            log_info("沙箱已清理完成！请在电脑微信中重新点开文章，即可秒级完成调试注入！")

        log_step("微信 4.x 公众号文章独立 DevTools 大屏守护已激活")
        log_info(f"服务控制大屏: http://127.0.0.1:{self.port}")
        log_info("正在持续扫描微信文章渲染进程 (WeChatAppEx.exe --wmpf-render-type=7)...")
        log_info("请在电脑微信中正常点击打开任意公众号推文或内嵌网页")

        try:
            while True:
                # 保持宿主进程 Hook 活跃
                self.hook_master_processes()

                # 1. 扫描当前所有活跃的文章渲染进程
                current_pids = []
                for p in psutil.process_iter(['pid', 'name', 'cmdline']):
                    try:
                        name = p.info['name'] or ''
                        cmd = ' '.join(p.info['cmdline'] or [])
                        if 'wechatappex' in name.lower() and '--wmpf-render-type=7' in cmd:
                            current_pids.append(p.info['pid'])
                    except Exception:
                        pass

                # 清理已退出的渲染器会话
                dead_pids = [pid for pid in self.renderer_sessions if pid not in current_pids]
                for dpid in dead_pids:
                    try:
                        s, sc = self.renderer_sessions.pop(dpid)
                        s.detach()
                    except Exception:
                        pass

                for pid in current_pids:
                    if pid in self.renderer_sessions:
                        continue

                    known_frames = self.injected_frames_map.setdefault(pid, set())
                    try:
                        s = frida.attach(pid)
                        sc = s.create_script(JS_MULTI_TAB_INJECT)
                        new_frames_detected = []

                        def on_msg(msg, data, p=pid):
                            if msg.get("type") == "send":
                                payload = msg.get("payload", {})
                                if payload.get("event") == "frames_detected":
                                    frames = payload.get("frames", [])
                                    for f in frames:
                                        if f not in known_frames:
                                            new_frames_detected.append(f)
                                            known_frames.add(f)
                                elif payload.get("event") == "frames_injected":
                                    log_info(f"PID {p} 成功注入 DevTools 审查探针！")

                        sc.on("message", on_msg)
                        sc.load()
                        self.renderer_sessions[pid] = (s, sc)
                        log_info(f"已成功接入文章渲染进程并激活探针 (PID: {pid})")
                    except Exception:
                        pass

                # 2. 检查 Chii 服务端 targets
                try:
                    with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/targets", timeout=0.4) as resp:
                        data = json.loads(resp.read().decode('utf-8'))
                        targets = data.get("targets", [])
                        for t in targets:
                            tid = t.get("id")
                            if tid and tid not in self.opened_target_ids:
                                self.opened_target_ids.add(tid)
                                title = t.get("title") or "微信公众号文章"
                                inspect_url = f"http://127.0.0.1:{self.port}/front_end/chii_app.html?ws=127.0.0.1:{self.port}/client/{tid}?target={tid}"
                                log_step(f"捕获到新推文/Tab上线: {title}")
                                log_info(f"审查大屏直达: {inspect_url}")
                                if self.auto_open:
                                    try:
                                        webbrowser.open(inspect_url)
                                    except Exception:
                                        pass
                except Exception:
                    pass

                time.sleep(0.8)
        except KeyboardInterrupt:
            log_info("已接收退出信号，守护服务已安全停止。")
        finally:
            for pid, (s, sc) in list(self.master_sessions.items()):
                try:
                    s.detach()
                except Exception:
                    pass
            self.master_sessions.clear()

            for pid, (s, sc) in list(self.renderer_sessions.items()):
                try:
                    s.detach()
                except Exception:
                    pass
            self.renderer_sessions.clear()

            if self.chii_process and self.chii_process.poll() is None:
                try:
                    self.chii_process.terminate()
                except Exception:
                    pass

def run_article_devtools(port: int = 8080, auto_open: bool = True):
    engine = ArticleDevToolsEngine(port=port, auto_open=auto_open)
    engine.run()
