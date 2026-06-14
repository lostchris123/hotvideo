"""
小红书爬虫模块
"""
from __future__ import annotations
import asyncio
import re
import json
import random
from typing import Optional, List, Dict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from playwright.async_api import Page, TimeoutError as PlaywrightTimeout, Browser
from loguru import logger

from .browser import BrowserManager
from .models import (
    XiaohongshuVideoRecord, PLATFORM_MODELS, get_platform_session
)
from config import get_settings


@dataclass
class XiaohongshuVideoInfo:
    """小红书视频/图文信息"""
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
    audio_url: str = ""
    
    likes: int = 0
    comments: int = 0
    collects: int = 0
    shares: int = 0
    duration: float = 0.0
    platform: str = "xiaohongshu"
    is_video: bool = True  # 新增：是否为视频（False表示图文）
    images: List[str] = field(default_factory=list)  # 新增：图片URL列表（图文用）
    
    def to_db_record(self, keyword: str = ""):
        """转换为数据库记录"""
        from .models import XiaohongshuVideoRecord, XiaohongshuImageRecord
        
        # 根据 is_video 选择对应的模型
        if self.is_video:
            ModelClass = XiaohongshuVideoRecord
            return ModelClass(
                author_name=self.author_name,
                author_id=self.author_id,
                ip_location=self.ip_location,
                author_signature=self.author_signature,
                fans_count=self.fans_count,
                liked_count=self.liked_count,
                video_id=self.video_id,
                description=self.description,
                tags=json.dumps(self.tags, ensure_ascii=False) if self.tags else None,
                video_url=self.video_url,
                thumbnail_url=self.thumbnail_url,
                publish_date=self.publish_date,
                likes=self.likes,
                comments=self.comments,
                collects=self.collects,
                shares=self.shares,
                duration=self.duration,
                keyword=keyword,
            )
        else:
            ModelClass = XiaohongshuImageRecord
            return ModelClass(
                author_name=self.author_name,
                author_id=self.author_id,
                ip_location=self.ip_location,
                author_signature=self.author_signature,
                fans_count=self.fans_count,
                liked_count=self.liked_count,
                video_id=self.video_id,
                description=self.description,
                tags=json.dumps(self.tags, ensure_ascii=False) if self.tags else None,
                video_url=self.video_url,
                thumbnail_url=self.thumbnail_url,
                publish_date=self.publish_date,
                likes=self.likes,
                comments=self.comments,
                collects=self.collects,
                shares=self.shares,
                keyword=keyword,
                images=json.dumps(self.images, ensure_ascii=False) if self.images else None,
            )


async def _random_sleep(min_sec: float = 1.0, max_sec: float = 3.0):
    """随机延迟辅助函数"""
    import random
    await asyncio.sleep(random.uniform(min_sec, max_sec))


