#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
tests/test_mcp_cli_e2e.py: WeChat-H5-DevTools 满血 CLI 与原生 MCP 协议端到端全链路集成实测套件
"""

import sys
import os
import json
import time
import subprocess
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def test_cli_commands():
    print("================================================================================")
    print("[PHASE-1] 启动 CLI 新增与强化子命令端到端物理实测")
    print("================================================================================")

    # 1. 测试 status --json
    print("[CLI-1] 测试 python -m wechat_h5_devtools.cli status --json...")
    res = subprocess.run(
        [sys.executable, "-m", "wechat_h5_devtools.cli", "status", "--json"],
        cwd=str(PROJECT_ROOT),
        capture_output=True,
        text=True,
        timeout=10
    )
    assert res.returncode == 0, f"status --json 退出码异常: {res.returncode}, stderr: {res.stderr}"
    status_data = json.loads(res.stdout.strip())
    assert "is_running" in status_data, "返回结果缺少 is_running 字段"
    print(f"  -> [PASS] status --json 执行成功！状态: {status_data.get('status_text')}")

    # 2. 测试 doctor --json
    print("[CLI-2] 测试 python -m wechat_h5_devtools.cli doctor --json...")
    res_doc = subprocess.run(
        [sys.executable, "-m", "wechat_h5_devtools.cli", "doctor", "--json"],
        cwd=str(PROJECT_ROOT),
        capture_output=True,
        text=True,
        timeout=10
    )
    assert res_doc.returncode == 0, f"doctor --json 退出码异常: {res_doc.returncode}"
    doc_data = json.loads(res_doc.stdout.strip())
    assert "wechat" in doc_data and "python" in doc_data, "doctor 数据结构不符合契约"
    print(f"  -> [PASS] doctor --json 验证通过！微信适配: {doc_data['wechat']['has_config']}")

    # 3. 测试 targets --json
    print("[CLI-3] 测试 python -m wechat_h5_devtools.cli targets --json...")
    res_tgt = subprocess.run(
        [sys.executable, "-m", "wechat_h5_devtools.cli", "targets", "--json"],
        cwd=str(PROJECT_ROOT),
        capture_output=True,
        text=True,
        timeout=10
    )
    assert res_tgt.returncode == 0, f"targets --json 退出码异常: {res_tgt.returncode}"
    print(f"  -> [PASS] targets --json 探针非阻塞安全返回！回显长度: {len(res_tgt.stdout.strip())} 字节")

    # 4. 测试 --help 包含所有新命令
    print("[CLI-4] 测试 --help 命令大盘完整性...")
    res_help = subprocess.run(
        [sys.executable, "-m", "wechat_h5_devtools.cli", "--help"],
        cwd=str(PROJECT_ROOT),
        capture_output=True,
        text=True,
        timeout=10
    )
    assert res_help.returncode == 0
    for cmd in ["status", "targets", "eval", "mcp", "doctor", "scan", "dump"]:
        assert cmd in res_help.stdout, f"--help 缺少命令: {cmd}"
    print("  -> [PASS] CLI 命令大盘完整性自检通过！包含 status, targets, eval, mcp 等所有新增指令！")


def test_mcp_stdio_server():
    print("\n================================================================================")
    print("[PHASE-2] 启动原生 MCP stdio 服务端 JSON-RPC 2.0 全链路协议回环实测")
    print("================================================================================")

    proc = subprocess.Popen(
        [sys.executable, "-m", "wechat_h5_devtools.cli", "mcp"],
        cwd=str(PROJECT_ROOT),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1
    )

    try:
        def send_and_recv(req: dict) -> dict:
            line = json.dumps(req, ensure_ascii=False) + "\n"
            proc.stdin.write(line)
            proc.stdin.flush()
            resp_line = proc.stdout.readline()
            return json.loads(resp_line.strip())

        # 1. 协议握手 initialize
        print("[MCP-1] 握手请求 initialize...")
        init_req = {
            "jsonrpc": "2.0",
            "id": 100,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "antigravity-ci", "version": "1.0"}
            }
        }
        init_res = send_and_recv(init_req)
        assert init_res["result"]["protocolVersion"] == "2024-11-05"
        assert init_res["result"]["serverInfo"]["name"] == "wechat-h5-devtools-mcp"
        print("  -> [PASS] initialize 握手成功，返回 protocolVersion=2024-11-05！")

        # 2. 握手确认 notifications/initialized
        print("[MCP-2] 发送 notifications/initialized...")
        proc.stdin.write(json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n")
        proc.stdin.flush()
        print("  -> [PASS] 握手通知发送完成！")

        # 3. 探活 ping
        print("[MCP-3] 发送 ping 请求...")
        ping_res = send_and_recv({"jsonrpc": "2.0", "id": 101, "method": "ping"})
        assert "result" in ping_res
        print("  -> [PASS] ping 响应成功！")

        # 4. 获取工具清单 tools/list
        print("[MCP-4] 获取工具清单 tools/list...")
        tools_res = send_and_recv({"jsonrpc": "2.0", "id": 102, "method": "tools/list"})
        tools = tools_res["result"]["tools"]
        tool_names = [t["name"] for t in tools]
        expected_tools = [
            "wechat_get_runtime_status",
            "wechat_doctor",
            "wechat_list_targets",
            "wechat_inspect_article",
            "wechat_evaluate_script",
            "wechat_static_scan",
            "wechat_deobfuscate_code",
            "wechat_list_supported_versions"
        ]
        for et in expected_tools:
            assert et in tool_names, f"MCP 缺少工具: {et}"
        print(f"  -> [PASS] 成功注册 {len(tools)} 个标准 MCP 工具: {', '.join(tool_names)}")

        # 5. 调用工具 wechat_get_runtime_status
        print("[MCP-5] 真实调用工具 wechat_get_runtime_status...")
        call_status = send_and_recv({
            "jsonrpc": "2.0",
            "id": 103,
            "method": "tools/call",
            "params": {"name": "wechat_get_runtime_status", "arguments": {}}
        })
        assert not call_status["result"]["isError"]
        status_obj = json.loads(call_status["result"]["content"][0]["text"])
        print(f"  -> [PASS] 工具调用成功！微信 PID: {status_obj.get('wechat_pid')}, 内核: {status_obj.get('kernel')}")

        # 6. 调用工具 wechat_list_supported_versions
        print("[MCP-6] 调用工具 wechat_list_supported_versions...")
        call_ver = send_and_recv({
            "jsonrpc": "2.0",
            "id": 104,
            "method": "tools/call",
            "params": {"name": "wechat_list_supported_versions", "arguments": {}}
        })
        assert not call_ver["result"]["isError"]
        ver_obj = json.loads(call_ver["result"]["content"][0]["text"])
        assert ver_obj["count"] >= 34
        print(f"  -> [PASS] 特征库遍历成功！共支持 {ver_obj['count']} 个 RadiumWMPF 内核版本！")

        # 7. 资源列表 resources/list 与读取 resources/read
        print("[MCP-7] 资源列表 resources/list 与读取 resources/read...")
        res_list = send_and_recv({"jsonrpc": "2.0", "id": 105, "method": "resources/list"})
        assert len(res_list["result"]["resources"]) == 3
        read_res = send_and_recv({
            "jsonrpc": "2.0",
            "id": 106,
            "method": "resources/read",
            "params": {"uri": "wechat://versions/supported"}
        })
        assert "contents" in read_res["result"]
        print("  -> [PASS] 3 大动态资源查询与读取成功！")

        # 8. 提示词列表 prompts/list 与获取 prompts/get
        print("[MCP-8] 提示词模板 prompts/list 与 prompts/get...")
        p_list = send_and_recv({"jsonrpc": "2.0", "id": 107, "method": "prompts/list"})
        assert len(p_list["result"]["prompts"]) == 2
        p_get = send_and_recv({
            "jsonrpc": "2.0",
            "id": 108,
            "method": "prompts/get",
            "params": {"name": "audit_wechat_h5_security", "arguments": {"target_url": "https://mp.weixin.qq.com/s/sample"}}
        })
        assert len(p_get["result"]["messages"]) > 0
        print("  -> [PASS] 2 套预设 Prompt 模板提取成功！")

        # 9. 异常调用防护
        print("[MCP-9] 边界防御：调用不存在的方法...")
        err_res = send_and_recv({"jsonrpc": "2.0", "id": 109, "method": "invalid_method"})
        assert err_res["error"]["code"] == -32601
        print("  -> [PASS] 异常方法调用防御通过 (返回 -32601)！")

    finally:
        try:
            proc.stdin.close()
            proc.terminate()
            proc.wait(timeout=3)
        except Exception:
            pass


def main():
    test_cli_commands()
    test_mcp_stdio_server()
    print("\n================================================================================")
    print("[ALL PASS] WeChat-H5-DevTools 满血 CLI 与原生 MCP 服务全链路端到端实测 100% 成功！")
    print("================================================================================\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
