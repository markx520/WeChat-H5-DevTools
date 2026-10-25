# -*- coding: utf-8 -*-
"""
wechat_h5_devtools.mcp.tools: 微信调试套件原生 MCP 工具矩阵与 Schema 定义
"""

import sys
import os
import json
import time
import urllib.request
import urllib.error
from pathlib import Path
from typing import Dict, Any, List, Optional

from ..injector.wechat_finder import WeChatFinder
from ..sandbox.browser_launcher import BrowserLauncher


PROJECT_ROOT = Path(__file__).resolve().parent.parent


TOOLS_SCHEMA = [
    {
        "name": "wechat_get_runtime_status",
        "description": "获取宿主机微信运行状态、主程序路径、进程 PID、内置渲染内核及进程矩阵",
        "inputSchema": {
            "type": "object",
            "properties": {},
            "required": []
        }
    },
    {
        "name": "wechat_doctor",
        "description": "全维度诊断宿主机微信、渲染内核版本、Edge/Chrome、Node.js、Frida 等开发调试环境",
        "inputSchema": {
            "type": "object",
            "properties": {
                "check_browsers": {
                    "type": "boolean",
                    "description": "是否检查宿主机 Edge 与 Chrome 浏览器安装状态",
                    "default": True
                }
            },
            "required": []
        }
    },
    {
        "name": "wechat_list_targets",
        "description": "通过微信内核 CDP 通道，列出当前微信正在渲染的所有公众号文章页面与内置 H5 网页目标",
        "inputSchema": {
            "type": "object",
            "properties": {
                "cdp_port": {
                    "type": "integer",
                    "description": "微信内核 CDP 服务监听端口 (默认: 62000)",
                    "default": 62000
                }
            },
            "required": []
        }
    },
    {
        "name": "wechat_inspect_article",
        "description": "获取微信 4.x 公众号文章独立 DevTools 大屏审查通道状态与前端审查面板入口",
        "inputSchema": {
            "type": "object",
            "properties": {
                "port": {
                    "type": "integer",
                    "description": "DevTools 服务端端口 (默认: 8080)",
                    "default": 8080
                },
                "auto_open": {
                    "type": "boolean",
                    "description": "捕获到文章时是否自动弹窗打开浏览器面板",
                    "default": False
                }
            },
            "required": []
        }
    },
    {
        "name": "wechat_evaluate_script",
        "description": "通过 CDP 协议向指定微信页面执行 JavaScript 脚本并同步获取返回值",
        "inputSchema": {
            "type": "object",
            "properties": {
                "target_id": {
                    "type": "string",
                    "description": "目标页面 ID (可通过 wechat_list_targets 获取)"
                },
                "script": {
                    "type": "string",
                    "description": "待在微信页面中执行的 JavaScript 代码片段"
                },
                "cdp_port": {
                    "type": "integer",
                    "description": "CDP 端口 (默认: 62000)",
                    "default": 62000
                }
            },
            "required": ["target_id", "script"]
        }
    },
    {
        "name": "wechat_static_scan",
        "description": "对指定本地前端工程目录执行静态代码审计，提取全量 API 路由与敏感密码学特征",
        "inputSchema": {
            "type": "object",
            "properties": {
                "target_dir": {
                    "type": "string",
                    "description": "待审计的前端工程目录绝对路径"
                },
                "export_path": {
                    "type": "string",
                    "description": "可选：导出 Markdown 审计报告的物理路径"
                }
            },
            "required": ["target_dir"]
        }
    },
    {
        "name": "wechat_deobfuscate_code",
        "description": "对前端工程目录执行 AST 语法树解混淆与 Webpack 模块解包",
        "inputSchema": {
            "type": "object",
            "properties": {
                "target_dir": {
                    "type": "string",
                    "description": "待解混淆的工程目录绝对路径"
                },
                "output_dir": {
                    "type": "string",
                    "description": "可选：解混淆代码输出目录"
                }
            },
            "required": ["target_dir"]
        }
    },
    {
        "name": "wechat_list_supported_versions",
        "description": "查询工具箱已适配支持的 RadiumWMPF 内核构建版本号与特征库概览",
        "inputSchema": {
            "type": "object",
            "properties": {},
            "required": []
        }
    }
]


