"""
抖音爬取模块 - 支持关键词搜索、详情提取、短链接解析
"""
from __future__ import annotations
import asyncio
import re
import json
import httpx
import urllib.parse
import subprocess
from typing import Optional, List, Dict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from playwright.async_api import Page, TimeoutError as PlaywrightTimeout, Browser
from loguru import logger

from .browser import BrowserManager
from config import get_settings


@dataclass
class VideoInfo:
    """视频信息"""
    author_name: str = ""
    author_id: str = ""
    ip_location: str = ""
    author_signature: str = ""
    fans_count: str = "0"
    liked_count: str = "0"
    
    video_id: str = ""
    description: str = ""
    tags: List = field(default_factory=list)
    video_url: str = ""
    thumbnail_url: str = ""
    publish_date: Optional[datetime] = None
    
    video_stream_url: str = ""
    video_stream_url_no_watermark: str = ""  # 无水印视频流地址
    audio_url: str = ""
    share_url: str = ""  # 分享链接
    
    likes: int = 0
    comments: int = 0
    collects: int = 0
    shares: int = 0
    
    platform: str = "douyin"  # 平台: douyin/xiaohongshu/shipinhao
    
    def to_db_record(self, keyword: str = ""):
        """转换为数据库记录（根据平台返回对应类型）"""
        from .models import PLATFORM_MODELS
        
        ModelClass = PLATFORM_MODELS.get(self.platform, PLATFORM_MODELS["douyin"])
        
        return ModelClass(
            author_name=self.author_name,
            author_id=self.author_id,
            ip_location=self.ip_location,
            author_signature=self.author_signature,
            fans_count=self.fans_count,
            liked_count=self.liked_count,
            video_id=self.video_id,
            description=self.description,
            tags=json.dumps(self.tags, ensure_ascii=False),
            video_url=self.video_url,
            thumbnail_url=self.thumbnail_url,
            publish_date=self.publish_date,
            likes=self.likes,
            comments=self.comments,
            collects=self.collects,
            shares=self.shares,
            keyword=keyword,
        )


