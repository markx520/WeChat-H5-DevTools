# -*- coding: utf-8 -*-
"""
WeChat-H5-DevTools CLI Module Runner
支持通过 python -m wechat_h5_devtools 运行 CLI 入口，解决部分系统 PATH 未包含 Scripts 目录的问题。
"""
import sys
from wechat_h5_devtools.cli import main

if __name__ == "__main__":
    main()