def execute_tool(name: str, args: Dict[str, Any]) -> Dict[str, Any]:
    """统一执行 MCP 工具并返回标准结构"""
    t0 = time.time()
    try:
        if name == "wechat_get_runtime_status":
            data = WeChatFinder.get_runtime_status()
            return _format_success(data, t0)

        elif name == "wechat_doctor":
            check_browsers = args.get("check_browsers", True)
            runtime_status = WeChatFinder.get_runtime_status()
            
            doctor_data = {
                "wechat": {
                    "is_running": runtime_status.get("is_running", False),
                    "pid": runtime_status.get("wechat_pid"),
                    "version": runtime_status.get("wechat_version"),
                    "kernel": runtime_status.get("kernel"),
                    "exe_path": runtime_status.get("exe_path"),
                    "status_text": runtime_status.get("status_text")
                },
                "python": {
                    "version": sys.version.split()[0],
                    "executable": sys.executable
                }
            }

            if check_browsers:
                edge_path = BrowserLauncher("edge").find_browser_exe()
                chrome_path = BrowserLauncher("chrome").find_browser_exe()
                doctor_data["browsers"] = {
                    "edge": edge_path or "未找到",
                    "chrome": chrome_path or "未找到"
                }

            # 检查 Frida
            try:
                import frida
                doctor_data["frida"] = {"installed": True, "version": getattr(frida, "__version__", "unknown")}
            except Exception:
                doctor_data["frida"] = {"installed": False, "version": None}

            # 检查 Node.js
            import shutil
            import subprocess
            node_exe = shutil.which("node")
            if node_exe:
                try:
                    res = subprocess.run([node_exe, "-v"], capture_output=True, text=True, timeout=2)
                    doctor_data["nodejs"] = {"installed": True, "version": res.stdout.strip(), "path": node_exe}
                except Exception:
                    doctor_data["nodejs"] = {"installed": True, "version": "unknown", "path": node_exe}
            else:
                doctor_data["nodejs"] = {"installed": False, "version": None, "path": None}

            return _format_success(doctor_data, t0)

        elif name == "wechat_list_targets":
            cdp_port = int(args.get("cdp_port", 62000))
            url = f"http://127.0.0.1:{cdp_port}/json/list"
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "WeChat-H5-DevTools-MCP"})
                with urllib.request.urlopen(req, timeout=3) as resp:
                    targets = json.loads(resp.read().decode("utf-8"))
                    return _format_success({
                        "cdp_port": cdp_port,
                        "connected": True,
                        "targets_count": len(targets),
                        "targets": targets
                    }, t0)
            except Exception as e:
                return _format_success({
                    "cdp_port": cdp_port,
                    "connected": False,
                    "targets_count": 0,
                    "targets": [],
                    "note": f"微信内核 CDP 调试通道 (端口 {cdp_port}) 未运行。可通过 'wx-h5 open' 或 'wx-h5 cdp' 启动。"
                }, t0)

        elif name == "wechat_inspect_article":
            port = int(args.get("port", 8080))
            auto_open = bool(args.get("auto_open", False))
            info = {
                "devtools_server_url": f"http://127.0.0.1:{port}/client",
                "port": port,
                "auto_open": auto_open,
                "status": "ready",
                "guidance": "在电脑微信内打开任意公众号文章，DevTools 服务将自动拦截并呈现独立审查面板。"
            }
            return _format_success(info, t0)

        elif name == "wechat_evaluate_script":
            target_id = args.get("target_id")
            script = args.get("script")
            cdp_port = int(args.get("cdp_port", 62000))
            if not target_id or not script:
                return _format_error("target_id 与 script 均为必填参数", t0)

            # 通过 CDP WebSocket 执行脚本
            import asyncio
            import websockets

            async def _eval_cdp():
                ws_url = f"ws://127.0.0.1:{cdp_port}/devtools/page/{target_id}"
                async with websockets.connect(ws_url, open_timeout=3) as ws:
                    req_payload = {
                        "id": 1,
                        "method": "Runtime.evaluate",
                        "params": {
                            "expression": script,
                            "returnByValue": True,
                            "awaitPromise": True
                        }
                    }
                    await ws.send(json.dumps(req_payload))
                    res_raw = await asyncio.wait_for(ws.recv(), timeout=5)
                    return json.loads(res_raw)

            try:
                loop = asyncio.new_event_loop()
                eval_res = loop.run_until_complete(_eval_cdp())
                loop.close()
                return _format_success(eval_res.get("result", {}), t0)
            except Exception as e:
                return _format_error(f"CDP 脚本执行失败: {str(e)}", t0)

        elif name == "wechat_static_scan":
            target_dir = args.get("target_dir")
            export_path = args.get("export_path")
            if not target_dir or not Path(target_dir).exists():
                return _format_error(f"待审计目录不存在: {target_dir}", t0)

            from ..extractor.api_analyzer import APIAnalyzer
            analyzer = APIAnalyzer(target_dir=str(target_dir))
            scan_res = analyzer.analyze()
            if export_path:
                analyzer.export_markdown(scan_res, str(export_path))
                scan_res["exported_file"] = str(export_path)

            return _format_success(scan_res, t0)

        elif name == "wechat_deobfuscate_code":
            target_dir = args.get("target_dir")
            output_dir = args.get("output_dir")
            if not target_dir or not Path(target_dir).exists():
                return _format_error(f"目标目录不存在: {target_dir}", t0)

            from ..extractor.deobfuscator import Deobfuscator
            deobf = Deobfuscator(input_dir=str(target_dir), output_dir=output_dir)
            ok = deobf.deobfuscate()
            return _format_success({
                "target_dir": str(target_dir),
                "output_dir": output_dir or f"{target_dir}_deobfuscated",
                "success": ok
            }, t0)

        elif name == "wechat_list_supported_versions":
            config_dir = PROJECT_ROOT / "resources" / "config"
            versions = []
            if config_dir.exists():
                for p in config_dir.glob("addresses.*.json"):
                    v_str = p.name.replace("addresses.", "").replace(".json", "")
                    try:
                        versions.append(int(v_str))
                    except ValueError:
                        pass
            versions.sort()
            return _format_success({
                "count": len(versions),
                "supported_kernel_builds": versions,
                "supported_versions": versions,
                "latest_supported": versions[-1] if versions else None
            }, t0)

        else:
            return _format_error(f"未识别的工具名称: {name}", t0, code=-32601)

    except Exception as e:
        return _format_error(f"工具执行异常: {str(e)}", t0)


def _format_success(data: Any, t0: float) -> Dict[str, Any]:
    duration_ms = round((time.time() - t0) * 1000, 2)
    return {
        "content": [
            {
                "type": "text",
                "text": json.dumps(data, ensure_ascii=False, indent=2)
            }
        ],
        "isError": False,
        "_meta": {"duration_ms": duration_ms}
    }


def _format_error(msg: str, t0: float, code: int = -32603) -> Dict[str, Any]:
    duration_ms = round((time.time() - t0) * 1000, 2)
    err_data = {"error": msg, "code": code}
    return {
        "content": [
            {
                "type": "text",
                "text": json.dumps(err_data, ensure_ascii=False, indent=2)
            }
        ],
        "isError": True,
        "_meta": {"duration_ms": duration_ms, "code": code}
    }