class DouyinCrawler:
    """抖音爬虫"""
    
    BASE_URL = "https://www.douyin.com"
    
    def __init__(self, browser_manager: BrowserManager = None, user_id: int = None):
        self.user_id = user_id
        if browser_manager:
            self.browser_manager = browser_manager
        else:
            self.browser_manager = BrowserManager(platform="douyin", user_id=user_id)
        self._browser: Optional[Browser] = None
        self._page: Optional[Page] = None
    async def _save_debug_screenshot(self, name: str):
        """
        保存调试截图
        
        Args:
            name: 截图名称
        """
        try:
            import os
            import time
            screenshot_dir = "debug_screenshots"
            if not os.path.exists(screenshot_dir):
                os.makedirs(screenshot_dir)
            screenshot_path = os.path.join(screenshot_dir, f"{name}_{int(time.time())}.png")
            await self._page.screenshot(path=screenshot_path, full_page=True)
            logger.info(f"保存调试截图: {screenshot_path}")
        except Exception as e:
            logger.warning(f"保存调试截图失败: {e}")
    
    async def start(self):
        """启动爬虫"""
        if self._browser is None:
            self._browser = await self.browser_manager.start()
        if self._page is None:
            self._page = await self.browser_manager.get_page()
        logger.info("抖音爬虫启动")
    
    async def close(self):
        """关闭爬虫（不关闭浏览器，保持登录状态）"""
        # 不关闭浏览器，保持登录状态
        self._page = None
        self._browser = None
        logger.info("抖音爬虫已关闭（浏览器保持运行）")
    
    async def ensure_browser(self):
        """确保浏览器可用"""
        try:
            if self._page is None or self._browser is None:
                await self.start()
            elif self._page.is_closed():
                logger.info("页面已关闭，重新获取")
                self._page = await self.browser_manager.get_page()
        except Exception as e:
            logger.warning(f"浏览器状态异常，重新启动: {e}")
            self._browser = None
            self._page = None
            await self.start()
    
    @staticmethod
    def is_short_url(url: str) -> bool:
        """判断是否为短链接"""
        return "v.douyin.com" in url or "douyin.com/video/" not in url and "douyin.com" in url
    
    @staticmethod
    async def parse_share_url(share_url: str) -> Dict:
        """
        解析抖音分享链接（支持短链接）
        
        Args:
            share_url: 分享链接（如 https://v.douyin.com/xxx/）
        
        Returns:
            {
                "video_id": "视频ID",
                "video_url": "视频详情页URL",
            }
        """
        logger.info(f"解析分享链接: {share_url}")
        
        headers = {
            "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        }
        
        try:
            async with httpx.AsyncClient(follow_redirects=True, timeout=30) as client:
                resp = await client.head(share_url, headers=headers)
                real_url = str(resp.url)
                
                if "www.douyin.com/video/" in real_url:
                    match = re.search(r'/video/(\d+)', real_url)
                    if match:
                        video_id = match.group(1)
                        return {
                            "video_id": video_id,
                            "video_url": f"https://www.douyin.com/video/{video_id}",
                        }
                
                if "www.douyin.com/video/" not in real_url:
                    resp = await client.get(share_url, headers=headers)
                    real_url = str(resp.url)
                    match = re.search(r'/video/(\d+)', real_url)
                    if match:
                        video_id = match.group(1)
                        return {
                            "video_id": video_id,
                            "video_url": f"https://www.douyin.com/video/{video_id}",
                        }
                
                logger.warning(f"无法解析链接: {real_url}")
                return {"video_id": "", "video_url": real_url}
                
        except Exception as e:
            logger.error(f"解析分享链接失败: {e}")
            raise
    
    async def get_video_stream_urls(self, video_id: str) -> Dict:
        """
        获取视频流地址
        
        Args:
            video_id: 视频ID
        
        Returns:
            {
                "audio_url": "音频流地址",
                "video_stream_url": "视频流地址",
            }
        """
        await self.ensure_browser()
        
        video_url = f"{self.BASE_URL}/video/{video_id}"
        
        try:
            await self._page.goto(video_url, wait_until="domcontentloaded", timeout=30000)
            await asyncio.sleep(3)
            
            stream_data = await self._page.evaluate("""
                () => {
                    const result = {
                        audio_url: '',
                        video_stream_url: ''
                    };
                    
                    try {
                        const scripts = document.querySelectorAll('script');
                        for (const script of scripts) {
                            const text = script.textContent || '';
                            if (text.includes('__RENDER_DATA__')) {
                                const match = text.match(/window\\.__RENDER_DATA__\\s*=\\s*(".*?")\\s*<\\/script>/);
                                if (match) {
                                    const decoded = decodeURIComponent(JSON.parse(match[1]));
                                    const data = JSON.parse(decoded);
                                    
                                    const videoInfo = data?.app?.videoInfo?.item_list?.[0] || 
                                                       data?.videoInfo?.item_list?.[0] ||
                                                       data?.aweme_detail;
                                    
                                    if (videoInfo) {
                                        result.audio_url = videoInfo.music?.play_url?.url_list?.[0] || 
                                                           videoInfo.music?.playUrl?.urlList?.[0] || '';
                                        
                                        const video = videoInfo.video;
                                        result.video_stream_url = video?.play_addr?.url_list?.[0] || 
                                                                   video?.playAddr?.urlList?.[0] || '';
                                        
                                        if (!result.video_stream_url && video?.bit_rate?.length > 0) {
                                            result.video_stream_url = video.bit_rate[0]?.play_addr?.url_list?.[0] || '';
                                        }
                                    }
                                    break;
                                }
                            }
                        }
                    } catch (e) {
                        console.error('提取视频流地址失败', e);
                    }
                    
                    return result;
                }
            """)
            
            logger.info(f"获取视频流地址: audio={bool(stream_data.get('audio_url'))}, video={bool(stream_data.get('video_stream_url'))}")
            return stream_data
            
        except Exception as e:
            logger.error(f"获取视频流地址失败: {e}")
            return {"audio_url": "", "video_stream_url": ""}
    
    async def _click_sort_button(self, sort_by: str):
        """
        点击筛选按钮并选择排序方式（备用方法）
        
        ⚠️ 注意：推荐使用URL参数方式实现排序，此方法作为备用
        URL参数：sort_type=0(综合), sort_type=1(最多点赞), sort_type=2(最新发布)
        
        Args:
            sort_by: 排序方式: newest(最新发布), most_likes(最多点赞)
        """
        try:
            logger.info(f"点击筛选按钮，选择排序: {sort_by}")
            
            # 排序选项文字映射
            sort_text_map = {
                "newest": "最新发布",
                "most_likes": "最多点赞",
                "default": "综合排序"
            }
            
            sort_text = sort_text_map.get(sort_by, "综合排序")
            
            # 方法1: 尝试使用Playwright的role选择器（最可靠）
            try:
                logger.info("尝试使用role选择器点击排序选项...")
                
                # 点击筛选按钮
                filter_btn = self._page.get_by_role("button", name="筛选").first
                await filter_btn.click(timeout=5000)
                logger.info("成功点击筛选按钮")
                await asyncio.sleep(2)
                
                # 使用role选择器点击排序选项
                sort_option = self._page.get_by_role("menuitem", name=sort_text).first
                await sort_option.click(timeout=5000)
                logger.info(f"成功点击排序选项: {sort_text}（使用role选择器）")
                await asyncio.sleep(3)
                return
                
            except Exception as e:
                logger.warning(f"使用role选择器失败: {e}")
            
            # 方法2: 使用更精确的CSS选择器
            try:
                logger.info("尝试使用CSS选择器点击排序选项...")
                
                # 点击筛选按钮
                filter_btn = self._page.locator("#search-toolbar-container").get_by_text("筛选").first
                await filter_btn.click(timeout=5000)
                logger.info("成功点击筛选按钮")
                await asyncio.sleep(2)
                
                # 保存调试截图
                await self._save_debug_screenshot(f"filter_menu_{sort_text}")
                
                # 尝试多种选择器
                selectors = [
                    f'[class*="menu"] [class*="item"]:has-text("{sort_text}")',
                    f'[class*="dropdown"] [class*="option"]:has-text("{sort_text}")',
                    f'div:has(> span:text("{sort_text}"))',
                    f'li:has-text("{sort_text}")',
                ]
                
                for selector in selectors:
                    try:
                        element = self._page.locator(selector).first
                        if await element.count() > 0:
                            await element.click(timeout=3000)
                            logger.info(f"成功点击排序选项: {sort_text}（使用选择器: {selector}）")
                            await asyncio.sleep(3)
                            return
                    except Exception:
                        continue
                        
            except Exception as e:
                logger.warning(f"使用CSS选择器失败: {e}")
            
            # 方法3: 使用Playwright的force点击
            try:
                logger.info("尝试使用force点击...")
                
                # 重新打开筛选菜单
                filter_btn = self._page.get_by_text("筛选", exact=True).first
                await filter_btn.click(timeout=5000)
                await asyncio.sleep(2)
                
                # 使用force点击
                sort_element = self._page.get_by_text(sort_text, exact=True).first
                await sort_element.click(force=True, timeout=5000)
                logger.info(f"成功点击排序选项: {sort_text}（使用force点击）")
                await asyncio.sleep(3)
                return
                
            except Exception as e:
                logger.warning(f"使用force点击失败: {e}")
            
            # 方法4: 使用鼠标移动+点击
            try:
                logger.info("尝试使用鼠标操作...")
                
                # 重新打开筛选菜单
                filter_btn = self._page.get_by_text("筛选", exact=True).first
                await filter_btn.click(timeout=5000)
                await asyncio.sleep(2)
                
                # 找到排序选项并获取其位置
                sort_element = self._page.get_by_text(sort_text, exact=True).first
                box = await sort_element.bounding_box()
                
                if box:
                    # 移动鼠标到元素上
                    await self._page.mouse.move(box['x'] + box['width'] / 2, box['y'] + box['height'] / 2)
                    await asyncio.sleep(0.5)
                    # 点击
                    await self._page.mouse.click(box['x'] + box['width'] / 2, box['y'] + box['height'] / 2)
                    logger.info(f"成功点击排序选项: {sort_text}（使用鼠标操作）")
                    await asyncio.sleep(3)
                    return
                    
            except Exception as e:
                logger.warning(f"使用鼠标操作失败: {e}")
            
            # 方法5: 最后尝试键盘操作
            try:
                logger.info("尝试使用键盘操作...")
                
                # 重新打开筛选菜单
                filter_btn = self._page.get_by_text("筛选", exact=True).first
                await filter_btn.click(timeout=5000)
                await asyncio.sleep(2)
                
                # 根据排序类型选择按键次数
                if sort_by == "most_likes":
                    # 最多点赞通常是第3个选项
                    for _ in range(2):
                        await self._page.keyboard.press('ArrowDown')
                        await asyncio.sleep(0.3)
                elif sort_by == "newest":
                    # 最新发布通常是第2个选项
                    await self._page.keyboard.press('ArrowDown')
                    await asyncio.sleep(0.3)
                
                await self._page.keyboard.press('Enter')
                logger.info(f"成功使用键盘操作选择排序: {sort_text}")
                await asyncio.sleep(3)
                return
                
            except Exception as e:
                logger.warning(f"使用键盘操作失败: {e}")
            
            # 所有方法都失败
            logger.warning(f"所有排序方法都失败，继续使用默认排序")
            
        except Exception as e:
            logger.warning(f"点击筛选按钮失败: {e}，继续使用默认排序")
    
    async def search_by_keyword(
        self, 
        keyword: str, 
        limit: int = 30, 
        min_likes: int = 0, 
        timeout: int = 60,
        sort_by: str = "default",
        download_videos: bool = False,
        progress_callback=None,
        cancel_check=None
    ) -> List[VideoInfo]:
        """
        根据关键词搜索视频
        
        Args:
            keyword: 搜索关键词
            limit: 符合条件的视频数量限制
            min_likes: 最小点赞数过滤条件
            timeout: 单个视频处理超时时间
            sort_by: 排序方式: default(综合排序), newest(最新发布), most_likes(最多点赞)
            download_videos: 是否在搜索时下载视频并提取缩略图
            progress_callback: 进度回调函数，接收进度字典
            cancel_check: 取消检查函数，返回True表示需要取消
        
        Returns:
            符合条件的视频信息列表
        """
        def report_progress(stage: str, collected: int, visited: int, qualified: int, message: str):
            if progress_callback:
                progress_callback({
                    "stage": stage,
                    "collected_links": collected,
                    "visited_pages": visited,
                    "qualified_count": qualified,
                    "message": message
                })
        
        def is_cancelled() -> bool:
            return cancel_check() if cancel_check else False
        
        await self.ensure_browser()
        
        report_progress("init", 0, 0, 0, "正在初始化...")
        
        logger.info(f"搜索关键词: {keyword}, 目标数量: {limit}, 最小点赞数: {min_likes}, 排序: {sort_by}")
        
        # 排序类型映射（通过URL参数实现）
        # sort_type=0 或不添加 -> 综合排序
        # sort_type=1 -> 最多点赞
        # sort_type=2 -> 最新发布
        sort_type_map = {
            "default": None,  # 不添加参数，默认综合排序
            "most_likes": 1,
            "newest": 2
        }
        
        sort_type = sort_type_map.get(sort_by, None)
        
        # 构建搜索URL
        if sort_type is not None:
            search_url = f"{self.BASE_URL}/search/{keyword}?type=video&sort_type={sort_type}"
            logger.info(f"使用排序参数 sort_type={sort_type}")
        else:
            search_url = f"{self.BASE_URL}/search/{keyword}?type=video"
        
        logger.info(f"搜索URL: {search_url}")
        
        try:
            await self._page.goto(search_url, wait_until="domcontentloaded", timeout=30000)
        except PlaywrightTimeout:
            logger.warning("页面加载超时(domcontentloaded)，尝试继续...")
        except Exception as e:
            logger.warning(f"页面导航异常: {e}，尝试继续...")
        
        await asyncio.sleep(3)
        
        try:
            await self._page.wait_for_selector('a[href*="/video/"]', timeout=10000)
            logger.info("搜索结果已加载")
        except:
            logger.warning("未检测到搜索结果，可能需要登录或遇到验证")
            try:
                await self._page.evaluate("window.scrollBy(0, 500)")
                await asyncio.sleep(2)
            except:
                pass
        
        await asyncio.sleep(2)
        
        # 排序已通过URL参数实现，无需额外操作
        
        if is_cancelled():
            logger.info("任务已取消")
            return []
        
        need_login = await self._check_login_required()
        if need_login:
            logger.warning("检测到登录弹窗，请手动登录后重试")
            await self._save_debug_screenshot("login_required")
            return []
        
        report_progress("collecting", 0, 0, 0, "正在收集搜索结果...")
        
        qualified_videos = []
        visited_ids = set()
        max_scrolls = 50
        scroll_count = 0
        max_detail_visits = limit * 10
        detail_visits = 0
        
        while len(qualified_videos) < limit and scroll_count < max_scrolls and detail_visits < max_detail_visits:
            if is_cancelled():
                logger.info("任务已取消")
                return []
            
            video_links = await self._extract_video_links_from_search()
            
            for link in video_links:
                if is_cancelled():
                    logger.info("任务已取消")
                    return []
                
                if link['video_id'] in visited_ids:
                    continue
                
                if len(qualified_videos) >= limit:
                    break
                
                visited_ids.add(link['video_id'])
                detail_visits += 1
                
                report_progress(
                    "visiting", 
                    len(visited_ids), 
                    detail_visits, 
                    len(qualified_videos),
                    f"正在访问详情页 (已访问 {detail_visits} 个)，已找到 {len(qualified_videos)} 个"
                )
                
                try:
                    logger.info(f"访问详情页 [{len(qualified_videos)+1}/{limit}]: {link['video_id']}")
                    
                    try:
                        video_info = await asyncio.wait_for(
                            self.get_video_detail(link['video_url'], min_likes=min_likes, timeout=timeout),
                            timeout=timeout + 30
                        )
                    except asyncio.TimeoutError:
                        logger.warning(f"视频处理超时({timeout}s): {link['video_id']}")
                        continue
                    
                    if video_info and video_info.video_id:
                        if video_info.likes >= min_likes:
                            video_info.platform = "douyin"
                            
                            # 如果需要下载视频
                            if download_videos and video_info.video_stream_url:
                                try:
                                    await self._download_and_extract_thumbnail(video_info)
                                except Exception as e:
                                    logger.warning(f"视频下载失败: {video_info.video_id}, {e}")
                            
                            await self.save_single_video_to_db(video_info, keyword)
                            qualified_videos.append(video_info)
                            logger.info(f"符合条件并已保存 [{len(qualified_videos)}/{limit}]: 点赞={video_info.likes}, 作者={video_info.author_name}")
                            report_progress(
                                "visiting", 
                                len(visited_ids), 
                                detail_visits, 
                                len(qualified_videos),
                                f"找到符合条件的内容 [{len(qualified_videos)}/{limit}]"
                            )
                        else:
                            logger.info(f"跳过: 点赞数 {video_info.likes} < {min_likes}")
                    else:
                        logger.warning(f"详情页提取失败: {link['video_id']}")
                    
                    await asyncio.sleep(2 + (detail_visits % 3))
                    
                except Exception as e:
                    logger.error(f"访问详情页失败: {link['video_id']}, {e}")
                    continue
            
            if len(qualified_videos) < limit and detail_visits < max_detail_visits:
                logger.info(f"当前符合条件 {len(qualified_videos)}/{limit}，滚动加载更多...")
                report_progress("collecting", len(visited_ids), detail_visits, len(qualified_videos), f"滚动加载更多... 已找到 {len(qualified_videos)} 个")
                await self._page.evaluate("window.scrollBy(0, 800)")
                await asyncio.sleep(2)
                scroll_count += 1
        
        logger.info(f"搜索完成: 访问 {detail_visits} 个详情页，找到 {len(qualified_videos)} 个符合条件的视频")
        report_progress("completed", len(visited_ids), detail_visits, len(qualified_videos), f"搜索完成，找到 {len(qualified_videos)} 个符合条件的内容")
        return qualified_videos
    
    async def _check_login_required(self) -> bool:
        """检查页面是否需要登录"""
        try:
            need_login = await self._page.evaluate("""
                () => {
                    // 检查是否有登录弹窗或登录提示
                    const loginModal = document.querySelector('[class*="login"], [class*="Login"]');
                    if (loginModal) {
                        return true;
                    }
                    
                    // 检查是否有登录按钮可见（通常在未登录时显示）
                    const loginBtn = document.querySelector('button[class*="login"], a[class*="login"]');
                    if (loginBtn && loginBtn.offsetParent !== null) {
                        const text = loginBtn.textContent || '';
                        if (text.includes('登录') || text.includes('login')) {
                            return true;
                        }
                    }
                    
                    // 检查页面URL是否包含login
                    if (window.location.href.includes('login')) {
                        return true;
                    }
                    
                    // 检查是否有验证码或滑块验证
                    const captcha = document.querySelector('[class*="captcha"], [class*="verify"], [class*="slider"]');
                    if (captcha) {
                        return true;
                    }
                    
                    return false;
                }
            """)
            
            return bool(need_login)
        except Exception as e:
            logger.warning(f"检查登录状态失败: {e}")
            return False
    
    async def _save_debug_screenshot(self, name: str):
        """保存调试截图"""
        try:
            from config import get_settings
            settings = get_settings()
            debug_dir = settings.DATA_DIR / "debug"
            debug_dir.mkdir(parents=True, exist_ok=True)
            
            screenshot_path = debug_dir / f"{name}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
            await self._page.screenshot(path=str(screenshot_path))
            logger.info(f"调试截图已保存: {screenshot_path}")
        except Exception as e:
            logger.warning(f"保存调试截图失败: {e}")
    
    async def _download_and_extract_thumbnail(self, video_info: VideoInfo) -> str:
        """
        下载视频并提取缩略图
        
        Args:
            video_info: 视频信息对象（会修改其 thumbnail_url 属性）
        
        Returns:
            缩略图URL
        """
        from crawler.downloader import VideoDownloader
        from config import get_settings
        
        settings = get_settings()
        downloader = VideoDownloader()
        
        video_path = None
        try:
            # 下载视频（支持分离的视频和音频轨道）
            if video_info.video_stream_url:
                logger.info(f"开始下载视频: {video_info.video_id}")
                
                video_path = await downloader.download_video_with_audio(
                    video_url=video_info.video_stream_url,
                    audio_url=video_info.audio_url or "",
                    filename=video_info.video_id
                )
                
                if video_path and video_path.exists():
                    logger.info(f"视频下载完成: {video_path}")
                    
                    # 提取缩略图
                    try:
                        thumb_path = downloader.extract_thumbnail(video_path)
                        if thumb_path:
                            video_info.thumbnail_url = f"/thumbnails/{thumb_path.name}"
                            logger.info(f"缩略图提取完成: {video_info.thumbnail_url}")
                    except Exception as e:
                        logger.warning(f"缩略图提取失败: {e}")
                    
                    return video_info.thumbnail_url
            
            return ""
            
        except Exception as e:
            logger.error(f"下载视频失败: {e}")
            return ""
    
    async def save_single_video_to_db(self, video_info, keyword: str):
        """保存单个视频到数据库"""
        from crawler.models import get_platform_session, DouyinVideoRecord
        import json
        
        session = get_platform_session("douyin")
        try:
            # 检查是否已存在
            existing = session.query(DouyinVideoRecord).filter_by(
                video_id=video_info.video_id
            ).first()
            
            if existing:
                # 更新已存在的记录
                existing.keyword = keyword
                existing.author_name = video_info.author_name
                existing.author_id = video_info.author_id
                existing.fans_count = video_info.fans_count
                existing.liked_count = video_info.liked_count
                existing.description = video_info.description
                existing.tags = json.dumps(video_info.tags, ensure_ascii=False) if video_info.tags else None
                existing.likes = video_info.likes
                existing.comments = video_info.comments
                existing.collects = video_info.collects
                existing.shares = video_info.shares
                existing.thumbnail_url = video_info.thumbnail_url
                existing.video_url = video_info.video_url
                logger.debug(f"更新视频记录: {video_info.video_id}")
            else:
                # 创建新记录
                record = DouyinVideoRecord(
                    video_id=video_info.video_id,
                    video_url=video_info.video_url,
                    author_name=video_info.author_name,
                    author_id=video_info.author_id,
                    fans_count=video_info.fans_count,
                    liked_count=video_info.liked_count,
                    description=video_info.description,
                    tags=json.dumps(video_info.tags, ensure_ascii=False) if video_info.tags else None,
                    likes=video_info.likes,
                    comments=video_info.comments,
                    collects=video_info.collects,
                    shares=video_info.shares,
                    thumbnail_url=video_info.thumbnail_url,
                    keyword=keyword,
                )
                session.add(record)
                logger.debug(f"新增视频记录: {video_info.video_id}")
            
            session.commit()
        except Exception as e:
            logger.error(f"保存视频到数据库失败: {e}")
            session.rollback()
        finally:
            session.close()
    
    async def save_to_db(self, videos: List, keyword: str, platform: str, min_likes: int = 0):
        """批量保存视频到数据库"""
        from crawler.models import get_platform_session, DouyinVideoRecord, XiaohongshuVideoRecord, ShipinhaoVideoRecord
        import json
        
        if not videos:
            return
        
        # 根据平台选择对应的模型
        model_map = {
            "douyin": DouyinVideoRecord,
            "xiaohongshu": XiaohongshuVideoRecord,
            "shipinhao": ShipinhaoVideoRecord,
        }
        
        ModelClass = model_map.get(platform, DouyinVideoRecord)
        session = get_platform_session(platform)
        
        saved_count = 0
        try:
            for video_info in videos:
                if not video_info or not hasattr(video_info, 'video_id') or not video_info.video_id:
                    continue
                
                # 检查是否已存在
                existing = session.query(ModelClass).filter_by(
                    video_id=video_info.video_id
                ).first()
                
                if existing:
                    # 更新已存在的记录
                    existing.keyword = keyword
                    if hasattr(video_info, 'author_name'):
                        existing.author_name = video_info.author_name
                    if hasattr(video_info, 'author_id'):
                        existing.author_id = video_info.author_id
                    if hasattr(video_info, 'fans_count'):
                        existing.fans_count = video_info.fans_count
                    if hasattr(video_info, 'liked_count'):
                        existing.liked_count = video_info.liked_count
                    if hasattr(video_info, 'description'):
                        existing.description = video_info.description
                    if hasattr(video_info, 'tags'):
                        existing.tags = json.dumps(video_info.tags, ensure_ascii=False) if video_info.tags else None
                    if hasattr(video_info, 'likes'):
                        existing.likes = video_info.likes
                    if hasattr(video_info, 'comments'):
                        existing.comments = video_info.comments
                    if hasattr(video_info, 'collects'):
                        existing.collects = video_info.collects
                    if hasattr(video_info, 'shares'):
                        existing.shares = video_info.shares
                    if hasattr(video_info, 'thumbnail_url'):
                        existing.thumbnail_url = video_info.thumbnail_url
                    logger.debug(f"更新视频记录: {video_info.video_id}")
                else:
                    # 创建新记录
                    record = ModelClass(
                        video_id=video_info.video_id,
                        video_url=getattr(video_info, 'video_url', ''),
                        author_name=getattr(video_info, 'author_name', ''),
                        author_id=getattr(video_info, 'author_id', ''),
                        fans_count=getattr(video_info, 'fans_count', '0'),
                        liked_count=getattr(video_info, 'liked_count', '0'),
                        description=getattr(video_info, 'description', ''),
                        tags=json.dumps(getattr(video_info, 'tags', []), ensure_ascii=False),
                        likes=getattr(video_info, 'likes', 0),
                        comments=getattr(video_info, 'comments', 0),
                        collects=getattr(video_info, 'collects', 0),
                        shares=getattr(video_info, 'shares', 0),
                        thumbnail_url=getattr(video_info, 'thumbnail_url', ''),
                        keyword=keyword,
                    )
                    session.add(record)
                    logger.debug(f"新增视频记录: {video_info.video_id}")
                
                saved_count += 1
            
            session.commit()
            logger.info(f"批量保存完成: {saved_count} 个视频")
        except Exception as e:
            logger.error(f"批量保存失败: {e}")
            session.rollback()
        finally:
            session.close()
    
    async def _extract_video_links_from_search(self) -> List[dict]:
        """从搜索页提取视频链接列表（仅ID和URL）"""
        links_data = await self._page.evaluate("""
            () => {
                const results = [];
                const seen = new Set();
                
                // 找所有视频链接
                const links = document.querySelectorAll('a[href*="/video/"]');
                
                links.forEach(link => {
                    const videoUrl = link.href;
                    const videoId = videoUrl.match(/video\\/(\\d+)/)?.[1] || '';
                    
                    if (!videoId || seen.has(videoId)) return;
                    seen.add(videoId);
                    
                    results.push({
                        video_id: videoId,
                        video_url: videoUrl,
                    });
                });
                
                return results;
            }
        """)
        
        return links_data or []
    
    async def _extract_search_results_v2(self) -> List[VideoInfo]:
        """提取搜索结果 - 多种选择器尝试"""
        videos = []
        
        # 尝试多种选择器策略
        extractors = [
            self._extract_with_selector_v1,
            self._extract_with_selector_v2,
            self._extract_from_render_data,
        ]
        
        for extractor in extractors:
            try:
                result = await extractor()
                if result:
                    videos.extend(result)
                    if videos:
                        return videos
            except Exception as e:
                logger.debug(f"选择器策略失败: {e}")
        
        return videos
    
    async def _extract_with_selector_v1(self) -> List[VideoInfo]:
        """选择器策略1: 通用视频卡片"""
        video_data = await self._page.evaluate("""
            () => {
                const results = [];
                
                // 查找所有可能的视频卡片 - 更新选择器列表
                const selectors = [
                    'li[data-e2e="search-common-video"]',
                    '[data-e2e="search-common-video"]',
                    '[class*="SearchVideoCard"]',
                    '[class*="VideoCard"]',
                    '[class*="video-card"]',
                    '[class*="search-result"] li',
                    'ul[class*="video"] > li',
                    'div[class*="video-item"]',
                    'a[href*="/video/"]',
                ];
                
                let cards = [];
                for (const sel of selectors) {
                    try {
                        const found = document.querySelectorAll(sel);
                        if (found.length > cards.length) {
                            cards = found;
                        }
                    } catch (e) {}
                }
                
                // 如果没有找到卡片，尝试直接找视频链接
                if (cards.length === 0) {
                    const links = document.querySelectorAll('a[href*="/video/"]');
                    links.forEach(link => {
                        const videoUrl = link.href;
                        const videoId = videoUrl.match(/video\\/(\\d+)/)?.[1] || '';
                        if (videoId) {
                            results.push({
                                video_id: videoId,
                                video_url: videoUrl,
                                description: '',
                                author_name: '',
                                likes: 0,
                                comments: 0,
                                shares: 0,
                                collects: 0,
                            });
                        }
                    });
                    return results;
                }
                
                cards.forEach(card => {
                    try {
                        // 视频链接
                        const link = card.querySelector('a[href*="/video/"]');
                        if (!link) return;
                        
                        const videoUrl = link.href;
                        const videoId = videoUrl.match(/video\\/(\\d+)/)?.[1] || '';
                        if (!videoId) return;
                        
                        // 描述
                        const descEl = card.querySelector('[class*="title"], [class*="desc"], h3, h4, p');
                        const description = descEl ? descEl.textContent.trim() : '';
                        
                        // 作者
                        const authorEl = card.querySelector('[class*="author"], [class*="name"], [class*="nickname"]');
                        const authorName = authorEl ? authorEl.textContent.trim() : '';
                        
                        // 统计
                        const stats = card.querySelectorAll('[class*="count"], [class*="stats"] span');
                        let likes = 0, comments = 0;
                        if (stats.length >= 1) likes = parseCount(stats[0].textContent);
                        if (stats.length >= 2) comments = parseCount(stats[1].textContent);
                        
                        results.push({
                            video_id: videoId,
                            video_url: videoUrl,
                            description: description,
                            author_name: authorName,
                            likes: likes,
                            comments: comments,
                            shares: 0,
                            collects: 0,
                        });
                    } catch (e) {}
                });
                
                function parseCount(str) {
                    str = str.trim();
                    if (str.includes('万')) return Math.floor(parseFloat(str) * 10000);
                    if (str.includes('亿')) return Math.floor(parseFloat(str) * 100000000);
                    return parseInt(str.replace(/[^0-9]/g, '')) || 0;
                }
                
                return results;
            }
        """)
        
        return [VideoInfo(**item) for item in video_data]
    
    async def _extract_with_selector_v2(self) -> List[VideoInfo]:
        """选择器策略2: 从所有链接提取"""
        video_data = await self._page.evaluate("""
            () => {
                const results = [];
                const seen = new Set();
                
                // 找所有视频链接
                const links = document.querySelectorAll('a[href*="/video/"]');
                
                links.forEach(link => {
                    const videoUrl = link.href;
                    const videoId = videoUrl.match(/video\\/(\\d+)/)?.[1] || '';
                    
                    if (!videoId || seen.has(videoId)) return;
                    seen.add(videoId);
                    
                    // 尝试从父元素获取更多信息
                    let card = link.closest('li, [class*="card"], [class*="item"]');
                    if (!card) card = link.parentElement?.parentElement?.parentElement;
                    
                    let description = '';
                    let authorName = '';
                    let likes = 0;
                    
                    if (card) {
                        const descEl = card.querySelector('[class*="title"], [class*="desc"], h3, h4, p');
                        description = descEl ? descEl.textContent.trim() : '';
                        
                        const authorEl = card.querySelector('[class*="author"], [class*="name"]');
                        authorName = authorEl ? authorEl.textContent.trim() : '';
                    }
                    
                    results.push({
                        video_id: videoId,
                        video_url: videoUrl,
                        description: description,
                        author_name: authorName,
                        likes: likes,
                        comments: 0,
                        shares: 0,
                        collects: 0,
                    });
                });
                
                return results;
            }
        """)
        
        return [VideoInfo(**item) for item in video_data]
    
    async def _extract_from_render_data(self) -> List[VideoInfo]:
        """从页面 RENDER_DATA 提取"""
        video_data = await self._page.evaluate("""
            () => {
                const results = [];
                
                try {
                    // 查找 __RENDER_DATA__
                    const scripts = document.querySelectorAll('script');
                    for (const script of scripts) {
                        const text = script.textContent || '';
                        if (text.includes('__RENDER_DATA__')) {
                            const match = text.match(/window\\.__RENDER_DATA__\\s*=\\s*(".*?")\\s*<\\/script>/);
                            if (match) {
                                const decoded = decodeURIComponent(JSON.parse(match[1]));
                                const data = JSON.parse(decoded);
                                
                                // 解析数据结构
                                const videoList = data?.app?.videoList || data?.data?.data || [];
                                
                                videoList.forEach(item => {
                                    const video = item.aweme_info || item;
                                    if (video?.aweme_id) {
                                        results.push({
                                            video_id: video.aweme_id,
                                            video_url: `https://www.douyin.com/video/${video.aweme_id}`,
                                            description: video.desc || '',
                                            author_name: video.author?.nickname || '',
                                            author_id: video.author?.unique_id || video.author?.uid || '',
                                            likes: video.statistics?.digg_count || 0,
                                            comments: video.statistics?.comment_count || 0,
                                            shares: video.statistics?.share_count || 0,
                                            collects: video.statistics?.collect_count || 0,
                                        });
                                    }
                                });
                                
                                break;
                            }
                        }
                    }
                } catch (e) {
                    console.error('RENDER_DATA 解析失败', e);
                }
                
                return results;
            }
        """)
        
        return [VideoInfo(**item) for item in video_data]
    
    async def get_video_detail(self, video_url: str, timeout: int = 60, min_likes: int = 0) -> VideoInfo:
        """
        获取视频详情（补充完整信息）- 访问详情页并截图
        
        Args:
            video_url: 视频链接
            timeout: 页面加载超时时间，默认60秒
            min_likes: 最小点赞数过滤，用于决定是否等待视频加载
        
        Returns:
            完整视频信息
        """
        video_id = self._extract_video_id_from_url(video_url)
        logger.info(f"获取视频详情: {video_id} (超时: {timeout}s)")
        
        await self.ensure_browser()
        
        try:
            logger.info(f"正在导航到页面: {video_url}")
            page_load_success = True
            try:
                await self._page.goto(video_url, wait_until="domcontentloaded", timeout=timeout*1000)
                logger.info("页面导航完成")
            except Exception as e:
                logger.warning(f"页面加载超时，尝试继续提取: {e}")
                page_load_success = False
                if "Timeout" not in str(e):
                    raise
            
            await asyncio.sleep(2)
            
            current_url = self._page.url
            logger.info(f"当前URL: {current_url}")
            
            if 'login' in current_url:
                logger.warning(f"被重定向到登录页: {current_url}")
                return VideoInfo(
                    video_url=video_url,
                    description="需要重新登录",
                    video_id=video_id
                )
            
            if video_id not in current_url:
                logger.warning(f"URL重定向到其他视频: 请求={video_id}, 当前={current_url}")
                return VideoInfo(
                    video_url=video_url,
                    description="页面重定向到其他视频",
                    video_id=video_id
                )
            
            if not page_load_success:
                page_ready = await self._check_page_ready()
                if not page_ready:
                    logger.warning("页面加载失败，未检测到有效内容")
                    return VideoInfo(
                        video_url=video_url,
                        description="页面加载失败",
                        video_id=video_id
                    )
            
            try:
                await self._page.wait_for_selector('[class*="fN2jqmuV"]', timeout=10000)
                logger.info("互动数据元素已加载")
            except Exception:
                logger.warning("未找到互动数据元素，继续尝试提取")
            
            await asyncio.sleep(1)
            
            logger.info("开始提取基础信息...")
            basic_data = await self._extract_basic_info(video_id)
            logger.info(f"基础信息提取完成: likes={basic_data.get('likes', 0)}")
            
            if not basic_data:
                return VideoInfo(
                    video_url=video_url,
                    description="页面加载超时或无法解析",
                    video_id=video_id
                )
            
            if min_likes > 0 and basic_data.get('likes', 0) < min_likes:
                logger.info(f"点赞数 {basic_data.get('likes')} < {min_likes}，跳过视频流提取")
                return VideoInfo(
                    video_id=video_id,
                    video_url=video_url,
                    author_name=basic_data.get('author_name', ''),
                    author_id=basic_data.get('author_id', ''),
                    description=basic_data.get('description', ''),
                    tags=basic_data.get('tags', []),
                    likes=basic_data.get('likes', 0),
                    comments=basic_data.get('comments', 0),
                    collects=basic_data.get('collects', 0),
                    shares=basic_data.get('shares', 0),
                    fans_count=basic_data.get('fans_count', '0'),
                    liked_count=basic_data.get('liked_count', '0'),
                    ip_location=basic_data.get('ip_location', ''),
                    author_signature=basic_data.get('author_signature', ''),
                )
            
            logger.info(f"点赞数 {basic_data.get('likes')} >= {min_likes}，等待视频元素加载...")
            
            video_stream_url = ""
            audio_url = ""
            thumbnail_url = ""
            
            try:
                await self._page.wait_for_selector('video', timeout=15000, state="attached")
                logger.info("视频元素已加载")
                
                await asyncio.sleep(2)
                
                logger.info("开始提取视频流地址...")
                video_data = await self._extract_video_stream()
                video_stream_url = video_data.get('video_stream_url', '')
                audio_url = video_data.get('audio_url', '')
                thumbnail_url = video_data.get('thumbnail_url', '')
                logger.info(f"视频流地址提取完成: {video_stream_url[:50] if video_stream_url else 'N/A'}...")
                
            except Exception as e:
                logger.warning(f"视频元素加载失败: {e}")
                logger.info("尝试备用方案提取视频流地址...")
                video_data = await self._extract_video_stream()
                video_stream_url = video_data.get('video_stream_url', '')
                audio_url = video_data.get('audio_url', '')
                thumbnail_url = video_data.get('thumbnail_url', '')
            
            logger.info("开始保存截图...")
            screenshot_path = await self._save_video_screenshot(video_id)
            logger.info(f"截图保存完成: {screenshot_path}")
            
            return VideoInfo(
                video_id=video_id,
                video_url=video_url,
                author_name=basic_data.get('author_name', ''),
                author_id=basic_data.get('author_id', ''),
                ip_location=basic_data.get('ip_location', ''),
                author_signature=basic_data.get('author_signature', ''),
                fans_count=basic_data.get('fans_count', '0'),
                liked_count=basic_data.get('liked_count', '0'),
                description=basic_data.get('description', ''),
                tags=basic_data.get('tags', []),
                likes=basic_data.get('likes', 0),
                comments=basic_data.get('comments', 0),
                collects=basic_data.get('collects', 0),
                shares=basic_data.get('shares', 0),
                audio_url=audio_url,
                video_stream_url=video_stream_url,
                thumbnail_url=thumbnail_url,
            )
        
        except Exception as e:
            logger.error(f"获取视频详情失败: {e}")
            raise
    
    async def _check_page_ready(self) -> bool:
        """检查页面是否加载完成且包含有效内容"""
        try:
            is_ready = await self._page.evaluate("""
                () => {
                    if (document.readyState === 'loading') {
                        return false;
                    }
                    const body = document.body;
                    if (!body || body.innerText.trim().length < 100) {
                        return false;
                    }
                    const videoElements = document.querySelectorAll('video');
                    const textContent = document.querySelectorAll('[class*="fN2jqmuV"], [class*="author"], [class*="video"]');
                    return videoElements.length > 0 || textContent.length > 0;
                }
            """)
            return bool(is_ready)
        except Exception as e:
            logger.warning(f"检查页面状态失败: {e}")
            return False
    
    async def _extract_basic_info(self, video_id: str) -> dict:
        """快速提取基础信息（点赞、评论、作者等）"""
        try:
            data = await self._page.evaluate("""
                () => {
                    const result = {};
                    
                    function parseCount(str) {
                        if (!str) return 0;
                        str = String(str).trim();
                        const match = str.match(/[\\d.]+/);
                        if (!match) return 0;
                        const num = parseFloat(match[0]);
                        if (str.includes('万')) return Math.floor(num * 10000);
                        if (str.includes('亿')) return Math.floor(num * 100000000);
                        return Math.floor(num) || 0;
                    }
                    
                    const title = document.title;
                    let rawDesc = '';
                    if (title && title.includes(' - 抖音')) {
                        rawDesc = title.replace(' - 抖音', '').trim();
                    }
                    
                    const tagMatches = rawDesc.match(/#[^#\\s]+/g) || [];
                    result.tags = tagMatches.map(t => t.trim());
                    result.description = rawDesc.replace(/#[^#\\s]+/g, '').replace(/\\s+/g, ' ').trim();
                    
                    const interactContainer = document.querySelector('[class*="fN2jqmuV"]');
                    
                    if (interactContainer) {
                        const numSpans = interactContainer.querySelectorAll('[class*="oYTywyxr"], [class*="Vc7Hm_bN"]');
                        const nums = [];
                        numSpans.forEach(el => {
                            const text = el.textContent.trim();
                            if (text && /^[\\d.]+[万亿]?$/.test(text)) {
                                nums.push(text);
                            }
                        });
                        
                        if (nums.length >= 4) {
                            result.likes = parseCount(nums[0]);
                            result.comments = parseCount(nums[1]);
                            result.collects = parseCount(nums[2]);
                            result.shares = parseCount(nums[3]);
                        } else {
                            const allNumDivs = interactContainer.querySelectorAll('div, span');
                            const allNums = [];
                            allNumDivs.forEach(el => {
                                const text = el.textContent.trim();
                                if (/^[\\d.]+[万亿]?$/.test(text) && text.length < 10 && !allNums.includes(text)) {
                                    allNums.push(text);
                                }
                            });
                            result.likes = parseCount(allNums[0]);
                            result.comments = parseCount(allNums[1]);
                            result.collects = parseCount(allNums[2]);
                            result.shares = parseCount(allNums[3]);
                        }
                    }
                    
                    if (!result.likes) {
                        const allNums = [];
                        document.querySelectorAll('div, span').forEach(el => {
                            const text = el.textContent.trim();
                            if (/^[\\d.]+[万亿]?$/.test(text) && text.length < 10) {
                                const parent = el.parentElement;
                                if (parent && parent.className && (
                                    parent.className.includes('fN2jqmuV') ||
                                    parent.className.includes('zqe4B9aR')
                                )) {
                                    if (!allNums.includes(text)) allNums.push(text);
                                }
                            }
                        });
                        result.likes = parseCount(allNums[0]);
                        result.comments = parseCount(allNums[1]);
                        result.collects = parseCount(allNums[2]);
                        result.shares = parseCount(allNums[3]);
                    }
                    
                    const allFollowBtns = document.querySelectorAll('button');
                    let videoFollowBtn = null;
                    
                    for (const btn of allFollowBtns) {
                        const text = btn.textContent.trim();
                        if (text === '关注') {
                            const parent = btn.closest('[class*="userMenuPanel"], [class*="tab-user"]');
                            if (!parent) {
                                videoFollowBtn = btn;
                                break;
                            }
                        }
                    }
                    
                    if (videoFollowBtn) {
                        let container = videoFollowBtn.parentElement;
                        for (let i = 0; i < 5 && container; i++) {
                            const links = container.querySelectorAll('a[href*="/user/"]');
                            for (const link of links) {
                                if (link.className.includes('tab-user') || link.closest('[class*="userMenuPanel"]')) {
                                    continue;
                                }
                                const span = link.querySelector('span');
                                if (span) {
                                    const text = span.textContent.trim();
                                    if (text && text.length < 30 && text !== '关注' && text !== '我的' && !text.match(/^[\\d.]+/)) {
                                        result.author_name = text;
                                        break;
                                    }
                                }
                            }
                            if (result.author_name) break;
                            container = container.parentElement;
                        }
                    }
                    
                    if (!result.author_name) {
                        const userLinks = document.querySelectorAll('a[href*="/user/"]');
                        for (const link of userLinks) {
                            if (link.className.includes('tab-user') || link.closest('[class*="userMenuPanel"]')) {
                                continue;
                            }
                            const span = link.querySelector('span');
                            if (span) {
                                const text = span.textContent.trim();
                                if (text && text.length < 30 && text !== '我的' && !text.match(/^[\\d.]+/)) {
                                    result.author_name = text;
                                    break;
                                }
                            }
                        }
                    }
                    
                    const authorLink = document.querySelector('a[href*="/user/"]');
                    if (authorLink) {
                        const match = authorLink.href.match(/user\\/([^/?]+)/);
                        result.author_id = match ? match[1] : '';
                    }
                    
                    const fansEls = document.querySelectorAll('[class*="EBi41nRR"]');
                    if (fansEls.length >= 2) {
                        result.fans_count = fansEls[0].textContent.trim();
                        result.liked_count = fansEls[1].textContent.trim();
                    }
                    
                    const sigEl = document.querySelector('[class*="signature"], [class*="bio"], [class*="introduction"]');
                    result.author_signature = sigEl ? sigEl.textContent.trim() : '';
                    
                    const ipEl = document.querySelector('[class*="ip-location"], [class*="location"]');
                    if (ipEl) {
                        const ipText = ipEl.textContent.trim();
                        result.ip_location = ipText.replace('IP:', '').replace('IP:', '').replace('IP属地:', '').trim();
                    }
                    
                    return result;
                }
            """)
            return data or {}
        except Exception as e:
            logger.error(f"提取基础信息失败: {e}")
            return {}
    
    async def _extract_video_stream(self) -> dict:
        """提取视频流地址"""
        try:
            # 首先检查是否有 __RENDER_DATA__
            has_render_data = await self._page.evaluate("""
                () => {
                    const scripts = document.querySelectorAll('script');
                    for (const script of scripts) {
                        if ((script.textContent || '').includes('__RENDER_DATA__')) {
                            return true;
                        }
                    }
                    return false;
                }
            """)
            logger.info(f"页面包含 __RENDER_DATA__: {has_render_data}")
            
            data = await self._page.evaluate("""
                () => {
                    const result = {
                        video_stream_url: '',
                        audio_url: '',
                        thumbnail_url: ''
                    };
                    
                    // 先尝试从 __RENDER_DATA__ 提取真实视频地址
                    try {
                        const scripts = document.querySelectorAll('script');
                        for (const script of scripts) {
                            const text = script.textContent || '';
                            if (text.includes('__RENDER_DATA__')) {
                                const match = text.match(/window\\.__RENDER_DATA__\\s*=\\s*"([^"]+)"/);
                                if (match) {
                                    const decoded = decodeURIComponent(match[1]);
                                    const renderData = JSON.parse(decoded);
                                    
                                    // 尝试多种数据结构
                                    let videoInfo = renderData?.app?.videoInfo?.item_list?.[0] || 
                                                   renderData?.videoInfo?.item_list?.[0] ||
                                                   renderData?.aweme_detail ||
                                                   renderData?.data?.item_list?.[0] ||
                                                   renderData?.item_list?.[0];
                                    
                                    console.log('找到 videoInfo:', !!videoInfo);
                                    
                                    if (videoInfo) {
                                        const music = videoInfo.music || videoInfo.sound;
                                        if (music) {
                                            result.audio_url = music.play_url?.url_list?.[0] || 
                                                             music.playUrl?.urlList?.[0] ||
                                                             music.url_list?.[0] || '';
                                        }
                                        
                                        const video = videoInfo.video;
                                        if (video) {
                                            // 优先从 bit_rate 获取高清视频
                                            if (video.bit_rate && video.bit_rate.length > 0) {
                                                let maxBitrate = 0;
                                                let bestUrl = '';
                                                for (const br of video.bit_rate) {
                                                    const url = br.play_addr?.url_list?.[0] || br.playAddr?.urlList?.[0] || '';
                                                    const bitrate = br.bit_rate || br.bitRate || 0;
                                                    if (url && bitrate >= maxBitrate) {
                                                        maxBitrate = bitrate;
                                                        bestUrl = url;
                                                    }
                                                }
                                                result.video_stream_url = bestUrl;
                                            }
                                            
                                            if (!result.video_stream_url) {
                                                result.video_stream_url = video.play_addr?.url_list?.[0] || 
                                                                         video.playAddr?.urlList?.[0] || '';
                                            }
                                            
                                            if (!result.video_stream_url) {
                                                result.video_stream_url = video.download_addr?.url_list?.[0] || '';
                                            }
                                            
                                            const coverUrl = video.cover?.url_list?.[0] ||
                                                             video.dynamic_cover?.url_list?.[0] ||
                                                             video.origin_cover?.url_list?.[0] ||
                                                             video.cover?.urlList?.[0] || '';
                                            if (coverUrl && !coverUrl.includes('owcRcADDpCps6bCFEAA6dAE9AA4zfsbgf9IGCm')) {
                                                result.thumbnail_url = coverUrl;
                                            }
                                        }
                                        break;
                                    }
                                }
                            }
                        }
                    } catch (e) {
                        console.error('提取视频流失败', e);
                    }
                    
                    // 如果没获取到，尝试从 video 元素获取
                    if (!result.video_stream_url || result.video_stream_url.startsWith('blob:')) {
                        const videoEl = document.querySelector('video');
                        if (videoEl) {
                            const src = videoEl.currentSrc || videoEl.src || '';
                            if (src && !src.startsWith('blob:')) {
                                result.video_stream_url = src;
                            }
                            if (videoEl.poster && !videoEl.poster.includes('owcRcADDpCps6bCFEAA6dAE9AA4zfsbgf9IGCm')) {
                                result.thumbnail_url = videoEl.poster;
                            }
                        }
                    }
                    
                    return result;
                }
            """)
            
            video_url = data.get('video_stream_url', '')
            logger.info(f"视频流地址提取完成: {video_url[:80] if video_url else 'N/A'}...")
            
            if not video_url or video_url.startswith('blob:'):
                logger.warning("无法获取真实视频URL或获取到blob URL，尝试备用方案...")
                fallback_result = await self._extract_video_stream_fallback()
                if fallback_result and fallback_result.get('video_stream_url') and not fallback_result.get('video_stream_url').startswith('blob:'):
                    logger.info(f"备用方案获取到有效视频URL: {fallback_result['video_stream_url'][:80]}...")
                    return fallback_result
                else:
                    logger.warning("备用方案也未能获取到有效URL，返回空结果")
                    return data or {}
            else:
                logger.info(f"获取到有效视频流地址")
            
            return data or {}
        except Exception as e:
            logger.error(f"提取视频流失败: {e}")
            return {}

    
    
    async def _extract_video_stream_fallback(self) -> dict:
        """备用方案: 通过改进的网络请求监听获取视频URL"""
        try:
            logger.info("尝试从页面和网络请求获取视频URL...")
            
            # 首先尝试从页面元素获取（避免blob URL）
            video_element_url = await self._page.evaluate("""
                () => {
                    const videos = document.querySelectorAll('video');
                    for (let video of videos) {
                        if (video.currentSrc && !video.currentSrc.startsWith('blob:')) {
                            return video.currentSrc;
                        }
                        if (video.src && !video.src.startsWith('blob:')) {
                            return video.src;
                        }
                    }
                    const sources = document.querySelectorAll('source');
                    for (const source of sources) {
                        if (source.src && !source.src.startsWith('blob:')) {
                            return source.src;
                        }
                    }
                    return "";
                }
            """)
            
            if video_element_url and not video_element_url.startswith("blob:"):
                logger.info(f"从元素获取到视频URL: {video_element_url[:80]}")
                return {"video_stream_url": video_element_url, "audio_url": "", "thumbnail_url": ""}
            
            # 使用路由方式捕获分离的视频/音频轨道
            video_urls = []
            audio_urls = []
            
            async def capture_media_requests(route, request):
                try:
                    url = request.url
                    resource_type = request.resource_type
                    
                    if resource_type in ['media', 'xhr', 'fetch']:
                        url_lower = url.lower()
                        
                        # 排除非媒体资源
                        if any(skip in url_lower for skip in ['cover', 'image', 'avatar', 'poster', 'preload']):
                            await route.continue_()
                            return
                        
                        # 检测分离的视频轨道 (media-video-*)
                        if 'media-video' in url_lower:
                            logger.info(f"捕获视频轨道: {url[:80]}")
                            video_urls.append(url)
                        
                        # 检测分离的音频轨道 (media-audio-*)
                        elif 'media-audio' in url_lower:
                            logger.info(f"捕获音频轨道: {url[:80]}")
                            audio_urls.append(url)
                        
                        # 检测合并的视频文件
                        elif any(fmt in url_lower for fmt in ['.mp4', '.mov', '.m3u8']):
                            if 'media-video' not in url_lower and 'media-audio' not in url_lower:
                                logger.info(f"捕获合并视频: {url[:80]}")
                                video_urls.append(url)
                            
                    await route.continue_()
                except:
                    try:
                        await route.continue_()
                    except:
                        pass
            
            # 设置路由
            await self._page.route("**/*", capture_media_requests)
            await asyncio.sleep(6)
            await self._page.unroute("**/*")
            
            # 处理捕获的URLs
            if video_urls:
                # 优先选择分离的视频轨道
                separated_video = [u for u in video_urls if 'media-video' in u.lower()]
                merged_video = [u for u in video_urls if 'media-video' not in u.lower()]
                
                if separated_video:
                    # 有分离的视频轨道
                    best_video = separated_video[0]
                    # 查找对应的音频轨道
                    separated_audio = [u for u in audio_urls if 'media-audio' in u.lower()]
                    best_audio = separated_audio[0] if separated_audio else ""
                    
                    logger.info(f"获取到分离视频: {best_video[:80]}")
                    if best_audio:
                        logger.info(f"获取到分离音频: {best_audio[:80]}")
                    
                    return {"video_stream_url": best_video, "audio_url": best_audio, "thumbnail_url": ""}
                
                elif merged_video:
                    # 只有合并的视频
                    best_url = max(merged_video, key=len)
                    logger.info(f"获取到合并视频URL: {best_url[:80]}")
                    return {"video_stream_url": best_url, "audio_url": "", "thumbnail_url": ""}

        except Exception as e:
            try:
                await self._page.unroute("**/*")
            except:
                pass
            logger.error(f"备用方案失败: {e}")
        
        logger.info("备用方案未能获取到视频URL")
        return {}

    async def _extract_video_stream_with_network_interception(self) -> dict:
        """增强的网络请求拦截方法""" 
        try:
            logger.info("启用网络请求拦截获取视频URL...")
            
            captured_requests = []
            
            def request_handler(request):
                url = request.url
                resource_type = request.resource_type
                if resource_type in ['media', 'xhr', 'fetch']:
                    # 查找视频相关请求
                    if any(media_type in url.lower() for media_type in ['.mp4', '/video/', '/v0/', 'playwm', 'download']):
                        if not any(skip_word in url.lower() for skip_word in ['cover', 'image', 'avatar', 'poster']):
                            captured_requests.append({
                                'url': url,
                                'resource_type': resource_type,
                                'timestamp': asyncio.get_event_loop().time()
                            })
            
            self._page.on("request", request_handler)
            
            # 等待请求捕获
            await asyncio.sleep(6)
            
            # 清理监听器
            self._page.off("request", request_handler)
            
            # 处理捕获的结果
            if captured_requests:
                # 按URL长度排序，通常更长的URL包含更多参数，更可能是视频URL
                sorted_requests = sorted(captured_requests, key=lambda x: len(x['url']), reverse=True)
                video_url = sorted_requests[0]['url']
                logger.info(f"通过网络拦截获取到视频URL: {video_url[:80]}...")
                return {'video_stream_url': video_url, 'audio_url': '', 'thumbnail_url': ''}
            
            return {}
            
        except Exception as e:
            logger.error(f"网络拦截方法失败: {e}")
            return {}


    def _extract_video_id_from_url(self, url: str) -> str:
        """从URL提取视频ID"""
        # 匹配标准视频链接格式: /video/xxxx
        import re
        match = re.search(r'video/(\d+)', url)
        if match:
            return match.group(1)
        
        # 匹配精选页面格式: /jingxuan?modal_id=xxxx
        match = re.search(r'modal_id=(\d+)', url)
        if match:
            return match.group(1)
        
        # 匹配/share/video/xxxx形式
        match = re.search(r'share/video/(\d+)', url)
        if match:
            return match.group(1)
        
        # 匹配带查询参数的格式，例如?video_id=xxx
        match = re.search(r'[?&]video_id=(\d+)', url)
        if match:
            return match.group(1)
        
        return ''

    async def _dump_page_structure(self, video_id: str):
        """导出页面结构用于调试"""
        try:
            from config import get_settings
            settings = get_settings()
            debug_dir = settings.DATA_DIR / "debug"
            debug_dir.mkdir(parents=True, exist_ok=True)
            
            # 提取关键元素信息
            page_info = await self._page.evaluate("""
                () => {
                    const info = {
                        url: window.location.href,
                        title: document.title,
                        elements: []
                    };
                    
                    // 提取所有按钮及其文本
                    document.querySelectorAll('button, [role="button"]').forEach(btn => {
                        const text = btn.textContent.trim();
                        if (text && text.length < 30) {
                            info.elements.push({
                                type: 'button',
                                text: text,
                                class: btn.className
                            });
                        }
                    });
                    
                    // 提取关注按钮附近的元素（作者信息）
                    document.querySelectorAll('button').forEach(btn => {
                        if (btn.textContent.includes('关注')) {
                            const parent = btn.parentElement;
                            if (parent) {
                                const siblings = parent.querySelectorAll('span, a, div');
                                siblings.forEach(el => {
                                    const text = el.textContent.trim();
                                    if (text && text.length < 30 && text !== '关注') {
                                        info.elements.push({
                                            type: 'author_area',
                                            text: text,
                                            tag: el.tagName,
                                            class: el.className
                                        });
                                    }
                                });
                            }
                        }
                    });
                    
                    // 提取所有包含数字的元素
                    document.querySelectorAll('*').forEach(el => {
                        const text = el.textContent.trim();
                        if (/^[\\d.]+[万亿]?$/.test(text) && text.length < 10) {
                            const parent = el.parentElement;
                            info.elements.push({
                                type: 'number',
                                text: text,
                                tag: el.tagName,
                                class: el.className,
                                parentClass: parent ? parent.className : ''
                            });
                        }
                    });
                    
                    // 提取作者相关元素
                    document.querySelectorAll('[class*="author"], [class*="user"]').forEach(el => {
                        const text = el.textContent.trim().slice(0, 50);
                        if (text) {
                            info.elements.push({
                                type: 'author',
                                text: text,
                                class: el.className
                            });
                        }
                    });
                    
                    return info;
                }
            """)
            
            import json
            dump_path = debug_dir / f"{video_id}_structure.json"
            with open(dump_path, 'w', encoding='utf-8') as f:
                json.dump(page_info, f, ensure_ascii=False, indent=2)
            
            logger.debug(f"页面结构已导出: {dump_path}")
        except Exception as e:
            logger.warning(f"导出页面结构失败: {e}")

    async def _save_video_screenshot(self, video_id: str) -> str:
        """保存视频详情页截图"""
        try:
            from config import get_settings
            settings = get_settings()
            screenshot_dir = settings.DATA_DIR / "screenshots"
            screenshot_dir.mkdir(parents=True, exist_ok=True)
            
            screenshot_path = screenshot_dir / f"{video_id}.png"
            await self._page.screenshot(path=str(screenshot_path), full_page=False)
            
            return str(screenshot_path)
        except Exception as e:
            logger.warning(f"保存截图失败: {e}")

    
    async def download_video_via_browser(self, video_url: str, video_id: str, suffix: str = "") -> Path:
        """通过浏览器下载视频到本地"""
        try:
            from config import get_settings
            from crawler.downloader import VideoDownloader
            
            settings = get_settings()
            
            # 创建下载器实例
            downloader = VideoDownloader()
            
            # 使用Downloader下载视频
            video_path = await downloader.download_video(
                url=video_url,
                filename=f"{video_id}{suffix}",
                timeout=120
            )
            
            logger.info(f"视频下载完成: {video_path}")
            return video_path  # 返回Path对象，而不是str
            
        except Exception as e:
            logger.error(f"视频下载失败: {e}")
            # 重试使用直接请求下载
            try:
                import aiohttp
                from config import get_settings
                from pathlib import Path
                
                settings = get_settings()
                video_path = settings.VIDEOS_DIR / f"{video_id}{suffix}.mp4"
                
                # 使用aiohttp直接从URL下载文件
                async with aiohttp.ClientSession() as session:
                    headers = {
                        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                        "Referer": "https://www.douyin.com/"
                    }
                    async with session.get(video_url, headers=headers) as resp:
                        if resp.status == 200:
                            with open(video_path, 'wb') as f:
                                async for chunk in resp.content.iter_chunked(8192):
                                    f.write(chunk)
                            logger.info(f"视频下载完成 (直接请求): {video_path}")
                            return video_path  # 返回Path对象，而不是str
                        else:
                            logger.error(f"视频下载失败 (状态码: {resp.status})")
                            return None
            except Exception as retry_e:
                logger.error(f"视频下载重试失败: {retry_e}")
                return None
