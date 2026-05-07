"""
视频号链接解析器 - 获取视频下载链接
通过 Playwright 打开页面并拦截网络请求获取真实下载地址
"""
import asyncio
import re
import json
import os
from typing import Optional, Dict, Any, List
from dataclasses import dataclass
from urllib.parse import urlparse, parse_qs
from loguru import logger

# 检查 Playwright 是否可用
try:
    from playwright.async_api import async_playwright, Page, Browser, BrowserContext
    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    PLAYWRIGHT_AVAILABLE = False
    logger.warning("Playwright 未安装，视频号解析功能不可用")


@dataclass
class ShipinhaoVideoResult:
    """视频号解析结果"""
    video_id: str
    original_url: str
    status: str = "pending"
    error: Optional[str] = None
    title: str = ""
    description: str = ""
    author: str = ""
    author_id: str = ""
    video_url: str = ""
    cover_url: str = ""
    likes: int = 0
    comments: int = 0
    shares: int = 0
    duration: float = 0.0
    raw_data: Optional[Dict] = None


class ShipinhaoLinkParser:
    """视频号链接解析器"""

    def __init__(self, headless: bool = True):
        self.headless = headless
        self._playwright = None
        self._browser: Optional[Browser] = None
        self._context: Optional[BrowserContext] = None

        # Chromium 可执行文件路径
        self.chromium_path = os.environ.get(
            "CHROMIUM_PATH",
            "/usr/bin/chromium"
        )

    async def _ensure_browser(self):
        """确保浏览器已启动"""
        if not PLAYWRIGHT_AVAILABLE:
            raise RuntimeError("Playwright 未安装")

        if self._browser is None:
            self._playwright = await async_playwright().start()

            # 尝试使用系统 chromium
            launch_options = {
                "headless": self.headless,
                "args": [
                    "--disable-blink-features=AutomationControlled",
                    "--disable-infobars",
                    "--no-sandbox",
                    "--disable-setuid-sandbox",
                    "--disable-dev-shm-usage",
                ]
            }

            # 如果系统 chromium 存在，使用它
            if os.path.exists(self.chromium_path):
                launch_options["executable_path"] = self.chromium_path
                logger.info(f"使用系统 Chromium: {self.chromium_path}")

            try:
                self._browser = await self._playwright.chromium.launch(**launch_options)
            except Exception as e:
                logger.error(f"启动浏览器失败: {e}")
                raise

            self._context = await self._browser.new_context(
                viewport={"width": 1280, "height": 720},
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                locale="zh-CN",
            )

    async def close(self):
        """关闭浏览器"""
        if self._context:
            await self._context.close()
            self._context = None
        if self._browser:
            await self._browser.close()
            self._browser = None
        if self._playwright:
            await self._playwright.stop()
            self._playwright = None

    def _parse_share_url(self, url: str) -> Dict[str, Any]:
        """解析分享链接格式"""
        parsed = urlparse(url)

        if "weixin.qq.com" in parsed.netloc and "/sph/" in parsed.path:
            path_parts = parsed.path.strip("/").split("/")
            if len(path_parts) >= 2 and path_parts[0] == "sph":
                return {
                    "video_id": path_parts[1],
                    "finder_url": f"https://channels.weixin.qq.com/finder-preview/pages/sph?id={path_parts[1]}"
                }

        if "channels.weixin.qq.com" in parsed.netloc:
            query = parse_qs(parsed.query)
            video_id = query.get("id", [None])[0]
            if video_id:
                return {
                    "video_id": video_id,
                    "finder_url": url
                }

        return {"error": "不支持链接格式"}

    async def parse_video_link(self, url: str, timeout: int = 30) -> ShipinhaoVideoResult:
        """解析视频号链接"""
        parsed = self._parse_share_url(url)
        if "error" in parsed:
            return ShipinhaoVideoResult(
                video_id="",
                original_url=url,
                status="error",
                error=parsed["error"]
            )

        video_id = parsed["video_id"]
        finder_url = parsed["finder_url"]

        result = ShipinhaoVideoResult(
            video_id=video_id,
            original_url=url,
        )

        captured_urls: List[str] = []
        captured_data: List[Dict] = []

        try:
            await self._ensure_browser()
            page = await self._context.new_page()

            async def on_response(response):
                url_str = response.url
                if "finder.video.qq.com" in url_str or "stodownload" in url_str:
                    captured_urls.append(url_str)
                    logger.info(f"捕获视频URL: {url_str[:100]}...")
                if "api" in url_str and "feed" in url_str:
                    try:
                        data = await response.json()
                        captured_data.append(data)
                    except:
                        pass

            page.on("response", on_response)

            logger.info(f"访问视频号页面: {finder_url}")
            await page.goto(finder_url, wait_until="domcontentloaded", timeout=timeout * 1000)
            await asyncio.sleep(5)

            try:
                title = await page.title()
                result.title = title

                content = await page.content()
                if "登录" in content or "扫码" in content:
                    result.error = "需要微信登录才能访问视频内容"
                    result.status = "error"
                    await page.close()
                    return result

            except Exception as e:
                logger.warning(f"获取页面内容失败: {e}")

            if captured_urls:
                result.video_url = captured_urls[0]
                result.status = "success"

            if captured_data:
                for data in captured_data:
                    self._extract_video_info(data, result)

            if not result.video_url:
                video_urls = await page.evaluate("""() => {
                    const urls = [];
                    document.querySelectorAll('video').forEach(v => {
                        if (v.src) urls.push(v.src);
                    });
                    return urls;
                }""")
                if video_urls:
                    result.video_url = video_urls[0]
                    result.status = "success"

            if not result.video_url and result.status != "error":
                result.status = "error"
                result.error = "未能获取到视频下载链接，可能需要登录"

            await page.close()

        except Exception as e:
            result.status = "error"
            result.error = str(e)
            logger.error(f"解析视频链接失败: {e}")

        return result

    def _extract_video_info(self, data: Dict, result: ShipinhaoVideoResult):
        """从 API 数据中提取视频信息"""
        try:
            feed_info = data.get("data", {}).get("feedInfo", {})
            if not feed_info:
                feed_info = data.get("feedInfo", {})

            if feed_info:
                author_info = feed_info.get("author", {})
                result.author = author_info.get("nickname", "")
                result.author_id = author_info.get("username", "")
                result.description = feed_info.get("desc", "") or feed_info.get("content", "")
                stats = feed_info.get("likeCnt", 0)
                result.likes = int(stats) if stats else 0

                media = feed_info.get("media", {})
                video_info = media.get("videoInfo", {})
                if video_info:
                    result.duration = video_info.get("duration", 0)
                    video_url = video_info.get("url", "")
                    if video_url:
                        result.video_url = video_url
        except Exception as e:
            logger.warning(f"提取视频信息失败: {e}")


def parse_shipinhao_video_sync(url: str, timeout: int = 30) -> Dict[str, Any]:
    """同步解析视频号链接"""
    if not PLAYWRIGHT_AVAILABLE:
        return {
            "status": "error",
            "error": "Playwright 未安装，无法解析视频链接",
            "hint": "请联系管理员安装 Playwright"
        }

    async def _parse():
        parser = ShipinhaoLinkParser(headless=True)
        try:
            result = await parser.parse_video_link(url, timeout)
            return {
                "video_id": result.video_id,
                "original_url": result.original_url,
                "status": result.status,
                "error": result.error,
                "title": result.title,
                "description": result.description,
                "author": result.author,
                "author_id": result.author_id,
                "video_url": result.video_url,
                "cover_url": result.cover_url,
                "likes": result.likes,
                "duration": result.duration,
            }
        finally:
            await parser.close()

    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            # 如果在异步环境中，创建新线程运行
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as executor:
                future = executor.submit(
                    lambda: asyncio.run(_parse())
                )
                return future.result(timeout=timeout + 10)
        else:
            return loop.run_until_complete(_parse())
    except Exception as e:
        return {
            "status": "error",
            "error": str(e)
        }
