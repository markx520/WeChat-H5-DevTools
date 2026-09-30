# -*- coding: utf-8 -*-
"""
wechat_h5_devtools/auth/manager.py
U验证商业授权管理器 (单例模式)
支持一机一码物理绑定、RSA双向加密握手、授权落盘与静默续期
"""

import sys
import os
import json
import time
import base64
import hashlib
from pathlib import Path
from typing import Dict, Any, Optional

# 动态挂载通用 U验证 模块
MODULE_PATH = Path(r"E:\模块\uverif_auth")
if MODULE_PATH.exists() and str(MODULE_PATH) not in sys.path:
    sys.path.insert(0, str(MODULE_PATH))

try:
    from client import UverifClient
    from fingerprint import MachineFingerprint
except ImportError:
    # 纯备用兜底轻量级机器码提取
    class MachineFingerprint:
        @staticmethod
        def generate_machine_code(prefix: str = "HWID", app_salt: str = "DEFAULT", legacy_16: bool = False) -> str:
            import platform
            raw = f"{platform.node()}||{platform.processor()}||{os.getenv('USERNAME', 'USER')}"
            h = hashlib.sha256((raw + app_salt).encode("utf-8")).hexdigest().upper()
            hex_48 = h[:48]
            chunks = [hex_48[i:i+4] for i in range(0, 48, 4)]
            return f"{prefix}-" + "-".join(chunks)

    class UverifClient:
        pass

from .credentials import (
    BASE_URL,
    APP_ID,
    APP_NAME,
    APP_KEY,
    VERSION,
    APP_TITLE,
    SERVICE_PUBLIC_KEY,
    APP_PRIVATE_KEY
)

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
CONFIG_DIR = PROJECT_ROOT / "config"
LICENSE_FILE = CONFIG_DIR / "license.key"

