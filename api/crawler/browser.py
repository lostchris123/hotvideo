"""
Playwright 浏览器管理模块 - 支持持久化登录状态和跨进程复用
支持多平台浏览器配置文件隔离
"""
from __future__ import annotations
import asyncio
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
        launch_args = [
            "--no-sandbox",
            "--disable-dev-shm-usage",

            "--disable-gpu",
            "--disable-gpu-compositing",
            "--disable-software-rasterizer",
            "--disable-features=VizDisplayCompositor",

            "--start-maximized",
            "--force-device-scale-factor=1",
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
        
        self._context = await self._playwright.chromium.launch_persistent_context(**launch_options)
        await self._context.set_viewport_size({
            "width": 1280,
            "height": 720
        })

        # 注入反检测脚本
        await self._inject_stealth()
        
        self._browser = self._context.browser
        
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
        self._page = await self._context.new_page()    
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
        """注入反检测脚本"""
        if not self._context:
            return
        
        await self._context.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', {
                get: () => undefined
            });
            Object.defineProperty(navigator, 'plugins', {
                get: () => [1, 2, 3, 4, 5]
            });
            Object.defineProperty(navigator, 'languages', {
                get: () => ['zh-CN', 'zh', 'en']
            });
        """)
    
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