class XiaohongshuCrawler:
    """小红书爬虫"""
    
    BASE_URL = "https://www.xiaohongshu.com"
    
    def __init__(self, browser_manager: BrowserManager = None, user_id: int = None):
        self.user_id = user_id
        if browser_manager:
            self.browser_manager = browser_manager
        else:
            self.browser_manager = BrowserManager(platform="xiaohongshu", user_id=user_id)
        self._browser: Optional[Browser] = None
        self._page: Optional[Page] = None
    
    async def start(self):
        """启动爬虫"""
        if self._browser is None:
            self._browser = await self.browser_manager.start()
        if self._page is None:
            self._page = await self.browser_manager.get_page()
        logger.info("小红书爬虫启动")
    
    async def close(self):
        """关闭爬虫（不关闭浏览器，保持登录状态）"""
        self._page = None
        self._browser = None
        logger.info("小红书爬虫已关闭（浏览器保持运行）")
    
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
    
    async def open_login_page(self):
        """打开小红书登录页面"""
        await self.ensure_browser()
        login_url = f"{self.BASE_URL}/login"
        await self._page.goto(login_url, wait_until="domcontentloaded")
        logger.info("已打开小红书登录页面")
        return {"url": login_url, "message": "请在新打开的浏览器窗口中完成登录"}
    
    async def check_login(self):
        """检查小红书登录状态"""
        await self.ensure_browser()
        try:
            profile_url = "https://www.xiaohongshu.com/user/profile/5aef997ee8ac2b40c60ead39"
            await self._page.goto(profile_url, wait_until="domcontentloaded", timeout=10000)
            await asyncio.sleep(2)
            
            current_url = self._page.url
            if "/login" in current_url or "/signin" in current_url:
                return {"logged_in": False, "message": "未登录"}
            
            user_avatar = await self._page.query_selector('img.avatar, div.user-avatar, div.avatar-img')
            user_name = await self._page.query_selector('div.nickname, div.user-name, h1.user-name')
            
            if user_avatar or user_name:
                return {"logged_in": True, "message": "已登录"}
            else:
                content = await self._page.content()
                if "登录" in content and ("手机号" in content or "验证码" in content):
                    return {"logged_in": False, "message": "未登录"}
                return {"logged_in": True, "message": "可能已登录"}
                
        except Exception as e:
            logger.error(f"检查登录状态失败: {e}")
            return {"logged_in": False, "message": f"检查失败: {str(e)}"}
    
    def _extract_video_id_from_url(self, url: str) -> str:
        """从URL中提取视频ID"""
        patterns = [
            r'/explore/([a-zA-Z0-9]+)',
            r'/discovery/item/([a-zA-Z0-9]+)',
            r'xhslink\.com/([a-zA-Z0-9]+)',
        ]
        for pattern in patterns:
            match = re.search(pattern, url)
            if match:
                return match.group(1)
        return url.split('/')[-1].split('?')[0]
    
    async def get_video_detail(self, video_url: str, timeout: int = 60) -> XiaohongshuVideoInfo:
        """
        获取小红书视频详情
        
        Args:
            video_url: 视频链接
            timeout: 页面加载超时时间
        
        Returns:
            视频信息
        """
        video_id = self._extract_video_id_from_url(video_url)
        logger.info(f"获取小红书视频详情: {video_id}")
        
        await self.ensure_browser()
        
        try:
            logger.info(f"正在导航到页面: {video_url}")
            try:
                await self._page.goto(video_url, wait_until="networkidle", timeout=timeout*1000)
                logger.info("页面导航完成")
            except Exception as e:
                logger.warning(f"页面加载超时，尝试继续提取: {e}")
            
            await asyncio.sleep(2)
            
            current_url = self._page.url
            logger.info(f"当前URL: {current_url}")
            
            # 检测是否被重定向到404或登录页
            if '/404' in current_url or '/login' in current_url:
                error_msg = "页面无法访问"
                if '/404' in current_url:
                    error_msg = "笔记暂时无法浏览或已删除"
                elif '/login' in current_url:
                    error_msg = "需要重新登录"
                logger.warning(f"视频访问受限: {video_id}, 原因: {error_msg}")
                return XiaohongshuVideoInfo(
                    video_url=video_url,
                    description=error_msg,
                    video_id=video_id
                )
            
            if video_id not in current_url:
                logger.warning(f"URL重定向，可能视频不存在: {video_id}")
            
            logger.info("开始提取视频信息...")
            video_info = await self._extract_video_info(video_id)
            
            return video_info
            
        except Exception as e:
            logger.error(f"获取视频详情失败: {e}")
            raise
    
    async def _extract_video_info(self, video_id: str) -> XiaohongshuVideoInfo:
        """从页面提取视频信息"""
        try:
            data = await self._page.evaluate("""
                () => {
                    const result = {};
                    
                    // 提取视频下载链接 - 优先从页面数据中获取CDN链接
                    let videoStreamUrl = '';
                    
                    // 方法1: 从window.__INITIAL_STATE__中提取
                    if (window.__INITIAL_STATE__) {
                        const state = window.__INITIAL_STATE__;
                        // 尝试多种可能的路径
                        const paths = [
                            state?.note?.noteDetailMap,
                            state?.note?.firstNoteId,
                        ];
                        
                        for (const path of paths) {
                            if (path) {
                                try {
                                    const noteData = path[Object.keys(path)[0]];
                                    if (noteData?.note?.video?.media?.stream?.h264?.[0]?.masterUrl) {
                                        videoStreamUrl = noteData.note.video.media.stream.h264[0].masterUrl;
                                        break;
                                    }
                                    if (noteData?.note?.video?.media?.stream?.h265?.[0]?.masterUrl) {
                                        videoStreamUrl = noteData.note.video.media.stream.h265[0].masterUrl;
                                        break;
                                    }
                                    if (noteData?.note?.video?.media?.stream?.h264?.[0]?.backupUrls?.[0]) {
                                        videoStreamUrl = noteData.note.video.media.stream.h264[0].backupUrls[0];
                                        break;
                                    }
                                } catch (e) {}
                            }
                        }
                    }
                    
                    // 方法2: 从video标签获取（可能是blob URL）
                    if (!videoStreamUrl) {
                        const videoElements = document.querySelectorAll('video');
                        for (const video of videoElements) {
                            if (video.src && !video.src.startsWith('blob:')) {
                                videoStreamUrl = video.src;
                                break;
                            }
                        }
                    }
                    
                    // 方法3: 从页面脚本中提取xhscdn.com链接
                    if (!videoStreamUrl || videoStreamUrl.startsWith('blob:')) {
                        const allScripts = document.body.innerHTML;
                        const cdnMatches = allScripts.match(/https?:\\/\\/[^"'\s]+xhscdn\\.com[^"'\s]*/g);
                        if (cdnMatches && cdnMatches.length > 0) {
                            // 找到.mp4或.stream链接
                            for (const url of cdnMatches) {
                                if (url.includes('.mp4') || url.includes('/stream/')) {
                                    videoStreamUrl = url;
                                    break;
                                }
                            }
                            // 如果没找到，用第一个
                            if (!videoStreamUrl || videoStreamUrl.startsWith('blob:')) {
                                videoStreamUrl = cdnMatches[0];
                            }
                        }
                    }
                    
                    result.video_stream_url = videoStreamUrl;
                    
                    // 提取描述/标题
                    const titleEl = document.querySelector('meta[property="og:title"]');
                    if (titleEl) {
                        result.description = titleEl.getAttribute('content') || '';
                    }
                    if (!result.description) {
                        const descEl = document.querySelector('#detail-desc, .note-content, [class*="title"]');
                        if (descEl) {
                            result.description = descEl.textContent.trim();
                        }
                    }
                    
                    // 提取标签
                    const tags = [];
                    const tagElements = document.querySelectorAll('a[href*="/search_result?keyword="], [class*="tag"]');
                    tagElements.forEach(el => {
                        const text = el.textContent.trim().replace('#', '');
                        if (text && text.length > 0 && text.length < 50) {
                            tags.push(text);
                        }
                    });
                    result.tags = [...new Set(tags)];
                    
                    // 提取作者信息
                    const authorEl = document.querySelector('[class*="author"] [class*="name"], [class*="userName"], .author-wrapper .username');
                    if (authorEl) {
                        result.author_name = authorEl.textContent.trim();
                    }
                    
                    // 提取作者ID
                    const authorLink = document.querySelector('a[href*="/user/profile/"]');
                    if (authorLink) {
                        const href = authorLink.getAttribute('href') || '';
                        const match = href.match(/profile\\/([a-zA-Z0-9]+)/);
                        if (match) {
                            result.author_id = match[1];
                        }
                    }
                    
                    // 提取互动数据
                    const likeEl = document.querySelector('[class*="like"] [class*="count"], [data-v-likene123] span');
                    const collectEl = document.querySelector('[class*="collect"] [class*="count"], [data-v-collect] span');
                    const commentEl = document.querySelector('[class*="comment"] [class*="count"], [data-v-comment] span');
                    
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
                    
                    if (likeEl) result.likes = parseCount(likeEl.textContent);
                    if (collectEl) result.collects = parseCount(collectEl.textContent);
                    if (commentEl) result.comments = parseCount(commentEl.textContent);
                    
                    // 提取所有图片URL - 优先从INITIAL_STATE获取图文图片列表
                    const imageList = [];
                    
                    if (window.__INITIAL_STATE__) {
                        try {
                            const state = window.__INITIAL_STATE__;
                            console.log('INITIAL_STATE结构:', JSON.stringify(Object.keys(state || {})));
                            
                            const noteData = state?.note?.noteDetailMap?.[Object.keys(state?.note?.noteDetailMap || {})[0]];
                            console.log('noteData结构:', noteData ? JSON.stringify(Object.keys(noteData)) : '无');
                            
                            if (noteData?.note?.imageList && noteData.note.imageList.length > 0) {
                                console.log('找到imageList, 长度:', noteData.note.imageList.length);
                                
                                // 提取所有图片URL
                                for (const img of noteData.note.imageList) {
                                    const imgUrl = img.urlDefault || img.url || img.livePhotoUrl;
                                    if (imgUrl && !imgUrl.includes('/avatar/')) {
                                        imageList.push(imgUrl);
                                    }
                                }
                                console.log('提取到图片数量:', imageList.length);
                            } else if (noteData?.note?.cover?.urlDefault) {
                                imageList.push(noteData.note.cover.urlDefault);
                            } else {
                                console.log('未找到imageList或cover');
                            }
                        } catch (e) {
                            console.log('从INITIAL_STATE获取图片失败', e);
                        }
                    } else {
                        console.log('未找到INITIAL_STATE');
                    }
                    
                    // 如果INITIAL_STATE没有图片，尝试从页面DOM获取
                    if (imageList.length === 0) {
                        console.log('尝试从DOM获取图片');
                        const imgSelectors = [
                            'img[src*="notes_pre_post"]',
                            'img[src*="webpic"]',
                            '.note-content img',
                            '[class*="note-content"] img',
                        ];
                        for (const selector of imgSelectors) {
                            const imgs = document.querySelectorAll(selector);
                            console.log(`选择器 ${selector} 找到 ${imgs.length} 个图片`);
                            for (const img of imgs) {
                                if (img.src && !img.src.includes('/avatar/') && img.src.startsWith('http')) {
                                    imageList.push(img.src);
                                }
                            }
                            if (imageList.length > 0) break;
                        }
                    }
                    
                    // 设置缩略图（第一张）和图片列表
                    if (imageList.length > 0) {
                        result.thumbnail_url = imageList[0];
                        result.images = imageList;
                        console.log('最终图片列表:', imageList.length, '张');
                    } else {
                        // 尝试从meta标签获取
                        const imgEl = document.querySelector('meta[property="og:image"]');
                        if (imgEl) {
                            const content = imgEl.getAttribute('content') || '';
                            if (content && !content.includes('/avatar/')) {
                                result.thumbnail_url = content;
                                result.images = [content];
                            }
                        }
                        console.log('最终缩略图URL:', result.thumbnail_url || '未找到');
                    }
                    
                    // 尝试从window.__INITIAL_STATE__获取更多数据
                    if (window.__INITIAL_STATE__) {
                        try {
                            const state = window.__INITIAL_STATE__;
                            const noteData = state?.note?.noteDetailMap?.[Object.keys(state?.note?.noteDetailMap || {})[0]];
                            if (noteData?.note) {
                                const note = noteData.note;
                                if (note.interactInfo) {
                                    result.likes = note.interactInfo.likedCount || result.likes;
                                    result.collects = note.interactInfo.collectedCount || result.collects;
                                    result.comments = note.interactInfo.commentCount || result.comments;
                                    result.shares = note.interactInfo.shareCount || 0;
                                }
                                if (note.user) {
                                    result.author_name = note.user.nickname || result.author_name;
                                    result.author_id = note.user.userId || result.author_id;
                                }
                            }
                        } catch (e) {
                            console.log('解析INITIAL_STATE失败', e);
                        }
                    }
                    
                    return result;
                }
            """)
            
            video_info = XiaohongshuVideoInfo(
                video_id=video_id,
                video_url=self._page.url,
                author_name=data.get('author_name', ''),
                author_id=data.get('author_id', ''),
                description=data.get('description', ''),
                tags=data.get('tags', []),
                likes=data.get('likes', 0),
                comments=data.get('comments', 0),
                collects=data.get('collects', 0),
                shares=data.get('shares', 0),
                video_stream_url=data.get('video_stream_url', ''),
                thumbnail_url=data.get('thumbnail_url', ''),
                images=data.get('images', []),
            )
            
            # 日志输出图片数量
            if video_info.images:
                logger.info(f"提取到 {len(video_info.images)} 张图片")
            else:
                logger.warning(f"未提取到图片")
            
            # 判断是否为视频：有有效的视频流URL则为视频，否则为图文
            # 有效的视频流URL应该包含视频格式标识（.mp4, /stream/, .m3u8等），且不能是图片格式
            video_url = video_info.video_stream_url or ""
            is_valid_video = (
                video_url and 
                video_url.strip() and
                not video_url.startswith('blob:') and
                not any(video_url.lower().endswith(ext) for ext in ['.jpg', '.jpeg', '.png', '.webp', '.gif']) and
                any(indicator in video_url.lower() for indicator in ['.mp4', '/stream/', '.m3u8', 'video', 'h264', 'h265'])
            )
            
            if is_valid_video:
                video_info.is_video = True
                logger.info(f"检测到视频内容: {video_id}, 视频链接: {video_info.video_stream_url[:50]}...")
            else:
                video_info.is_video = False
                if video_info.video_stream_url:
                    logger.info(f"检测到图文内容: {video_id}, 忽略非视频链接: {video_info.video_stream_url[:50]}...")
                else:
                    logger.info(f"检测到图文内容: {video_id}, 无视频流")
            
            return video_info
            
        except Exception as e:
            logger.error(f"提取视频信息失败: {e}")
            # 提取失败时，默认标记为图文（is_video=False），避免误存到视频库
            return XiaohongshuVideoInfo(video_id=video_id, video_url=self._page.url, is_video=False)
    
    async def _download_and_extract_thumbnail(self, video_info: XiaohongshuVideoInfo) -> str:
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
                logger.info(f"开始下载小红书视频: {video_info.video_id}")
                
                video_path = await downloader.download_video_with_audio(
                    video_url=video_info.video_stream_url,
                    audio_url=video_info.audio_url or "",
                    filename=video_info.video_id
                )
                
                if video_path and video_path.exists():
                    logger.info(f"小红书视频下载完成: {video_path}")
                    
                    # 提取缩略图
                    try:
                        thumb_path = downloader.extract_thumbnail(video_path)
                        if thumb_path:
                            video_info.thumbnail_url = f"/thumbnails/{thumb_path.name}"
                            logger.info(f"缩略图提取完成: {video_info.thumbnail_url}")
                    except Exception as e:
                        logger.warning(f"缩略图提取失败: {e}")
                    
                    # 提取视频时长
                    try:
                        video_info.duration = await downloader.get_video_duration(video_path)
                        if video_info.duration > 0:
                            logger.info(f"视频时长提取完成: {video_info.duration:.2f}秒")
                    except Exception as e:
                        logger.warning(f"视频时长提取失败: {e}")
                    
                    return video_info.thumbnail_url
            
            return ""
            
        except Exception as e:
            logger.error(f"下载小红书视频失败: {e}")
            return ""
    
    async def save_single_video_to_db(self, video_info: XiaohongshuVideoInfo, keyword: str = ""):
        """保存单个视频/图文到数据库
        
        根据 video_info.is_video 自动选择存储表
        同时下载缩略图到本地
        
        Args:
            video_info: 视频/图文信息
            keyword: 搜索关键词
        """
        import httpx
        from pathlib import Path
        from config import get_settings
        
        settings = get_settings()
        
        # 下载所有图片到本地，并生成本地访问路径
        local_image_urls = []
        if video_info.images and len(video_info.images) > 0:
            try:
                images_dir = settings.DATA_DIR / "images" / video_info.video_id
                images_dir.mkdir(parents=True, exist_ok=True)
                
                async with httpx.AsyncClient() as client:
                    for idx, img_url in enumerate(video_info.images):
                        if not img_url or not img_url.startswith('http'):
                            continue
                        
                        try:
                            # 为每张图片生成文件名
                            ext = '.jpg'
                            if '.png' in img_url.lower():
                                ext = '.png'
                            elif '.webp' in img_url.lower():
                                ext = '.webp'
                            
                            local_path = images_dir / f"{idx + 1}{ext}"
                            
                            # 如果本地文件不存在，则下载
                            if not local_path.exists():
                                response = await client.get(img_url, timeout=30, follow_redirects=True)
                                response.raise_for_status()
                                
                                with open(local_path, 'wb') as f:
                                    f.write(response.content)
                            
                            # 生成本地访问URL（静态文件路径）
                            local_image_urls.append(f"/images/{video_info.video_id}/{idx + 1}{ext}")
                            logger.debug(f"图片 {idx + 1}/{len(video_info.images)} 下载完成")
                        except Exception as e:
                            logger.warning(f"下载图片 {idx + 1} 失败: {e}")
                
                if local_image_urls:
                    logger.info(f"共下载 {len(local_image_urls)} 张图片: {video_info.video_id}")
                    # 更新 video_info 的 images 为本地URL
                    video_info.images = local_image_urls
            except Exception as e:
                logger.warning(f"下载图片失败: {e}")
        
        # 设置缩略图
        # 对于图文内容，优先使用本地下载的第一张图片作为缩略图
        if not video_info.is_video and local_image_urls:
            video_info.thumbnail_url = local_image_urls[0]
            logger.info(f"图文缩略图设置: {video_info.thumbnail_url}")
        # 对于视频内容，下载缩略图到本地
        elif video_info.thumbnail_url and video_info.thumbnail_url.startswith('http'):
            try:
                thumbnail_dir = settings.DATA_DIR / "thumbnails"
                thumbnail_dir.mkdir(parents=True, exist_ok=True)
                local_thumbnail_path = thumbnail_dir / f"{video_info.video_id}.jpg"
                
                # 如果本地文件不存在，则下载
                if not local_thumbnail_path.exists():
                    async with httpx.AsyncClient() as client:
                        response = await client.get(video_info.thumbnail_url, timeout=30)
                        response.raise_for_status()
                        
                        with open(local_thumbnail_path, 'wb') as f:
                            f.write(response.content)
                        
                        logger.info(f"缩略图下载完成: {video_info.video_id}")
                
                # 更新为本地路径
                video_info.thumbnail_url = f"/thumbnails/{video_info.video_id}.jpg"
            except Exception as e:
                logger.warning(f"下载缩略图失败: {e}")
        
        # 根据 is_video 属性自动选择对应的模型
        if video_info.is_video:
            ModelClass = XiaohongshuVideoRecord
            session = get_platform_session("xiaohongshu")
        else:
            from crawler.models import XiaohongshuImageRecord
            ModelClass = XiaohongshuImageRecord
            session = get_platform_session("xiaohongshu_image")
        
        # 解析数字格式的辅助函数
        def parse_count(value):
            if isinstance(value, int):
                return value
            if not value:
                return 0
            value = str(value).strip()
            try:
                if '万' in value:
                    num = float(value.replace('万', ''))
                    return int(num * 10000)
                elif '亿' in value:
                    num = float(value.replace('亿', ''))
                    return int(num * 100000000)
                else:
                    return int(float(value))
            except:
                return 0
        
        try:
            existing = session.query(ModelClass).filter_by(
                video_id=video_info.video_id
            ).first()
            
            # 确保数值类型正确
            likes = parse_count(video_info.likes)
            comments = parse_count(video_info.comments)
            collects = parse_count(video_info.collects)
            shares = parse_count(video_info.shares)
            
            if existing:
                existing.keyword = keyword
                existing.author_name = video_info.author_name
                existing.author_id = video_info.author_id
                existing.fans_count = video_info.fans_count
                existing.liked_count = video_info.liked_count
                existing.description = video_info.description
                existing.tags = json.dumps(video_info.tags, ensure_ascii=False) if video_info.tags else None
                existing.likes = likes
                existing.comments = comments
                existing.collects = collects
                existing.shares = shares
                existing.thumbnail_url = video_info.thumbnail_url
                existing.video_url = video_info.video_url
                existing.video_stream_url = video_info.video_stream_url
                existing.duration = video_info.duration
                # 图文记录：保存图片列表
                if hasattr(existing, 'images') and video_info.images:
                    existing.images = json.dumps(video_info.images, ensure_ascii=False)
                logger.debug(f"更新记录: {video_info.video_id}")
            else:
                record_data = {
                    "video_id": video_info.video_id,
                    "video_url": video_info.video_url,
                    "author_name": video_info.author_name,
                    "author_id": video_info.author_id,
                    "fans_count": video_info.fans_count,
                    "liked_count": video_info.liked_count,
                    "description": video_info.description,
                    "tags": json.dumps(video_info.tags, ensure_ascii=False) if video_info.tags else None,
                    "likes": likes,
                    "comments": comments,
                    "collects": collects,
                    "shares": shares,
                    "thumbnail_url": video_info.thumbnail_url,
                    "video_stream_url": video_info.video_stream_url,
                    "duration": video_info.duration,
                    "keyword": keyword,
                    "user_id": self.user_id,  # 关联当前用户
                }
                # 图文记录：添加图片列表
                if hasattr(ModelClass, 'images') and video_info.images:
                    record_data["images"] = json.dumps(video_info.images, ensure_ascii=False)
                
                record = ModelClass(**record_data)
                session.add(record)
                logger.debug(f"新增记录: {video_info.video_id}")
            
            session.commit()
        except Exception as e:
            logger.error(f"保存到数据库失败: {e}")
            session.rollback()
        finally:
            session.close()
    
    async def save_to_db(self, videos: List[XiaohongshuVideoInfo], keyword: str, platform: str = "xiaohongshu", min_likes: int = 0, skip_likes_filter: bool = False):
        """批量保存视频/图文到数据库
        
        Args:
            videos: 视频/图文列表
            keyword: 搜索关键词
            platform: 平台
            min_likes: 最小点赞数过滤
            skip_likes_filter: 是否跳过点赞数过滤（用于链接搜索）
        """
        for video in videos:
            if skip_likes_filter:
                await self.save_single_video_to_db(video, keyword)
            else:
                likes = int(video.likes) if video.likes else 0
                if likes >= min_likes:
                    await self.save_single_video_to_db(video, keyword)
    
    async def search_by_keyword(
        self, 
        keyword: str, 
        limit: int = 30, 
        min_likes: int = 0, 
        timeout: int = 60, 
        content_type: str = "video",
        download_videos: bool = False,
        progress_callback=None,
        cancel_check=None
    ) -> List[XiaohongshuVideoInfo]:
        """根据关键词搜索小红书内容（支持视频和图文）
        
        Args:
            keyword: 搜索关键词
            limit: 搜索结果数量限制
            min_likes: 最小点赞数
            timeout: 超时时间
            content_type: 内容类型，"video"或"image"
            download_videos: 是否在搜索时下载视频并提取缩略图
            progress_callback: 进度回调函数，接收进度字典
            cancel_check: 取消检查函数，返回True表示需要取消
        """
        import urllib.parse
        
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
        
        logger.info(f"搜索小红书关键词: {keyword}, 类型: {content_type}, 目标数量: {limit}")
        
        # 先访问首页，确保登录状态
        await self._page.goto(self.BASE_URL, wait_until="domcontentloaded", timeout=30000)
        await asyncio.sleep(2)
        
        # 搜索
        encoded_keyword = urllib.parse.quote(keyword)
        search_url = f"{self.BASE_URL}/search_result?keyword={encoded_keyword}"
        
        logger.info(f"搜索URL: {search_url}")
        
        try:
            await self._page.goto(search_url, wait_until="networkidle", timeout=30000)
        except PlaywrightTimeout:
            logger.warning("页面加载超时，尝试继续...")
        except Exception as e:
            logger.warning(f"页面导航异常: {e}，尝试继续...")
        
        await asyncio.sleep(3)
        
        if is_cancelled():
            logger.info("任务已取消")
            return []
        
        # 注意：不再点击视频/图文标签，直接搜索所有内容
        # 通过访问详情页判断内容类型，自动分流到对应的数据表
        
        await asyncio.sleep(2)
        
        # ========== 第一阶段：在搜索页滚动加载，收集所有链接 ==========
        logger.info("=== 第一阶段：收集搜索结果链接 ===")
        report_progress("collecting", 0, 0, 0, "正在收集搜索结果链接...")
        
        all_links = []
        visited_ids = set()
        scroll_count = 0
        max_scrolls = 50
        no_new_count = 0
        max_no_new = 3
        target_link_count = limit * 10
        
        while scroll_count < max_scrolls:
            if is_cancelled():
                logger.info("任务已取消")
                return []
            
            video_links = await self._extract_video_links_from_search()
            new_links = [l for l in video_links if l['video_id'] not in visited_ids]
            
            if new_links:
                all_links.extend(new_links)
                for l in new_links:
                    visited_ids.add(l['video_id'])
                logger.info(f"本次提取 {len(new_links)} 条新链接，累计 {len(all_links)} 条")
                no_new_count = 0
            else:
                no_new_count += 1
                logger.info(f"本次无新链接，连续无新链接次数: {no_new_count}/{max_no_new}")
                if no_new_count >= max_no_new:
                    logger.info("连续多次无新链接，停止滚动")
                    break
            
            if len(all_links) >= target_link_count:
                logger.info(f"已收集足够链接 ({len(all_links)} >= {target_link_count})，停止滚动")
                break
            
            scroll_count += 1
            logger.info(f"滚动加载更多 [{scroll_count}/{max_scrolls}]...")
            report_progress("collecting", len(all_links), 0, 0, f"正在收集链接... 已收集 {len(all_links)} 条")
            await self._page.evaluate("window.scrollBy(0, 1000)")
            await asyncio.sleep(random.uniform(2.0, 5.0) + (scroll_count % 2))
        
        logger.info(f"第一阶段完成，共收集 {len(all_links)} 条链接")
        
        if not all_links:
            logger.warning("未收集到任何链接，搜索结束")
            return []
        
        if is_cancelled():
            logger.info("任务已取消")
            return []
        
        # ========== 第二阶段：逐个访问详情页，筛选符合条件的内容 ==========
        logger.info("=== 第二阶段：访问详情页筛选内容 ===")
        report_progress("visiting", len(all_links), 0, 0, f"开始访问详情页，共 {len(all_links)} 条链接待筛选")
        
        qualified_videos = []
        detail_visits = 0
        max_detail_visits = limit * 10
        consecutive_failures = 0
        max_consecutive_failures = 3
        
        for link in all_links:
            if is_cancelled():
                logger.info("任务已取消")
                return []
            
            if len(qualified_videos) >= limit:
                break
            
            if detail_visits >= max_detail_visits:
                logger.info(f"已达到最大访问次数 {max_detail_visits}，停止筛选")
                break
            
            from crawler.models import XiaohongshuImageRecord
            session_video = get_platform_session("xiaohongshu")
            session_image = get_platform_session("xiaohongshu_image")
            try:
                existing_video = session_video.query(XiaohongshuVideoRecord).filter_by(
                    video_id=link['video_id']
                ).first()
                existing_image = session_image.query(XiaohongshuImageRecord).filter_by(
                    video_id=link['video_id']
                ).first()
                if existing_video or existing_image:
                    logger.info(f"内容已存在于数据库，跳过: {link['video_id']}")
                    continue
            finally:
                session_video.close()
                session_image.close()
            
            detail_visits += 1
            
            report_progress(
                "visiting", 
                len(all_links), 
                detail_visits, 
                len(qualified_videos),
                f"正在访问详情页 [{detail_visits}/{len(all_links)}]，已找到 {len(qualified_videos)} 个"
            )
            
            try:
                logger.info(f"访问详情页 [{detail_visits}/{len(all_links)}]: {link['video_id']}")
                
                try:
                    video_info = await asyncio.wait_for(
                        self.get_video_detail(link['video_url'], timeout=timeout),
                        timeout=timeout + 30
                    )
                except asyncio.TimeoutError:
                    logger.warning(f"内容处理超时({timeout}s): {link['video_id']}")
                    consecutive_failures += 1
                    if consecutive_failures >= max_consecutive_failures:
                        logger.error(f"连续{consecutive_failures}次访问失败，可能触发反爬机制，中断搜索")
                        raise Exception(f"检测到小红书反爬机制，连续{consecutive_failures}次访问被拒绝。请稍后重试或手动访问几个内容后再试。")
                    continue
                
                if content_type == "video":
                    if not video_info.is_video:
                        logger.info(f"视频页面检测到图文内容，跳过: {link['video_id']}")
                        await asyncio.sleep(1)
                        continue
                else:
                    if video_info.is_video:
                        logger.info(f"图文页面检测到视频内容，跳过: {link['video_id']}")
                        await asyncio.sleep(1)
                        continue
                
                if video_info and video_info.description and "暂时无法浏览" in video_info.description:
                    consecutive_failures += 1
                    logger.warning(f"内容访问受限: {link['video_id']}, 连续失败次数: {consecutive_failures}")
                    if consecutive_failures >= max_consecutive_failures:
                        logger.error(f"连续{consecutive_failures}次触发反爬机制，中断搜索")
                        raise Exception(f"检测到小红书反爬机制，连续{consecutive_failures}次访问被拒绝。建议：\n1. 等待几分钟后重试\n2. 在浏览器中手动浏览几个内容\n3. 降低搜索频率")
                    await asyncio.sleep(3)
                    continue
                
                consecutive_failures = 0
                
                if video_info and video_info.video_id:
                    def parse_likes(value):
                        if isinstance(value, int):
                            return value
                        if not value:
                            return 0
                        value = str(value).strip()
                        try:
                            if '万' in value:
                                num = float(value.replace('万', ''))
                                return int(num * 10000)
                            elif '亿' in value:
                                num = float(value.replace('亿', ''))
                                return int(num * 100000000)
                            else:
                                return int(float(value))
                        except:
                            return 0
                    
                    likes = parse_likes(video_info.likes)
                    if likes >= min_likes:
                        video_info.likes = likes
                        video_info.platform = "xiaohongshu"
                        
                        # 如果需要下载视频
                        if download_videos and video_info.video_stream_url:
                            try:
                                await self._download_and_extract_thumbnail(video_info)
                            except Exception as e:
                                logger.warning(f"视频下载失败: {video_info.video_id}, {e}")
                        
                        await self.save_single_video_to_db(video_info, keyword)
                        qualified_videos.append(video_info)
                        logger.info(f"符合条件并已保存 [{len(qualified_videos)}/{limit}]: 点赞={likes}, 作者={video_info.author_name}")
                        report_progress(
                            "visiting", 
                            len(all_links), 
                            detail_visits, 
                            len(qualified_videos),
                            f"找到符合条件的内容 [{len(qualified_videos)}/{limit}]"
                        )
                    else:
                        logger.info(f"跳过: 点赞数 {likes} < {min_likes}")
                else:
                    logger.warning(f"详情页提取失败: {link['video_id']}")
                
                await asyncio.sleep(random.uniform(6.0, 12.0) + (detail_visits % 3))
                
            except Exception as e:
                if "反爬机制" in str(e):
                    raise
                # 检查是否是取消导致的异常
                if is_cancelled():
                    logger.info("检测到取消信号，停止搜索")
                    return []
                logger.error(f"访问详情页失败: {link['video_id']}, {e}")
                consecutive_failures += 1
                if consecutive_failures >= max_consecutive_failures:
                    raise Exception(f"检测到小红书反爬机制，连续{consecutive_failures}次访问失败。请稍后重试。")
                continue
        
        logger.info(f"搜索完成: 访问 {detail_visits} 个详情页，找到 {len(qualified_videos)} 个符合条件的{content_type}")
        report_progress("completed", len(all_links), detail_visits, len(qualified_videos), f"搜索完成，找到 {len(qualified_videos)} 个符合条件的内容")
        return qualified_videos
    
    async def _extract_video_links_from_search(self) -> List[Dict]:
        """从搜索结果页面提取视频链接（优先从页面数据中获取token）"""
        try:
            result = await self._page.evaluate("""
                () => {
                    const results = [];
                    const debugInfo = [];
                    let stateKeys = [];
                    let searchKeys = [];
                    let noteListInfo = null;
                    let feedsDebug = null;
                    
                    function safeStringify(obj, depth) {
                        if (depth === undefined) depth = 2;
                        if (depth <= 0) return '...';
                        if (obj === null) return 'null';
                        if (obj === undefined) return 'undefined';
                        if (typeof obj !== 'object') return String(obj);
                        if (Array.isArray(obj)) {
                            return '[' + obj.slice(0, 3).map(function(item) { return safeStringify(item, depth - 1); }).join(', ') + (obj.length > 3 ? '...' : '') + ']';
                        }
                        var keys = Object.keys(obj).slice(0, 5);
                        return '{' + keys.map(function(k) { return k + ': ' + safeStringify(obj[k], depth - 1); }).join(', ') + '}';
                    }
                    
                    if (window.__INITIAL_STATE__) {
                        try {
                            var state = window.__INITIAL_STATE__;
                            stateKeys = Object.keys(state);
                            
                            if (state.search) {
                                searchKeys = Object.keys(state.search);
                                
                                var feeds = state.search.feeds;
                                feedsDebug = {
                                    type: typeof feeds,
                                    isArray: Array.isArray(feeds),
                                    length: feeds ? feeds.length : 0,
                                    keys: feeds && typeof feeds === 'object' ? Object.keys(feeds).slice(0, 10) : []
                                };
                                
if (feeds && typeof feeds === 'object') {
                                    // Vue 响应式对象，实际数据在 _rawValue 或 _value 中
                                    var arr = null;
                                    if (feeds._rawValue && Array.isArray(feeds._rawValue)) {
                                        arr = feeds._rawValue;
                                    } else if (feeds._value && Array.isArray(feeds._value)) {
                                        arr = feeds._value;
                                    } else {
                                        // 如果不是 Vue 响应式对象，尝试获取里面的数组
                                        var possibleArrays = Object.keys(feeds).filter(function(k) { return Array.isArray(feeds[k]); });
                                        if (possibleArrays.length > 0) {
                                            feedsDebug.possibleArrayKeys = possibleArrays;
                                            arr = feeds[possibleArrays[0]];
                                        }
                                    }
                                    
                                    if (arr && arr.length > 0) {
                                        var firstItem = arr[0];
                                        noteListInfo = {
                                            path: 'search.feeds._rawValue',
                                            length: arr.length,
                                            firstItemKeys: Object.keys(firstItem || {}),
                                            firstItemSample: safeStringify(firstItem)
                                        };
                                        
                                        for (var i = 0; i < arr.length; i++) {
                                            var item = arr[i];
                                            // 从顶层字段获取
                                            var noteId = item.id;
                                            var xsecToken = item.xsecToken;
                                            
                                            // 如果没有顶层字段，尝试从 noteCard 获取
                                            if (!noteId && item.noteCard) {
                                                noteId = item.noteCard.id || item.noteCard.noteId;
                                            }
                                            
                                            if (noteId && xsecToken) {
                                                // 构造带 token 的 URL
                                                var url = 'https://www.xiaohongshu.com/explore/' + noteId + '?xsec_token=' + encodeURIComponent(xsecToken);
                                                results.push({
                                                    video_id: noteId,
                                                    video_url: url,
                                                    has_token: true
                                                });
                                            }
                                        }
                                    }
                                }
                                
                                if (Array.isArray(feeds) && feeds.length > 0 && results.length === 0) {
                                    var firstItem = feeds[0];
                                    noteListInfo = {
                                        path: 'search.feeds (array)',
                                        length: feeds.length,
                                        firstItemKeys: Object.keys(firstItem || {}),
                                        firstItemSample: safeStringify(firstItem)
                                    };
                                    
                                    for (var i = 0; i < feeds.length; i++) {
                                        var item = feeds[i];
                                        var note = item.note || item;
                                        var noteId = note.noteId || note.id || note.note_id || item.id;
                                        
                                        if (noteId) {
                                            var url = note.url || note.link || note.noteUrl || item.url || item.link || '';
                                            
                                            if (url && url.indexOf('xsec_token') !== -1) {
                                                results.push({
                                                    video_id: noteId,
                                                    video_url: url,
                                                    has_token: true
                                                });
                                            }
                                        }
                                    }
                                }
                            }
                        } catch (e) {
                            debugInfo.push({ type: 'error', msg: '解析 __INITIAL_STATE__ 失败: ' + e.message });
                        }
                    }
                    
                    if (results.length === 0) {
                        var selectors = [
                            'a[href*="/explore/"]',
                            'a[href*="/discovery/item/"]'
                        ];
                        
                        for (var s = 0; s < selectors.length; s++) {
                            var videoLinks = document.querySelectorAll(selectors[s]);
                            for (var l = 0; l < videoLinks.length; l++) {
                                var link = videoLinks[l];
                                var hrefAttr = link.getAttribute('href') || '';
                                var fullHref = link.href || '';
                                
                                if (debugInfo.length < 5) {
                                    debugInfo.push({
                                        type: 'dom',
                                        hrefAttr: hrefAttr,
                                        fullHref: fullHref,
                                        hasToken: fullHref.indexOf('xsec_token') !== -1
                                    });
                                }
                                
                                var videoId = null;
                                var patterns = [
                                    /explore\\/([a-zA-Z0-9]+)/,
                                    /item\\/([a-zA-Z0-9]+)/
                                ];
                                
                                for (var p = 0; p < patterns.length; p++) {
                                    var match = fullHref.match(patterns[p]);
                                    if (match) {
                                        videoId = match[1];
                                        break;
                                    }
                                }
                                
                                if (videoId && fullHref) {
                                    results.push({
                                        video_id: videoId,
                                        video_url: fullHref,
                                        has_token: fullHref.indexOf('xsec_token') !== -1
                                    });
                                }
                            }
                        }
                    }
                    
                    var seen = new Set();
                    var filtered = results.filter(function(item) {
                        if (seen.has(item.video_id)) return false;
                        seen.add(item.video_id);
                        return true;
                    });
                    
                    return {
                        links: filtered,
                        debug: debugInfo,
                        stateKeys: stateKeys,
                        searchKeys: searchKeys,
                        noteListInfo: noteListInfo,
                        feedsDebug: feedsDebug,
                        hasInitialState: !!window.__INITIAL_STATE__
                    };
                }
            """)
            
            links = result.get('links', [])
            debug = result.get('debug', [])
            has_initial_state = result.get('hasInitialState', False)
            state_keys = result.get('stateKeys', [])
            search_keys = result.get('searchKeys', [])
            note_list_info = result.get('noteListInfo')
            feeds_debug = result.get('feedsDebug')
            
            # 打印调试信息
            logger.info(f"=== 链接提取调试信息 ===")
            logger.info(f"hasInitialState: {has_initial_state}")
            logger.info(f"stateKeys: {state_keys}")
            logger.info(f"searchKeys: {search_keys}")
            
            # 打印 feeds 调试信息
            if feeds_debug:
                logger.info(f"feedsDebug: type={feeds_debug.get('type')}, isArray={feeds_debug.get('isArray')}, length={feeds_debug.get('length')}")
                logger.info(f"feedsDebug keys: {feeds_debug.get('keys')}")
                if feeds_debug.get('possibleArrayKeys'):
                    logger.info(f"feedsDebug possibleArrayKeys: {feeds_debug.get('possibleArrayKeys')}")
            
            if note_list_info:
                logger.info(f"noteListInfo: path={note_list_info.get('path')}, length={note_list_info.get('length')}")
                logger.info(f"noteListInfo firstItemKeys: {note_list_info.get('firstItemKeys')}")
                logger.info(f"noteListInfo firstItemSample: {note_list_info.get('firstItemSample', '')[:300]}")
            
            for i, info in enumerate(debug):
                info_type = info.get('type', 'dom')
                if info_type == 'error':
                    logger.warning(f"错误: {info.get('msg', 'N/A')}")
                elif info_type == 'api':
                    logger.info(f"API请求: {info.get('url', 'N/A')[:150]}")
                else:
                    logger.info(f"DOM链接{i+1}: hrefAttr={info.get('hrefAttr', 'N/A')[:80]}")
                    logger.info(f"         fullHref={info.get('fullHref', 'N/A')[:120]}")
                    logger.info(f"         hasToken={info.get('hasToken', False)}")
            
            # 打印提取结果统计
            if links:
                with_token = sum(1 for l in links if l.get('has_token'))
                logger.info(f"从搜索页面提取到 {len(links)} 个视频链接，其中 {with_token} 个带token")
                if links[0].get('video_url'):
                    logger.info(f"第一个链接示例: {links[0].get('video_url', 'N/A')[:150]}")
            else:
                logger.warning("未提取到任何视频链接")
            
            return links or []
        except Exception as e:
            logger.error(f"提取视频链接失败: {e}")
            return []
    
    async def __aenter__(self):
        await self.start()
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close()
        return False