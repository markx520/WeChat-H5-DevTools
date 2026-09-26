# WeChat-H5-DevTools 开发者贡献指南与全链路排错溯源规范 (Contributing & Troubleshooting)

感谢您关注并参与 **WeChat-H5-DevTools** 项目的开源建设！

本项目致力于构建面向微信 4.x/RadiumWMPF 新内核的工业级 Webview 调试与安全分析工具箱。为了让所有外部开发者、逆向安全研究人员与前端工程师能够**低门槛、无缝、高效地参与本项目维护与新内核适配**，特制定本规范。

---

## 目录索引

- [一、本地研发环境极速搭建](#一本地研发环境极速搭建)
- [二、系统架构全景与模块职责拓扑](#二系统架构全景与模块职责拓扑)
- [三、微信版本升级与 RadiumWMPF 内核适配 SOP (核心秘籍)](#三微信版本升级与-radiumwmpf-内核适配-sop-核心秘籍)
- [四、五大核心故障排错溯源定位诊断矩阵](#四五大核心故障排错溯源定位诊断矩阵)
- [五、代码风格、规范与 PR 提交门禁](#五代码风格规范与-pr-提交门禁)

---

## 一、本地研发环境极速搭建

### 1. 基础环境依赖基线
- **Python**：3.9+（推荐 3.10 ~ 3.12 64-bit）
- **Node.js**：18+ LTS（用于驱动 AST 语法树反混淆与 Webpack 分包还原引擎）
- **C/C++ 运行时**：Windows 10/11 需具备 Microsoft Visual C++ 2015-2022 Redistributable (x64)
- **微信客户端**：测试需在本地安装 Windows 微信 3.9.x、4.0.x 或 4.1.x（主推当前官方最新版 4.1.13.12）

### 2. 本地仓库克隆与开发依赖安装
在终端（PowerShell 或 CMD）中执行以下命令：

```bash
# 1. 克隆代码仓库
git clone https://github.com/markx520/WeChat-H5-DevTools.git
cd WeChat-H5-DevTools

# 2. 创建并激活 Python 虚拟环境 (推荐)
python -m venv venv
# Windows PowerShell
.\venv\Scripts\Activate.ps1
# Windows CMD
.\venv\Scripts\activate.bat

# 3. 安装项目运行依赖
pip install -r requirements.txt

# 4. 以可编辑开发模式注册 CLI 入口 (重要：任何源码修改立即生效)
pip install -e .

# 5. 安装 GUI 扩展依赖 (若需调试桌面客户端)
pip install pywebview
```

### 3. 环境就绪诊断验证
安装完成后，在终端运行内置体检诊断命令：
```bash
wx-h5 doctor
```
**期望输出**：
- Python 解释器版本 [PASS]
- Frida 动态插桩库版本 [PASS]
- Node.js 运行时及全局路径 [PASS]
- 微信主程序路径与 RadiumWMPF 内核版本探测 [PASS]

---

## 二、系统架构全景与模块职责拓扑

项目源码位于 `wechat_h5_devtools/` 目录下，遵循高内聚、低耦合的模块化设计：

```text
wechat_h5_devtools/
├── cli.py                     # CLI 命令行主调度中枢 (Click 框架)
├── injector/                  # 调试通道接管与透明代理中枢
│   ├── process_hooker.py      # 微信进程启动参数管理与调试端口调度引擎
│   ├── proxy_injector.py      # 本地 HTTP/HTTPS 透明代理网关
│   ├── wechat_finder.py       # 微信多版本路径与内核版本自适应探测器
│   ├── assets/                # 内置静态资源 (vconsole.min.js 等)
│   └── scripts/hook_inapp.js  # 进程级调试通道适配探针
├── sandbox/                   # 外部脱机浏览器高保真沙箱
│   ├── browser_launcher.py    # Edge / Chrome 独立沙箱调起器
│   ├── user_agents.py         # 微信各平台 User-Agent 矩阵生成器
│   └── polyfills/             # 微信 JSBridge 环境模拟与 Polyfill
├── extractor/                 # AST 前端代码格式化与静态审计引擎
│   ├── deobfuscator.py        # AST 代码解构与格式化调度器
│   ├── webcrack_runner.js     # Node.js 运行时执行环境
│   ├── project_dumper.py      # Webpack 静态分包提取器
│   ├── sourcemap_rebuilder.py # SourceMap 源码映射还原器
│   └── api_analyzer.py        # 前端 API 路由与参数静态解析器
├── gui/                       # 桌面 GUI 交互组件 (Modern Fluent WebGUI / pywebview)
├── utils/                     # 通用工具集 (日志落盘、路径解析)
└── resources/config/          # 各代微信内核版本配置池 (addresses.*.json)
```

---

## 三、内核版本配置文件扩展说明 (Kernel Configuration)

当微信客户端发布新版本时，工具采用解耦的 JSON 配置文件实现平滑兼容。开发者参与新版本适配时，请按以下标准流程提交配置：

### 1. 确认当前系统生效的内核构建号
按快捷键 Win + R 输入并回车：
```text
%AppData%\Tencent\xwechat\XPlugin\Plugins\RadiumWMPF
```
查看最新生成的纯数字文件夹（如 `25510`、`25560`），该数字即为当前内核构建号。

### 2. 添加对应内核配置文件
在 `wechat_h5_devtools/resources/config/` 目录下添加或完善 `addresses.<版本构建号>.json` 文件，声明目标架构与调试端口接管参数：
```json
{
  "kernel_version": "25560",
  "arch": "x64",
  "target_module": "radium.dll",
  "cli_flags": [
    "--remote-debugging-port=8899",
    "--enable-blink-features=DevTools"
  ]
}
```

### 3. 执行环境体检验证
运行环境体检命令验证配置生效：
```bash
wx-h5 doctor
```

---

## 四、核心排障溯源定位诊断矩阵

| 故障场景与现象 | 根因分类 | 核心排障文件与代码位置 | 自愈排障与定位步骤 |
| :--- | :--- | :--- | :--- |
| **1. 启动报拒绝访问 (Access is denied / os error 5)** | Windows UAC 完整性级别隔离 | `wechat_h5_devtools/injector/process_hooker.py` | 微信以管理员权限启动时，普通终端无权附加调试端口。<br>1. 右键终端以**“管理员身份运行”**；<br>2. 检查当前用户组权限设置。 |
| **2. 启动代理后微信内置网页无法联网** | 端口冲突或 HTTPS 根证书未受信 | `wechat_h5_devtools/injector/proxy_injector.py` | 1. 运行 `netstat -ano \| findstr 8899`，若冲突改用 `wx-h5 proxy --port 8999`；<br>2. 安装代理证书至受信任的根证书颁发机构。 |
| **3. 微信大版本升级后调试服务无响应** | 内核构建号未配置或调试端口未通 | `wechat_h5_devtools/injector/wechat_finder.py` | 运行 `wx-h5 doctor` 检查当前内核版本是否在已适配列表中；如为新版本请补充对应配置。 |
| **4. 网页调试时显示线上旧缓存** | 浏览器磁盘强缓存与 304 协商 | `wechat_h5_devtools/sandbox/browser_launcher.py` | 1. 工具默认已重写响应头注入 `no-cache, no-store`；<br>2. 在 vConsole 的 Storage 面板点击 Clear Cache 刷新缓存。 |
| **5. AST 代码解析时 Node 报内存溢出** | V8 堆内存默认上限限制 | `wechat_h5_devtools/extractor/deobfuscator.py` | 1. 扩大 Node 内存限制：`$env:NODE_OPTIONS="--max-old-space-size=8192"`；<br>2. 开启分块增量处理模式。 |

---

## 五、代码风格、规范与 PR 提交门禁

为了保证代码库的工业级防腐与技术合规，所有提交至本项目的 Pull Request (PR) 必须 100% 满足以下门禁：

### 1. 语法检查与静态编译门禁
提交前必须自检所有修改过的 Python 文件，确保零语法错误：
```bash
python -m py_compile wechat_h5_devtools/**/*.py
```

### 2. 100% 纯净日志与零 Emoji 铁律
- 日志时间戳格式统一为：`[%Y-%m-%d %H:%M:%S]` 或 `[%H:%M:%S]`；
- **严禁包含任何 Emoji 与装饰符号**；统一采用纯文本规范化标签：`[INFO]`, `[SUCCESS]`, `[ERROR]`, `[WARN]`, `[PASS]`, `[HOOK]`, `[PROXY]`, `[AST]`。

### 3. 敏感数据与凭据物理隔离
- **绝对严禁提交含有任何真实用户敏感信息的数据**：包括但不限于用户 OpenID、SessionKey、Auth Token、真实商户密钥、私有抓包 HAR 文件等；
- 测试用例中的凭据必须使用标准掩码（如 `wx4b9281**********************`）。

### 4. Git Commit 信息规范 (Conventional Commits)
Commit 规范示例：
- `feat(injector): add support for RadiumWMPF kernel 26010`
- `fix(proxy): resolve port conflict auto-fallback mechanism`
- `docs(faq): add troubleshooting for SSL certificate verification`
- `perf(extractor): optimize chunked AST deobfuscation memory peak`

### 5. 开源技术诚信与非商业合规
- 凡引入第三方开源代码、逆向 PoC 或借鉴思路，必须按照项目规则在 `README.md` 与关于面板中添加完整的 GitHub 原作者仓库链接；
- 本项目基于 **CC BY-NC-SA 4.0** 协议开源，任何 PR 均默认继承该协议，严禁夹带商业专有代码或为二手倒卖保留隐蔽特权通道。
