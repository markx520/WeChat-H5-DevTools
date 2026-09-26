#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
WeChat-H5-DevTools 工业级微信内核 CDP 调试引擎 (Kernel CDP Engine)
===================================================================
架构特性:
1. 双通道服务 (Dual-Port Gateway):
   - 9421 端口 (DEBUG_PORT): 接收来自微信 RadiumWMPF 内核 (flue.dll) 的内部调试 WebSocket 连接。
   - 62000 端口 (CDP_PORT): 兼容标准 Chrome DevTools Protocol，提供 HTTP 查询与 WebSocket 双工调试。
2. 协议编解码与中继 (Protocol Translation):
   - 双向透明桥接 WARemoteDebug_DebugMessage Protobuf 二进制与标准 CDP JSON 报文。
   - 实时解析 setupContext / chromeDevtoolsResult / customMessage，动态感知文章 URL 与标题。
3. 微信进程感知与 Frida 动态注入:
   - 自动探测宿主机 WeChatAppEx.exe Broker 主控制进程及版本号 (如 25510)。
   - 动态加载 addresses.<version>.json 中的 LoadStartHookOffset 与 CDPFilterHookOffset。
   - 注入 flue.dll 解除 CDP 过滤并强制开启 DevTools (0x1)。
   - 附着 Weixin.exe Hook CreateProcessW，确保后续渲染沙箱继承 --no-sandbox 等调试参数。
4. 容错与优雅退出:
   - 智能端口预检与冲突提示。
   - 捕获 SIGINT / Ctrl+C 优雅注销 Hook、关闭服务器。