class AuthManager:
    _instance = None

    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            cls._instance = super(AuthManager, cls).__new__(cls)
            cls._instance._init_manager()
        return cls._instance

    def _init_manager(self):
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        self.udid = MachineFingerprint.generate_machine_code(prefix="HWID", app_salt=APP_KEY)
        
        self.client = UverifClient(
            base_url=BASE_URL,
            app_id=APP_ID,
            app_name=APP_NAME,
            app_key=APP_KEY,
            version=VERSION,
            rsa_public_key=SERVICE_PUBLIC_KEY,
            rsa_private_key=APP_PRIVATE_KEY,
            custom_udid=self.udid
        )
        self._auth_cache: Optional[Dict[str, Any]] = None
        self._load_local_license()

    def _load_local_license(self):
        """读取并校验本地 license.key 缓存"""
        if not LICENSE_FILE.exists():
            self._auth_cache = None
            return

        try:
            content = LICENSE_FILE.read_text(encoding="utf-8").strip()
            data = json.loads(base64.b64decode(content.encode("utf-8")).decode("utf-8"))
            
            # 校验硬件机器码一致性 (防拷贝扩散)
            if data.get("udid") != self.udid:
                self._auth_cache = None
                LICENSE_FILE.unlink(missing_ok=True)
                return
                
            expire_time = int(data.get("vip_expire_time", 0))
            if expire_time < int(time.time()):
                self._auth_cache = None
                return

            self._auth_cache = data
            self.client.session_token = data.get("token")
            self.client.is_logged_in = True
            self.client.vip_expire_date = data.get("vip_expire_date")
            self.client.vip_expire_time = expire_time
        except Exception:
            self._auth_cache = None

    def _save_local_license(self, token: str, account: str, vip_date: str, vip_time: int):
        """将授权结果混淆落盘至 config/license.key"""
        data = {
            "token": token,
            "account": account,
            "udid": self.udid,
            "vip_expire_date": vip_date,
            "vip_expire_time": vip_time,
            "saved_at": int(time.time())
        }
        b64 = base64.b64encode(json.dumps(data).encode("utf-8")).decode("utf-8")
        LICENSE_FILE.write_text(b64, encoding="utf-8")
        self._auth_cache = data

    def get_hardware_udid(self) -> str:
        """获取本机专属硬件机器码"""
        return self.udid

    def is_authorized(self) -> bool:
        """判断当前客户端是否具有有效授权"""
        if self._auth_cache and self.client.is_logged_in:
            if self.client.vip_expire_time >= int(time.time()):
                return True
        return False

    def get_auth_info(self) -> Dict[str, Any]:
        """获取当前授权展示详情"""
        is_auth = self.is_authorized()
        if is_auth:
            return {
                "is_authorized": True,
                "account": self._auth_cache.get("account", "VIP用户"),
                "vip_expire_date": self._auth_cache.get("vip_expire_date", "永久会员"),
                "status_text": f"授权正常 · {self._auth_cache.get('vip_expire_date', '永久会员')}",
                "udid": self.udid
            }
        return {
            "is_authorized": False,
            "account": "",
            "vip_expire_date": "未授权",
            "status_text": "未登录 · 需激活",
            "udid": self.udid
        }

    def login_user(self, account: str, password: str) -> Dict[str, Any]:
        """使用账号密码通过 RSA 双向加密进行登录"""
        account = account.strip()
        password = password.strip()
        if not account or not password:
            return {"success": False, "msg": "请输入账号和密码"}

        res = self.client.login_user(account, password)
        if res.get("code") == 0:
            info = res.get("data", {}).get("info", {})
            token = res.get("data", {}).get("token", "")
            vip_date = info.get("vipExpDate") or "永久会员"
            vip_time = int(info.get("vipExpTime") or 9999999999)
            
            # 授权持久化落盘
            self._save_local_license(token, account, vip_date, vip_time)
            return {
                "success": True,
                "msg": "登录成功！",
                "account": account,
                "vip_expire_date": vip_date
            }
        else:
            return {
                "success": False,
                "msg": res.get("msg") or "账号或密码错误"
            }

    def register_user(self, account: str, password: str) -> Dict[str, Any]:
        """注册新账号并自动免密登录与硬件绑定"""
        account = account.strip()
        password = password.strip()
        if not account or not password:
            return {"success": False, "msg": "请输入账号和密码"}
        if len(account) < 5 or len(account) > 18:
            return {"success": False, "msg": "账号长度需在 5~18 位字符之间"}
        if len(password) < 6:
            return {"success": False, "msg": "密码长度不能少于 6 位"}

        reg_res = self.client.register_user(account, password)
        if reg_res.get("code") == 0:
            # 注册成功后自动执行静默登录与硬件机器码绑定
            login_res = self.login_user(account, password)
            if login_res.get("success"):
                return {
                    "success": True,
                    "msg": "注册成功，已自动为您登录！",
                    "account": account,
                    "vip_expire_date": login_res.get("vip_expire_date", "未开通")
                }
            return {"success": True, "msg": "注册成功，请使用新账号登录！", "account": account}
        else:
            return {
                "success": False,
                "msg": reg_res.get("msg") or "注册失败，该账号可能已被占用"
            }

    def modify_password(self, account: str, old_password: str, new_password: str) -> Dict[str, Any]:
        """修改用户密码"""
        account = account.strip()
        old_password = old_password.strip()
        new_password = new_password.strip()
        if not account or not old_password or not new_password:
            return {"success": False, "msg": "请填写完整的账号与密码信息"}
        if len(new_password) < 6:
            return {"success": False, "msg": "新密码长度不能少于 6 位"}
        if old_password == new_password:
            return {"success": False, "msg": "新密码不能与原密码相同"}

        pwd_res = self.client.modify_password(
            new_password=new_password,
            old_password=old_password,
            account=account
        )
        if pwd_res.get("code") == 0:
            return {"success": True, "msg": "密码修改成功，请使用新密码登录！"}
        else:
            return {
                "success": False,
                "msg": pwd_res.get("msg") or "原账号密码验证失败"
            }

    def recharge_card(self, card_key: str, account: str = "", password: str = "") -> Dict[str, Any]:
        """使用卡密为账号充值续期 (支持已登录状态直接充值，或未登录状态验证账号密码充值)"""
        card_key = card_key.strip()
        account = account.strip()
        password = password.strip()

        if not card_key:
            return {"success": False, "msg": "请输入充值卡密"}

        # 若当前未处于登录态，且传入了账号密码，则先进行登录验证
        if not (self.client.is_logged_in and self.client.session_token):
            if not account or not password:
                return {"success": False, "msg": "未登录状态下请填写需要充值的账号与密码"}
            auth_res = self.login_user(account, password)
            if not auth_res.get("success"):
                return {"success": False, "msg": f"账号验证失败: {auth_res.get('msg')}"}

        res = self.client.recharge_by_card(card_key)
        if res.get("code") == 0:
            # 刷新 VIP 状态
            info_res = self.client.get_info()
            if info_res.get("code") == 0:
                user_info = info_res.get("data", {})
                new_date = user_info.get("vipExpDate") or "已充值"
                new_time = int(user_info.get("vipExpTime") or 0)
                self.client.vip_expire_date = new_date
                self.client.vip_expire_time = new_time
            else:
                new_date = self.client.vip_expire_date or "已充值"
                new_time = self.client.vip_expire_time or (int(time.time()) + 30 * 86400)
            target_acc = (self._auth_cache.get("account") if self._auth_cache else "") or account or "VIP用户"
            self._save_local_license(
                self.client.session_token,
                target_acc,
                new_date,
                new_time
            )
            return {"success": True, "msg": f"充值成功！最新有效期至: {new_date}", "vip_expire_date": new_date}
        else:
            return {"success": False, "msg": res.get("msg") or "卡密无效或已被使用"}

    def logout(self) -> bool:
        """退出当前登录并清理本地授权缓存"""
        self._auth_cache = None
        self.client.is_logged_in = False
        self.client.session_token = None
        LICENSE_FILE.unlink(missing_ok=True)
        return True

# 全局单例
auth_mgr = AuthManager()
