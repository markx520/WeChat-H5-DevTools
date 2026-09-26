# Changelog

All notable changes to the WeChat-H5-DevTools project are documented in this file.
The format is aligned with standard semantic versioning and changelog conventions.

---

## [1.1.2] - 2026-09-26

1. **Custom WeChat Installation Path Support**: Added host path configuration card and native directory picker in GUI, enabling persistent path storage and auto-detection.
2. **Log Desensitization & Streamlining**: Cleaned high-frequency repetitive logs and sanitized local host disk paths, dramatically reducing token and storage overhead.
3. **Industry-Standard Disclaimer Refactor**: Restructured legal disclaimer into standard four-tier terms covering technical scope, disclaimer, trademarks, and non-commercial open-source licensing.
4. **Repository Link Alignment**: Synchronized all documentation links, project metadata, and issue trackers to `markx520/WeChat-H5-DevTools`.

---

## [1.1.1] - 2026-09-17

1. **Eliminate Hardcoded Host Paths**: Refactored scripts and UI to use adaptive relative path discovery, ensuring immediate out-of-the-box readiness across blank machines.
2. **Deep GUI Package Bundling**: Bundled desktop frontend package into core modules, supporting both one-click CLI invocation and browser preview fallback.
3. **Intelligent Diagnostics Command**: Upgraded `doctor` diagnostic suite to automatically inspect WeChat, browser engines, Node.js, and Python runtimes.
4. **Multi-Platform Compatibility**: Supported dual-track packaging (PEP 621 and setup.py), conditioned winreg/windll imports, and added mirror acceleration guides.

---

## [1.1.0] - 2026-09-15

1. **Browser Debugging Protocol Gateway**: Implemented dual-channel CDP gateway bridging native WeChat windows to external Chrome/Edge DevTools for standalone inspection.
2. **Full-Featured Desktop GUI Workbench**: Shipped dark emerald desktop console with runtime perception, one-click injection, proxy capturing, AST deobfuscation, and log management.
3. **Native Direct-Connect Optimization**: Direct WeChat internal window inspection without unwanted blank tabs, optimized port binding, and hardened architecture mismatch diagnostics.

---

## [1.0.0] - 2026-09-14

1. **Initial Stable Release**: Supported hot-injection of mobile vConsole and native F12 keyboard DevTools into WeChat built-in web views.
2. **Offline Simulation Sandbox**: Provided WeChat API polyfills and high-fidelity isolated sandbox for instant browser preview without host client dependency.
3. **Frontend Source Reconstruction**: Supported recursive resource asset dumping and AST-level JavaScript deobfuscation.
4. **Dark Minimal Desktop Client**: Provided unified GUI dashboard for one-click debugging operations.
