"""
视频号抓包 API 接口
"""
from fastapi import APIRouter, HTTPException, Query, BackgroundTasks
from pydantic import BaseModel
from typing import List, Optional
from loguru import logger
import asyncio

from crawler.shipinhao_sniffer import (
    ShipinhaoSniffer, CapturedVideo, 
    get_sniffer, init_sniffer, MITMPROXY_AVAILABLE
)

router = APIRouter(prefix="/api/shipinhao", tags=["视频号"])


class VideoResponse(BaseModel):
    """视频信息响应"""
    video_url: str
    video_id: str = ""
    title: str = ""
    author: str = ""
    author_id: str = ""
    cover_url: str = ""
    duration: float = 0.0
    captured_at: str


class SnifferStatusResponse(BaseModel):
    """抓包服务状态"""
    running: bool
    port: int
    captured_count: int
    mitmproxy_available: bool
    proxy_config: Optional[dict] = None


class StartSnifferRequest(BaseModel):
    """启动抓包请求"""
    port: int = 8080


@router.get("/status", response_model=SnifferStatusResponse)
async def get_sniffer_status():
    """
    获取抓包服务状态
    
    返回：
    - running: 服务是否运行中
    - port: 代理端口
    - captured_count: 已捕获视频数量
    - proxy_config: 代理配置说明
    """
    sniffer = get_sniffer()
    
    if sniffer:
        return SnifferStatusResponse(
            running=sniffer.is_running(),
            port=sniffer.port,
            captured_count=len(sniffer.get_captured_videos()),
            mitmproxy_available=MITMPROXY_AVAILABLE,
            proxy_config=sniffer.get_proxy_config() if not sniffer.is_running() else None
        )
    
    return SnifferStatusResponse(
        running=False,
        port=8080,
        captured_count=0,
        mitmproxy_available=MITMPROXY_AVAILABLE,
        proxy_config=ShipinhaoSniffer.get_proxy_config() if MITMPROXY_AVAILABLE else None
    )


@router.post("/start")
async def start_sniffer(request: StartSnifferRequest, background_tasks: BackgroundTasks):
    """
    启动抓包服务
    
    启动后，用户需要：
    1. 安装 mitmproxy 证书（访问 http://mitm.it）
    2. 设置系统代理指向 127.0.0.1:端口
    3. 打开 PC 微信播放视频号视频
    """
    if not MITMPROXY_AVAILABLE:
        raise HTTPException(
            status_code=500, 
            detail="mitmproxy 未安装，请运行: pip install mitmproxy"
        )
    
    sniffer = get_sniffer()
    
    if sniffer and sniffer.is_running():
        return {"message": "抓包服务已在运行", "port": sniffer.port}
    
    # 初始化并启动
    sniffer = await init_sniffer(port=request.port)
    
    # 在后台运行
    background_tasks.add_task(sniffer.start)
    
    return {
        "message": "抓包服务启动中",
        "port": request.port,
        "proxy_config": sniffer.get_proxy_config()
    }


@router.post("/stop")
async def stop_sniffer():
    """停止抓包服务"""
    sniffer = get_sniffer()
    
    if not sniffer:
        raise HTTPException(status_code=400, detail="抓包服务未初始化")
    
    if not sniffer.is_running():
        return {"message": "抓包服务未运行"}
    
    await sniffer.stop()
    return {"message": "抓包服务已停止"}


@router.get("/videos", response_model=List[VideoResponse])
async def get_captured_videos(
    limit: int = Query(50, ge=1, le=200, description="返回数量限制"),
    clear: bool = Query(False, description="是否清空已捕获记录")
):
    """
    获取已捕获的视频列表
    
    Args:
        limit: 返回最近 N 个视频
        clear: 获取后是否清空记录
    """
    sniffer = get_sniffer()
    
    if not sniffer:
        raise HTTPException(status_code=400, detail="抓包服务未初始化，请先启动服务")
    
    videos = sniffer.get_captured_videos(limit=limit)
    
    result = [
        VideoResponse(
            video_url=v.video_url,
            video_id=v.video_id,
            title=v.title,
            author=v.author,
            author_id=v.author_id,
            cover_url=v.cover_url,
            duration=v.duration,
            captured_at=v.captured_at.isoformat()
        )
        for v in videos
    ]
    
    if clear and videos:
        sniffer.clear_captured_videos()
        logger.info(f"已清空 {len(videos)} 条捕获记录")
    
    return result


@router.delete("/videos")
async def clear_captured_videos():
    """清空已捕获的视频记录"""
    sniffer = get_sniffer()
    
    if not sniffer:
        raise HTTPException(status_code=400, detail="抓包服务未初始化")
    
    count = len(sniffer.get_captured_videos())
    sniffer.clear_captured_videos()
    
    return {"message": f"已清空 {count} 条记录"}


@router.get("/proxy-config")
async def get_proxy_config():
    """
    获取代理配置说明
    
    返回如何设置系统代理和安装证书的详细说明
    """
    if not MITMPROXY_AVAILABLE:
        raise HTTPException(
            status_code=500,
            detail="mitmproxy 未安装，请运行: pip install mitmproxy"
        )
    
    return ShipinhaoSniffer.get_proxy_config()
