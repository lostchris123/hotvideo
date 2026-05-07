"""
视频号抓包模块 - 使用 mitmproxy 捕获视频链接
"""
from __future__ import annotations
import asyncio
import json
import re
import time
from typing import Optional, List, Dict, Callable
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from loguru import logger

try:
    from mitmproxy import http, ctx
    from mitmproxy.tools.dump import DumpMaster
    from mitmproxy.options import Options
    MITMPROXY_AVAILABLE = True
except ImportError:
    MITMPROXY_AVAILABLE = False
    logger.warning("mitmproxy 未安装，视频号抓包功能不可用。请运行: pip install mitmproxy")


@dataclass
class CapturedVideo:
    """捕获的视频信息"""
    video_url: str
    video_id: str = ""
    title: str = ""
    author: str = ""
    author_id: str = ""
    cover_url: str = ""
    duration: float = 0.0
    captured_at: datetime = field(default_factory=datetime.now)
    request_url: str = ""  # 原始请求URL
    
    def to_dict(self) -> dict:
        return {
            "video_url": self.video_url,
            "video_id": self.video_id,
            "title": self.title,
            "author": self.author,
            "author_id": self.author_id,
            "cover_url": self.cover_url,
            "duration": self.duration,
            "captured_at": self.captured_at.isoformat(),
            "request_url": self.request_url,
        }


