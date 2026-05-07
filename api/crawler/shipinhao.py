"""
视频号爬虫模块
"""
from __future__ import annotations
import asyncio
import re
import json
from typing import Optional, List, Dict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from playwright.async_api import Page, TimeoutError as PlaywrightTimeout, Browser
from loguru import logger

from .browser import BrowserManager
from .models import (
    ShipinhaoVideoRecord, PLATFORM_MODELS, get_platform_session
)
from config import get_settings


@dataclass
class ShipinhaoVideoInfo:
    """视频号视频信息"""
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
    publish_date: Optional[datetime] = None
    
    video_stream_url: str = ""
    audio_url: str = ""
    
    likes: int = 0
    comments: int = 0
    collects: int = 0
    shares: int = 0
    duration: float = 0.0
    platform: str = "shipinhao"
    
    def to_db_record(self, keyword: str = ""):
        """转换为数据库记录"""
        ModelClass = PLATFORM_MODELS.get(self.platform, PLATFORM_MODELS["shipinhao"])
        
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
            publish_date=self.publish_date,
            likes=self.likes,
            comments=self.comments,
            collects=self.collects,
            shares=self.shares,
            keyword=keyword,
        )


class ShipinhaoCrawler:
    """视频号爬虫"""
    
    # 视频号通常在微信内打开，这里使用视频号助手或其他入口
    BASE_URL = "https://channels.weixin.qq.com"
    
    def __init__(self, browser_manager: BrowserManager = None, user_id: int = None):
        self.user_id = user_id
        if browser_manager:
            self.browser_manager = browser_manager
        else:
            self.browser_manager = BrowserManager(platform="shipinhao", user_id=user_id)
        self._browser: Optional[Browser] = None
        self._page: Optional[Page] = None
    
    async def start(self):
        """启动爬虫"""
        if self._browser is None:
            self._browser = await self.browser_manager.start()
        if self._page is None:
            self._page = await self.browser_manager.get_page()
        logger.info("视频号爬虫启动")
    
    async def close(self):
        """关闭爬虫（不关闭浏览器，保持登录状态）"""
        self._page = None
        self._browser = None
        logger.info("视频号爬虫已关闭（浏览器保持运行）")
    
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
        """打开视频号登录页面"""
        await self.ensure_browser()
        # 视频号通常通过微信扫码登录
        login_url = f"{self.BASE_URL}/login"
        await self._page.goto(login_url, wait_until="domcontentloaded")
        logger.info("已打开视频号登录页面")
        return {"url": login_url, "message": "请在新打开的浏览器窗口中使用微信扫码登录"}
    
    async def check_login(self):
        """检查视频号登录状态"""
        await self.ensure_browser()
        try:
            # 访问视频号助手检查登录状态
            await self._page.goto(self.BASE_URL, wait_until="domcontentloaded", timeout=10000)
            await asyncio.sleep(2)
            
            # 检查登录状态
            login_button = await self._page.query_selector('div.login-btn, button.login')
            user_info = await self._page.query_selector('div.user-info, img.user-avatar')
            
            if user_info:
                return {"logged_in": True, "message": "已登录"}
            elif login_button:
                return {"logged_in": False, "message": "未登录"}
            else:
                content = await self._page.content()
                if "登录" in content:
                    return {"logged_in": False, "message": "未登录"}
                return {"logged_in": True, "message": "可能已登录"}
                
        except Exception as e:
            logger.error(f"检查登录状态失败: {e}")
            return {"logged_in": False, "message": f"检查失败: {str(e)}"}
    
    async def search_by_keyword(
        self, 
        keyword: str, 
        limit: int = 30, 
        min_likes: int = 0, 
        timeout: int = 60,
        progress_callback=None,
        cancel_check=None
    ) -> List[ShipinhaoVideoInfo]:
        """根据关键词搜索视频号视频"""
        await self.ensure_browser()
        
        logger.info(f"搜索视频号关键词: {keyword}")
        logger.warning("视频号搜索功能需要进一步实现，目前返回空列表")
        
        if progress_callback:
            progress_callback({
                "stage": "completed",
                "collected_links": 0,
                "visited_pages": 0,
                "qualified_count": 0,
                "message": "视频号搜索功能暂未实现"
            })
        
        # 视频号的搜索通常需要在微信内完成，这里暂时返回空列表
        # 后续可以实现通过视频号助手或其他方式搜索
        return []
    
    async def save_to_db(self, videos: List[ShipinhaoVideoInfo], keyword: str, min_likes: int = 1000):
        """保存到数据库 - 只保存点赞数超过指定数值的视频"""
        from crawler.models import get_platform_session, PLATFORM_MODELS
        from .shipinhao_models import ShipinhaoVideoInfo
        
        # 过滤点赞数大于等于min_likes的视频
        filtered_videos = [v for v in videos if v.likes >= min_likes]
        logger.info(f"保存 视频号 视频：总计 {len(videos)} 个，过滤后 {len(filtered_videos)} 个（点赞数≥{min_likes}）")
        
        session = get_platform_session("shipinhao")
        ModelClass = PLATFORM_MODELS.get("shipinhao")
        
        try:
            for video in filtered_videos:
                if not video.video_id:
                    continue
                
                video.platform = "shipinhao"
                record = video.to_db_record(keyword)
                
                existing = session.query(ModelClass).filter_by(
                    video_id=video.video_id
                ).first()
                
                if existing:
                    for key, value in record.__dict__.items():
                        if key not in ['_sa_instance_state', 'id', 'created_at']:
                            setattr(existing, key, value)
                else:
                    session.add(record)
            
            session.commit()
            logger.info(f"已保存 {len(filtered_videos)} 条记录到视频号数据库（仅包含点赞数≥{min_likes}的视频）")
        except Exception as e:
            session.rollback()
            logger.error(f"保存数据库失败: {e}")
            raise
        finally:
            session.close()
    
    async def __aenter__(self):
        await self.start()
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close()
        return False
