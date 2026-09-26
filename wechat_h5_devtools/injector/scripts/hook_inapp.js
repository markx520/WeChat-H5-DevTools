/**
 * WeChat-H5-DevTools 进程 Hook 脚本 (Frida 17+ 兼容版)
 * 功能: 拦截微信主进程及衍生进程 CreateProcessW，实现 Browser 管理进程与 Renderer 渲染进程分流精准注入
 */

var TARGET_CDP_PORT = "{{CDP_PORT}}";
if (TARGET_CDP_PORT.indexOf("{{") !== -1) {
    TARGET_CDP_PORT = "9222";
}

var TARGET_PROXY_PORT = "{{PROXY_PORT}}";
if (TARGET_PROXY_PORT.indexOf("{{") !== -1) {
    TARGET_PROXY_PORT = "8899";
}

var ENABLE_PROXY = "{{ENABLE_PROXY}}";
if (ENABLE_PROXY.indexOf("{{") !== -1) {
    ENABLE_PROXY = "true";
}

var TARGET_BACKEND_PORT = "{{BACKEND_PORT}}";
if (TARGET_BACKEND_PORT.indexOf("{{") !== -1) {
    TARGET_BACKEND_PORT = "62000";
}

var cpsPtr = null;
try {
    cpsPtr = Process.getModuleByName("kernel32.dll").findExportByName("CreateProcessW");
} catch (e) {
    cpsPtr = Module.findExportByName(null, "CreateProcessW");
}

var allocatedStrings = [];

if (cpsPtr) {
    send(JSON.stringify({
        event: "hook_installed",
        address: cpsPtr.toString(),
        arch: Process.arch
    }));

    Interceptor.attach(cpsPtr, {
        onEnter: function (args) {
            this.cmdlinePtr = args[1];
            if (this.cmdlinePtr) {
                var cmd = this.cmdlinePtr.readUtf16String();
                if (cmd) {
                    var lowerCmd = cmd.toLowerCase();
                    if (lowerCmd.indexOf("wechatappex.exe") !== -1 || lowerCmd.indexOf("weixinext.exe") !== -1 || lowerCmd.indexOf("--type=renderer") !== -1) {
                        // 严格过滤崩溃收集器
                        if (lowerCmd.indexOf("crashpad") === -1) {
                            var newCmd = cmd;
                            var isBrowserMaster = (lowerCmd.indexOf("--type=") === -1);
                            var isRenderer = (lowerCmd.indexOf("--type=renderer") !== -1);

                        // 1. 通用无沙箱模式与开发者检查器支持
                        if (newCmd.indexOf("--no-sandbox") === -1) {
                            newCmd += " --no-sandbox";
                        }
                        if (newCmd.indexOf("--enable-chrome-inspector") === -1) {
                            newCmd += " --enable-chrome-inspector";
                        }
                        if (newCmd.indexOf("--xweb-enable-inspect") === -1) {
                            newCmd += " --xweb-enable-inspect=1";
                        }

                        // 2. 透明代理注入（按需启用）
                        if (ENABLE_PROXY === "true") {
                            if (newCmd.indexOf("--ignore-certificate-errors") === -1) {
                                newCmd += " --ignore-certificate-errors";
                            }
                            if (newCmd.indexOf("--proxy-server") === -1) {
                                newCmd += " --proxy-server=http://127.0.0.1:" + TARGET_PROXY_PORT;
                            }
                        }

                        // 3. 核心分流：仅对顶层 Browser 管理进程注入远程 CDP 调试端口
                        if (isBrowserMaster) {
                            if (newCmd.indexOf("--remote-debugging-port") === -1) {
                                newCmd += " --remote-debugging-port=" + TARGET_CDP_PORT;
                            }
                            // 现代 Chromium 必备：允许全域 WebSocket 握手，杜绝 403 Forbidden
                            if (newCmd.indexOf("--remote-allow-origins") === -1) {
                                newCmd += " --remote-allow-origins=*";
                            }
                            // 跨域与站点隔离安全松绑，确保外部 DevTools 能够探测所有 WebContents
                            if (newCmd.indexOf("--disable-web-security") === -1) {
                                newCmd += " --disable-web-security";
                            }
                            if (newCmd.indexOf("--disable-features=IsolateOrigins") === -1) {
                                newCmd += " --disable-features=IsolateOrigins,site-per-process";
                            }
                        }

                        this.injectedCmd = newCmd;
                        this.isBrowserMaster = isBrowserMaster;

                        // 4. 架构自适应内存覆写并防止 GC 回收
                        var buf = Memory.allocUtf16String(newCmd);
                        allocatedStrings.push(buf);
                        args[1] = buf;

                        // x64 架构下同步覆写 rdx 寄存器；ia32 架构仅依赖 args[1]
                        if (Process.arch === "x64" && this.context.rdx !== undefined) {
                            this.context.rdx = buf;
                        }
                    }
                }
            }
        }
    },
        onLeave: function (retval) {
            if (this.injectedCmd) {
                send(JSON.stringify({
                    event: "process_injected",
                    is_browser_master: this.isBrowserMaster,
                    cdp_port: this.isBrowserMaster ? TARGET_CDP_PORT : null,
                    cmd: this.injectedCmd
                }));
            }
        }
    });
} else {
    send(JSON.stringify({
        event: "hook_error",
        message: "未能定位到 CreateProcessW 函数导出！"
    }));
}

