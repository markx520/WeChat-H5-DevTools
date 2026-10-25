# -*- coding: utf-8 -*-
"""
wechat_h5_devtools.mcp.resources: 微信调试套件原生 MCP 动态资源矩阵
"""

import json
import urllib.request
from pathlib import Path
from typing import Dict, Any, List, Optional

from ..injector.wechat_finder import WeChatFinder

PROJECT_ROOT = Path(__file__).resolve().parent.parent

RESOURCES_SCHEMA = [
    {
        "uri": "wechat://runtime/status",
        "name": "微信实时运行大盘状态",
        "description": "提供宿主机微信主程序 PID、版本、渲染进程池及连接状态的实时 JSON 视图",
        "mimeType": "application/json"
    },
    {
        "uri": "wechat://targets/active",
        "name": "微信活动渲染目标清单",
        "description": "实时提供通过微信内核 CDP 捕获的公众号文章与内置 H5 页面目标",
        "mimeType": "application/json"
    },
    {
        "uri": "wechat://versions/supported",
        "name": "微信内核已适配构建版本列表",
        "description": "列出所有已提取内存特征码并适配成功的 RadiumWMPF 内核版本",
        "mimeType": "application/json"
    }
]


def read_resource(uri: str) -> Dict[str, Any]:
    """读取指定 URI 的资源内容"""
    if uri == "wechat://runtime/status":
        status = WeChatFinder.get_runtime_status()
        return {
            "contents": [
                {
                    "uri": uri,
                    "mimeType": "application/json",
                    "text": json.dumps(status, ensure_ascii=False, indent=2)
                }
            ]
        }

    elif uri == "wechat://targets/active":
        cdp_port = 62000
        url = f"http://127.0.0.1:{cdp_port}/json/list"
        targets = []
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "WeChat-H5-DevTools-MCP"})
            with urllib.request.urlopen(req, timeout=2) as resp:
                targets = json.loads(resp.read().decode("utf-8"))
        except Exception:
            pass

        return {
            "contents": [
                {
                    "uri": uri,
                    "mimeType": "application/json",
                    "text": json.dumps({"active_targets": targets, "count": len(targets)}, ensure_ascii=False, indent=2)
                }
            ]
        }

    elif uri == "wechat://versions/supported":
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
        return {
            "contents": [
                {
                    "uri": uri,
                    "mimeType": "application/json",
                    "text": json.dumps({"count": len(versions), "versions": versions}, ensure_ascii=False, indent=2)
                }
            ]
        }

    else:
        raise ValueError(f"Resource not found: {uri}")