class WechatVideoAddon:
    """
    mitmproxy 插件 - 捕获视频号请求
    """
    
    # 视频号视频 URL 特征
    VIDEO_URL_PATTERNS = [
        r"finder\.video\.qq\.com",          # 视频号视频CDN
        r"channels.*\.qq\.com",              # 频道相关
        r"aweme\.snssdk\.com",               # 抖音CDN（视频号可能复用）
        r"v[0-9]+\.web\.video\.qq\.com",     # 腾讯视频CDN
        r"cache\.video\.qq\.com",            # 缓存服务器
    ]
    
    # 视频号 API 特征
    API_PATTERNS = [
        r"channels\.weixin\.qq\.com",
        r"finder\.video\.qq\.com/cgi-bin",
    ]
    
    def __init__(self, on_video_captured: Callable[[CapturedVideo], None] = None):
        """
        Args:
            on_video_captured: 视频捕获回调函数
        """
        self.on_video_captured = on_video_captured
        self.captured_videos: List[CapturedVideo] = []
        self._seen_urls = set()  # 避免重复
    
    def response(self, flow: http.HTTPFlow):
        """处理响应"""
        try:
            request_url = flow.request.pretty_url
            response_url = flow.response.text if flow.response else ""
            
            # 检查是否是视频请求
            if self._is_video_request(request_url, flow):
                video = self._extract_video_info(flow)
                if video and video.video_url not in self._seen_urls:
                    self._seen_urls.add(video.video_url)
                    self.captured_videos.append(video)
                    logger.info(f"[视频号] 捕获视频: {video.title or video.video_id}")
                    
                    # 回调通知
                    if self.on_video_captured:
                        self.on_video_captured(video)
            
            # 检查是否是 API 响应（包含视频信息）
            elif self._is_api_response(request_url):
                videos = self._extract_from_api_response(flow)
                for video in videos:
                    if video.video_url not in self._seen_urls:
                        self._seen_urls.add(video.video_url)
                        self.captured_videos.append(video)
                        logger.info(f"[视频号] 从API捕获视频: {video.title or video.video_id}")
                        
                        if self.on_video_captured:
                            self.on_video_captured(video)
                            
        except Exception as e:
            logger.error(f"[视频号] 处理请求失败: {e}")
    
    def _is_video_request(self, url: str, flow: http.HTTPFlow) -> bool:
        """判断是否是视频请求"""
        # 检查 URL 特征
        for pattern in self.VIDEO_URL_PATTERNS:
            if re.search(pattern, url):
                # 排除非视频类型
                content_type = flow.response.headers.get("Content-Type", "")
                if "video" in content_type or "mp4" in url or "m3u8" in url:
                    return True
        
        # 检查 Content-Type
        if flow.response:
            content_type = flow.response.headers.get("Content-Type", "")
            if "video" in content_type.lower():
                # 且来自微信相关域名
                if "qq.com" in url or "weixin" in url:
                    return True
        
        return False
    
    def _is_api_response(self, url: str) -> bool:
        """判断是否是 API 响应"""
        for pattern in self.API_PATTERNS:
            if re.search(pattern, url):
                return True
        return False
    
    def _extract_video_info(self, flow: http.HTTPFlow) -> Optional[CapturedVideo]:
        """从请求中提取视频信息"""
        url = flow.request.pretty_url
        
        # 提取 video_id
        video_id = self._extract_video_id(url)
        
        # 从请求头或 URL 参数提取信息
        referer = flow.request.headers.get("Referer", "")
        title = self._extract_title_from_url(url) or self._extract_title_from_referer(referer)
        
        return CapturedVideo(
            video_url=url,
            video_id=video_id,
            title=title,
            request_url=url,
        )
    
    def _extract_from_api_response(self, flow: http.HTTPFlow) -> List[CapturedVideo]:
        """从 API 响应中提取视频列表"""
        videos = []
        
        try:
            response_text = flow.response.text
            data = json.loads(response_text)
            
            # 尝试解析不同的响应格式
            video_list = self._parse_video_list(data)
            
            for item in video_list:
                video_url = item.get("video_url") or item.get("play_url") or item.get("url")
                if video_url:
                    video = CapturedVideo(
                        video_url=video_url,
                        video_id=item.get("video_id") or item.get("id", ""),
                        title=item.get("title") or item.get("desc", ""),
                        author=item.get("author", ""),
                        author_id=item.get("author_id", ""),
                        cover_url=item.get("cover") or item.get("thumbnail", ""),
                        duration=item.get("duration", 0.0),
                        request_url=flow.request.pretty_url,
                    )
                    videos.append(video)
                    
        except json.JSONDecodeError:
            pass
        except Exception as e:
            logger.debug(f"[视频号] 解析API响应失败: {e}")
        
        return videos
    
    def _parse_video_list(self, data: dict) -> List[dict]:
        """解析视频列表（处理不同的响应格式）"""
        # 格式1: {"data": {"videoList": [...]}}
        if isinstance(data.get("data"), dict):
            for key in ["videoList", "videos", "list", "items"]:
                if key in data["data"] and isinstance(data["data"][key], list):
                    return data["data"][key]
        
        # 格式2: {"videoList": [...]}
        for key in ["videoList", "videos", "list", "items"]:
            if key in data and isinstance(data[key], list):
                return data[key]
        
        # 格式3: 单个视频对象
        if "video_url" in data or "play_url" in data:
            return [data]
        
        return []
    
    def _extract_video_id(self, url: str) -> str:
        """从 URL 提取视频 ID"""
        # 尝试多种模式
        patterns = [
            r"video_id=([a-zA-Z0-9_\-]+)",
            r"/([a-zA-Z0-9_\-]{20,})",
            r"vid=([a-zA-Z0-9_\-]+)",
        ]
        
        for pattern in patterns:
            match = re.search(pattern, url)
            if match:
                return match.group(1)
        
        # 使用 URL hash 作为 ID
        return str(hash(url) % 10**10)
    
    def _extract_title_from_url(self, url: str) -> str:
        """从 URL 参数提取标题"""
        match = re.search(r"title=([^&]+)", url)
        if match:
            from urllib.parse import unquote
            return unquote(match.group(1))
        return ""
    
    def _extract_title_from_referer(self, referer: str) -> str:
        """从 Referer 提取标题"""
        if not referer:
            return ""
        # 从 finder 页面 URL 提取
        match = re.search(r"finder/post/([a-zA-Z0-9]+)", referer)
        if match:
            return f"视频号视频_{match.group(1)}"
        return ""