/**
 * 进程内自建 CDP 服务端 (代表微信主动在 WeChatAppEx.exe 进程内监听 9222 端口)
 * 具备双向数据中继 (Pump) 与降级容灾能力
 */
function initInProcessCDPServer() {
    var isMaster = false;
    try {
        var pGetCmd = Module.findGlobalExportByName("GetCommandLineW");
        if (pGetCmd) {
            var fnCmd = new NativeFunction(pGetCmd, "pointer", []);
            var fullCmd = fnCmd().readUtf16String();
            if (fullCmd) {
                var lowerCmd = fullCmd.toLowerCase();
                if (lowerCmd.indexOf("wechatappex.exe") !== -1 || lowerCmd.indexOf("weixinext.exe") !== -1) {
                    // 仅针对顶层 Browser 管理主进程启动监听，规避渲染子进程 EADDRINUSE 冲突
                    if (lowerCmd.indexOf("--type=") === -1) {
                        isMaster = true;
                    }
                }
            }
        }
    } catch (e) {}

    if (!isMaster) {
        return;
    }

    var targetPortInt = parseInt(TARGET_CDP_PORT, 10) || 9222;
    var backendPortInt = parseInt(TARGET_BACKEND_PORT, 10) || 62000;

    function pump(source, dest, onClose) {
        source.read(16384).then(function (buf) {
            if (buf.byteLength === 0) {
                onClose();
                return;
            }
            dest.writeAll(buf).then(function () {
                pump(source, dest, onClose);
            }).catch(onClose);
        }).catch(onClose);
    }

    Socket.listen({
        family: "ipv4",
        host: "127.0.0.1",
        port: targetPortInt
    }).then(function (listener) {
        send(JSON.stringify({
            event: "cdp_server_started",
            port: targetPortInt,
            pid: Process.id,
            backend_port: backendPortInt
        }));

        function acceptLoop() {
            listener.accept().then(function (clientConn) {
                Socket.connect({
                    family: "ipv4",
                    host: "127.0.0.1",
                    port: backendPortInt
                }).then(function (backendConn) {
                    var closed = false;
                    function closeBoth() {
                        if (closed) return;
                        closed = true;
                        try { clientConn.close(); } catch (e) {}
                        try { backendConn.close(); } catch (e) {}
                    }
                    pump(clientConn.input, backendConn.output, closeBoth);
                    pump(backendConn.input, clientConn.output, closeBoth);
                }).catch(function (err) {
                    // 后台 CDP 引擎尚未准备就绪时的轻量级降级兜底响应
                    clientConn.input.read(2048).then(function (reqBuf) {
                        var reqStr = "";
                        var b = new Uint8Array(reqBuf);
                        for (var i = 0; i < b.length; i++) {
                            reqStr += String.fromCharCode(b[i]);
                        }
                        var path = (reqStr.split("\r\n")[0] || "").split(" ")[1] || "";
                        var body = "";
                        if (path.indexOf("/json/version") !== -1) {
                            body = JSON.stringify({
                                "Browser": "WeChat/RadiumWMPF (InProcess-CDP-Fallback)",
                                "Protocol-Version": "1.3",
                                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/122.0.0.0 Safari/537.36 MicroMessenger/4.1.0",
                                "V8-Version": "12.2.281",
                                "WebKit-Version": "537.36",
                                "webSocketDebuggerUrl": "ws://127.0.0.1:" + targetPortInt + "/devtools/browser"
                            });
                        } else {
                            body = JSON.stringify([{
                                "description": "WeChat WebContents",
                                "devtoolsFrontendUrl": "/devtools/inspector.html?ws=127.0.0.1:" + targetPortInt + "/devtools/page/default",
                                "id": "default",
                                "title": "微信公众号推文 / 内置网页",
                                "type": "page",
                                "url": "about:blank",
                                "webSocketDebuggerUrl": "ws://127.0.0.1:" + targetPortInt + "/devtools/page/default"
                            }]);
                        }
                        var resp = "HTTP/1.1 200 OK\r\n" +
                            "Content-Type: application/json; charset=UTF-8\r\n" +
                            "Access-Control-Allow-Origin: *\r\n" +
                            "Content-Length: " + body.length + "\r\n" +
                            "Connection: close\r\n\r\n" + body;
                        var out = [];
                        for (var j = 0; j < resp.length; j++) {
                            out.push(resp.charCodeAt(j));
                        }
                        clientConn.output.writeAll(new Uint8Array(out).buffer).then(function () {
                            clientConn.close();
                        });
                    }).catch(function () {
                        try { clientConn.close(); } catch (e) {}
                    });
                });
                acceptLoop();
            }).catch(function (e) {});
        }
        acceptLoop();
    }).catch(function (err) {
        send(JSON.stringify({
            event: "cdp_server_error",
            port: targetPortInt,
            error: err.toString()
        }));
    });
}

initInProcessCDPServer();


