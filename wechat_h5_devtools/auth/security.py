# -*- coding: utf-8 -*-
"""
wechat_h5_devtools.auth.security
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
商业软件反逆向、反调试与防破解主动防御模块 (anti-reversing-defense)
遵循黑盒原则：检测到异常时静默退出，绝对不外露反调试提示
"""

import sys
import ctypes

def enforce_anti_debug():
    """执行用户态反调试与动态逆向监控检测"""
    if sys.platform != "win32":
        return
    try:
        # 1. 检测 IsDebuggerPresent
        if ctypes.windll.kernel32.IsDebuggerPresent():
            sys.exit(0)
            
        # 2. 检测 CheckRemoteDebuggerPresent
        is_remote = ctypes.c_bool(False)
        handle = ctypes.windll.kernel32.GetCurrentProcess()
        if ctypes.windll.kernel32.CheckRemoteDebuggerPresent(handle, ctypes.byref(is_remote)):
            if is_remote.value:
                sys.exit(0)
    except Exception:
        pass
