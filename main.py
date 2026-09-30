#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
WeChat-H5-DevTools 主启动入口
"""

import sys
from pathlib import Path

# 将当前根目录追加到 sys.path
root_dir = Path(__file__).parent.resolve()
if str(root_dir) not in sys.path:
    sys.path.insert(0, str(root_dir))

if __name__ == "__main__":
    try:
        from wechat_h5_devtools.auth.security import enforce_anti_debug
        enforce_anti_debug()
    except Exception:
        pass

    if len(sys.argv) == 1:
        # 用户无参数直接双击运行：默认拉起现代化桌面图形控制台
        from wechat_h5_devtools.gui.app import launch_gui
        launch_gui()
    else:
        # 用户带参数命令行执行：挂接终端控制台并执行满血 CLI
        if sys.platform == "win32":
            try:
                import ctypes
                if ctypes.windll.kernel32.AttachConsole(-1):
                    sys.stdout = open("CONOUT$", "w", encoding="utf-8", errors="replace")
                    sys.stderr = open("CONOUT$", "w", encoding="utf-8", errors="replace")
            except Exception:
                pass
        from wechat_h5_devtools.cli import main
        main()
