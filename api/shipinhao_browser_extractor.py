"""
视频号浏览器提取 - 使用 Playwright 自动化浏览器
"""
import asyncio
import json
import re
from typing import Dict, Any, Optional
from playwright.async_api import async_playwright, Browser, Page, BrowserContext
from urllib.parse import urlparse, parse_qs
import logging

logger = logging.getLogger(__name__)


class ShipinhaoBrowserExtractor:
    """视频号浏览器提取器"""
    
    def __init__(self):
        self.browser: Optional[Browser] = None
        self.context: Optional[BrowserContext] = None
        self.page: Optional[Page] = None
        self.playwright = None
    
    async def init(self, headless: bool = True):
        """初始化浏览器"""
        self.playwright = await async_playwright().start()
        
        # 使用 Chromium
        self.browser = await self.playwright.chromium.launch(
            headless=headless,
            args=[
                '--no-sandbox',
                '--disable-setuid-sandbox',
                '--disable-dev-shm-usage',
                '--disable-gpu',
                '--disable-software-rasterizer',
            ]
        )
        
        # 创建上下文
        self.context = await self.browser.new_context(
            viewport={'width': 1920, 'height': 1080},
            user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
        )
        
        # 创建页面
        self.page = await self.context.new_page()
        
        logger.info("浏览器初始化成功")
    
    async def extract_video(self, url: str) -> Dict[str, Any]:
        """
        提取视频信息
        
        Args:
            url: 视频号链接
            
        Returns:
            包含视频信息的字典
        """
        if not self.page:
            await self.init()
        
        try:
            logger.info(f"开始提取视频: {url}")
            
            # 1. 解析视频ID
            video_id = self._extract_video_id(url)
            if not video_id:
                return {
                    "status": "error",
                    "error": "无法解析视频ID"
                }
            
            # 2. 构建视频号预览链接
            preview_url = f"https://channels.weixin.qq.com/finder-preview/pages/sph?id={video_id}"
            
            logger.info(f"访问预览页面: {preview_url}")
            
            # 3. 访问预览页面
            await self.page.goto(preview_url, wait_until='networkidle', timeout=30000)
            
            # 等待页面加载
            await asyncio.sleep(2)
            
            # 4. 尝试获取视频信息
            video_info = await self._get_video_info_from_page()
            
            if video_info:
                video_info['video_id'] = video_id
                video_info['original_url'] = url
                video_info['status'] = 'success'
                
                logger.info(f"视频信息提取成功: {video_info.get('title', 'N/A')}")
                return video_info
            else:
                # 尝试从网络请求中获取下载链接
                download_url = await self._get_download_url_from_network()
                
                if download_url:
                    return {
                        "video_id": video_id,
                        "original_url": url,
                        "video_url": download_url,
                        "status": "success",
                        "note": "已获取下载链接，但部分信息可能不完整"
                    }
                else:
                    return {
                        "video_id": video_id,
                        "original_url": url,
                        "status": "partial",
                        "error": "无法获取视频详细信息，可能需要登录"
                    }
        
        except Exception as e:
            logger.error(f"提取视频失败: {str(e)}", exc_info=True)
            return {
                "status": "error",
                "error": f"提取失败: {str(e)}"
            }
    
    def _extract_video_id(self, url: str) -> Optional[str]:
        """从URL提取视频ID"""
        # 匹配 weixin.qq.com/sph/VIDEO_ID
        match = re.search(r'weixin\.qq\.com/sph/([^?/&]+)', url)
        if match:
            return match.group(1)
        
        # 匹配 channels.weixin.qq.com/sph/VIDEO_ID
        match = re.search(r'channels\.weixin\.qq\.com/sph/([^?/&]+)', url)
        if match:
            return match.group(1)
        
        # 直接输入的视频ID
        if len(url) > 5 and len(url) < 30 and not url.startswith('http'):
            return url
        
        return None
    
    async def _get_video_info_from_page(self) -> Optional[Dict[str, Any]]:
        """从页面获取视频信息"""
        try:
            # 尝试从页面脚本中提取数据
            page_content = await self.page.content()
            
            # 查找包含视频信息的 JSON 数据
            # 视频号通常在 __wxConfig 或 window.__data__ 中
            patterns = [
                r'__wxConfig\s*=\s*({[^}]+})',
                r'window\.__data__\s*=\s*({[^}]+})',
                r'finderData\s*=\s*({[^}]+})',
            ]
            
            for pattern in patterns:
                matches = re.findall(pattern, page_content)
                for match in matches:
                    try:
                        data = json.loads(match)
                        
                        # 提取视频信息
                        if 'finderData' in data:
                            finder_data = data['finderData']
                            if 'feed' in finder_data:
                                feed = finder_data['feed']
                                return {
                                    'title': feed.get('feedDesc', ''),
                                    'description': feed.get('feedDesc', ''),
                                    'author': feed.get('nickname', ''),
                                    'author_id': feed.get('username', ''),
                                    'cover_url': feed.get('coverUrl', ''),
                                    'likes': feed.get('likeCount', 0),
                                    'comments': feed.get('commentCount', 0),
                                    'shares': feed.get('shareCount', 0),
                                }
                    except json.JSONDecodeError:
                        continue
            
            return None
        except Exception as e:
            logger.error(f"从页面获取视频信息失败: {str(e)}")
            return None
    
    async def _get_download_url_from_network(self) -> Optional[str]:
        """从网络请求中获取下载链接"""
        try:
            # 监听网络请求
            download_url = None
            
            async def handle_response(response):
                nonlocal download_url
                url = response.url
                
                # 查找视频下载链接
                if 'finder.video.qq.com' in url and 'stodownload' in url:
                    download_url = url
                    logger.info(f"找到下载链接: {url[:100]}...")
            
            # 添加响应监听器
            self.page.on('response', handle_response)
            
            # 刷新页面以触发网络请求
            await self.page.reload(wait_until='networkidle', timeout=30000)
            
            # 等待一段时间让请求完成
            await asyncio.sleep(3)
            
            # 移除监听器
            self.page.remove_listener('response', handle_response)
            
            return download_url
        
        except Exception as e:
            logger.error(f"从网络请求获取下载链接失败: {str(e)}")
            return None
    
    async def close(self):
        """关闭浏览器"""
        if self.page:
            await self.page.close()
        if self.context:
            await self.context.close()
        if self.browser:
            await self.browser.close()
        if self.playwright:
            await self.playwright.stop()
        
        logger.info("浏览器已关闭")


# 单例实例
_extractor: Optional[ShipinhaoBrowserExtractor] = None


async def get_extractor() -> ShipinhaoBrowserExtractor:
    """获取提取器实例（单例）"""
    global _extractor
    
    if _extractor is None:
        _extractor = ShipinhaoBrowserExtractor()
        await _extractor.init()
    
    return _extractor


async def close_extractor():
    """关闭提取器"""
    global _extractor
    
    if _extractor:
        await _extractor.close()
        _extractor = None
