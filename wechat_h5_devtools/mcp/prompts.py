# -*- coding: utf-8 -*-
"""
wechat_h5_devtools.mcp.prompts: 微信调试套件原生 MCP 预设提示词模板
"""

from typing import Dict, Any, List

PROMPTS_SCHEMA = [
    {
        "name": "audit_wechat_h5_security",
        "description": "引导 AI 智能体对指定微信公众号文章或内嵌 H5 执行全流程代码安全与网络通信审计",
        "arguments": [
            {
                "name": "target_url",
                "description": "待审计的目标公众号推文或 H5 网页 URL",
                "required": True
            }
        ]
    },
    {
        "name": "troubleshoot_devtools_connection",
        "description": "引导 AI 智能体根据微信版本与报错信息，自动化排查 DevTools 挂接失败与内核特征未命中问题",
        "arguments": [
            {
                "name": "error_message",
                "description": "报错信息或当前异常表现",
                "required": False
            }
        ]
    }
]


def get_prompt_messages(name: str, args: Dict[str, Any]) -> List[Dict[str, Any]]:
    """生成指定 Prompt 模板的会话消息"""
    if name == "audit_wechat_h5_security":
        url = args.get("target_url", "")
        content = (
            f"长官要求对目标微信 H5 页面进行全维度安全审计。\n"
            f"目标 URL: {url}\n\n"
            f"请按照以下标准流程执行审计工作：\n"
            f"1. 调用 `wechat_get_runtime_status` 核验宿主机微信客户端与内核运行态；\n"
            f"2. 调用 `wechat_list_targets` 检索当前是否有对应的活动页面；\n"
            f"3. 若本地已有导出的前端工程目录，调用 `wechat_static_scan` 提取全量 API 路由与敏感加密签名（AES/RSA/DES）；\n"
            f"4. 总结潜在的数据泄漏风险、越权接口与前端防护加固建议，形成高信噪比报告汇报长官。"
        )
        return [
            {"role": "user", "content": {"type": "text", "text": content}}
        ]

    elif name == "troubleshoot_devtools_connection":
        err = args.get("error_message", "无法正常弹出 DevTools 或未拦截到热键")
        content = (
            f"当前微信 DevTools 调试通道发生异常。\n"
            f"异常表现/报错信息: {err}\n\n"
            f"请执行以下排查诊断 SOP：\n"
            f"1. 调用 `wechat_doctor` 检查 Frida 依赖、浏览器路径与渲染内核版本；\n"
            f"2. 调用 `wechat_list_supported_versions` 确认当前的 RadiumWMPF 构建版本是否在支持清单中；\n"
            f"3. 如果微信客户端经过大版本更新导致构建号不匹配，说明需要提取最新 addresses.<build>.json 内存特征码；\n"
            f"4. 给出清晰明确的排错结论与处置建议。"
        )
        return [
            {"role": "user", "content": {"type": "text", "text": content}}
        ]

    else:
        raise ValueError(f"Prompt not found: {name}")