"""

import os
import sys
import json
import zlib
import time
import re
import socket
import logging
import asyncio
import threading
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional, Dict, Any, List, Set, Tuple

import psutil

try:
    import frida
except ImportError:
    frida = None

try:
    import click
except ImportError:
    click = None

import websockets
from websockets.asyncio.server import serve
from websockets.http11 import Response
from websockets.datastructures import Headers

from ..utils.logger import log_info, log_warn, log_error, log_step, print_table
from .wechat_finder import WeChatFinder
from . import wmpf_debug_pb2

# 抑制 websockets 拦截 HTTP 请求时的握手异常噪音
logging.getLogger("websockets.server").setLevel(logging.CRITICAL)
logging.getLogger("websockets").setLevel(logging.CRITICAL)

# 默认服务端口
DEFAULT_DEBUG_PORT = 9421
DEFAULT_CDP_PORT = 62000

# 消息类别常量
class MessageCategory:
    CHROME_DEVTOOLS = "chromeDevtools"
    CHROME_DEVTOOLS_RESULT = "chromeDevtoolsResult"
    SETUP_CONTEXT = "setupContext"
    CUSTOM_MESSAGE = "customMessage"

# 压缩算法标识
class CompressAlgo:
    NONE = 0
    ZLIB = 1


class ProtocolCodec:
    """WMPF 调试协议编解码器 (双向转换 Protobuf 与 CDP JSON)"""

    @staticmethod
    def encode_chrome_devtools(payload: str, op_id: str, jscontext_id: str = "", compress: bool = False) -> bytes:
        """将 CDP JSON 指令编码为 WARemoteDebug_ChromeDevtools 载荷"""
        msg = wmpf_debug_pb2.WARemoteDebug_ChromeDevtools()
        msg.opId = str(op_id)
        msg.payload = payload
        msg.jscontextId = jscontext_id

        encoded = msg.SerializeToString()
        if compress:
            encoded = zlib.compress(encoded)
        return encoded

    @staticmethod
    def decode_chrome_devtools_result(data: bytes, compressed: bool = False) -> Dict[str, Any]:
        """解码来自内核的 WARemoteDebug_ChromeDevtoolsResult"""
        if compressed:
            data = zlib.decompress(data)

        msg = wmpf_debug_pb2.WARemoteDebug_ChromeDevtoolsResult()
        msg.ParseFromString(data)
        return {
            "op_id": msg.opId,
            "payload": msg.payload,
            "jscontext_id": msg.jscontextId
        }

    @staticmethod
    def decode_setup_context(data: bytes, compressed: bool = False) -> Dict[str, Any]:
        """解码设置上下文消息 WARemoteDebug_SetupContext"""
        if compressed:
            data = zlib.decompress(data)

        msg = wmpf_debug_pb2.WARemoteDebug_SetupContext()
        msg.ParseFromString(data)
        return {
            "jscontext_id": msg.jscontextId,
            "appid": msg.appid,
            "version": msg.version,
            "platform": msg.platform,
            "sdk_version": msg.sdkVersion
        }

    @staticmethod
    def decode_custom_message(data: bytes, compressed: bool = False) -> Dict[str, Any]:
        """解码自定义消息 WARemoteDebug_CustomMessage"""
        if compressed:
            data = zlib.decompress(data)

        msg = wmpf_debug_pb2.WARemoteDebug_CustomMessage()
        msg.ParseFromString(data)
        return {
            "name": msg.name,
            "payload": msg.payload,
            "timestamp": msg.timestamp
        }

    @staticmethod
    def wrap_debug_message(data: bytes, category: str, seq: int, compress_algo: int = 0) -> bytes:
        """将数据打包为外层 WARemoteDebug_DebugMessage 消息"""
        msg = wmpf_debug_pb2.WARemoteDebug_DebugMessage()
        msg.seq = seq
        msg.category = category
        msg.data = data
        msg.compressAlgo = compress_algo
        msg.originalSize = len(data)
        return msg.SerializeToString()

    @staticmethod
    def unwrap_debug_message(raw_bytes: bytes) -> Dict[str, Any]:
        """解包外层 WARemoteDebug_DebugMessage"""
        msg = wmpf_debug_pb2.WARemoteDebug_DebugMessage()
        msg.ParseFromString(raw_bytes)
        return {
            "seq": msg.seq,
            "category": msg.category,
            "data": msg.data,
            "compress_algo": msg.compressAlgo,
            "original_size": msg.originalSize
        }


@dataclass
class TargetInfo:
    """CDP 调试目标 (Page / Webview) 数据模型"""
    target_id: str
    title: str = "微信公众号 / 内置浏览器页面"
    url: str = "about:blank"
    type: str = "page"
    appid: Optional[str] = None
    jscontext_id: Optional[str] = None
    session_id: Optional[str] = None
    attached: bool = False
    is_article: bool = False
    priority: int = 0
    created_at: float = field(default_factory=time.time)
    last_active_at: float = field(default_factory=time.time)

    def to_cdp_dict(self, cdp_port: int) -> Dict[str, Any]:
        """转换为 Chrome DevTools 规范的 /json 或 /json/list 目标对象"""
        desc = "WeChat Official Account Article (Auto-Bridged)" if self.is_article else f"WeChat RadiumWMPF Webview (AppId: {self.appid or 'H5-Page'})"
        return {
            "description": desc,
            "devtoolsFrontendUrl": f"/devtools/inspector.html?ws=127.0.0.1:{cdp_port}/devtools/page/{self.target_id}",
            "id": self.target_id,
            "title": self.title,
            "type": self.type,
            "url": self.url,
            "webSocketDebuggerUrl": f"ws://127.0.0.1:{cdp_port}/devtools/page/{self.target_id}"
        }

    def to_target_info(self) -> Dict[str, Any]:
        """转换为 Target 域内的 TargetInfo 结构体"""
        return {
            "targetId": self.target_id,
            "type": self.type,
            "title": self.title,
            "url": self.url,
            "attached": self.attached,
            "browserContextId": None
        }


class TargetManager:
    """活跃 Target 动态管理器与全自动路由仲裁器"""

    def __init__(self, cdp_port: int = DEFAULT_CDP_PORT, initial_url: Optional[str] = None):
        self.cdp_port = cdp_port
        self.initial_url = initial_url
        self.targets: Dict[str, TargetInfo] = {}
        self.target_sessions: Dict[str, str] = {}  # target_id -> session_id
        self.session_targets: Dict[str, str] = {}  # session_id -> target_id
        self.active_article_target_id: Optional[str] = None
        self.active_context_id: Optional[str] = None

        init_url = initial_url or "about:blank"
        init_title = "微信公众号 / 内置浏览器页面 (就绪)"
        if initial_url:
            if "mp.weixin.qq.com" in initial_url:
                init_title = f"微信公众号推文 ({initial_url[:45]}...)"
            else:
                init_title = f"微信页面 ({initial_url[:45]})"

        # 初始化默认页面 Target
        self.default_target = TargetInfo(
            target_id="default",
            title=init_title,
            url=init_url,
            priority=0
        )
        self.targets["default"] = self.default_target

    def calculate_priority(self, url: str, title: str, target_type: str = "page") -> int:
        """评估 Target 优先级打分，自动识别微信公众号文章与核心 H5 网页"""
        if target_type in ("service_worker", "worker", "shared_worker", "background_page"):
            return -100

        score = 0
        if target_type == "page":
            score += 20

        # 用户初始指定 URL 精准匹配 (最高优先)
        if self.initial_url and (self.initial_url in url or (url and url in self.initial_url and len(url) > 15)):
            score += 200

        # 微信公众号推文特征匹配 (高优先)
        if "mp.weixin.qq.com" in url:
            score += 150
        elif "weixin.qq.com" in url:
            score += 120
        # 普通互联网 HTTP/HTTPS 网页
        elif url.startswith("http://") or url.startswith("https://"):
            if any(k in url for k in ("servicewechat.com", "wx-open.qq.com", "qq.com/applet")):
                score += 10
            else:
                score += 80
        elif url and url != "about:blank":
            score += 30

        # 标题辅助加权
        if title and title != "微信页面" and "about:blank" not in title:
            score += 15

        return score

    def upsert_target(self, target_id: str, title: str = "", url: str = "", target_type: str = "page", appid: Optional[str] = None) -> TargetInfo:
        """更新或录入 Target 并动态刷新权重与文章标记"""
        if target_id not in self.targets:
            new_title = title or ("微信公众号推文" if "mp.weixin.qq.com" in url else "微信页面")
            target = TargetInfo(
                target_id=target_id,
                title=new_title,
                url=url or "about:blank",
                type=target_type,
                appid=appid
            )
            self.targets[target_id] = target
        else:
            target = self.targets[target_id]
            if title:
                target.title = title
            if url:
                target.url = url
            if target_type:
                target.type = target_type
            if appid:
                target.appid = appid

        target.priority = self.calculate_priority(target.url, target.title, target.type)
        target.is_article = (target.priority >= 80)
        target.last_active_at = time.time()

        if target_id in self.target_sessions:
            target.session_id = self.target_sessions[target_id]
            target.attached = True

        return target

    def bind_session(self, target_id: str, session_id: str):
        """将微信内核返回的 sessionId 与指定 target 建立双向绑定"""
        target = self.get_target(target_id)
        if not target:
            target = self.upsert_target(target_id=target_id)

        target.session_id = session_id
        target.attached = True
        target.last_active_at = time.time()

        self.target_sessions[target_id] = session_id
        self.session_targets[session_id] = target_id

        # 评估是否应当选为活跃文章主目标
        current_best = self.get_active_article_target()
        if not current_best or target.priority >= current_best.priority:
            self.active_article_target_id = target_id
            self.default_target.title = target.title
            self.default_target.url = target.url
            self.default_target.session_id = session_id
            self.default_target.attached = True
            self.default_target.is_article = target.is_article
            self.default_target.priority = target.priority
            log_info(f"[AutoBridge] 默认调试靶标已锚定: TargetId={target_id} | SessionId={session_id} | 标题={target.title[:30]}")

    def unbind_session(self, session_id: str) -> Optional[str]:
        """解绑 sessionId"""
        target_id = self.session_targets.pop(session_id, None)
        if target_id:
            self.target_sessions.pop(target_id, None)
            target = self.targets.get(target_id)
            if target:
                target.session_id = None
                target.attached = False
            if self.active_article_target_id == target_id:
                self.active_article_target_id = None
        return target_id

    def get_active_article_target(self) -> Optional[TargetInfo]:
        """获取当前最高优先级的公众号/网页 Target"""
        if self.active_article_target_id and self.active_article_target_id in self.targets:
            return self.targets[self.active_article_target_id]
        sorted_targets = sorted(self.targets.values(), key=lambda t: t.priority, reverse=True)
        if sorted_targets and sorted_targets[0].priority > 0 and sorted_targets[0].target_id != "default":
            return sorted_targets[0]
        return None

    def find_best_unattached_target(self) -> Optional[TargetInfo]:
        """寻找尚未附着 (未绑定 session) 且优先级最高的目标"""
        candidates = [
            t for t in self.targets.values()
            if t.target_id != "default" and not t.attached and not t.session_id and t.priority >= 30
        ]
        if not candidates:
            return None
        candidates.sort(key=lambda t: t.priority, reverse=True)
        return candidates[0]

    def get_target_by_session(self, session_id: str) -> Optional[TargetInfo]:
        """根据 session_id 查找目标"""
        target_id = self.session_targets.get(session_id)
        if target_id:
            return self.targets.get(target_id)
        return None

    def get_session_by_target(self, target_id: str) -> Optional[str]:
        """根据 target_id 查找 session_id"""
        if target_id == "default":
            return self.default_target.session_id or (
                self.target_sessions.get(self.active_article_target_id) if self.active_article_target_id else None
            )
        return self.target_sessions.get(target_id)

    def update_target_url(self, target_id: str, new_url: str, new_title: Optional[str] = None) -> TargetInfo:
        """动态更新指定或默认目标的 URL 与 Title"""
        target = self.get_target(target_id)
        target.url = new_url
        if new_title:
            target.title = new_title
        elif "mp.weixin.qq.com" in new_url:
            target.title = f"微信公众号推文 ({new_url[:45]}...)"
        elif new_url.startswith("http://") or new_url.startswith("https://"):
            target.title = f"微信页面 ({new_url[:45]})"
        target.priority = self.calculate_priority(target.url, target.title, target.type)
        target.is_article = (target.priority >= 80)
        target.last_active_at = time.time()
        return target

    def get_all_targets(self) -> List[TargetInfo]:
        """获取所有目标列表"""
        return list(self.targets.values())

    def get_targets_json(self) -> List[Dict[str, Any]]:
        """获取标准 CDP JSON 目标数组，优先返回公众号文章"""
        all_targets = list(self.targets.values())
        sorted_targets = sorted(all_targets, key=lambda t: (t.is_article, t.priority), reverse=True)
        return [t.to_cdp_dict(self.cdp_port) for t in sorted_targets]

    def get_target(self, target_id: str) -> Optional[TargetInfo]:
        """获取指定 ID 的目标"""
        return self.targets.get(target_id) or self.targets.get("default")

    def handle_setup_context(self, jscontext_id: str, appid: str, version: str, platform: str, sdk_version: str):
        """处理来自微信的 setupContext 消息"""
        self.active_context_id = jscontext_id
        target_id = jscontext_id or f"ctx_{int(time.time() * 1000)}"

        new_title = f"微信页面 ({appid})" if appid else "微信公众号 / 内置页面"
        self.upsert_target(target_id=target_id, title=new_title, url="https://mp.weixin.qq.com", appid=appid)
        target = self.targets[target_id]
        target.jscontext_id = jscontext_id

        if self.default_target.url == "about:blank":
            self.default_target.title = new_title
            self.default_target.appid = appid
            self.default_target.jscontext_id = jscontext_id

        log_info(f"[Target] 识别到微信运行上下文: jscontextId={jscontext_id} appid={appid} sdkVersion={sdk_version}")

    def analyze_cdp_payload(self, payload: str, jscontext_id: Optional[str] = None):
        """实时解析 CDP 响应与事件载荷，提取页面 Title 与 URL 并自动触发权重刷新"""
        try:
            parsed = json.loads(payload)
            if not isinstance(parsed, dict):
                return

            target = None
            if jscontext_id and jscontext_id in self.targets:
                target = self.targets[jscontext_id]
            elif self.active_context_id and self.active_context_id in self.targets:
                target = self.targets[self.active_context_id]
            else:
                target = self.default_target

            method = parsed.get("method", "")
            params = parsed.get("params", {})

            # 1. 页面导航事件 (Page.frameNavigated)
            if method == "Page.frameNavigated":
                frame = params.get("frame", {})
                new_url = frame.get("url")
                new_title = frame.get("title")
                if new_url and not new_url.startswith("data:"):
                    target.url = new_url
                if new_title:
                    target.title = new_title
                target.priority = self.calculate_priority(target.url, target.title, target.type)
                target.is_article = (target.priority >= 80)
                target.last_active_at = time.time()
                log_info(f"[Target更新] 页面导航 -> 标题: {target.title} | URL: {target.url[:90]}")

            # 2. 文档内哈希/无刷新跳转 (Page.navigatedWithinDocument)
            elif method == "Page.navigatedWithinDocument":
                new_url = params.get("url")
                if new_url:
                    target.url = new_url
                    target.priority = self.calculate_priority(target.url, target.title, target.type)
                    target.is_article = (target.priority >= 80)
                    target.last_active_at = time.time()
                    log_info(f"[Target更新] 内部导航 -> URL: {target.url[:90]}")

            # 3. 目标创建或状态变动 (Target.targetCreated / Target.targetInfoChanged)
            elif method in ("Target.targetCreated", "Target.targetInfoChanged"):
                info = params.get("targetInfo", {})
                tid = info.get("targetId")
                title = info.get("title")
                url = info.get("url")
                ttype = info.get("type", "page")
                if tid:
                    t = self.upsert_target(tid, title=title or "", url=url or "", target_type=ttype)
                    log_info(f"[Target更新] 目标变动 -> ID: {tid} | 标题: {t.title} | URL: {t.url[:60]}")

            # 4. 执行结果中的页面属性提取
            elif "result" in parsed and isinstance(parsed["result"], dict):
                res_obj = parsed["result"].get("result", {})
                if res_obj.get("type") == "string":
                    val = str(res_obj.get("value", ""))
                    if val.startswith("http://") or val.startswith("https://"):
                        target.url = val
                        target.priority = self.calculate_priority(target.url, target.title, target.type)
                        target.is_article = (target.priority >= 80)
                        target.last_active_at = time.time()
        except Exception:
            pass

    def handle_custom_message(self, name: str, payload: str, timestamp: int):
        """处理自定义事件消息"""
        try:
            if payload.startswith("{") and payload.endswith("}"):
                data = json.loads(payload)
                if "path" in data or "url" in data:
                    new_url = data.get("url") or data.get("path")
                    if new_url:
                        self.default_target.url = new_url
                        log_info(f"[Target] 自定义路由变更 -> {new_url}")
                if "title" in data:
                    self.default_target.title = data.get("title")
        except Exception:
            pass


class ProcessHookManager:
    """微信主进程与 WeChatAppEx 渲染内核 Frida 注入控制器"""

    def __init__(self, custom_version: Optional[int] = None):
        self.custom_version = custom_version
        self.sessions: List[Any] = []
        self.scripts: List[Any] = []
        self.lock = threading.Lock()

    @staticmethod
    def detect_wechat_environment() -> Dict[str, Any]:
        """探测微信主程序与 WeChatAppEx Broker 架构"""
        wechat_appex_procs = []
        weixin_procs = []

        for proc in psutil.process_iter(['pid', 'name', 'ppid', 'exe', 'cmdline']):
            try:
                name = (proc.info['name'] or '').lower()
                if name in ['wechat.exe', 'weixin.exe']:
                    weixin_procs.append(proc.info)
                elif name in ['wechatappex.exe', 'weixinext.exe']:
                    wechat_appex_procs.append(proc.info)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue

        broker_info = None
        wmpf_version = None

        if wechat_appex_procs:
            # 统计作为子进程父进程最多的 PID
            ppid_counts = {}
            for p in wechat_appex_procs:
                ppid = p['ppid']
                ppid_counts[ppid] = ppid_counts.get(ppid, 0) + 1

            candidate_broker_pid = None
            for pid, _ in sorted(ppid_counts.items(), key=lambda x: x[1], reverse=True):
                if any(p['pid'] == pid for p in wechat_appex_procs):
                    candidate_broker_pid = pid
                    break

            if candidate_broker_pid:
                broker_info = next(p for p in wechat_appex_procs if p['pid'] == candidate_broker_pid)
            else:
                non_renderers = [p for p in wechat_appex_procs if not any('--type=' in arg for arg in (p.get('cmdline') or []))]
                broker_info = non_renderers[0] if non_renderers else wechat_appex_procs[0]

            exe_path = broker_info.get('exe') or ''
            if exe_path:
                match = re.search(r'RadiumWMPF[\\/]+(\d+)', exe_path, re.IGNORECASE)
                if match:
                    wmpf_version = int(match.group(1))
                else:
                    nums = re.findall(r'\d+', exe_path)
                    valid_nums = [int(n) for n in nums if int(n) > 5000]
                    if valid_nums:
                        wmpf_version = valid_nums[-1]

            if not wmpf_version and exe_path:
                pe_ver = WeChatFinder.get_pe_version(Path(exe_path))
                if pe_ver:
                    nums = re.findall(r'\d+', pe_ver)
                    if nums:
                        wmpf_version = int(nums[-1])

        return {
            "broker": broker_info,
            "wmpf_version": wmpf_version,
            "appex_count": len(wechat_appex_procs),
            "weixin_procs": weixin_procs,
            "weixin_count": len(weixin_procs)
        }

    @staticmethod
    def load_version_config(version: int) -> Optional[Dict[str, Any]]:
        """从 resources/config/addresses.<version>.json 读取地址与偏移"""
        pkg_dir = Path(__file__).resolve().parent.parent
        root_dir = pkg_dir.parent
        candidates = [
            pkg_dir / "resources" / "config" / f"addresses.{version}.json",
            root_dir / "resources" / "config" / f"addresses.{version}.json",
            Path.cwd() / "resources" / "config" / f"addresses.{version}.json",
        ]
        config_path = None
        for cand in candidates:
            if cand.exists():
                config_path = cand
                break

        if not config_path:
            log_warn(f"未找到版本 {version} 的地址配置文件: {candidates[0]}")
            return None

        try:
            with open(config_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data
        except Exception as e:
            log_error(f"读取配置文件失败: {e}")
            return None

    def inject_flue_hook(self, pid: int, version: int, config: Dict[str, Any]) -> bool:
        """向 WeChatAppEx.exe Broker 注入 flue.dll Hook 脚本"""
        if not frida:
            log_error("系统未安装 Frida 动态插桩框架，请执行: pip install frida")
            return False

        hook_template = """
        var config = @@CONFIG@@;

        function findTargetModule() {
            return Process.findModuleByName("flue.dll") || Process.findModuleByName("WeChatAppEx.exe");
        }

        function hookFlue() {
            var mod = findTargetModule();
            if (!mod) {
                return false;
            }
            var base = mod.base;
            send("[INFO] 核心引擎模块已定位: " + mod.name + " (" + base + ")");

            // 1. CDP 过滤绕过 (CDPFilterHookOffset)
            if (config.CDPFilterHookOffset) {
                try {
                    var cdpTarget = base.add(ptr(config.CDPFilterHookOffset));
                    Interceptor.attach(cdpTarget, {
                        onEnter: function (args) {
                            this.v216 = args[0];
                        },
                        onLeave: function (retval) {
                            try {
                                var v216Val = this.v216.readPointer();
                                if (v216Val.add(8).readU32() === 6) {
                                    v216Val.add(8).writeU32(0x0);
                                    send("[PASS] [CDPFilter] 已拦截放行 CDP 过滤: 0x6 -> 0x0");
                                }
                            } catch (e) {}
                        }
                    });
                    send("[SUCCESS] CDPFilterHook 成功挂载 -> 偏移 " + config.CDPFilterHookOffset);
                } catch (e) {
                    send("[ERROR] CDPFilterHook 挂载异常: " + e);
                }
            }

            // 2. 强制开启 DevTools 与场景值放行 (LoadStartHookOffset)
            if (config.LoadStartHookOffset) {
                try {
                    var loadStartTarget = base.add(ptr(config.LoadStartHookOffset));
                    var sceneOffsets = config.SceneOffsets || [64, 1512, 8, 1448, 16, 456];
                    Interceptor.attach(loadStartTarget, {
                        onEnter: function (args) {
                            try {
                                // 强制开启 DevTools (rdx 标志位 0x1)
                                if ((this.context.rdx & 0xFF) !== 1) {
                                    this.context.rdx = (this.context.rdx & ~0xFF) | 0x1;
                                    send("[PASS] [LoadStart] 强制激活 DevTools (rdx |= 0x1)");
                                }

                                // 场景值校验与放行 (SceneOffsets)
                                if (sceneOffsets && sceneOffsets.length === 6) {
                                    var passArgs = this.context.rcx.add(sceneOffsets[0]).readPointer().add(sceneOffsets[1]).readPointer();
                                    var scenePtr = passArgs.add(sceneOffsets[2]).readPointer().add(sceneOffsets[3]).readPointer().add(sceneOffsets[4]).readPointer().add(sceneOffsets[5]);
                                    var scene = scenePtr.readInt();
                                    var sceneList = [1005, 1053, 1074, 1145, 1256, 1260, 1302, 1308];
                                    if (sceneList.indexOf(scene) !== -1) {
                                        scenePtr.writeInt(1101);
                                        send("[PASS] [SceneHook] 场景值限制已解除: " + scene + " -> 1101");
                                    }
                                } else if (sceneOffsets && sceneOffsets.length === 4) {
                                    var passArgs = this.context.rcx.add(56).readPointer().add(sceneOffsets[0]).readPointer();
                                    var scenePtr = passArgs.add(8).readPointer().add(sceneOffsets[1]).readPointer().add(sceneOffsets[2]).readPointer().add(sceneOffsets[3]);
                                    var scene = scenePtr.readInt();
                                    var sceneList = [1005, 1053, 1074, 1145, 1256, 1260, 1302, 1308];
                                    if (sceneList.indexOf(scene) !== -1) {
                                        scenePtr.writeInt(1101);
                                        send("[PASS] [SceneHook] 场景值限制已解除: " + scene + " -> 1101");
                                    }
                                }
                            } catch (e) {}
                        }
                    });
                    send("[SUCCESS] LoadStartHook 成功挂载 -> 偏移 " + config.LoadStartHookOffset);
                } catch (e) {
                    send("[ERROR] LoadStartHook 挂载异常: " + e);
                }
            }
            return true;
        }

        if (!hookFlue()) {
            var retryCount = 0;
            var timer = setInterval(function () {
                retryCount++;
                if (hookFlue() || retryCount > 30) {
                    clearInterval(timer);
                }
            }, 1000);
        }
        """

        script_code = hook_template.replace("@@CONFIG@@", json.dumps(config))

        try:
            log_step(f"正在附加至 WeChatAppEx Broker 主控制进程 (PID: {pid})...")
            session = frida.attach(pid)
            script = session.create_script(script_code)

            def on_msg(message, data):
                if message.get("type") == "send":
                    payload = message.get("payload", "")
                    log_info(f"[flue.dll:{pid}] {payload}")
                elif message.get("type") == "error":
                    log_warn(f"[flue.dll:{pid}] Frida 异常: {message.get('description', '')}")

            script.on("message", on_msg)
            script.load()

            with self.lock:
                self.sessions.append(session)
                self.scripts.append(script)

            log_info(f"成功注入 flue.dll 解除 CDP 过滤与开启 DevTools (版本: {version})")
            return True
        except Exception as e:
            log_error(f"附加 WeChatAppEx (PID: {pid}) 失败: {e}")
            return False

    def inject_weixin_hook(self, weixin_pid: int) -> bool:
        """向 Weixin.exe 注入 CreateProcessW Hook，确保子进程启动带全量调试参数"""
        if not frida:
            return False

        hook_code = """
        var cpsPtr = null;
        try {
            cpsPtr = Process.getModuleByName("kernel32.dll").findExportByName("CreateProcessW");
        } catch (e) {
            cpsPtr = Module.findExportByName(null, "CreateProcessW");
        }

        var allocatedStrings = [];

        if (cpsPtr) {
            send("[INFO] Weixin.exe CreateProcessW API 已定位: " + cpsPtr);
            Interceptor.attach(cpsPtr, {
                onEnter: function (args) {
                    this.cmdlinePtr = args[1];
                    if (this.cmdlinePtr) {
                        var cmd = this.cmdlinePtr.readUtf16String();
                        if (cmd && (cmd.indexOf("WeChatAppEx.exe") !== -1 || cmd.indexOf("WeixinExt.exe") !== -1 || cmd.indexOf("--type=renderer") !== -1)) {
                            if (cmd.indexOf("crashpad") === -1) {
                                var newCmd = cmd;
                                if (newCmd.indexOf("--no-sandbox") === -1) {
                                    newCmd += " --no-sandbox";
                                }
                                if (newCmd.indexOf("--enable-chrome-inspector") === -1) {
                                    newCmd += " --enable-chrome-inspector";
                                }
                                if (newCmd.indexOf("--enable-vconsole") === -1) {
                                    newCmd += " --enable-vconsole";
                                }
                                if (newCmd.indexOf("--xweb-enable-inspect") === -1) {
                                    newCmd += " --xweb-enable-inspect=1";
                                }
                                if (newCmd.indexOf("--ignore-certificate-errors") === -1) {
                                    newCmd += " --ignore-certificate-errors";
                                }
                                this.injectedCmd = newCmd;
                                var buf = Memory.allocUtf16String(newCmd);
                                allocatedStrings.push(buf);
                                args[1] = buf;
                                this.context.rdx = buf;
                            }
                        }
                    }
                },
                onLeave: function (retval) {
                    if (this.injectedCmd) {
                        send("[PASS] 已为新拉起的渲染子进程注入 --no-sandbox 等调试参数");
                    }
                }
            });
            send("[SUCCESS] CreateProcessW Hook 已就绪！");
        } else {
            send("[ERROR] 未能定位到 CreateProcessW 导出函数");
        }
        """

        try:
            log_step(f"正在附加至微信主程序 Weixin.exe (PID: {weixin_pid})...")
            session = frida.attach(weixin_pid)
            script = session.create_script(hook_code)

            def on_msg(message, data):
                if message.get("type") == "send":
                    log_info(f"[Weixin:{weixin_pid}] {message.get('payload', '')}")
                elif message.get("type") == "error":
                    log_warn(f"[Weixin:{weixin_pid}] Frida 异常: {message.get('description', '')}")

            script.on("message", on_msg)
            script.load()

            with self.lock:
                self.sessions.append(session)
                self.scripts.append(script)

            log_info(f"成功注入 Weixin.exe CreateProcessW 进程孵化拦截器")
            return True
        except Exception as e:
            log_warn(f"附加 Weixin.exe (PID: {weixin_pid}) 失败: {e}")
            return False

    def detach_all(self):
        """安全脱钩注销所有 Frida 进程会话"""
        with self.lock:
            for s in self.sessions:
                try:
                    s.detach()
                except Exception:
                    pass
            self.sessions.clear()
            self.scripts.clear()
        log_info("已安全注销所有 Frida 插桩会话")


class KernelCDPEngine:
    """双通道微信内核 CDP 调试网关引擎 (全自动 Target 路由桥接版)"""

    def __init__(
        self,
        debug_port: int = DEFAULT_DEBUG_PORT,
        cdp_port: int = DEFAULT_CDP_PORT,
        public_cdp_port: Optional[int] = None,
        auto_hook: bool = True,
        custom_version: Optional[int] = None,
        initial_url: Optional[str] = None
    ):
        self.debug_port = debug_port
        self.cdp_port = cdp_port
        self.public_cdp_port = public_cdp_port or cdp_port
        self.auto_hook = auto_hook
        self.custom_version = custom_version
        self.initial_url = initial_url

        self.target_manager = TargetManager(cdp_port=self.public_cdp_port, initial_url=self.initial_url)
        self.hook_manager = ProcessHookManager(custom_version=self.custom_version)
        self.codec = ProtocolCodec()

        # 微信内核 flue.dll 连接集合
        self.flue_clients: Set[Any] = set()
        # 外部 CDP 调试客户端集合 (Chrome/Edge/Playwright)
        self.cdp_clients: Set[Any] = set()
        # 记录客户端连接请求路径 (websocket -> request_path)
        self.connection_paths: Dict[Any, str] = {}
        # 针对特定 Target 的 Page 调试客户端集合 (target_id -> Set[websocket])
        self.page_clients: Dict[str, Set[Any]] = {}
        # 外部待匹配响应映射: cdp_request_id -> (websocket, target_id)
        self.pending_cdp_requests: Dict[int, Tuple[Any, Optional[str]]] = {}

        # 内部自动化调度控制 (90000~99999 专用隔离区间)
        self._internal_seq: int = 90000
        self._pending_internal_ops: Dict[int, Dict[str, Any]] = {}
        self.auto_bridge_task: Optional[asyncio.Task] = None

        self.seq_counter: int = 0
        self.is_running: bool = False

        self.debug_server = None
        self.cdp_server = None

    @staticmethod
    def check_port(port: int, host: str = "0.0.0.0") -> Tuple[bool, Optional[str]]:
        """检测指定端口是否可用，若占用则尝试排查占用进程"""
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            s.bind((host, port))
            s.close()
            return True, None
        except OSError as e:
            occupant = None
            try:
                for conn in psutil.net_connections(kind='inet'):
                    if conn.laddr and conn.laddr.port == port and conn.status == 'LISTEN':
                        if conn.pid:
                            try:
                                p = psutil.Process(conn.pid)
                                occupant = f"PID {conn.pid} ({p.name()})"
                            except Exception:
                                occupant = f"PID {conn.pid}"
                        break
            except Exception:
                pass

            msg = f"端口 {port} 已被占用！"
            if occupant:
                msg += f" (占用进程: {occupant})"
            msg += f" 详情: {e}"
            return False, msg

    def _next_internal_seq(self) -> int:
        """生成内部专用 CDP 请求序号 (90000~99999 隔离区间)"""
        self._internal_seq += 1
        if self._internal_seq > 99999:
            self._internal_seq = 90001
        return self._internal_seq

    async def _send_internal_cmd(self, method: str, params: Dict[str, Any], op_meta: Optional[Dict[str, Any]] = None) -> int:
        """向微信内核 flue.dll 发送内部 Target 嗅探与挂接控制指令"""
        if not self.flue_clients:
            return 0
        seq = self._next_internal_seq()
        meta = op_meta or {}
        meta["method"] = method
        meta["created_at"] = time.time()
        self._pending_internal_ops[seq] = meta

        cmd = {
            "id": seq,
            "method": method,
            "params": params
        }
        payload = json.dumps(cmd, ensure_ascii=False)
        await self.send_to_flue(payload, jscontext_id=self.target_manager.active_context_id or "")
        return seq

    async def _auto_bridge_worker(self):
        """后台全自动 Target 嗅探与路由挂接协程"""
        log_step("[AutoBridge] 全自动 Target 路由巡检协程已激活")
        try:
            # 1. 激活内核 Target 事件推送广播
            await self._send_internal_cmd("Target.setDiscoverTargets", {"discover": True}, {"op": "setDiscover"})
        except Exception as e:
            log_warn(f"[AutoBridge] 激活 setDiscoverTargets 异常: {e}")

        # 2. 持续循环巡检
        while self.is_running and self.flue_clients:
            try:
                # 轮询获取当前所有目标
                await self._send_internal_cmd("Target.getTargets", {}, {"op": "getTargets"})

                # 自动侦测是否有高优先级未挂接的公众号/H5网页
                best_target = self.target_manager.find_best_unattached_target()
                if best_target:
                    log_step(f"[AutoBridge] 发现待挂接目标: {best_target.target_id} | 标题: {best_target.title[:30]} | 优先级: {best_target.priority}")
                    await self._send_internal_cmd(
                        "Target.attachToTarget",
                        {"targetId": best_target.target_id, "flatten": True},
                        {"op": "attach", "target_id": best_target.target_id}
                    )

                # 依据当前挂接状态自适应休眠节流
                active_target = self.target_manager.get_active_article_target()
                if active_target and active_target.attached:
                    await asyncio.sleep(3.0)
                else:
                    await asyncio.sleep(1.5)
            except asyncio.CancelledError:
                break
            except Exception as e:
                log_warn(f"[AutoBridge] 巡检异常: {e}")
                await asyncio.sleep(2.0)

    async def handle_flue_connection(self, websocket):
        """处理来自微信内核 flue.dll 的调试 WebSocket 连接 (端口 9421)"""
        log_info(f"[flue.dll] 微信内核调试器已建立连接 -> {websocket.remote_address}")
        self.flue_clients.add(websocket)

        # 首次建立连接时，激活全自动桥接工作流
        if not self.auto_bridge_task or self.auto_bridge_task.done():
            self.auto_bridge_task = asyncio.create_task(self._auto_bridge_worker())

        try:
            async for raw_msg in websocket:
                if isinstance(raw_msg, bytes):
                    await self._on_flue_binary_message(raw_msg)
                elif isinstance(raw_msg, str):
                    self.target_manager.analyze_cdp_payload(raw_msg)
                    await self._process_cdp_payload_from_kernel(raw_msg)
        except websockets.exceptions.ConnectionClosed:
            log_warn("[flue.dll] 微信内核调试连接已断开")
        finally:
            self.flue_clients.discard(websocket)
            if not self.flue_clients and self.auto_bridge_task:
                self.auto_bridge_task.cancel()

    async def _on_flue_binary_message(self, raw_bytes: bytes):
        """处理 flue.dll 推送的二进制 Protobuf 消息"""
        try:
            unwrapped = self.codec.unwrap_debug_message(raw_bytes)
            category = unwrapped.get("category")
            data = unwrapped.get("data", b"")
            compress_algo = unwrapped.get("compress_algo", 0)
            is_compressed = bool(compress_algo & CompressAlgo.ZLIB)

            # 1. CDP 结果与实时事件推送
            if category == MessageCategory.CHROME_DEVTOOLS_RESULT:
                decoded = self.codec.decode_chrome_devtools_result(data, is_compressed)
                payload = decoded.get("payload", "")
                jscontext_id = decoded.get("jscontext_id", "")

                # 实时提取 Title 与 URL，更新 Target 矩阵
                self.target_manager.analyze_cdp_payload(payload, jscontext_id)

                # 全自动桥接与消息分流
                await self._process_cdp_payload_from_kernel(payload)

            # 2. 上下文环境就绪
            elif category == MessageCategory.SETUP_CONTEXT:
                decoded = self.codec.decode_setup_context(data, is_compressed)
                self.target_manager.handle_setup_context(
                    jscontext_id=decoded.get("jscontext_id", ""),
                    appid=decoded.get("appid", ""),
                    version=decoded.get("version", ""),
                    platform=decoded.get("platform", ""),
                    sdk_version=decoded.get("sdk_version", "")
                )
                # 收到 setupContext 后立即发起一次嗅探
                await self._send_internal_cmd("Target.getTargets", {}, {"op": "getTargets"})

            # 3. 自定义事件
            elif category == MessageCategory.CUSTOM_MESSAGE:
                decoded = self.codec.decode_custom_message(data, is_compressed)
                self.target_manager.handle_custom_message(
                    name=decoded.get("name", ""),
                    payload=decoded.get("payload", ""),
                    timestamp=decoded.get("timestamp", 0)
                )
        except Exception as e:
            log_warn(f"[flue.dll] 解析消息异常: {e}")

    async def _process_cdp_payload_from_kernel(self, payload: str):
        """核心处理与路由来自微信内核的 CDP 报文 (支持全自动 Target 识别与平滑单播)"""
        try:
            msg = json.loads(payload)
        except Exception:
            await self.broadcast_to_cdp(payload)
            return

        msg_id = msg.get("id")
        method = msg.get("method", "")
        params = msg.get("params", {})
        session_id = msg.get("sessionId") or params.get("sessionId")

        # 1. 内部自动化控制指令响应拦截 (90000 范围)
        if msg_id and msg_id in self._pending_internal_ops:
            op_info = self._pending_internal_ops.pop(msg_id)
            op = op_info.get("op")

            if op == "getTargets":
                target_infos = msg.get("result", {}).get("targetInfos", [])
                for info in target_infos:
                    tid = info.get("targetId")
                    title = info.get("title", "")
                    url = info.get("url", "")
                    ttype = info.get("type", "page")
                    if tid:
                        self.target_manager.upsert_target(tid, title=title, url=url, target_type=ttype)

                # 检查是否存在未挂接的最优推文网页
                best = self.target_manager.find_best_unattached_target()
                if best:
                    log_step(f"[AutoBridge] 发现候选推文页面: {best.target_id} | URL={best.url[:60]}")
                    await self._send_internal_cmd(
                        "Target.attachToTarget",
                        {"targetId": best.target_id, "flatten": True},
                        {"op": "attach", "target_id": best.target_id}
                    )
                return

            elif op == "attach":
                target_id = op_info.get("target_id")
                new_session_id = msg.get("result", {}).get("sessionId")
                if target_id and new_session_id:
                    self.target_manager.bind_session(target_id, new_session_id)
                    log_info(f"[SUCCESS] [AutoBridge] 自动挂接完成: TargetId={target_id} -> SessionId={new_session_id}")
                    # 向外部客户端广播 attachedToTarget 事件
                    target = self.target_manager.get_target(target_id)
                    attached_evt = {
                        "method": "Target.attachedToTarget",
                        "params": {
                            "sessionId": new_session_id,
                            "targetInfo": target.to_target_info() if target else {},
                            "waitingForDebugger": False
                        }
                    }
                    await self.broadcast_to_cdp(json.dumps(attached_evt, ensure_ascii=False))
                return

            elif op == "setDiscover":
                return

        # 2. 内核主动推送的 Target 状态变动事件
        if method in ("Target.targetCreated", "Target.targetInfoChanged"):
            target_info_dict = params.get("targetInfo", {})
            tid = target_info_dict.get("targetId")
            if tid:
                t = self.target_manager.upsert_target(
                    target_id=tid,
                    title=target_info_dict.get("title", ""),
                    url=target_info_dict.get("url", ""),
                    target_type=target_info_dict.get("type", "page")
                )
                if t.is_article and not t.attached and not t.session_id:
                    log_step(f"[AutoBridge] 监听到新推文 Target 创建: {tid}，触发自动挂接...")
                    await self._send_internal_cmd(
                        "Target.attachToTarget",
                        {"targetId": tid, "flatten": True},
                        {"op": "attach", "target_id": tid}
                    )

        elif method == "Target.attachedToTarget":
            att_session_id = params.get("sessionId")
            att_target_info = params.get("targetInfo", {})
            att_target_id = att_target_info.get("targetId")
            if att_target_id and att_session_id:
                self.target_manager.upsert_target(
                    att_target_id,
                    title=att_target_info.get("title", ""),
                    url=att_target_info.get("url", ""),
                    target_type=att_target_info.get("type", "page")
                )
                self.target_manager.bind_session(att_target_id, att_session_id)
                log_info(f"[AutoBridge] 捕获内核已挂载事件: TargetId={att_target_id} SessionId={att_session_id}")

        elif method in ("Target.targetDestroyed", "Target.detachedFromTarget"):
            des_session_id = params.get("sessionId")
            if des_session_id:
                self.target_manager.unbind_session(des_session_id)

        # 3. 外部请求响应路由 (优先定向回复等待该响应的外部客户端)
        if msg_id and msg_id in self.pending_cdp_requests:
            client_ws, target_id = self.pending_cdp_requests.pop(msg_id)
            try:
                # 剔除顶层 sessionId，保障单页模式兼容性
                if "sessionId" in msg:
                    cloned = dict(msg)
                    cloned.pop("sessionId", None)
                    await client_ws.send(json.dumps(cloned, ensure_ascii=False))
                else:
                    await client_ws.send(payload)
                return
            except Exception:
                pass

        # 4. 针对特定 session 的页面级事件单播
        if session_id:
            bound_target_id = self.target_manager.session_targets.get(session_id)
            if bound_target_id:
                target_clients = set(self.page_clients.get(bound_target_id, set()))
                if bound_target_id == self.target_manager.active_article_target_id:
                    target_clients.update(self.page_clients.get("default", set()))

                if target_clients:
                    event_for_page = dict(msg)
                    event_for_page.pop("sessionId", None)
                    page_payload = json.dumps(event_for_page, ensure_ascii=False)
                    dead = set()
                    for c in target_clients:
                        try:
                            await c.send(page_payload)
                        except Exception:
                            dead.add(c)
                    if bound_target_id in self.page_clients:
                        self.page_clients[bound_target_id].difference_update(dead)
                    if "default" in self.page_clients:
                        self.page_clients["default"].difference_update(dead)

        # 5. 全局广播 (供给 Browser 模式全景 Inspector)
        await self.broadcast_to_cdp(payload)

    async def broadcast_to_cdp(self, payload: str):
        """将 CDP 消息广播至所有已连接的 CDP 客户端"""
        if not self.cdp_clients:
            return
        dead = set()
        for client in list(self.cdp_clients):
            try:
                await client.send(payload)
            except Exception:
                dead.add(client)
        self.cdp_clients.difference_update(dead)

    async def send_to_flue(self, payload: str, jscontext_id: str = ""):
        """将 CDP 客户端指令打包成 Protobuf 并发送给微信内核 flue.dll"""
        if not self.flue_clients:
            return

        self.seq_counter += 1
        inner_data = self.codec.encode_chrome_devtools(
            payload=payload,
            op_id=str(self.seq_counter),
            jscontext_id=jscontext_id,
            compress=False
        )

        wrapped_data = self.codec.wrap_debug_message(
            data=inner_data,
            category=MessageCategory.CHROME_DEVTOOLS,
            seq=self.seq_counter,
            compress_algo=CompressAlgo.NONE
        )

        dead = set()
        for client in list(self.flue_clients):
            try:
                await client.send(wrapped_data)
            except Exception:
                dead.add(client)
        self.flue_clients.difference_update(dead)

    def cdp_http_request_handler(self, connection, request) -> Optional[Response]:
        """处理 62000 端口上的 HTTP 请求 (/json, /json/version, /json/list 等)"""
        path = request.path.split("?")[0].rstrip("/")
        # 记录本次请求路径到 connection 上，供 WebSocket 升级提取
        setattr(connection, "req_path", request.path)

        if path in ("/json", "/json/list", ""):
            targets_list = self.target_manager.get_targets_json()
            body = json.dumps(targets_list, ensure_ascii=False, indent=2).encode("utf-8")
            headers = Headers([
                ("Content-Type", "application/json; charset=UTF-8"),
                ("Content-Length", str(len(body))),
                ("Access-Control-Allow-Origin", "*"),
                ("Cache-Control", "no-cache")
            ])
            return Response(200, "OK", headers, body)

        elif path == "/json/version":
            version_data = {
                "Browser": f"WeChat/RadiumWMPF (Build {self.custom_version or 25510})",
                "Protocol-Version": "1.3",
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36 MicroMessenger/4.1.0",
                "V8-Version": "12.2.281",
                "WebKit-Version": "537.36 (@c368d4f)",
                "webSocketDebuggerUrl": f"ws://127.0.0.1:{self.public_cdp_port}/devtools/browser"
            }
            body = json.dumps(version_data, ensure_ascii=False, indent=2).encode("utf-8")
            headers = Headers([
                ("Content-Type", "application/json; charset=UTF-8"),
                ("Content-Length", str(len(body))),
                ("Access-Control-Allow-Origin", "*"),
                ("Cache-Control", "no-cache")
            ])
            return Response(200, "OK", headers, body)

        elif path == "/json/protocol":
            body = b"{}"
            headers = Headers([
                ("Content-Type", "application/json; charset=UTF-8"),
                ("Content-Length", str(len(body))),
                ("Access-Control-Allow-Origin", "*")
            ])
            return Response(200, "OK", headers, body)

        elif path == "/json/new":
            active_art = self.target_manager.get_active_article_target()
            target = active_art or self.target_manager.get_target("default")
            body = json.dumps(target.to_cdp_dict(self.cdp_port), ensure_ascii=False).encode("utf-8")
            headers = Headers([
                ("Content-Type", "application/json; charset=UTF-8"),
                ("Content-Length", str(len(body))),
                ("Access-Control-Allow-Origin", "*")
            ])
            return Response(200, "OK", headers, body)

        elif path.startswith("/json/activate/"):
            body = b"Target activated"
            headers = Headers([("Content-Type", "text/plain"), ("Content-Length", str(len(body)))])
            return Response(200, "OK", headers, body)

        elif path.startswith("/json/close/"):
            body = b"Target is closing"
            headers = Headers([("Content-Type", "text/plain"), ("Content-Length", str(len(body)))])
            return Response(200, "OK", headers, body)

        # 其它路径允许升级为 WebSocket
        return None

    async def handle_cdp_connection(self, websocket):
        """处理来自外部开发者工具 (Chrome/Edge/Playwright) 的 CDP WebSocket 连接 (端口 62000)"""
        req_path = getattr(websocket, "req_path", "") or ""
        log_info(f"[CDP Client] 外部调试器客户端已连接 -> {websocket.remote_address} (Path: {req_path})")
        self.cdp_clients.add(websocket)

        # 识别目标 Target ID
        bound_target_id = "default"
        if req_path.startswith("/devtools/page/"):
            bound_target_id = req_path.split("/devtools/page/")[-1].split("?")[0].strip() or "default"

        if bound_target_id not in self.page_clients:
            self.page_clients[bound_target_id] = set()
        self.page_clients[bound_target_id].add(websocket)

        try:
            async for text_msg in websocket:
                if not isinstance(text_msg, str):
                    continue

                try:
                    cmd = json.loads(text_msg)
                    msg_id = cmd.get("id")
                    method = cmd.get("method", "")

                    # 1. 本地直接响应 Target 探测命令，保障 Chrome/Edge 无缝握手
                    if method == "Target.getTargets":
                        all_targets = self.target_manager.get_all_targets()
                        resp = {
                            "id": msg_id,
                            "result": {
                                "targetInfos": [t.to_target_info() for t in all_targets]
                            }
                        }
                        await websocket.send(json.dumps(resp, ensure_ascii=False))
                        continue

                    elif method == "Target.setDiscoverTargets":
                        await websocket.send(json.dumps({"id": msg_id, "result": {}}))
                        for t in self.target_manager.get_all_targets():
                            evt = {
                                "method": "Target.targetCreated",
                                "params": {"targetInfo": t.to_target_info()}
                            }
                            await websocket.send(json.dumps(evt, ensure_ascii=False))
                            if t.attached and t.session_id:
                                att_evt = {
                                    "method": "Target.attachedToTarget",
                                    "params": {
                                        "sessionId": t.session_id,
                                        "targetInfo": t.to_target_info(),
                                        "waitingForDebugger": False
                                    }
                                }
                                await websocket.send(json.dumps(att_evt, ensure_ascii=False))
                        continue

                    elif method == "Target.getTargetInfo":
                        target_id = cmd.get("params", {}).get("targetId", bound_target_id)
                        t = self.target_manager.get_target(target_id)
                        resp = {
                            "id": msg_id,
                            "result": {
                                "targetInfo": t.to_target_info() if t else self.target_manager.default_target.to_target_info()
                            }
                        }
                        await websocket.send(json.dumps(resp, ensure_ascii=False))
                        continue

                    elif method in ("Target.setAutoAttach", "Target.attachToTarget", "Target.detachFromTarget"):
                        target_id = cmd.get("params", {}).get("targetId", bound_target_id)
                        target_session = self.target_manager.get_session_by_target(target_id) or f"session_{target_id}"
                        resp = {
                            "id": msg_id,
                            "result": {"sessionId": target_session} if method == "Target.attachToTarget" else {}
                        }
                        await websocket.send(json.dumps(resp, ensure_ascii=False))
                        continue

                    elif method == "Browser.getVersion":
                        ver_resp = {
                            "id": msg_id,
                            "result": {
                                "protocolVersion": "1.3",
                                "product": f"WeChat/RadiumWMPF (Build {self.custom_version or 25510})",
                                "revision": "@c368d4f",
                                "userAgent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
                                "jsVersion": "12.2.281"
                            }
                        }
                        await websocket.send(json.dumps(ver_resp, ensure_ascii=False))
                        continue

                    # 2. 页面导航与历史记录增强
                    elif method == "Page.navigate":
                        nav_url = cmd.get("params", {}).get("url", "")
                        target = self.target_manager.update_target_url(bound_target_id, nav_url)
                        log_info(f"[Page.navigate] DevTools 地址栏触发导航 -> URL: {nav_url}")

                        frame_id = "wechat_main_frame"
                        loader_id = f"loader_{int(time.time() * 1000)}"

                        # 若已挂载 session，自动注入 sessionId 转发给微信内核
                        session_id = self.target_manager.get_session_by_target(bound_target_id)
                        if self.flue_clients:
                            if session_id and "sessionId" not in cmd:
                                cmd["sessionId"] = session_id
                            if msg_id:
                                self.pending_cdp_requests[msg_id] = (websocket, bound_target_id)
                            await self.send_to_flue(json.dumps(cmd, ensure_ascii=False), jscontext_id=self.target_manager.active_context_id or "")

                        nav_resp = {
                            "id": msg_id,
                            "result": {
                                "frameId": frame_id,
                                "loaderId": loader_id
                            }
                        }
                        await websocket.send(json.dumps(nav_resp, ensure_ascii=False))

                        origin = nav_url.split("?")[0] if nav_url else "about:blank"
                        events = [
                            {"method": "Page.frameStartedLoading", "params": {"frameId": frame_id}},
                            {"method": "Page.frameNavigated", "params": {
                                "frame": {
                                    "id": frame_id,
                                    "loaderId": loader_id,
                                    "url": nav_url,
                                    "domainAndRegistry": "weixin.qq.com" if "weixin.qq.com" in nav_url else "",
                                    "securityOrigin": origin,
                                    "mimeType": "text/html"
                                }
                            }},
                            {"method": "Page.domContentEventFired", "params": {"timestamp": time.time()}},
                            {"method": "Page.loadEventFired", "params": {"timestamp": time.time()}},
                            {"method": "Page.frameStoppedLoading", "params": {"frameId": frame_id}}
                        ]
                        for evt in events:
                            await websocket.send(json.dumps(evt, ensure_ascii=False))

                        changed_evt = {
                            "method": "Target.targetInfoChanged",
                            "params": {"targetInfo": target.to_target_info()}
                        }
                        await self.broadcast_to_cdp(json.dumps(changed_evt, ensure_ascii=False))
                        continue

                    elif method == "Page.getNavigationHistory":
                        target = self.target_manager.get_target(bound_target_id)
                        current_url = target.url if target else "about:blank"
                        current_title = target.title if target else "微信公众号 / 内置浏览器页面"
                        nav_hist_resp = {
                            "id": msg_id,
                            "result": {
                                "currentIndex": 0,
                                "entries": [
                                    {
                                        "id": 1,
                                        "url": current_url,
                                        "userTypedURL": current_url,
                                        "title": current_title,
                                        "transitionType": "typed"
                                    }
                                ]
                            }
                        }
                        await websocket.send(json.dumps(nav_hist_resp, ensure_ascii=False))
                        continue

                    elif method == "Page.getResourceTree":
                        target = self.target_manager.get_target(bound_target_id)
                        current_url = target.url if target else "about:blank"
                        tree_resp = {
                            "id": msg_id,
                            "result": {
                                "frameTree": {
                                    "frame": {
                                        "id": "wechat_main_frame",
                                        "loaderId": "loader_init",
                                        "url": current_url,
                                        "domainAndRegistry": "weixin.qq.com" if "weixin.qq.com" in current_url else "",
                                        "securityOrigin": current_url,
                                        "mimeType": "text/html"
                                    },
                                    "resources": []
                                }
                            }
                        }
                        await websocket.send(json.dumps(tree_resp, ensure_ascii=False))
                        continue

                    elif method == "Page.reload":
                        target = self.target_manager.get_target(bound_target_id)
                        session_id = self.target_manager.get_session_by_target(bound_target_id)
                        if self.flue_clients:
                            if session_id and "sessionId" not in cmd:
                                cmd["sessionId"] = session_id
                            if msg_id:
                                self.pending_cdp_requests[msg_id] = (websocket, bound_target_id)
                            await self.send_to_flue(json.dumps(cmd, ensure_ascii=False), jscontext_id=self.target_manager.active_context_id or "")

                        await websocket.send(json.dumps({"id": msg_id, "result": {}}))
                        reload_evt = {
                            "method": "Page.frameNavigated",
                            "params": {
                                "frame": {
                                    "id": "wechat_main_frame",
                                    "loaderId": f"loader_{int(time.time()*1000)}",
                                    "url": target.url if target else "about:blank",
                                    "securityOrigin": target.url if target else "about:blank",
                                    "mimeType": "text/html"
                                }
                            }
                        }
                        await websocket.send(json.dumps(reload_evt, ensure_ascii=False))
                        continue

                    elif method == "Page.stopLoading":
                        await websocket.send(json.dumps({"id": msg_id, "result": {}}))
                        continue

                    # 3. 兜底保障：当微信内核调试会话尚未建立时，对 DevTools 基础握手指令提供合成响应，杜绝白屏超时
                    if not self.flue_clients:
                        if method in (
                            "Page.enable", "Network.enable", "Runtime.enable", "DOM.enable",
                            "CSS.enable", "Log.enable", "Security.enable", "Overlay.enable",
                            "Emulation.setFocusEmulationEnabled", "Runtime.runIfWaitingForDebugger",
                            "DOM.requestChildNodes", "DOM.pushNodesByBackendIdsToFrontend",
                            "Overlay.setShowGridOverlays", "Overlay.setShowFlexOverlays",
                            "Overlay.setInspectMode"
                        ):
                            await websocket.send(json.dumps({"id": msg_id, "result": {}}))
                            continue
                        elif method == "DOM.getDocument":
                            target = self.target_manager.get_target(bound_target_id)
                            doc_url = target.url if target else "about:blank"
                            doc_resp = {
                                "id": msg_id,
                                "result": {
                                    "root": {
                                        "nodeId": 1,
                                        "backendNodeId": 1,
                                        "nodeType": 9,
                                        "nodeName": "#document",
                                        "localName": "",
                                        "nodeValue": "",
                                        "childNodeCount": 1,
                                        "children": [
                                            {
                                                "nodeId": 2,
                                                "parentId": 1,
                                                "backendNodeId": 2,
                                                "nodeType": 1,
                                                "nodeName": "HTML",
                                                "localName": "html",
                                                "nodeValue": "",
                                                "childNodeCount": 2,
                                                "children": [
                                                    {"nodeId": 3, "parentId": 2, "backendNodeId": 3, "nodeType": 1, "nodeName": "HEAD", "localName": "head", "nodeValue": "", "childNodeCount": 0, "children": []},
                                                    {"nodeId": 4, "parentId": 2, "backendNodeId": 4, "nodeType": 1, "nodeName": "BODY", "localName": "body", "nodeValue": "", "childNodeCount": 0, "children": []}
                                                ]
                                            }
                                        ],
                                        "documentURL": doc_url,
                                        "baseURL": doc_url,
                                        "xmlVersion": ""
                                    }
                                }
                            }
                            await websocket.send(json.dumps(doc_resp, ensure_ascii=False))
                            continue
                        elif method == "Runtime.evaluate":
                            eval_resp = {
                                "id": msg_id,
                                "result": {
                                    "result": {
                                        "type": "string",
                                        "value": "WeChat H5 DevTools Ready"
                                    }
                                }
                            }
                            await websocket.send(json.dumps(eval_resp, ensure_ascii=False))
                            continue
                        elif method in ("CSS.getComputedStyleForNode", "CSS.getMatchedStylesForNode"):
                            await websocket.send(json.dumps({"id": msg_id, "result": {"computedStyle": [], "matchedCSSRules": []}}))
                            continue
                        elif method == "Debugger.enable":
                            await websocket.send(json.dumps({"id": msg_id, "result": {"debuggerId": "wechat_debugger"}}))
                            continue
                        else:
                            if msg_id:
                                await websocket.send(json.dumps({"id": msg_id, "result": {}}))
                                continue

                    # 4. 全自动 Session 路由注入：转发给微信内核
                    session_id = self.target_manager.get_session_by_target(bound_target_id)
                    if session_id and "sessionId" not in cmd:
                        cmd["sessionId"] = session_id

                    if msg_id:
                        self.pending_cdp_requests[msg_id] = (websocket, bound_target_id)

                    forward_text = json.dumps(cmd, ensure_ascii=False)
                    await self.send_to_flue(forward_text, jscontext_id=self.target_manager.active_context_id or "")

                except Exception as e:
                    log_warn(f"[CDP Client] 指令处理异常: {e}")
        except websockets.exceptions.ConnectionClosed:
            log_warn("[CDP Client] 外部调试器客户端已断开")
        finally:
            self.cdp_clients.discard(websocket)
            if bound_target_id in self.page_clients:
                self.page_clients[bound_target_id].discard(websocket)

    async def start(self):
        """启动双通道调试引擎服务"""
        # 1. 端口可用性预检
        ok_debug, err_debug = self.check_port(self.debug_port)
        if not ok_debug:
            log_error(f"[PORT CHECK FAILED] {err_debug}")
            log_warn(f"提示: 请检查是否已有正在运行的 WMPFDebugger 实例，或通过 --debug-port 指定其它端口。")
            return False

        ok_cdp, err_cdp = self.check_port(self.cdp_port)
        if not ok_cdp:
            log_error(f"[PORT CHECK FAILED] {err_cdp}")
            log_warn(f"提示: 请检查端口 {self.cdp_port} 占用情况，或通过 --cdp-port 指定其它端口。")
            return False

        # 2. 启动 9421 端口 Debug WebSocket 服务
        log_step(f"启动微信内核 flue.dll 内部调试网关: ws://127.0.0.1:{self.debug_port} ...")
        self.debug_server = await serve(self.handle_flue_connection, "0.0.0.0", self.debug_port)

        # 3. 启动 62000 端口标准 CDP 服务 (HTTP + WebSocket 复合网关)
        log_step(f"启动标准 Chrome DevTools Protocol 兼容网关: http://127.0.0.1:{self.cdp_port} ...")
        self.cdp_server = await serve(
            self.handle_cdp_connection,
            "0.0.0.0",
            self.cdp_port,
            process_request=self.cdp_http_request_handler
        )

        self.is_running = True

        # 4. 执行微信进程探测与 Frida Hook 注入
        if self.auto_hook:
            self._execute_wechat_hooks()

        # 5. 输出服务状态大盘
        self._print_dashboard()
        return True

    def _execute_wechat_hooks(self):
        """执行微信进程探测与 Hook 注入"""
        env = ProcessHookManager.detect_wechat_environment()
        broker = env.get("broker")
        detected_version = env.get("wmpf_version")
        final_version = self.custom_version or detected_version or 25510
        weixin_procs = env.get("weixin_procs", [])

        if not broker:
            log_warn("当前未检测到运行中的 WeChatAppEx.exe 进程，网关将在后台持续等待内核连接...")
        else:
            log_info(f"探测到 WeChatAppEx Broker 主控制进程 PID: {broker['pid']} (版本: {final_version})")
            config = ProcessHookManager.load_version_config(final_version)
            if config:
                # 注入 WeChatAppEx flue.dll
                self.hook_manager.inject_flue_hook(broker['pid'], final_version, config)
            else:
                log_warn(f"未找到针对版本 {final_version} 的偏移配置，跳过内核 Hook")

        # 附着到 Weixin.exe
        if weixin_procs:
            main_weixin = weixin_procs[0]
            self.hook_manager.inject_weixin_hook(main_weixin['pid'])

    def _print_dashboard(self):
        """打印服务运行状态大盘"""
        rows = [
            ["内核接收端口 (DEBUG_PORT)", f"ws://127.0.0.1:{self.debug_port}", "[green]RUNNING (监听中)[/green]"],
            ["CDP 调试端口 (CDP_PORT)", f"http://127.0.0.1:{self.cdp_port}", "[green]RUNNING (HTTP/WS 就绪)[/green]"],
            ["目标列表查询 (Targets)", f"http://127.0.0.1:{self.cdp_port}/json", "[cyan]GET /json (自动置顶推文)[/cyan]"],
            ["内核版本接口 (Version)", f"http://127.0.0.1:{self.cdp_port}/json/version", "[cyan]GET /json/version[/cyan]"],
            ["全自动 Target 桥接状态", "动态嗅探 + Session 自动注入", "[bold green]ENABLED (全自动挂载)[/bold green]"],
            ["Edge 浏览器直连", f"edge://inspect", "[bold cyan]添加 127.0.0.1:62000 直连[/bold cyan]"],
            ["Chrome 独立调试页", f"devtools://devtools/bundled/inspector.html?ws=127.0.0.1:{self.cdp_port}", "[bold green]浏览器一键开启[/bold green]"],
        ]
        print_table("微信公众号 / 内置内核浏览器 CDP 调试引擎大盘", ["服务项", "访问端点 / 协议", "运行状态"], rows)
        log_info("引擎已进入全双工监听状态。请在微信中打开公众号文章或 H5 页面进行调试。按 Ctrl+C 可随时退出。")

    async def run(self):
        """持续运行主循环"""
        if await self.start():
            try:
                while self.is_running:
                    await asyncio.sleep(1)
            except asyncio.CancelledError:
                pass
            finally:
                self.stop()

    def stop(self):
        """停止调试网关并清理所有资源"""
        if not self.is_running:
            return
        self.is_running = False
        log_step("正在关闭双通道 CDP 调试服务...")

        if self.auto_bridge_task:
            self.auto_bridge_task.cancel()
        if self.debug_server:
            self.debug_server.close()
        if self.cdp_server:
            self.cdp_server.close()

        self.hook_manager.detach_all()
        log_info("[SUCCESS] 微信内核 CDP 调试引擎已完全卸载并安全退出。")

# 模块别名导出，确保向下兼容
KernelCDPManager = KernelCDPEngine


def run_cdp_engine(
    debug_port: int = DEFAULT_DEBUG_PORT,
    cdp_port: int = DEFAULT_CDP_PORT,
    public_cdp_port: Optional[int] = None,
    no_hook: bool = False,
    version: Optional[int] = None,
    initial_url: Optional[str] = None
):
    """启动入口"""
    engine = KernelCDPEngine(
        debug_port=debug_port,
        cdp_port=cdp_port,
        public_cdp_port=public_cdp_port,
        auto_hook=not no_hook,
        custom_version=version,
        initial_url=initial_url
    )

    try:
        asyncio.run(engine.run())
    except KeyboardInterrupt:
        log_warn("收到中断信号 (Ctrl+C)，正在安全退出...")
        engine.stop()


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="WeChat-H5-DevTools 工业级微信内核 CDP 调试引擎")
    parser.add_argument("--debug-port", "-d", type=int, default=DEFAULT_DEBUG_PORT, help="flue.dll 内部调试端口 (默认: 9421)")
    parser.add_argument("--cdp-port", "-c", type=int, default=DEFAULT_CDP_PORT, help="标准 CDP 兼容端口 (默认: 62000)")
    parser.add_argument("--version", "-v", type=int, default=None, help="手动指定 WMPF 内核版本号 (如: 25510)")
    parser.add_argument("--no-hook", action="store_true", default=False, help="仅启动网关服务，不执行 Frida 注入")
    parser.add_argument("--url", "-u", default=None, help="初始目标文章链接 URL")
    args = parser.parse_args()

    run_cdp_engine(
        debug_port=args.debug_port,
        cdp_port=args.cdp_port,
        no_hook=args.no_hook,
        version=args.version,
        initial_url=args.url
    )
