"""爬虫模块"""
from .browser import BrowserManager
from .douyin import DouyinCrawler, VideoInfo
from .downloader import VideoDownloader
from .models import (
    DouyinVideoRecord, 
    XiaohongshuVideoRecord, 
    ShipinhaoVideoRecord,
    PLATFORM_MODELS,
    get_platform_session,
)

__all__ = [
    "BrowserManager", 
    "DouyinCrawler", 
    "VideoDownloader", 
    "VideoInfo",
    "DouyinVideoRecord",
    "XiaohongshuVideoRecord", 
    "ShipinhaoVideoRecord",
    "PLATFORM_MODELS",
    "get_platform_session",
]