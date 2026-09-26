#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
WeChat-H5-DevTools setup.py shim.
All configuration is driven by pyproject.toml (PEP 517 / PEP 621).
This file provides full backwards-compatibility for older pip and setuptools versions.
"""
from setuptools import setup, find_packages
from pathlib import Path

def read_requirements():
    req_path = Path(__file__).parent / "requirements.txt"
    if req_path.exists():
        return [
            line.strip()
            for line in req_path.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.startswith("#")
        ]
    return []

if __name__ == "__main__":
    try:
        setup()
    except Exception:
        setup(
            name="wechat-h5-devtools",
            version="1.1.2",
            description="微信公众号与内嵌 H5 满血调试与逆向工程套件",
            license="CC-BY-NC-SA-4.0",
            packages=find_packages(),
            install_requires=read_requirements(),
            entry_points={
                "console_scripts": [
                    "wx-h5 = wechat_h5_devtools.cli:main"
                ]
            },
            package_data={
                "wechat_h5_devtools": [
                    "injector/assets/*",
                    "injector/scripts/*.js",
                    "injector/*.proto",
                    "sandbox/_runtime_extension/*",
                    "sandbox/polyfills/*.js",
                    "extractor/*.js",
                    "gui/*",
                    "resources/config/*"
                ]
            },
            include_package_data=True,
            python_requires=">=3.9",
        )

