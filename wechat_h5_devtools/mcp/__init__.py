# -*- coding: utf-8 -*-
"""
WeChat-H5-DevTools 原生 MCP (Model Context Protocol) 模块中枢
支持 AI 宿主 (Antigravity Nexus, Claude Desktop, Cursor 等) 通过 stdio/JSON-RPC 2.0 协议直接控制微信调试套件
"""

from .server import MCPServer, run_stdio_server

__all__ = ["MCPServer", "run_stdio_server"]
