# -*- coding: utf-8 -*-
"""
wechat_h5_devtools.mcp.server: WeChat-H5-DevTools 原生 MCP stdio 协议调度服务核心
完全基于 Python 标准库实现，零外部依赖，极速轻量，支持 Antigravity Nexus / Claude Desktop 管道直连
"""

import sys
import io
import json
import logging
from typing import Dict, Any, Optional

from .tools import TOOLS_SCHEMA, execute_tool
from .resources import RESOURCES_SCHEMA, read_resource
from .prompts import PROMPTS_SCHEMA, get_prompt_messages

# 配置独立日志：所有调试日志强制引向 stderr，确保 stdout 纯净用于 JSON-RPC
logger = logging.getLogger("wechat_h5_devtools.mcp")
logger.setLevel(logging.INFO)
stderr_handler = logging.StreamHandler(sys.stderr)
stderr_handler.setFormatter(logging.Formatter("[MCP-SERVER] %(asctime)s - %(levelname)s - %(message)s"))
logger.addHandler(stderr_handler)


class MCPServer:
    """标准 MCP JSON-RPC 2.0 stdio 服务端实现"""

    PROTOCOL_VERSION = "2024-11-05"
    SERVER_NAME = "wechat-h5-devtools-mcp"
    SERVER_VERSION = "1.2.1"

    def __init__(self):
        self.running = False

    def handle_message(self, raw_line: str) -> Optional[Dict[str, Any]]:
        """处理单条 JSON-RPC 请求并返回响应对象"""
        raw_line = raw_line.strip()
        if not raw_line:
            return None

        try:
            req = json.loads(raw_line)
        except Exception as e:
            logger.error(f"JSON 解析失败: {e}")
            return {
                "jsonrpc": "2.0",
                "id": None,
                "error": {"code": -32700, "message": f"Parse error: {str(e)}"}
            }

        req_id = req.get("id")
        method = req.get("method")
        params = req.get("params", {})

        logger.info(f"收到调用请求: method={method}, id={req_id}")

        # 1. 初始化握手
        if method == "initialize":
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "protocolVersion": self.PROTOCOL_VERSION,
                    "capabilities": {
                        "tools": {"listChanged": False},
                        "resources": {"subscribe": False, "listChanged": False},
                        "prompts": {"listChanged": False}
                    },
                    "serverInfo": {
                        "name": self.SERVER_NAME,
                        "version": self.SERVER_VERSION
                    }
                }
            }

        # 2. 客户端完成握手通知
        elif method == "notifications/initialized":
            logger.info("MCP 客户端握手完成，正式就绪！")
            return None

        # 3. 探活 ping
        elif method == "ping":
            return {"jsonrpc": "2.0", "id": req_id, "result": {}}

        # 4. 工具查询与执行
        elif method == "tools/list":
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {"tools": TOOLS_SCHEMA}
            }

        elif method == "tools/call":
            tool_name = params.get("name")
            arguments = params.get("arguments", {})
            logger.info(f"执行工具: {tool_name}")
            result = execute_tool(tool_name, arguments)
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": result
            }

        # 5. 资源查询与读取
        elif method == "resources/list":
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {"resources": RESOURCES_SCHEMA}
            }

        elif method == "resources/read":
            uri = params.get("uri")
            try:
                data = read_resource(uri)
                return {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": data
                }
            except Exception as e:
                return {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "error": {"code": -32602, "message": f"Resource error: {str(e)}"}
                }

        # 6. 提示词模板
        elif method == "prompts/list":
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {"prompts": PROMPTS_SCHEMA}
            }

        elif method == "prompts/get":
            prompt_name = params.get("name")
            arguments = params.get("arguments", {})
            try:
                messages = get_prompt_messages(prompt_name, arguments)
                return {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {
                        "description": prompt_name,
                        "messages": messages
                    }
                }
            except Exception as e:
                return {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "error": {"code": -32602, "message": f"Prompt error: {str(e)}"}
                }

        # 7. 未知方法拦截
        else:
            logger.warning(f"未知的方法调用: {method}")
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {"code": -32601, "message": f"Method not found: {method}"}
            }

    def start_stdio(self):
        """进入标准输入输出阻塞式主循环"""
        logger.info(f"启动 WeChat-H5-DevTools MCP Server (v{self.SERVER_VERSION}) - stdio 模式...")
        self.running = True

        # 保证标准输出 UTF-8 编码
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8")
        if hasattr(sys.stdin, "reconfigure"):
            sys.stdin.reconfigure(encoding="utf-8")

        # 核心防污染隔离：保留物理 stdout 专用于传输 JSON-RPC 报文
        # 将全局 sys.stdout 重定向至 sys.stderr，彻底防止第三方库或工具函数的 print 污染协议通道
        real_stdout = sys.stdout
        sys.stdout = sys.stderr

        try:
            for line in sys.stdin:
                if not self.running:
                    break
                resp = self.handle_message(line)
                if resp is not None:
                    out_text = json.dumps(resp, ensure_ascii=False)
                    real_stdout.write(out_text + "\n")
                    real_stdout.flush()
        except (KeyboardInterrupt, BrokenPipeError):
            logger.info("收到退出信号，MCP 服务安全退出。")
        except Exception as e:
            logger.error(f"MCP 服务运行时异常: {e}")
        finally:
            sys.stdout = real_stdout
            self.running = False


def run_stdio_server():
    """入口函数"""
    server = MCPServer()
    server.start_stdio()


if __name__ == "__main__":
    run_stdio_server()
