# WeChat-H5-DevTools Complete FAQ & Troubleshooting Guide

This document gathers solutions for frequent troubleshooting issues when using WeChat-H5-DevTools across Frida Hook injection, transparent proxy middleware, offline browser sandbox emulation, AST deobfuscation, and GUI desktop interaction.

---

## Table of Contents

- [I. General Setup & Environment](#i-general-setup--environment)
  - [Q1: 'wx-h5' is not recognized as an internal or external command?](#q1-wx-h5-is-not-recognized-as-an-internal-or-external-command)
  - [Q8: Node.js crashes with JavaScript heap out of memory during AST deobfuscation?](#q8-nodejs-crashes-with-javascript-heap-out-of-memory-during-ast-deobfuscation)
  - [Q11: Missing WebView2 Runtime when starting GUI?](#q11-missing-webview2-runtime-when-starting-gui)
- [II. Kernel Hooking & Process Identification](#ii-kernel-hooking--process-identification)
  - [Q2: 'wx-h5 hook' reports existing WeChat process already running?](#q2-wx-h5-hook-reports-existing-wechat-process-already-running)
  - [Q5: How does the tool decouple across WeChat client and RadiumWMPF kernel upgrades?](#q5-how-does-the-tool-decouple-across-wechat-client-and-radiumwmpf-kernel-upgrades)
  - [Q7: Dozens of WeChat processes exist in Task Manager. How does it lock the renderer?](#q7-dozens-of-wechat-processes-exist-in-task-manager-how-does-it-lock-the-renderer)
  - [Q10: Hook injection fails with 'Access is denied' (os error 5)?](#q10-hook-injection-fails-with-access-is-denied-os-error-5)
  - [Q12: WeChat 4.x Official Accounts or web pages render as solid black screen?](#q12-wechat-4x-official-accounts-or-web-pages-render-as-solid-black-screen)
- [III. Transparent Proxy & Network Routing](#iii-transparent-proxy--network-routing)
  - [Q4: In-app browser cannot connect or reports proxy connection refused?](#q4-in-app-browser-cannot-connect-or-reports-proxy-connection-refused)
  - [Q9: Assets return 403 Forbidden or CORS blocked in sandbox/overrides?](#q9-assets-return-403-forbidden-or-cors-blocked-in-sandboxoverrides)
- [IV. Local Overrides & Sandbox Emulation](#iv-local-overrides--sandbox-emulation)
  - [Q3: Offline browser shows 'Please open in WeChat' or custom JSSDK APIs fail?](#q3-offline-browser-shows-please-open-in-wechat-or-custom-jssdk-apis-fail)
  - [Q6: After Local Overrides, refreshing still serves old remote cached code?](#q6-after-local-overrides-refreshing-still-serves-old-remote-cached-code)

---

## I. General Setup & Environment

### Q1: 'wx-h5' is not recognized as an internal or external command?

- **Cause**: Python virtual environment or system Scripts path is not added to your system PATH, or package is not installed in editable mode.
- **Solution**:
  1. In the repository root, install dependencies and editable CLI entry:
     ```bash
     pip install -r requirements.txt
     pip install -e .
     ```
  2. Ensure your Python Scripts directory is added to system Path;
  3. Or invoke directly via Python module entry:
     ```bash
     python main.py <command> [args]
     ```

---

### Q8: Node.js crashes with JavaScript heap out of memory during AST deobfuscation?

- **Cause**: Monolithic bundles expand 20-40x in Babel AST memory, hitting default 1.4GB Node.js memory ceiling.
- **Solution**:
  1. **Allocate V8 virtual heap**:
     ```bash
     # Windows PowerShell
     $env:NODE_OPTIONS="--max-old-space-size=8192"
     wx-h5 deobfuscate "./output/sample_app/344"

     # Windows CMD
     set NODE_OPTIONS=--max-old-space-size=8192
     wx-h5 deobfuscate "./output/sample_app/344"
     ```
  2. **Enable Chunked Deobfuscation (--chunked)**:
     Splits modules by dictionary entries, folding constants per isolated module before merging, dropping peak memory from 4GB to ~400MB:
     ```bash
     wx-h5 deobfuscate "./output/sample_app/344" --chunked
     ```
  3. **Exclude third-party vendor libraries (--exclude-libs)**:
     Skip heavy libraries such as Vue, React, ECharts, and CryptoJS.

---

### Q11: Missing WebView2 Runtime when starting GUI?

- **Cause**: Modern Fluent WebGUI leverages Windows native Microsoft Edge WebView2 hardware acceleration and pywebview bridge.
- **Solution**:
  1. **Install GUI bridge**: Run pip install pywebview;
  2. **Ensure WebView2 Runtime**: Windows 11 includes WebView2 Runtime out-of-the-box. For Windows 10, install Microsoft Edge WebView2 Evergreen Runtime;
  3. **Browser Fallback**: If WebView2 cannot be installed, launch with --browser to preview directly in your default browser:
     ```bash
     wx-h5 gui --browser
     ```

---

## II. Kernel Hooking & Process Identification

### Q2: 'wx-h5 hook' reports existing WeChat process already running?

- **Cause**: Running instances cannot be cold-launched with remote debugging parameters attached.
- **Solution**:
  1. Completely exit WeChat from system tray;
  2. Or terminate remaining processes in terminal:
     ```bash
     taskkill /F /IM WeChat.exe /IM Weixin.exe /IM WeChatAppEx.exe /T
     ```
  3. Re-run wx-h5 hook.

---

### Q5: How does the tool decouple across WeChat client and RadiumWMPF kernel upgrades?

- **Mechanism**:
  1. **Dynamic Signature Masking**: Runtime scanning of Chromium VTables and DevTools branch without static memory offsets;
  2. **Three-Tier Redundancy**:
     - L1: Process creation intercept (CreateProcessW) adding --remote-debugging-port;
     - L2: WeixinJSBridge dispatch pipeline injection injecting vConsole script tags;
     - L3: Transparent network proxy fallback injecting script payloads in stream;
  3. **Incrementally updated address maps**: addresses.<kernel_version>.json configuration pool covering kernels 25560, 25510, 16389, etc.

---

### Q7: Dozens of WeChat processes exist in Task Manager. How does it lock the renderer?

- **Mechanism**:
  1. **PEB Inspection**: Auto-scans CLI flags for --type=renderer and loaded module maps (radium.dll / wmpf.dll);
  2. **Targeted Listing**:
     ```bash
     wx-h5 inspect --list
     wx-h5 hook --pid <PID>
     ```

---

### Q10: Hook injection fails with 'Access is denied' (os error 5)?

- **Cause**: Windows UAC Integrity Level isolation. Medium integrity terminals cannot inspect High integrity Administrator processes.
- **Solution**:
  1. Right click PowerShell/CMD and select **Run as administrator**;
  2. Standalone binary builds automatically request UAC elevation via embedded requireAdministrator manifest.

---

### Q12: WeChat 4.x Official Accounts or web pages render as solid black screen?

- **Symptoms**:
  Clicking "Official Accounts" on the left navigation bar or opening any in-app H5 page displays a completely black, blank container with no articles or graphics rendered.
- **Root Cause Analysis**:
  In WeChat 4.x, the host shell (`Weixin.exe`) and the web renderer are completely decoupled:
  1. **Host Container**: `Weixin.exe` only draws borders and toolbars. In dark mode, its empty background is solid black;
  2. **Abrupt Renderer Termination**: When opening web content, `WeChatAppEx.exe` (Blink/flue.dll) is spawned to composite web frames into the host container. If this child process crashes on launch, the container exposes its default black void;
  3. **Three Critical Triggers**:
     - **GPU Rendering Pipeline Crash (~70%)**: Hardware acceleration conflicts with specific display drivers (especially Intel integrated graphics or dynamic dual-GPU switching), causing Chromium to fail three times and terminate the render pipeline;
     - **Corrupted Hot-Updated Runtime Components (~20%)**: Automatically downloaded RadiumWMPF / flue.dll components in `%APPDATA%` were corrupted by power cutoff or antivirus isolation;
     - **Orphan Zombie Process Lock (~10%)**: A lingering `WeChatAppEx.exe` holds exclusive SQLite/LevelDB locks, blocking newly spawned child processes from initializing.
- **Definitive 4-Step Permanent Solution**:
  1. **Step 1: Completely purge corrupted runtime and deadlocks**
     - Open Task Manager and terminate all `Weixin.exe` and `WeChatAppEx.exe` processes;
     - Press `Win + R`, enter `%APPDATA%\Tencent\xwechat` and press Enter;
     - Delete the `xplugin` folder (WeChat will cleanly re-download fresh components on launch);
     - Delete the `crash` folder to clear lock files and error dumps.
  2. **Step 2: Windows System Graphics Preference lockdown**
     - Go to Windows Settings -> System -> Display -> Graphics Settings;
     - Add `Weixin.exe` and `WeChatAppEx.exe`, and explicitly set their Graphics Preference to **"High Performance"** (discrete GPU) or **"Power Saving"** (integrated GPU) to eliminate dual-GPU switching crashes.
  3. **Step 3: Antivirus exclusion for custom non-standard paths**
     - If WeChat is installed in non-standard drives (e.g. `D:\TOOLSOFTWARE\Tencent\WeChat`), add both the installation folder and `%APPDATA%\Tencent\` to your security software whitelist.
  4. **Step 4: Pin to physically verified stable version 4.1.10.27**
     - If issues persist on experimental builds, switch to the physically verified **4.1.10.27** release, which features rock-solid Chromium VTable topology and zero renderer crashes.

---

## III. Transparent Proxy & Network Routing

### Q4: In-app browser cannot connect or reports proxy connection refused?

- **Cause**: Port conflicts (e.g. Clash, Fiddler), system proxy override, or untrusted HTTPS root certificate.
- **Solution**:
  1. Check port: netstat -ano | findstr 8899; use --port 8999 if conflict found;
  2. Check Windows Proxy settings pointing to 127.0.0.1:8899;
  3. Install generated Root CA into **Trusted Root Certification Authorities**.

---

### Q9: Assets return 403 Forbidden or CORS blocked in sandbox/overrides?

- **Solution**:
  1. Reverse proxy automatically adds Referer: https://servicewechat.com/ and genuine WeChat desktop UA header;
  2. Automatically strips Content-Security-Policy and injects Access-Control-Allow-Origin: *.

---

## IV. Local Overrides & Sandbox Emulation

### Q3: Offline browser shows 'Please open in WeChat' or custom JSSDK APIs fail?

- **Solution**:
  Launch via wx-h5 open "<URL>" --browser edge --ua ios. At document_start (0ms), mock implementations for 30+ WeixinJSBridge functions are injected before page scripts execute.

---

### Q6: After Local Overrides, refreshing still serves old remote cached code?

- **Solution**:
  1. Proxy crushes cache headers with no-cache, no-store, must-revalidate and strips ETag;
  2. Clear cache in vConsole Storage tab or delete %APPDATA%\Tencent\WeChat\radium\web\cache;
  3. Append random query param (e.g. ?_t=1726315890) to bust the cache.
