"""
Playwright 浏览器管理模块 - 支持持久化登录状态和跨进程复用
支持多平台浏览器配置文件隔离
"""
from __future__ import annotations
import asyncio
import os
import subprocess
import signal
import platform as sys_platform
from typing import Optional, Dict
from pathlib import Path
from playwright.async_api import async_playwright, Browser, Page, BrowserContext, Playwright
from loguru import logger
from crawler.browser_pool import browser_pool

from config import get_settings


def get_chrome_executable_path() -> Optional[str]:
    """获取 Chrome 可执行文件路径（跨平台）"""
    system = sys_platform.system()
    
    if system == "Darwin":  # macOS
        paths = [
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            "/Applications/Chromium.app/Contents/MacOS/Chromium",
        ]
    elif system == "Linux":
        paths = [
            "/opt/google/chrome/chrome",
            "/usr/bin/google-chrome",
            "/usr/bin/chromium-browser",
            "/usr/bin/chromium",
        ]
    elif system == "Windows":
        paths = [
            "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe",
            "C:\\Program Files (x86)\\Google\\Chrome\\Application\\chrome.exe",
        ]
    else:
        return None
    
    for path in paths:
        if Path(path).exists():
            return path
    
    return None  # 使用 Playwright 自带的 Chromium


# 平台CDP端口映射
PLATFORM_CDP_PORTS = {
    "douyin": 9222,
    "xiaohongshu": 9223,
    "shipinhao": 9224,
}