class ShipinhaoSniffer:
    """
    视频号抓包管理器
    """
    
    def __init__(self, port: int = 8080, web_port: int = 8081):
        """
        Args:
            port: mitmproxy 代理端口
            web_port: mitmproxy web 界面端口
        """
        if not MITMPROXY_AVAILABLE:
            raise ImportError("mitmproxy 未安装，请运行: pip install mitmproxy")
        
        self.port = port
        self.web_port = web_port
        self.addon = WechatVideoAddon(on_video_captured=self._on_video_captured)
        self.master: Optional[DumpMaster] = None
        self._running = False
        self._video_callbacks: List[Callable] = []
    
    def _on_video_captured(self, video: CapturedVideo):
        """视频捕获回调"""
        for callback in self._video_callbacks:
            try:
                callback(video)
            except Exception as e:
                logger.error(f"视频回调执行失败: {e}")
    
    def add_video_callback(self, callback: Callable[[CapturedVideo], None]):
        """添加视频捕获回调"""
        self._video_callbacks.append(callback)
    
    async def start(self):
        """启动抓包服务"""
        if self._running:
            logger.warning("抓包服务已在运行")
            return
        
        logger.info(f"[视频号] 启动抓包服务，代理端口: {self.port}")
        
        # 配置 mitmproxy
        opts = Options()
        opts.listen_port = self.port
        # opts.web_open_browser = False
        # opts.web_port = self.web_port
        
        # 创建 master
        self.master = DumpMaster(opts)
        self.master.addons.add(self.addon)
        
        self._running = True
        
        # 在后台运行
        try:
            await self.master.run()
        except Exception as e:
            logger.error(f"抓包服务运行错误: {e}")
            self._running = False
    
    async def stop(self):
        """停止抓包服务"""
        if self.master:
            self.master.shutdown()
            self._running = False
            logger.info("[视频号] 抓包服务已停止")
    
    def get_captured_videos(self, limit: int = 100) -> List[CapturedVideo]:
        """获取已捕获的视频列表"""
        return self.addon.captured_videos[-limit:]
    
    def clear_captured_videos(self):
        """清空已捕获的视频列表"""
        self.addon.captured_videos.clear()
        self.addon._seen_urls.clear()
        logger.info("[视频号] 已清空捕获记录")
    
    def is_running(self) -> bool:
        """检查服务状态"""
        return self._running
    
    @staticmethod
    def get_proxy_config() -> dict:
        """获取代理配置信息"""
        return {
            "proxy_host": "127.0.0.1",
            "proxy_port": 8080,
            "cert_install_url": "http://mitm.it",
            "instruction": """请按以下步骤设置代理：
1. 安装 mitmproxy 证书：
   - 浏览器访问 http://mitm.it
   - 下载对应系统的证书并安装信任

2. 设置系统代理：
   Windows: 设置 → 网络 → 代理 → 手动设置代理
            地址: 127.0.0.1  端口: 8080
   
   macOS: 系统偏好设置 → 网络 → 高级 → 代理
          HTTP/HTTPS代理: 127.0.0.1:8080
   
3. 打开 PC 微信，播放视频号视频

4. 视频链接会自动捕获并返回
"""
        }


# 全局单例
_sniffer_instance: Optional[ShipinhaoSniffer] = None

def get_sniffer() -> Optional[ShipinhaoSniffer]:
    """获取全局抓包实例"""
    global _sniffer_instance
    return _sniffer_instance

async def init_sniffer(port: int = 8080) -> ShipinhaoSniffer:
    """初始化全局抓包实例"""
    global _sniffer_instance
    if _sniffer_instance is None:
        _sniffer_instance = ShipinhaoSniffer(port=port)
    return _sniffer_instance