class BrowserManager:
    """浏览器管理器 - 支持跨进程复用和多平台隔离"""
    
    def __init__(
        self,
        headless: bool = None,
        proxy: str = None,
        user_data_dir: str = None,
        platform: str = "douyin",
        user_id: int = None,
    ):
        settings = get_settings()
        self.headless = headless if headless is not None else settings.HEADLESS
        self.proxy = proxy or settings.PROXY
        self.platform = platform
        self.user_id = user_id
        
        # 根据用户ID和平台获取CDP端口
        if user_id:
            self.cdp_port = settings.get_user_cdp_port(user_id, platform)
        else:
            self.cdp_port = PLATFORM_CDP_PORTS.get(platform, 9222)
        
        # 持久化用户数据目录 - 按用户ID和平台隔离
        if user_data_dir:
            self.user_data_dir = user_data_dir
        else:
            self.user_data_dir = str(settings.get_browser_profile_dir(platform, user_id))
        
        # DISPLAY 配置（非 headless 模式）
        self.display = os.environ.get('DISPLAY', '')

        # 确保目录存在
        Path(self.user_data_dir).mkdir(parents=True, exist_ok=True)
        
        self._browser: Optional[Browser] = None
        self._context: Optional[BrowserContext] = None
        self._page: Optional[Page] = None
        self._playwright: Optional[Playwright] = None
        self._is_connected: bool = False  # 是否是连接模式（非启动模式）
    
    async def start(self) -> Browser:
        """启动或连接浏览器"""
        # 先检查浏览器池中是否有现有实例
        existing = await browser_pool.get(self.user_id, self.platform)
        if existing:
            browser = existing.get("browser")
            context = existing.get("context")
            if browser and context:
                # 检查是否还活着 - 更严格的检查
                try:
                    # 尝试检查浏览器是否连接
                    if not browser.is_connected():
                        raise Exception("Browser not connected")
                    # 尝试获取页面来验证连接
                    pages = context.pages
                    self._browser = browser
                    self._context = context
                    self._is_connected = True
                    logger.info(f"[{self.platform}] 复用浏览器池中的实例, user={self.user_id}")
                    return browser
                except Exception as e:
                    logger.warning(f"[{self.platform}] 浏览器池实例已失效: {e}")
                    await browser_pool.unregister(self.user_id, self.platform)
        
        # 先尝试连接已运行的浏览器（通过CDP）
        browser = await self._try_connect_existing()
        if browser:
            self._browser = browser
            self._is_connected = True
            logger.info("已连接到运行中的浏览器")
            # 注册到浏览器池
            await browser_pool.register(self.user_id, self.platform, browser, self._context)
            return browser
        
        # 检查容量，必要时清理旧实例
        await browser_pool.check_capacity()
        
        # 启动新浏览器
        self._is_connected = False
        browser = await self._launch_new_browser()
        
        # 注册到浏览器池
        await browser_pool.register(self.user_id, self.platform, browser, self._context)
        
        return browser
    
    async def _try_connect_existing(self) -> Optional[Browser]:
        """尝试连接已运行的浏览器（通过 CDP）"""
        try:
            if not self._playwright:
                self._playwright = await async_playwright().start()
            
            # 尝试连接用户的所有可能 CDP 端口
            # 一个用户只有一个浏览器，所有平台共享同一个 CDP 端口
            possible_ports = [
                9222,  # 默认抖音端口
                self.cdp_port,  # 当前平台端口
            ]
            # 添加其他平台的端口
            from config import get_settings
            settings = get_settings()
            for plat in ["douyin", "xiaohongshu", "shipinhao"]:
                port = settings.get_user_cdp_port(self.user_id or 1, plat)
                if port not in possible_ports:
                    possible_ports.append(port)
            
            for port in possible_ports:
                try:
                    browser = await self._playwright.chromium.connect_over_cdp(
                        f"http://localhost:{port}",
                        timeout=3000
                    )
                    # 更新 CDP 端口为实际连接的端口
                    self.cdp_port = port
                    # 获取现有上下文
                    contexts = browser.contexts
                    if contexts:
                        self._context = contexts[0]
                        logger.info(f"[{self.platform}] 连接到端口 {port} 的浏览器，共 {len(contexts)} 个上下文")
                    else:
                        self._context = await browser.new_context()
                    return browser
                except Exception as e:
                    logger.debug(f"[{self.platform}] 端口 {port} 连接失败: {e}")
                    continue
            
            logger.debug(f"[{self.platform}] 无法连接到任何现有浏览器")
            return None
            
        except Exception as e:
            logger.debug(f"[{self.platform}] _try_connect_existing 异常: {e}")
            return None
    
    async def _launch_new_browser(self) -> Browser:
        """启动新浏览器"""
        # 先清理可能的锁文件（防止之前的进程没有正常退出）
        import subprocess
        try:
            subprocess.run(["rm", "-rf", f"{self.user_data_dir}/SingletonLock", 
                          f"{self.user_data_dir}/SingletonCookie", 
                          f"{self.user_data_dir}/SingletonSocket"],
                          capture_output=True, timeout=5)
            logger.debug(f"[{self.platform}] 已清理浏览器锁文件")
        except Exception as e:
            logger.debug(f"[{self.platform}] 清理锁文件失败: {e}")
        
        logger.info(f"[{self.platform}] 启动新浏览器, headless={self.headless}")
        
        if not self._playwright:
            self._playwright = await async_playwright().start()
        
        # 浏览器启动参数
        # CDP 调试端口（让 noVNC 能看到这个浏览器窗口）
        # VNC offset: cdp_port - 9222 -> novnc port = 26080 + offset
        vnc_offset = self.cdp_port - 9222
        logger.info(f"[{self.platform}] CDP端口={self.cdp_port}, noVNC观察端口=http://<host>:{26080 + vnc_offset}")

        launch_args = [
            "--no-sandbox",
            "--disable-dev-shm-usage",

            "--disable-gpu",
            "--disable-gpu-compositing",
            "--disable-software-rasterizer",
            "--disable-features=VizDisplayCompositor",

            "--start-maximized",
            "--force-device-scale-factor=1",
            f"--remote-debugging-port={self.cdp_port}",
        ]

        if self.proxy:
            launch_args.append(f"--proxy-server={self.proxy}")
        
        # 获取 Chrome 可执行文件路径（跨平台）
        chrome_path = get_chrome_executable_path()
        
        launch_options = {
            "user_data_dir": self.user_data_dir,
            "headless": self.headless,
            "args": launch_args,
            "user_agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            #"viewport": {"width": 1280, "height": 720},
            "viewport": None,
            "screen": {"width": 1280, "height": 720},
            "device_scale_factor": 1,
            "locale": "zh-CN",
            "timezone_id": "Asia/Shanghai",
            # 关键：不让 Playwright 处理信号，这样进程退出时浏览器不会关闭
            "handle_sigint": False,
            "handle_sigterm": False,
            "handle_sighup": False,
            # DISPLAY 环境变量在启动前设置
        }
        
        if chrome_path:
            launch_options["executable_path"] = chrome_path
            logger.info(f"[{self.platform}] 使用本地 Chrome: {chrome_path}")
        else:
            logger.info(f"[{self.platform}] 使用 Playwright 自带 Chromium")

        # 设置 DISPLAY 环境变量（非 headless 模式）
        if not self.headless and (self.display or os.environ.get("DISPLAY")):
            os.environ["DISPLAY"] = self.display or os.environ.get("DISPLAY", ":99")
            logger.info(f"[{self.platform}] 设置 DISPLAY={os.environ['DISPLAY']}")
        
        # 用 subprocess 启动 chromium（带 CDP 端口），这样进程独立存活，可被下次任务复用
        import subprocess as _sp, socket as _sock, httpx as _httpx
        display = os.environ.get("DISPLAY", ":99")

        # 先检查端口是否已有 chromium，有则直接跳过启动
        _cs = _sock.socket()
        _cs.settimeout(1)
        _port_in_use = _cs.connect_ex(('localhost', self.cdp_port)) == 0
        _cs.close()

        if not _port_in_use:
            cmd = [
                launch_options.get("executable_path", "chromium"),
                f"--remote-debugging-port={self.cdp_port}",
                f"--user-data-dir={self.user_data_dir}",
                "--no-first-run", "--no-sandbox", "--disable-setuid-sandbox",
                "--disable-dev-shm-usage", "--disable-gpu",
                "--disable-blink-features=AutomationControlled",
                "--disable-extensions",
                "--window-position=0,0", "--window-size=1280,720",
                "--force-device-scale-factor=1", "--high-dpi-support=1",
            ]
            env = os.environ.copy()
            env["DISPLAY"] = display
            _sp.Popen(cmd, env=env, stdout=_sp.DEVNULL, stderr=_sp.DEVNULL)
            logger.info(f"[{self.platform}] chromium 进程已启动 (CDP:{self.cdp_port}, DISPLAY={display})")

            # 等待 chromium CDP 就绪（只在新启动时等）
            for _i in range(20):
                await asyncio.sleep(0.5)
                try:
                    async with _httpx.AsyncClient() as _c:
                        _r = await _c.get(f"http://localhost:{self.cdp_port}/json/version", timeout=2)
                        if _r.status_code == 200:
                            logger.info(f"[{self.platform}] CDP 就绪 (尝试 {_i+1} 次)")
                            break
                except Exception:
                    continue
            else:
                logger.warning(f"[{self.platform}] CDP 等待超时，继续尝试连接")
        else:
            logger.info(f"[{self.platform}] CDP:{self.cdp_port} 已有浏览器在运行，跳过启动直接连接")
        
        # 通过 CDP 连接
        browser = await self._playwright.chromium.connect_over_cdp(f"http://localhost:{self.cdp_port}")
        self._context = browser.contexts[0] if browser.contexts else await browser.new_context()
        self._browser = browser

        # 注入反检测脚本
        await self._inject_stealth()

        logger.info(f"[{self.platform}] 浏览器启动成功（CDP端口: {self.cdp_port}，用户数据: {self.user_data_dir}）")
        return self._browser
    
    async def new_page(self) -> Page:
        """创建新页面"""
        if not self._context:
            await self.start()
        
        # 如果已有页面，复用
        pages = self._context.pages
        if pages:
            self._page = pages[0]
            logger.info(f"[{self.platform}] 复用已有页面，当前共 {len(pages)} 个页面")
        else:
            self._page = await self._context.new_page()
            logger.info(f"[{self.platform}] 创建新页面")
        # 关键：强制 viewport
        await self._page.set_viewport_size({
            "width": 1280,
            "height": 720
        })    
        
        return self._page
    
    async def get_page(self) -> Page:
        """获取当前页面"""
        if self._page and not self._page.is_closed():
            return self._page
        return await self.new_page()
    
    async def _inject_stealth(self):
        """注入完整反检测脚本（覆盖主流自动化检测点）"""
        if not self._context:
            return

        await self._context.add_init_script("""
        (() => {
            // 1. 隐藏 webdriver 标记
            Object.defineProperty(navigator, 'webdriver', { get: () => undefined });

            // 2. 伪造 plugins（空 plugins 是自动化特征）
            const fakePlugins = [
                { name: 'Chrome PDF Plugin', filename: 'internal-pdf-viewer', description: 'Portable Document Format' },
                { name: 'Chrome PDF Viewer', filename: 'mhjfbmdgcfjbbpaeojofohoefgiehjai', description: '' },
                { name: 'Native Client', filename: 'internal-nacl-plugin', description: '' },
            ];
            Object.defineProperty(navigator, 'plugins', {
                get: () => Object.assign(fakePlugins, { item: (i) => fakePlugins[i], namedItem: (n) => fakePlugins.find(p => p.name === n), refresh: () => {} }),
            });

            // 3. 语言
            Object.defineProperty(navigator, 'languages', { get: () => ['zh-CN', 'zh', 'en-US', 'en'] });

            // 4. window.chrome 对象（Headless 下缺失）
            if (!window.chrome) {
                window.chrome = {
                    app: { isInstalled: false, InstallState: { DISABLED: 'disabled', INSTALLED: 'installed', NOT_INSTALLED: 'not_installed' }, RunningState: { CANNOT_RUN: 'cannot_run', READY_TO_RUN: 'ready_to_run', RUNNING: 'running' } },
                    runtime: { OnInstalledReason: {}, OnRestartRequiredReason: {}, PlatformArch: {}, PlatformNaclArch: {}, PlatformOs: {}, RequestUpdateCheckStatus: {} },
                    loadTimes: () => ({}),
                    csi: () => ({}),
                };
            }

            // 5. permissions API（自动化环境 query 行为异常）
            const originalQuery = window.navigator.permissions && window.navigator.permissions.query;
            if (originalQuery) {
                window.navigator.permissions.query = (parameters) =>
                    parameters.name === 'notifications'
                        ? Promise.resolve({ state: Notification.permission })
                        : originalQuery(parameters);
            }

            // 6. platform
            Object.defineProperty(navigator, 'platform', { get: () => 'Win32' });

            // 7. hardwareConcurrency（模拟 8 核）
            Object.defineProperty(navigator, 'hardwareConcurrency', { get: () => 8 });

            // 8. deviceMemory
            Object.defineProperty(navigator, 'deviceMemory', { get: () => 8 });

            // 9. 隐藏 Headless 相关 UA 特征
            Object.defineProperty(navigator, 'appVersion', {
                get: () => navigator.userAgent.replace('Headless', ''),
            });

            // 10. WebGL 厂商/渲染器伪装
            const getParameter = WebGLRenderingContext.prototype.getParameter;
            WebGLRenderingContext.prototype.getParameter = function(parameter) {
                if (parameter === 37445) return 'Intel Inc.';
                if (parameter === 37446) return 'Intel Iris OpenGL Engine';
                return getParameter.call(this, parameter);
            };

            // 11. iframe 内也注入（防止 iframe 检测）
            const _origCreateElement = document.createElement.bind(document);
            document.createElement = function(tag, ...args) {
                const el = _origCreateElement(tag, ...args);
                if (tag.toLowerCase() === 'iframe') {
                    el.addEventListener('load', () => {
                        try {
                            if (el.contentWindow && el.contentWindow.navigator) {
                                Object.defineProperty(el.contentWindow.navigator, 'webdriver', { get: () => undefined });
                            }
                        } catch(e) {}
                    });
                }
                return el;
            };

            // 12. 修复 toString 防止检测被覆盖
            const _nativeToString = Function.prototype.toString;
            Function.prototype.toString = function() {
                if (this === Function.prototype.toString) return _nativeToString.call(this);
                return _nativeToString.call(this);
            };
        })();
        """)
    
    async def random_sleep(self, min_sec: float = 1.0, max_sec: float = 3.0):
        """随机延迟，模拟人工操作节奏"""
        import asyncio, random
        delay = random.uniform(min_sec, max_sec)
        await asyncio.sleep(delay)

    async def close(self):
        """关闭浏览器（不保留状态）"""
        if self._page and not self._page.is_closed():
            await self._page.close()
        if self._context:
            await self._context.close()
        if self._browser:
            await self._browser.close()
        if self._playwright:
            await self._playwright.stop()
        
        self._browser = None
        self._context = None
        self._page = None
        self._playwright = None
        self._is_connected = False
        logger.info("浏览器已关闭")
    
    async def __aenter__(self):
        await self.start()
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        pass
