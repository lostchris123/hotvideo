from __future__ import annotations
"""
抖音爆款文案提取平台 - API主入口（含用户系统）
"""
import asyncio
from typing import Optional, List, Dict
import sys
import re
from fastapi import FastAPI, HTTPException, Query, Depends, UploadFile, File, Form, status, Request, Body
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, HTMLResponse
from pydantic import BaseModel, Field
from loguru import logger
from contextlib import asynccontextmanager
import asyncio
import json

from config import get_settings
from crawler.models import init_all_platforms, get_platform_session, PLATFORM_MODELS, Template, DeletedVideoRecord, CreditTransaction
from datetime import datetime
from crawler.douyin import DouyinCrawler
from crawler.xiaohongshu import XiaohongshuCrawler
from crawler.shipinhao import ShipinhaoCrawler

# 配置日志
settings = get_settings()
log_dir = settings.LOGS_DIR
log_dir.mkdir(parents=True, exist_ok=True)

# 移除默认处理器
logger.remove()

# 添加控制台输出
logger.add(
    sys.stdout,
    format="<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | <level>{level: <8}</level> | <cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - <level>{message}</level>",
    level="DEBUG" if settings.DEBUG else "INFO",
)

# 添加文件输出 - 按天轮转
logger.add(
    str(log_dir / "api_{time:YYYY-MM-DD}.log"),
    rotation="00:00",
    retention="30 days",
    format="{time:YYYY-MM-DD HH:mm:ss.SSS} | {level: <8} | {name}:{function}:{line} - {message}",
    level="DEBUG" if settings.DEBUG else "INFO",
    encoding="utf-8",
)

# 添加错误日志单独文件
logger.add(
    str(log_dir / "error_{time:YYYY-MM-DD}.log"),
    rotation="00:00",
    retention="30 days",
    format="{time:YYYY-MM-DD HH:mm:ss.SSS} | {level: <8} | {name}:{function}:{line} - {message}",
    level="ERROR",
    encoding="utf-8",
)

logger.info("日志系统初始化完成")

# 导入用户系统
from api.auth import (
    register_user, authenticate_user, create_access_token, 
    get_current_user, get_current_admin, init_admin_user,
    UserCreate, UserLogin, TokenResponse, UserResponse, get_user_count
)
from api.credits import CreditManager, require_credits, TransactionType
from api.tickets import TicketManager
from api.search_tasks import get_task_manager
from crawler.models import User, UserRole, PlatformLoginStatus, get_user_session


# 用户级爬虫管理器
class UserCrawlerManager:
    """管理所有用户的爬虫实例"""
    def __init__(self):
        self._crawlers: Dict[str, Dict] = {}  # {user_id: {platform: crawler}}
    
    def get_crawler(self, user_id: int, platform: str):
        """获取或创建用户的爬虫实例"""
        key = str(user_id)
        if key not in self._crawlers:
            self._crawlers[key] = {}
        
        if platform not in self._crawlers[key]:
            crawler_classes = {
                "douyin": DouyinCrawler,
                "xiaohongshu": XiaohongshuCrawler,
                "shipinhao": ShipinhaoCrawler,
            }
            CrawlerClass = crawler_classes.get(platform)
            if not CrawlerClass:
                raise HTTPException(status_code=400, detail=f"不支持的平台: {platform}")
            self._crawlers[key][platform] = CrawlerClass(user_id=user_id)
        
        return self._crawlers[key][platform]
    
    def cleanup_user(self, user_id: int):
        """清理指定用户的所有爬虫实例"""
        key = str(user_id)
        if key in self._crawlers:
            del self._crawlers[key]


# 全局爬虫管理器实例
crawler_manager = UserCrawlerManager()

# 浏览器池定时清理
_cleanup_task = None

async def _periodic_browser_cleanup():
    """定时清理浏览器池（每10分钟检查，空闲超1小时则关闭）"""
    from crawler.browser_pool import browser_pool
    while True:
        try:
            await asyncio.sleep(600)  # 每10分钟检查一次
            async with browser_pool._lock:
                await browser_pool._cleanup_idle()
                memory_percent = __import__("psutil").virtual_memory().percent / 100
                if memory_percent > browser_pool.memory_threshold:
                    await browser_pool._cleanup_lru()
                    logger.warning(f"[BrowserPool] 内存过高 {memory_percent:.1%}, 已执行LRU清理")
                else:
                    logger.info(f"[BrowserPool] 定时检查完成, 实例数={len(browser_pool._instances)}, 内存={memory_percent:.1%}")
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"[BrowserPool] 定时清理失败: {e}")

@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理"""
    global _cleanup_task
    
    # 初始化数据库
    init_all_platforms()
    
    # 初始化管理员账号
    init_admin_user()
    
    # 启动定时清理任务
    _cleanup_task = asyncio.create_task(_periodic_browser_cleanup())
    
    logger.info("应用启动完成（浏览器池定时清理已启用）")
    
    yield
    
    # 关闭时取消定时任务
    if _cleanup_task:
        _cleanup_task.cancel()
    logger.info("应用关闭（浏览器保持运行）")

# 设置
settings = get_settings()

# 确保目录存在
(settings.DATA_DIR / "thumbnails").mkdir(parents=True, exist_ok=True)
(settings.DATA_DIR / "images").mkdir(parents=True, exist_ok=True)
(settings.DATA_DIR / "uploads" / "payment_proofs").mkdir(parents=True, exist_ok=True)

app = FastAPI(
    title="抖音爆款文案提取平台",
    description="基于关键词搜索视频，提取文案及缩略图（含用户积分系统）",
    version="1.0.0",
    lifespan=lifespan,
)

# 挂载缩略图静态资源
thumbnails_dir = settings.DATA_DIR / "thumbnails"
app.mount("/thumbnails", StaticFiles(directory=str(thumbnails_dir)), name="thumbnails")

# 挂载图片静态资源（图文多张图片）
images_dir = settings.DATA_DIR / "images"
app.mount("/images", StaticFiles(directory=str(images_dir)), name="images")

# 挂载上传文件静态资源
uploads_dir = settings.DATA_DIR / "uploads"
app.mount("/uploads", StaticFiles(directory=str(uploads_dir)), name="uploads")

# CORS配置
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# 自定义验证错误处理器 - 返回字符串而不是对象
@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    """处理验证错误，返回简单的字符串错误消息"""
    errors = exc.errors()
    if errors:
        # 提取第一个错误的信息
        error = errors[0]
        loc = " -> ".join(str(l) for l in error.get("loc", []))
        msg = error.get("msg", "验证失败")
        detail = f"{loc}: {msg}"
    else:
        detail = "请求参数验证失败"
    
    logger.warning(f"验证错误: {detail}")
    return JSONResponse(
        status_code=422,
        content={"detail": detail}
    )


# ============ 数据模型 ============

class SearchRequest(BaseModel):
    keyword: str
    limit: int = Field(20, le=20, description="搜索结果数量限制，最大值为20")
    min_likes: int = Field(1000, ge=0, description="最小点赞数")
    platform: str = Field("douyin", description="平台名称")
    content_type: str = Field("video", description="内容类型: video/image，仅小红书有效")
    download_videos: bool = Field(True, description="是否在搜索时下载视频并提取缩略图（默认 True）")

class SearchResponse(BaseModel):
    keyword: str
    total: int
    videos: List[dict]
    message: Optional[str] = None

class TranscriptRequest(BaseModel):
    video_url: str
    platform: str = "douyin"
    timeout: Optional[int] = Field(None, ge=10, le=180, description="超时时间（秒）")

class TranscriptResponse(BaseModel):
    video_id: str
    text: str
    duration: float

class BatchTranscriptRequest(BaseModel):
    video_urls: List[str]
    concurrency: int = 3
    platform: str = "douyin"
    timeout: Optional[int] = Field(90, ge=10, le=180, description="单个视频超时时间（秒）")

class SearchByUrlRequest(BaseModel):
    """基于视频链接搜索请求"""
    video_url: str
    platform: str = "douyin"
    content_type: str = Field("video", description="内容类型: video/image，仅小红书有效")
    timeout: Optional[int] = Field(None, ge=10, le=180, description="超时时间（秒）")

class SearchByUrlResponse(BaseModel):
    """基于视频链接搜索响应"""
    video_id: str
    video_url: str
    author_name: str
    author_id: str
    fans_count: str
    liked_count: str
    description: str
    tags: List[str]
    likes: int
    comments: int
    collects: int
    shares: int
    thumbnail_url: str
    message: str


class UpdateVideoRequest(BaseModel):
    """更新视频请求"""
    transcript: Optional[str] = None
    description: Optional[str] = None


class CreateTemplateRequest(BaseModel):
    """创建模板请求"""
    name: str
    content: str
    source_video_id: Optional[str] = None
    source_platform: Optional[str] = "douyin"
    category: Optional[str] = "用户模板"
    tags: Optional[List[str]] = None
    description: Optional[str] = None
    parent_id: Optional[int] = None
    version: Optional[int] = 1
    version_note: Optional[str] = None


class UpdateTemplateRequest(BaseModel):
    """更新模板请求"""
    name: Optional[str] = None
    content: Optional[str] = None
    category: Optional[str] = None
    tags: Optional[List[str]] = None
    description: Optional[str] = None
    version_note: Optional[str] = None


class CreateTicketRequest(BaseModel):
    """创建工单请求"""
    amount: int = Field(..., gt=0, description="申请积分数量")
    payment_method: str = Field(..., description="支付方式(alipay/wechat)")


class ProcessTicketRequest(BaseModel):
    """处理工单请求"""
    approved: bool = Field(..., description="是否通过")
    admin_note: Optional[str] = Field(None, description="管理员备注")


# ============ 工具函数 ============

def get_crawler(platform: str, user_id: int = None):
    """获取指定平台的爬虫（用户级隔离）
    
    Args:
        platform: 平台名称
        user_id: 用户ID（None 表示使用共享爬虫）
    """
    return crawler_manager.get_crawler(user_id or 0, platform)


# ============ 认证相关接口 ============

@app.post("/api/auth/register", response_model=TokenResponse)
async def api_register(request: UserCreate):
    """用户注册"""
    # 已移除用户数量限制，允许无限注册
    
    try:
        user = register_user(request.username, request.email, request.phone, request.password)
        access_token = create_access_token(
            data={"sub": user.username, "user_id": user.id, "role": user.role.value}
        )
        return {
            "access_token": access_token,
            "token_type": "bearer",
            "user": UserResponse.model_validate(user)
        }
    except HTTPException as e:
        raise e
    except Exception as e:
        logger.error(f"注册失败: {e}")
        raise HTTPException(status_code=500, detail="注册失败")


@app.post("/api/auth/login", response_model=TokenResponse)
async def api_login(request: UserLogin):
    """用户登录"""
    user = authenticate_user(request.username, request.password)
    
    access_token = create_access_token(
        data={"sub": user.username, "user_id": user.id, "role": user.role.value}
    )
    return {
        "access_token": access_token,
        "token_type": "bearer",
        "user": UserResponse.model_validate(user)
    }


@app.get("/api/auth/me", response_model=UserResponse)
async def api_get_me(current_user: User = Depends(get_current_user)):
    """获取当前用户信息"""
    return UserResponse.model_validate(current_user)


# ============ 统计接口 ============

@app.get("/api/statistics")
async def api_get_statistics(current_user: User = Depends(get_current_user)):
    """获取统计数据（管理员看所有，用户看自己的）"""
    from crawler.models import get_user_session, CreditTransaction
    from sqlalchemy import func
    
    session = get_user_session()
    try:
        # 基础统计数据
        total_videos = 0
        total_transcripts = 0
        total_templates = 0
        total_deleted = 0
        
        # 遍历所有平台统计视频
        for platform, ModelClass in PLATFORM_MODELS.items():
            plat_session = get_platform_session(platform)
            try:
                # 根据用户角色过滤
                if current_user.role == UserRole.ADMIN:
                    video_count = plat_session.query(ModelClass).count()
                    transcript_count = plat_session.query(ModelClass).filter(ModelClass.transcript != None).count()
                else:
                    video_count = plat_session.query(ModelClass).filter(
                        ModelClass.user_id == current_user.id
                    ).count()
                    transcript_count = plat_session.query(ModelClass).filter(
                        ModelClass.user_id == current_user.id,
                        ModelClass.transcript != None
                    ).count()
                
                total_videos += video_count
                total_transcripts += transcript_count
            finally:
                plat_session.close()
        
        # 统计模板
        if current_user.role == UserRole.ADMIN:
            total_templates = session.query(Template).count()
        else:
            total_templates = session.query(Template).filter(
                Template.user_id == current_user.id
            ).count()
        
        # 统计删除的视频
        if current_user.role == UserRole.ADMIN:
            total_deleted = session.query(DeletedVideoRecord).count()
        else:
            total_deleted = session.query(DeletedVideoRecord).filter(
                DeletedVideoRecord.user_id == current_user.id
            ).count()
        
        # 统计用户数量（仅管理员可见）
        total_users = 0
        if current_user.role == UserRole.ADMIN:
            total_users = session.query(User).count()
        
        return {
            "total_videos": total_videos,
            "total_transcripts": total_transcripts,
            "total_templates": total_templates,
            "total_deleted": total_deleted,
            "total_users": total_users if current_user.role == UserRole.ADMIN else None,
        }
    finally:
        session.close()


# ============ 积分相关接口 ============

@app.get("/api/credits/balance")
async def api_get_credits(current_user: User = Depends(get_current_user)):
    """获取当前用户积分余额"""
    credits = CreditManager.get_user_credits(current_user.id)
    return {
        "credits": credits,
        "is_admin": current_user.role == UserRole.ADMIN
    }


@app.get("/api/credits/history")
async def api_get_credit_history(
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    current_user: User = Depends(get_current_user)
):
    """获取积分交易记录"""
    history = CreditManager.get_transaction_history(current_user.id, limit, offset)
    return {
        "total": len(history),
        "history": history
    }


@app.post("/api/credits/costs")
async def api_get_credit_costs():
    """获取各项操作的积分消耗"""
    return {
        "search": 5,
        "transcript": 10,
        "download": 2,
        "analyze": 20,
        "ai_create": 5  # AI创作功能：内容扩写、缩写、生成标题
    }


# ============ 工单相关接口 ============

@app.post("/api/tickets")
async def api_create_ticket(
    amount: int = Form(..., gt=0, description="申请积分数量"),
    payment_method: str = Form(..., description="支付方式(alipay/wechat)"),
    payment_proof: Optional[UploadFile] = File(None),
    current_user: User = Depends(get_current_user)
):
    """创建充值工单"""
    try:
        ticket = TicketManager.create_ticket(
            user_id=current_user.id,
            amount=amount,
            payment_method=payment_method,
            payment_proof=payment_proof
        )
        return {
            "message": "工单创建成功",
            "ticket_id": ticket.id,
            "status": ticket.status.value
        }
    except HTTPException as e:
        raise e
    except Exception as e:
        logger.error(f"创建工单失败: {e}")
        raise HTTPException(status_code=500, detail="创建工单失败")


@app.get("/api/tickets")
async def api_get_my_tickets(
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    current_user: User = Depends(get_current_user)
):
    """获取我的工单列表"""
    tickets = TicketManager.get_user_tickets(current_user.id, limit, offset)
    return {
        "total": len(tickets),
        "tickets": tickets
    }


# ============ 管理员接口 ============

@app.get("/api/admin/tickets")
async def api_get_all_tickets(
    status: Optional[str] = Query(None, description="状态筛选(pending/approved/rejected)"),
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    current_user: User = Depends(get_current_admin)
):
    """获取所有工单（管理员）"""
    tickets = TicketManager.get_all_tickets(status, limit, offset)
    pending_count = TicketManager.get_pending_count()
    return {
        "total": len(tickets),
        "pending_count": pending_count,
        "tickets": tickets
    }


@app.post("/api/admin/tickets/{ticket_id}/process")
async def api_process_ticket(
    ticket_id: int,
    request: ProcessTicketRequest,
    current_user: User = Depends(get_current_admin)
):
    """处理工单（管理员审批）"""
    try:
        success = TicketManager.process_ticket(
            ticket_id=ticket_id,
            admin_id=current_user.id,
            approved=request.approved,
            admin_note=request.admin_note
        )
        if success:
            return {"message": "工单处理成功"}
        else:
            raise HTTPException(status_code=500, detail="处理失败")
    except HTTPException as e:
        raise e
    except Exception as e:
        logger.error(f"处理工单失败: {e}")
        raise HTTPException(status_code=500, detail="处理工单失败")


@app.get("/api/admin/users/credits")
async def api_get_users_credits(
    current_user: User = Depends(get_current_admin)
):
    """获取所有用户的充值金额和剩余积分（管理员）"""
    from crawler.models import get_user_session
    from sqlalchemy import func
    
    session = get_user_session()
    try:
        users = session.query(User).all()
        
        result = []
        for user in users:
            total_recharged = session.query(func.sum(CreditTransaction.amount)).filter(
                CreditTransaction.user_id == user.id,
                CreditTransaction.type == TransactionType.RECHARGE
            ).scalar() or 0
            
            result.append({
                "id": user.id,
                "username": user.username,
                "email": user.email,
                "phone": user.phone,
                "role": user.role.value,
                "credits": user.credits,
                "total_recharged": total_recharged,
                "is_active": user.is_active,
                "created_at": user.created_at.isoformat() if user.created_at else None
            })
        
        return {
            "total": len(result),
            "users": result
        }
    finally:
        session.close()


@app.get("/api/admin/users/{user_id}/transactions")
async def api_get_user_transactions(
    user_id: int,
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    current_user: User = Depends(get_current_admin)
):
    """获取指定用户的积分明细（管理员）"""
    from crawler.models import get_user_session
    
    session = get_user_session()
    try:
        user = session.query(User).filter(User.id == user_id).first()
        if not user:
            raise HTTPException(status_code=404, detail="用户不存在")
        
        transactions = CreditManager.get_transaction_history(user_id, limit, offset)
        
        return {
            "user_id": user_id,
            "username": user.username,
            "credits": user.credits,
            "total": len(transactions),
            "transactions": transactions
        }
    finally:
        session.close()


# ============ 原有API接口 ============


@app.get("/api/vnc-url")
async def get_vnc_url(
    platform: str = "douyin",
    current_user: User = Depends(get_current_user)
):
    """获取当前用户对应平台的 noVNC 观察地址"""
    settings = get_settings()
    host = settings.HOST_IP
    cdp_port = settings.get_user_cdp_port(current_user.id, platform)
    novnc_port = cdp_port - 9222 + 26080
    vnc_url = f"http://{host}:{novnc_port}/vnc.html?autoconnect=true&resize=scale"
    return {
        "vnc_url": vnc_url,
        "cdp_port": cdp_port,
        "novnc_port": novnc_port,
        "user_id": current_user.id,
        "platform": platform,
    }

@app.get("/api/status")
async def get_status(current_user: User = Depends(get_current_user)):
    """获取服务状态"""
    from pathlib import Path
    settings = get_settings()
    
    platform_status = {}
    for platform in ["douyin", "xiaohongshu", "shipinhao"]:
        profile_dir = settings.get_browser_profile_dir(platform, current_user.id)
        has_session = profile_dir.exists() and any(profile_dir.iterdir()) if profile_dir.exists() else False
        platform_status[platform] = {
            "has_session": has_session,
            "profile_dir": str(profile_dir)
        }
    
    return {
        "status": "running",
        "message": "服务就绪",
        "platforms": ["douyin", "xiaohongshu", "shipinhao"],
        "browser_profiles": platform_status
    }


@app.get("/api/test-browser")
async def test_browser_endpoint(
    platform: str = "douyin",
    force: bool = False,
    current_user: User = Depends(get_current_user)
):
    """
    测试浏览器 - 验证登录后能否正常搜索
    执行实际搜索操作，检查能否找到数据
    """
    from crawler.browser import BrowserManager
    import urllib.parse
    
    browser_mgr = BrowserManager(platform=platform, user_id=current_user.id)
    test_keyword = "美食"
    
    try:
        logger.info(f"[{platform}] 开始测试浏览器，用户={current_user.id}")
        
        # 启动浏览器
        browser = await browser_mgr.start()
        
        # 获取或创建页面 - 优先使用 browser_mgr 的 context
        if browser_mgr._context:
            context = browser_mgr._context
        else:
            context = browser.contexts[0] if browser.contexts else None
            if not context:
                try:
                    context = await browser.new_context()
                except Exception as e:
                    logger.error(f"[{platform}] 创建context失败: {e}")
                    # 从浏览器池移除失效实例
                    await browser_pool.unregister(current_user.id, platform)
                    return {
                        "success": False,
                        "error": str(e),
                        "message": "浏览器实例已失效，请重试"
                    }
        
        pages = context.pages
        page = pages[0] if pages else await context.new_page()
        
        # 构造搜索 URL
        if platform == "douyin":
            search_url = f"https://www.douyin.com/search/{test_keyword}?type=video"
        elif platform == "xiaohongshu":
            search_url = f"https://www.xiaohongshu.com/search_result?keyword={urllib.parse.quote(test_keyword)}"
        else:
            search_url = f"https://www.douyin.com/search/{test_keyword}?type=video"
        
        logger.info(f"[{platform}] 正在打开搜索页面: {search_url}")
        await page.goto(search_url, timeout=30000, wait_until="domcontentloaded")
        await asyncio.sleep(5)  # 等待页面加载
        
        # 检查页面标题
        title = await page.title()
        logger.info(f"[{platform}] 页面标题: {title}")
        
        # 检查是否已登录（标题不应包含"登录"）
        is_logged_in = "登录" not in title and len(title) > 5
        
        if not is_logged_in:
            logger.warning(f"[{platform}] 未检测到登录状态")
            return {
                "success": False,
                "browser_running": True,
                "logged_in": False,
                "search_works": False,
                "test_keyword": test_keyword,
                "page_title": title,
                "message": f"{platform}未登录，请先打开登录页登录"
            }
        
        # 检查是否能找到视频内容
        logger.info(f"[{platform}] 检查搜索结果...")
        
        # 尝试查找视频链接
        try:
            if platform == "douyin":
                video_elements = await page.locator("a[href*=\"/video/\"]").count()
            elif platform == "xiaohongshu":
                video_elements = await page.locator("a[href*=\"/explore/\"]").count()
            else:
                video_elements = 0
            
            logger.info(f"[{platform}] 找到 {video_elements} 个视频元素")
            
            if video_elements > 0:
                return {
                    "success": True,
                    "browser_running": True,
                    "logged_in": True,
                    "search_works": True,
                    "found_videos": video_elements,
                    "test_keyword": test_keyword,
                    "page_title": title,
                    "message": f"{platform}搜索正常，找到 {video_elements} 个结果"
                }
            else:
                return {
                    "success": False,
                    "browser_running": True,
                    "logged_in": True,
                    "search_works": False,
                    "found_videos": 0,
                    "test_keyword": test_keyword,
                    "page_title": title,
                    "message": f"{platform}搜索未找到数据，可能需要二次验证"
                }
        except Exception as e:
            logger.error(f"[{platform}] 检查搜索结果失败: {e}")
            return {
                "success": False,
                "browser_running": True,
                "logged_in": True,
                "search_works": False,
                "test_keyword": test_keyword,
                "page_title": title,
                "message": f"检查搜索结果失败: {str(e)}"
            }
        
    except Exception as e:
        logger.error(f"[{platform}] 浏览器测试失败: {e}", exc_info=True)
        return {
            "success": False,
            "error": str(e),
            "message": "浏览器测试失败"
        }


# ============ License 接口 ============

from api.license import get_license_manager, LicenseManager, require_license, get_license_limits

class LicenseUploadRequest(BaseModel):
    license_key: str
    machine_code: str
    expire_date: str
    max_users: int = 10
    features: List[str] = []
    customer_name: str = ""


@app.get("/api/license/info")
async def get_license_info():
    """获取License信息"""
    manager = get_license_manager()
    info = manager.get_license_info()
    if info:
        return info
    return {
        "is_valid": False,
        "message": "未授权：请导入License",
        "machine_code": LicenseManager.get_machine_code(),
    }


@app.get("/api/license/machine-code")
async def get_machine_code():
    """获取当前机器码"""
    return {"machine_code": LicenseManager.get_machine_code()}


@app.post("/api/license/import")
async def import_license(
    license_data: LicenseUploadRequest,
    current_user: User = Depends(get_current_admin)
):
    """导入License（仅管理员）- License由销售方生成后提供"""
    manager = get_license_manager()
    
    success, message = manager.import_license(license_data.dict())
    
    if success:
        return {"message": message, "license_info": manager.get_license_info()}
    else:
        raise HTTPException(status_code=400, detail=message)


@app.get("/api/license/check")
async def check_license():
    """检查License状态（前端拦截器使用）"""
    manager = get_license_manager()
    valid, message = manager.verify_license()
    
    # 如果License无效，尝试重新加载（可能刚导入了新License）
    if not valid:
        from api.license import refresh_license
        if refresh_license():
            valid, message = manager.verify_license()
    
    return {"is_valid": valid, "message": message}


@app.post("/api/license/refresh")
async def refresh_license_status(current_user: User = Depends(get_current_admin)):
    """刷新License状态（管理员手动刷新）"""
    from api.license import refresh_license
    
    if refresh_license():
        manager = get_license_manager()
        return {
            "message": "License刷新成功",
            "is_valid": manager.is_valid(),
            "license_info": manager.get_license_info()
        }
    else:
        raise HTTPException(status_code=400, detail="License刷新失败，请检查是否已导入有效的License")


# ============ 前端日志接口 ============

class FrontendLogRequest(BaseModel):
    level: str = "info"
    message: str
    url: Optional[str] = None
    line: Optional[int] = None
    col: Optional[int] = None
    stack: Optional[str] = None
    timestamp: Optional[str] = None


@app.post("/api/logs/frontend")
async def log_frontend_error(
    request: FrontendLogRequest,
    current_user: User = Depends(get_current_user)
):
    """接收前端日志"""
    log_message = f"[前端] {request.message}"
    if request.url:
        log_message += f" | URL: {request.url}"
    if request.line:
        log_message += f" | Line: {request.line}"
    if request.stack:
        log_message += f"\nStack: {request.stack}"
    
    if request.level == "error":
        logger.error(log_message)
    elif request.level == "warn":
        logger.warning(log_message)
    else:
        logger.info(log_message)
    
    return {"status": "ok"}


@app.get("/api/login")
@app.get("/api/ensure-browser")
async def ensure_browser_running(
    platform: str = "douyin",
    current_user: User = Depends(get_current_user)
):
    """确保浏览器进程已启动（不导航页面，不影响当前任务）"""
    crawler = get_crawler(platform, current_user.id)
    try:
        await crawler.ensure_browser()
        return {"message": "浏览器已就绪", "status": "ok"}
    except Exception as e:
        logger.error(f"启动浏览器失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/open-login")  # 前端兼容别名
async def open_login_page(platform: str = "douyin",
    force: bool = False, current_user: User = Depends(get_current_user)):
    """打开登录页面"""
    crawler = get_crawler(platform, current_user.id)
    
    # 根据平台获取登录URL
    login_urls = {
        "douyin": "https://www.douyin.com",
        "xiaohongshu": "https://www.xiaohongshu.com",
        "shipinhao": "https://channels.weixin.qq.com",
    }
    login_url = login_urls.get(platform, "https://www.douyin.com")
    
    try:
        await crawler.ensure_browser()
        await crawler._page.goto(login_url, timeout=120000, wait_until="domcontentloaded")
        save_login_status(current_user.id, platform, False, "")
        return {"message": f"已打开{platform}登录页面，请在浏览器中扫码登录"}
    except Exception as e:
        logger.error(f"打开登录页失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/check-login")
async def check_login(platform: str = "douyin",
    force: bool = False, current_user: User = Depends(get_current_user)):
    """检查登录状态 - 访问个人中心页面判断"""
    crawler = get_crawler(platform, current_user.id)
    
    try:
        # 检查是否有 VNC 会话中的浏览器
        import httpx
        settings = get_settings()
        cdp_port = settings.get_user_cdp_port(current_user.id, platform)
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(f"http://localhost:{cdp_port}/json", timeout=2)
                if resp.status_code == 200:
                    logger.info(f"[{platform}] 复用 VNC 会话中的浏览器")
        except:
            pass
        await crawler.ensure_browser()
        
        # 优先访问个人中心页面判断登录状态
        user_pages = {
            "douyin": "https://www.douyin.com/user/self?from_tab_name=main",
            "xiaohongshu": "https://www.xiaohongshu.com/user/me",
            "shipinhao": "https://channels.weixin.qq.com/platform",
        }
        
        base_urls = {
            "douyin": "https://www.douyin.com",
            "xiaohongshu": "https://www.xiaohongshu.com",
            "shipinhao": "https://channels.weixin.qq.com",
        }
        
        user_page_url = user_pages.get(platform)
        base_url = base_urls.get(platform, "https://www.douyin.com")
        
        # 小红书直接在首页检测登录状态，跳过个人中心页面（避免超时）
        if platform == "xiaohongshu":
            logger.info(f"[{platform}] 直接在首页检测登录状态")
            await crawler._page.goto(base_url, wait_until="domcontentloaded", timeout=15000)
            await asyncio.sleep(2)
            
            current_url = crawler._page.url
            logger.info(f"[{platform}] 首页URL: {current_url}")
            
            # 检查是否被重定向到登录页
            if "/login" in current_url or "/passport" in current_url:
                return {
                    "logged_in": False,
                    "message": "未登录，请先访问 /api/login 进行登录"
                }
            
            # 在首页检测小红书登录特征
            login_status = await crawler._page.evaluate("""
                () => {
                    const result = {
                        hasLoginButton: false,
                        hasUserAvatar: false,
                        hasUsername: false,
                        username: ''
                    };
                    
                    // 检查登录按钮
                    const allElements = document.querySelectorAll('button, a, div');
                    for (const el of allElements) {
                        const text = el.textContent?.trim() || '';
                        if (text === '登录' || text === 'Login' || text.includes('扫码登录')) {
                            result.hasLoginButton = true;
                            break;
                        }
                    }
                    
                    // 小红书特定的头像选择器
                    const avatarSelectors = [
                        'img[class*="avatar"]',
                        '[class*="user-avatar"] img',
                        '.side-nav img',
                        'img[src*="xhscdn"]',
                    ];
                    
                    for (const selector of avatarSelectors) {
                        const img = document.querySelector(selector);
                        if (img && img.src && !img.src.includes('default')) {
                            result.hasUserAvatar = true;
                            break;
                        }
                    }
                    
                    // 小红书特定的用户名选择器
                    const nameSelectors = [
                        '[class*="nickname"]',
                        '[class*="user-name"]',
                        '[class*="username"]',
                        '.side-nav [class*="name"]',
                    ];
                    
                    for (const selector of nameSelectors) {
                        const el = document.querySelector(selector);
                        if (el) {
                            const text = el.textContent?.trim();
                            if (text && text.length > 0 && text.length < 30) {
                                result.hasUsername = true;
                                result.username = text;
                                break;
                            }
                        }
                    }
                    
                    return result;
                }
            """)
            
            logger.info(f"[{platform}] 首页登录状态检测: {login_status}")
            
            if not login_status.get('hasLoginButton') and (login_status.get('hasUserAvatar') or login_status.get('hasUsername')):
                save_login_status(current_user.id, platform, True, login_status.get('username', ''))
                return {
                    "logged_in": True,
                    "message": f"已登录，用户名: {login_status.get('username', '未知')}"
                }
            elif login_status.get('hasLoginButton'):
                return {
                    "logged_in": False,
                    "message": "未登录，检测到登录按钮"
                }
            else:
                return {
                    "logged_in": False,
                    "message": "未检测到登录状态，请先登录"
                }
        
        # 其他平台：先尝试访问个人中心页面
        if user_page_url:
            logger.info(f"[{platform}] 访问个人中心页面检查登录状态: {user_page_url}")
            try:
                await crawler._page.goto(user_page_url, wait_until="domcontentloaded", timeout=15000)
                await asyncio.sleep(3)
                
                current_url = crawler._page.url
                logger.info(f"[{platform}] 个人中心页面URL: {current_url}")
                
                # 检查是否被重定向到登录页
                login_patterns = ["/login", "/passport", "/signin"]
                if any(p in current_url for p in login_patterns):
                    logger.info(f"[{platform}] 被重定向到登录页，判定为未登录")
                    return {
                        "logged_in": False,
                        "message": "未登录，请先访问 /api/login 进行登录"
                    }
                
                # 如果在个人中心页面，检测用户信息
                if "/user/" in current_url or "/me" in current_url:
                    # 根据平台使用不同的选择器
                    if platform == "xiaohongshu":
                        nickname_selectors = [
                            '.user-nickname',
                            '[class*="nickname"]',
                            '.user-info .name',
                            'h1[class*="nickname"]',
                            '[class*="user-header"] [class*="name"]',
                            '.user-page .nickname',
                            '.profile-header .name',
                            '[data-v-*] .nickname',
                        ]
                        avatar_selectors = [
                            '.user-avatar img',
                            '.avatar img',
                            '[class*="avatar"] img',
                            'img[class*="user-avatar"]',
                            '.user-header img',
                            '.profile-header img',
                        ]
                    elif platform == "shipinhao":
                        nickname_selectors = [
                            '[class*="nickname"]',
                            '[class*="user-name"]',
                            '.user-info .name',
                        ]
                        avatar_selectors = [
                            'img[class*="avatar"]',
                            '.avatar img',
                        ]
                    else:  # douyin
                        nickname_selectors = [
                            '[class*="nickname"]',
                            '[class*="user-name"]',
                            'h1[class*="name"]',
                            '[class*="profile"] h1',
                            '[class*="user-info"] [class*="name"]',
                        ]
                        avatar_selectors = [
                            'img[class*="avatar"]',
                            '[class*="user-avatar"] img',
                            'img[class*="profile-avatar"]',
                        ]
                    
                    user_info = await crawler._page.evaluate("""
                        () => {
                            const result = {
                                hasUserInfo: false,
                                nickname: '',
                                avatar: '',
                                userId: '',
                                debug: {
                                    foundElements: [],
                                    title: document.title
                                }
                            };
                            
                            // 检查页面标题
                            const title = document.title;
                            if (title && !title.includes('登录') && !title.includes('login') && !title.includes('Login')) {
                                result.hasUserInfo = true;
                            }
                            
                            // 检查昵称
                            const nicknameSelectors = """ + json.dumps(nickname_selectors) + """;
                            for (const selector of nicknameSelectors) {
                                try {
                                    const el = document.querySelector(selector);
                                    if (el) {
                                        const text = el.textContent?.trim();
                                        result.debug.foundElements.push({type: 'nickname', selector, text});
                                        if (text && text.length > 0 && text.length < 50) {
                                            result.nickname = text;
                                            result.hasUserInfo = true;
                                            break;
                                        }
                                    }
                                } catch (e) {}
                            }
                            
                            // 检查头像
                            const avatarSelectors = """ + json.dumps(avatar_selectors) + """;
                            for (const selector of avatarSelectors) {
                                try {
                                    const img = document.querySelector(selector);
                                    if (img && img.src) {
                                        result.avatar = img.src;
                                        result.debug.foundElements.push({type: 'avatar', selector, src: img.src.substring(0, 50)});
                                        if (!img.src.includes('default') && !img.src.includes('blank')) {
                                            result.hasUserInfo = true;
                                        }
                                        break;
                                    }
                                } catch (e) {}
                            }
                            
                            // 检查是否有特定元素表明已登录
                            const loginIndicators = [
                                '.user-menu',
                                '.user-dropdown',
                                '[class*="user-menu"]',
                                '.logout-btn',
                                '[class*="logout"]',
                                'a[href*="/logout"]',
                            ];
                            
                            for (const selector of loginIndicators) {
                                try {
                                    const el = document.querySelector(selector);
                                    if (el) {
                                        result.debug.foundElements.push({type: 'loginIndicator', selector});
                                        result.hasUserInfo = true;
                                        break;
                                    }
                                } catch (e) {}
                            }
                            
                            return result;
                        }
                    """)
                    
                    logger.info(f"[{platform}] 个人中心页面用户信息: {user_info}")
                    
                    if user_info.get('hasUserInfo'):
                        save_login_status(current_user.id, platform, True, user_info.get('nickname', ''))
                        return {
                            "logged_in": True,
                            "message": f"已登录，昵称: {user_info.get('nickname', '未知')}",
                            "user_info": user_info
                        }
            except Exception as e:
                logger.warning(f"[{platform}] 访问个人中心页面失败: {e}")
        
        # 如果个人中心检测失败，回退到首页检测
        logger.info(f"[{platform}] 回退到首页检测登录状态")
        await crawler._page.goto(base_url, wait_until="domcontentloaded", timeout=10000)
        await asyncio.sleep(2)
        
        current_url = crawler._page.url
        logger.info(f"[{platform}] 首页URL: {current_url}")
        
        # 检查是否被重定向到登录页
        if "/login" in current_url or "/passport" in current_url:
            return {
                "logged_in": False,
                "message": "未登录，请先访问 /api/login 进行登录"
            }
        
        # 在首页检测登录特征
        login_status = await crawler._page.evaluate("""
            () => {
                const result = {
                    hasLoginButton: false,
                    hasUserAvatar: false,
                    hasUsername: false,
                    username: ''
                };
                
                // 检查登录按钮
                const allElements = document.querySelectorAll('button, a, div');
                for (const el of allElements) {
                    const text = el.textContent?.trim() || '';
                    if (text === '登录' || text === 'Login' || text.includes('扫码登录')) {
                        result.hasLoginButton = true;
                        break;
                    }
                }
                
                // 检查用户头像
                const avatarSelectors = [
                    'img[class*="avatar"]',
                    '[class*="user-avatar"] img',
                    'img[src*="tiktok"]',
                    'img[src*="douyin"]',
                ];
                
                for (const selector of avatarSelectors) {
                    const img = document.querySelector(selector);
                    if (img && img.src && !img.src.includes('default')) {
                        result.hasUserAvatar = true;
                        break;
                    }
                }
                
                // 检查用户名
                const nameSelectors = [
                    '[class*="nickname"]',
                    '[class*="user-name"]',
                    '[class*="username"]',
                ];
                
                for (const selector of nameSelectors) {
                    const el = document.querySelector(selector);
                    if (el) {
                        const text = el.textContent?.trim();
                        if (text && text.length > 0 && text.length < 30) {
                            result.hasUsername = true;
                            result.username = text;
                            break;
                        }
                    }
                }
                
                return result;
            }
        """)
        
        logger.info(f"[{platform}] 首页登录状态: {login_status}")
        
        # 判断逻辑
        if login_status.get('hasLoginButton'):
            return {
                "logged_in": False,
                "message": "未登录，请先访问 /api/login 进行登录"
            }
        elif login_status.get('hasUserAvatar') or login_status.get('hasUsername'):
            save_login_status(current_user.id, platform, True, login_status.get('username', ''))
            return {
                "logged_in": True,
                "message": f"已登录，用户名: {login_status.get('username', '未知')}"
            }
        else:
            # 无法确定状态，保守判断为未登录
            return {
                "logged_in": False,
                "message": "未检测到登录状态，请先登录"
            }
            
    except Exception as e:
        logger.error(f"检查登录状态失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


def save_login_status(user_id: int, platform: str, logged_in: bool, nickname: str = ""):
    """保存平台登录状态到数据库"""
    try:
        session = get_user_session()
        existing = session.query(PlatformLoginStatus).filter(
            PlatformLoginStatus.user_id == user_id,
            PlatformLoginStatus.platform == platform
        ).first()
        
        if existing:
            existing.is_logged_in = logged_in
            existing.nickname = nickname
            existing.last_checked_at = datetime.now()
        else:
            new_status = PlatformLoginStatus(
                user_id=user_id,
                platform=platform,
                is_logged_in=logged_in,
                nickname=nickname,
                last_checked_at=datetime.now()
            )
            session.add(new_status)
        
        session.commit()
        session.close()
    except Exception as e:
        logger.error(f"保存登录状态失败: {e}")


@app.get("/api/login-status")
async def get_login_status(platform: str = "douyin",
    force: bool = False, current_user: User = Depends(get_current_user)):
    """获取保存的登录状态"""
    try:
        session = get_user_session()
        status = session.query(PlatformLoginStatus).filter(
            PlatformLoginStatus.user_id == current_user.id,
            PlatformLoginStatus.platform == platform
        ).first()
        session.close()
        
        if status:
            return {
                "logged_in": status.is_logged_in,
                "nickname": status.nickname,
                "last_checked_at": status.last_checked_at.isoformat() if status.last_checked_at else None
            }
        return {
            "logged_in": False,
            "nickname": "",
            "last_checked_at": None
        }
    except Exception as e:
        logger.error(f"获取登录状态失败: {e}")
        return {
            "logged_in": False,
            "nickname": "",
            "last_checked_at": None
        }


# ============ 浏览器会话管理接口 ============

from api.session import unified_session_manager, session_monitor

class CreateSessionRequest(BaseModel):
    """创建会话请求"""
    platform: str = Field("douyin", description="平台: douyin/xiaohongshu/shipinhao")
    debug: bool = Field(True, description="调试模式: True=打开本地浏览器窗口, False=返回VNC URL")


@app.post("/api/session/create")
async def create_browser_session(
    request: CreateSessionRequest,
    current_user: User = Depends(get_current_user)
):
    """
    创建浏览器会话
    
    - debug=True: 在服务器上弹出可见浏览器窗口，用户可以直接操作（本地开发/有显示器的服务器）
    - debug=False: 返回VNC URL，用户在浏览器中访问远程桌面（云服务器部署）
    """
    try:
        result = await unified_session_manager.create_session(
            user_id=current_user.id,
            platform=request.platform,
            debug=request.debug
        )
        
        return {
            "success": True,
            **result
        }
    except Exception as e:
        logger.error(f"创建会话失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/session/status")
async def get_session_status(
    current_user: User = Depends(get_current_user)
):
    """获取会话资源使用情况（browser_pool 真实数据）"""
    from crawler.browser_pool import browser_pool
    import psutil, subprocess

    stats = browser_pool.get_stats()

    # 统计系统内 chromium 进程数
    try:
        result = subprocess.run(
            ["pgrep", "-f", "remote-debugging-port"],
            capture_output=True, text=True
        )
        browser_processes = len([p for p in result.stdout.strip().split("\n") if p]) if result.stdout.strip() else 0
    except Exception:
        browser_processes = stats["total_instances"]

    # 内存占用（MB）
    memory = psutil.virtual_memory()
    memory_used_mb = round((memory.total - memory.available) / 1024 / 1024)

    return {
        "success": True,
        "session_count": stats["total_instances"],
        "max_sessions": stats.get("max_instances", 10),
        "browser_processes": browser_processes,
        "memory_mb": memory_used_mb,
        "memory_percent": round(memory.percent, 1),
        "warning": stats["total_instances"] >= stats.get("max_instances", 10),
        "instances": stats.get("instances", [])
    }


@app.delete("/api/session")
async def destroy_browser_session(current_user: User = Depends(get_current_user)):
    """销毁浏览器会话（同时清理 browser_pool 和 session 字典）"""
    from crawler.browser_pool import browser_pool
    import subprocess as _sp

    # 1. 清理 browser_pool 里该用户的所有实例（含 chromium 进程）
    removed = 0
    async with browser_pool._lock:
        to_remove = [k for k in list(browser_pool._instances.keys()) if k[0] == current_user.id]
        for key in to_remove:
            await browser_pool._close_instance(key, "用户清理会话")
            removed += 1

    # 2. 清理 session 字典（可能为空，忽略返回值）
    await unified_session_manager.destroy_session(current_user.id)

    # 3. 兜底：pkill 残余 chromium
    _sp.run(["pkill", "-9", "-f", "chromium"], capture_output=True)

    return {"success": True, "message": f"已清理 {removed} 个浏览器实例及相关会话"}


@app.get("/api/session/list")
async def list_browser_sessions(
    debug: bool = Query(True, description="调试模式"),
    current_user: User = Depends(get_current_admin)
):
    """列出所有浏览器会话（管理员）"""
    sessions = await unified_session_manager.list_sessions(debug=debug)
    return {"sessions": sessions}



@app.get("/api/browser-pool/status")
async def get_browser_pool_status(current_user: User = Depends(get_current_user)):
    """获取浏览器实例池状态"""
    from crawler.browser_pool import browser_pool
    return {
        "success": True,
        **browser_pool.get_stats()
    }


@app.post("/api/browser-pool/cleanup")
async def cleanup_browser_pool(current_user: User = Depends(get_current_user)):
    """手动清理当前用户的浏览器实例"""
    from crawler.browser_pool import browser_pool
    import asyncio
    
    # 只清理当前用户的实例
    user_id = current_user.id
    removed_count = 0
    
    async with browser_pool._lock:
        to_remove = [key for key in browser_pool._instances.keys() if key[0] == user_id]
        for key in to_remove:
            await browser_pool._close_instance(key, "用户手动清理")
            removed_count += 1
    
    return {
        "success": True,
        "message": f"已清理 {removed_count} 个浏览器实例",
        "user_id": user_id
    }


@app.delete("/api/session/all")
async def cleanup_all_sessions(current_user: User = Depends(get_current_user)):
    """清理当前用户的浏览器会话"""
    import subprocess
    
    user_id = current_user.id
    
    # 清理当前用户的 VNC 会话
    await unified_session_manager.destroy_session(user_id)
    
    # 清理当前用户的浏览器进程（通过 CDP 端口识别）
    settings = get_settings()
    for platform in ["douyin", "xiaohongshu", "shipinhao"]:
        cdp_port = settings.get_user_cdp_port(user_id, platform)
        try:
            # 查找并清理使用该 CDP 端口的浏览器进程
            subprocess.run(["pkill", "-9", "-f", f"--remote-debugging-port={cdp_port}"], capture_output=True)
        except:
            pass
    
    return {
        "success": True,
        "message": f"用户 {user_id} 的会话已清理"
    }


@app.delete("/api/session/all/admin")
async def cleanup_all_sessions_admin(current_user: User = Depends(get_current_admin)):
    """管理员：清理所有用户的浏览器会话"""
    import subprocess
    
    # 清理所有 VNC 会话
    await unified_session_manager._vnc_manager.cleanup_all()
    await unified_session_manager._simple_manager.cleanup_all()
    
    # 强制清理所有浏览器进程
    try:
        subprocess.run(["pkill", "-9", "-f", "chrome"], capture_output=True)
        subprocess.run(["pkill", "-9", "-f", "chromium"], capture_output=True)
        subprocess.run(["pkill", "-9", "-f", "Xvnc"], capture_output=True)
        subprocess.run(["pkill", "-9", "-f", "websockify"], capture_output=True)
        subprocess.run(["rm", "-rf", "/tmp/.X*-lock"], capture_output=True)
    except Exception as e:
        logger.warning(f"清理进程失败: {e}")
    
    return {
        "success": True,
        "message": "所有用户的会话已清理"
    }


@app.post("/api/session/open-login")
async def open_login_in_session(
    platform: str = "douyin",
    force: bool = False,
    debug: bool = Query(True, description="调试模式"),
    current_user: User = Depends(get_current_user)
):
    """
    在会话中打开登录页面
    
    - debug=True: 直接在本地浏览器窗口中打开
    - debug=False: 返回VNC URL，弹窗显示10秒
    """
    import httpx
    
    session = await unified_session_manager.get_session(current_user.id, debug=debug)
    if not session:
        raise HTTPException(status_code=400, detail="请先创建会话")
    
    login_urls = {
        "douyin": "https://www.douyin.com",
        "xiaohongshu": "https://www.xiaohongshu.com",
        "shipinhao": "https://channels.weixin.qq.com"
    }
    
    login_url = login_urls.get(platform, "https://www.douyin.com")
    
    if debug:
        cdp_port = session.get("cdp_port")
        if not cdp_port:
            raise HTTPException(status_code=400, detail="会话信息不完整")
        
        try:
            async with httpx.AsyncClient() as client:
                pages_resp = await client.get(f"http://localhost:{cdp_port}/json")
                pages = pages_resp.json()
                
                if pages:
                    page_id = pages[0]["id"]
                    navigate_url = f"http://localhost:{cdp_port}/json/navigate?{page_id}"
                    await client.get(navigate_url, params={"url": login_url})
                else:
                    await client.get(f"http://localhost:{cdp_port}/json/new", params={"url": login_url})
            
            return {
                "success": True,
                "mode": "debug",
                "message": f"已打开{platform}登录页面，请在浏览器窗口中操作"
            }
        except Exception as e:
            logger.error(f"打开登录页失败: {e}")
            raise HTTPException(status_code=500, detail=str(e))
    else:
        vnc_url = session.get("vnc_url")
        if not vnc_url:
            raise HTTPException(status_code=400, detail="VNC会话信息不完整")
        
        return {
            "success": True,
            "mode": "vnc",
            "vnc_url": vnc_url,
            "vnc_password": session.get("vnc_password"),
            "login_url": login_url,
            "message": f"请在VNC远程桌面中打开{platform}登录页面",
            "expires_in": 10
        }


@app.get("/api/session/screenshot")
async def get_session_screenshot(
    debug: bool = Query(True, description="调试模式"),
    current_user: User = Depends(get_current_user)
):
    """获取当前会话的截图"""
    import httpx
    import base64
    
    session = await unified_session_manager.get_session(current_user.id, debug=debug)
    if not session:
        raise HTTPException(status_code=400, detail="请先创建会话")
    
    if debug:
        cdp_port = session.get("cdp_port")
        if not cdp_port:
            raise HTTPException(status_code=400, detail="会话信息不完整")
        
        try:
            async with httpx.AsyncClient() as client:
                pages_resp = await client.get(f"http://localhost:{cdp_port}/json")
                pages = pages_resp.json()
                
                if not pages:
                    raise HTTPException(status_code=400, detail="没有打开的页面")
                
                ws_url = pages[0].get("webSocketDebuggerUrl")
                if not ws_url:
                    raise HTTPException(status_code=400, detail="无法获取WebSocket URL")
                
                return {
                    "success": True,
                    "mode": "debug",
                    "message": "请使用CDP WebSocket获取截图",
                    "ws_url": ws_url,
                    "cdp_port": cdp_port
                }
        except Exception as e:
            logger.error(f"获取截图失败: {e}")
            raise HTTPException(status_code=500, detail=str(e))
    else:
        return {
            "success": True,
            "mode": "vnc",
            "vnc_url": session.get("vnc_url"),
            "message": "VNC模式下请直接查看远程桌面"
        }

# ============ 异步搜索任务接口 ============

class AsyncSearchRequest(BaseModel):
    """异步搜索请求"""
    keyword: str
    limit: int = Field(20, le=20, description="搜索结果数量限制，最大值为20")
    min_likes: int = Field(0, ge=0, description="最小点赞数")
    platform: str = Field("douyin", description="平台名称")
    content_type: str = Field("video", description="内容类型: video/image，仅小红书有效")
    sort_by: str = Field("default", description="排序方式: default(综合排序), newest(最新发布), most_likes(最多点赞)")
    download_videos: bool = Field(True, description="是否在搜索时下载视频并提取缩略图（默认 True）")

@app.post("/api/search/async")
async def create_async_search(
    request: AsyncSearchRequest,
    current_user: User = Depends(get_current_user)
):
    """创建异步搜索任务"""
    require_license()
    
    task_manager = get_task_manager()
    
    if task_manager.has_running_task(current_user.id):
        running_task = task_manager.get_running_task(current_user.id)
        raise HTTPException(
            status_code=400, 
            detail=f"已有搜索任务正在运行，请等待完成后再创建新任务。当前任务: {running_task['keyword']}"
        )
    
    task = task_manager.create_task(
        user_id=current_user.id,
        keyword=request.keyword,
        platform=request.platform,
        content_type=request.content_type,
        limit=request.limit,
        min_likes=request.min_likes
    )
    
    crawler = get_crawler(request.platform, current_user.id)
    
    async def run_search(progress_callback=None, cancel_check=None):
        await crawler.ensure_browser()
        
        if request.platform == "xiaohongshu":
            return await crawler.search_by_keyword(
                request.keyword, 
                request.limit, 
                request.min_likes, 
                timeout=90,
                content_type=request.content_type,
                download_videos=request.download_videos,
                progress_callback=progress_callback,
                cancel_check=cancel_check
            )
        else:
            return await crawler.search_by_keyword(
                request.keyword, 
                request.limit, 
                request.min_likes, 
                timeout=90,
                sort_by=request.sort_by,
                content_type=request.content_type,
                download_videos=request.download_videos,
                progress_callback=progress_callback,
                cancel_check=cancel_check
            )
    
    task_manager.start_task(task.id, run_search)
    
    return {
        "task_id": task.id,
        "keyword": task.keyword,
        "platform": task.platform,
        "content_type": task.content_type,
        "status": task.status,
        "message": "搜索任务已创建，请轮询获取进度"
    }

@app.get("/api/search/tasks")
async def list_search_tasks(
    limit: int = Query(20, ge=1, le=50),
    offset: int = Query(0, ge=0),
    current_user: User = Depends(get_current_user)
):
    """获取用户搜索任务列表"""
    task_manager = get_task_manager()
    tasks = task_manager.get_user_tasks(current_user.id, limit, offset)
    return {
        "total": len(tasks),
        "tasks": tasks
    }

@app.get("/api/search/tasks/{task_id}")
async def get_search_task_status(
    task_id: int,
    current_user: User = Depends(get_current_user)
):
    """获取搜索任务状态"""
    task_manager = get_task_manager()
    task = task_manager.get_task(task_id)
    
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    
    if task["user_id"] != current_user.id :
        raise HTTPException(status_code=403, detail="无权访问此任务")
    
    return task

@app.post("/api/search/tasks/{task_id}/cancel")
async def cancel_search_task(
    task_id: int,
    current_user: User = Depends(get_current_user)
):
    """取消搜索任务"""
    task_manager = get_task_manager()
    task = task_manager.get_task(task_id)
    
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    
    if task["user_id"] != current_user.id :
        raise HTTPException(status_code=403, detail="无权取消此任务")
    
    if task["status"] not in ["pending", "running"]:
        raise HTTPException(status_code=400, detail=f"任务状态为 {task['status']}，无法取消")
    
    success = task_manager.cancel_task(task_id)
    if success:
        return {"message": "任务已取消", "task_id": task_id}
    else:
        raise HTTPException(status_code=500, detail="取消任务失败")

@app.post("/api/search/tasks/reset-stuck")
async def reset_stuck_search_tasks(
    current_user: User = Depends(get_current_user)
):
    """重置所有卡住的搜索任务"""
    task_manager = get_task_manager()
    count = task_manager.reset_stuck_tasks(current_user.id)
    return {
        "message": f"已重置 {count} 个卡住的任务",
        "reset_count": count
    }

@app.post("/api/search", response_model=SearchResponse)
async def search_videos(
    request: SearchRequest,
    current_user: User = Depends(get_current_user)
):
    """搜索视频/图文"""
    require_license()
    
    # 小红书特殊处理：视频和图文共用同一个爬虫，但数据存储在不同的表
    platform = request.platform
    if platform == "xiaohongshu" and request.content_type == "image":
        platform = "xiaohongshu_image"
    
    crawler = get_crawler(request.platform, current_user.id)
    
    try:
        logger.info(f"搜索关键词: {request.keyword}, 平台: {platform}, 类型: {request.content_type}, 下载视频: {request.download_videos}, 用户: {current_user.username}")
        
        await crawler.ensure_browser()
        
        # 对于小红书，传递content_type给爬虫
        if request.platform == "xiaohongshu":
            videos = await crawler.search_by_keyword(
                request.keyword, 
                request.limit, 
                request.min_likes, 
                timeout=90,
                content_type=request.content_type,
                download_videos=request.download_videos
            )
        else:
            videos = await crawler.search_by_keyword(
                request.keyword, 
                request.limit, 
                request.min_likes, 
                timeout=90,
                download_videos=request.download_videos
            )
        
        logger.info(f"搜索完成，找到 {len(videos)} 个符合点赞数>={request.min_likes}的内容")
        
        session = get_platform_session(platform)
        ModelClass = PLATFORM_MODELS.get(platform)
        if ModelClass:
            try:
                for v in videos:
                    record = session.query(ModelClass).filter_by(video_id=v.video_id).first()
                    if record and record.user_id is None:
                        record.user_id = current_user.id
                session.commit()
            finally:
                session.close()
        
        video_responses = [
            {
                "video_id": v.video_id,
                "video_url": v.video_url,
                "author_name": v.author_name,
                "fans_count": v.fans_count,
                "liked_count": v.liked_count,
                "description": v.description,
                "tags": v.tags,
                "likes": v.likes,
                "comments": v.comments,
                "collects": v.collects,
                "shares": v.shares,
                "thumbnail_url": v.thumbnail_url,
            }
            for v in videos
        ]
        
        return SearchResponse(
            keyword=request.keyword,
            total=len(videos),
            videos=video_responses,
            message=f"搜索完成，已找到 {len(videos)} 个符合点赞数>={request.min_likes}的内容"
        )
        
    except Exception as e:
        logger.error(f"搜索失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/search-by-url")
async def search_by_url(
    request: SearchByUrlRequest,
    current_user: User = Depends(get_current_user)
):
    """基于视频/图文链接搜索单个内容"""
    require_license()
    
    # 小红书特殊处理：视频和图文共用同一个爬虫，但数据存储在不同的表
    platform = request.platform
    if platform == "xiaohongshu" and request.content_type == "image":
        platform = "xiaohongshu_image"
    
    crawler = get_crawler(request.platform, current_user.id)
    
    try:
        logger.info(f"基于链接搜索: {request.video_url}, 平台: {platform}, 类型: {request.content_type}")
        
        await crawler.ensure_browser()
        
        # 获取视频详情
        video_info = await crawler.get_video_detail(request.video_url, timeout=request.timeout or 60)
        
        if not video_info.video_id:
            raise HTTPException(status_code=400, detail="无法解析链接，请检查链接是否正确")
        
        # 保存到数据库（跳过点赞数过滤）
        # 爬虫会根据 video_info.is_video 自动选择存储表
        await crawler.save_to_db([video_info], "", platform, 0, skip_likes_filter=True)
        
        # 将内容关联到当前用户（数据隔离）
        session = get_platform_session(platform)
        ModelClass = PLATFORM_MODELS.get(platform)
        if ModelClass:
            try:
                record = session.query(ModelClass).filter_by(video_id=video_info.video_id).first()
                if record and record.user_id is None:
                    record.user_id = current_user.id
                    session.commit()
            finally:
                session.close()
        
        logger.info(f"视频搜索成功: {video_info.video_id}")
        
        return SearchByUrlResponse(
            video_id=video_info.video_id,
            video_url=video_info.video_url,
            author_name=video_info.author_name,
            author_id=video_info.author_id,
            fans_count=video_info.fans_count,
            liked_count=video_info.liked_count,
            description=video_info.description,
            tags=video_info.tags,
            likes=video_info.likes,
            comments=video_info.comments,
            collects=video_info.collects,
            shares=video_info.shares,
            thumbnail_url=video_info.thumbnail_url,
            message="视频搜索成功"
        )
        
    except Exception as e:
        logger.error(f"基于链接搜索失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/search/url")
async def search_by_url_get(
    video_url: str,
    platform: str = "douyin",
    content_type: str = "video",
    timeout: int = None,
    current_user: User = Depends(get_current_user)
):
    """基于视频链接搜索（GET兼容接口）"""
    request = SearchByUrlRequest(
        video_url=video_url,
        platform=platform,
        content_type=content_type,
        timeout=timeout
    )
    return await search_by_url(request, current_user)



@app.post("/api/transcript")
async def transcript_video(
    request: TranscriptRequest,
    current_user: User = Depends(get_current_user)
):
    """提取文案 - 消耗10积分"""
    require_license()
    
    # 检查积分（管理员跳过）
    if current_user.role != UserRole.ADMIN:
        if not CreditManager.check_credits(current_user.id, 10):
            raise HTTPException(
                status_code=403,
                detail="积分不足，无法提取文案。当前需要 10 积分，请前往财务页面充值。"
            )
    
    crawler = get_crawler(request.platform, current_user.id)
    
    try:
        logger.info(f"提取文案: {request.video_url} (超时设置: {request.timeout or 60}s)")
        
        from config import get_settings
        settings = get_settings()
        
        # 先尝试从URL提取video_id，检查本地视频是否存在
        video_id = None
        if "douyin.com" in request.video_url:
            # 抖音URL格式: https://www.douyin.com/video/7620199110754293691
            match = re.search(r'/video/(\d+)', request.video_url)
            if match:
                video_id = match.group(1)
        elif "xiaohongshu" in request.video_url:
            # 小红书URL格式：
            # https://www.xiaohongshu.com/explore/67a6b784000000000102abcd
            # https://www.xiaohongshu.com/discovery/item/67a6b784000000000102abcd
            match = re.search(r'/(explore|discovery/item)/([a-zA-Z0-9]+)', request.video_url)
            if match:
                video_id = match.group(2)
        
        # 检查本地视频是否存在
        video_path = None
        thumbnail_url = ""
        
        if video_id:
            existing_video_path = settings.VIDEOS_DIR / f"{video_id}.mp4"
            if existing_video_path.exists():
                logger.info(f"本地视频已存在，跳过网页流程: {existing_video_path}")
                video_path = existing_video_path
                # 检查缩略图是否已存在
                existing_thumb = settings.THUMBNAIL_DIR / f"{video_id}.jpg"
                if existing_thumb.exists():
                    thumbnail_url = f"/thumbnails/{existing_thumb.name}"
        
        # 如果本地视频不存在，需要访问网页获取视频流
        video_info = None
        if not video_path:
            await crawler.ensure_browser()
            video_info = await crawler.get_video_detail(request.video_url, timeout=request.timeout or 60)
            video_id = video_info.video_id
            
            # 再次检查本地视频（可能在其他地方已下载）
            existing_video_path = settings.VIDEOS_DIR / f"{video_id}.mp4"
            if existing_video_path.exists():
                logger.info(f"视频已存在，跳过下载: {existing_video_path}")
                video_path = existing_video_path
                existing_thumb = settings.THUMBNAIL_DIR / f"{video_id}.jpg"
                if existing_thumb.exists():
                    thumbnail_url = f"/thumbnails/{existing_thumb.name}"
            elif video_info.video_stream_url:
                # 视频不存在，需要下载
                from crawler.downloader import VideoDownloader
                downloader = VideoDownloader()
                
                try:
                    video_path = await downloader.download_video_with_audio(
                        video_url=video_info.video_stream_url,
                        audio_url=video_info.audio_url or "",
                        filename=video_id
                    )
                    logger.info(f"视频下载完成: {video_path}")
                    
                    if video_path:
                        try:
                            thumb_path = downloader.extract_thumbnail(video_path)
                            thumbnail_url = f"/thumbnails/{thumb_path.name}"
                            logger.info(f"缩略图提取完成: {thumbnail_url}")
                        except Exception as e:
                            logger.warning(f"缩略图提取失败: {e}")
                except Exception as e:
                    logger.error(f"视频下载失败: {e}")
        
        # 提取音频和转写文案
        text = ""
        duration = 0.0
        wav_path = None
        
        if video_path and video_path.exists():
            from crawler.downloader import VideoDownloader
            downloader = VideoDownloader()
            
            try:
                audio_path = downloader.extract_audio(video_path)
                wav_path = downloader.convert_to_wav(audio_path)
                
                from asr import get_asr_engine
                asr = get_asr_engine()
                result = asr.transcribe(str(wav_path))
                text = result.get("text", "")
                duration = result.get("segments", [{}])[-1].get("end", 0.0) if result.get("segments") else 0.0
                logger.info(f"文案提取完成: {len(text)} 字")
            except Exception as e:
                logger.error(f"音频处理失败: {e}")
        else:
            text = ""
            duration = 0.0
        
        # 检查是否成功提取文案
        if not text or len(text.strip()) == 0:
            # 检查是否是静音音频文件（无音频视频）
            if wav_path and wav_path.exists():
                import subprocess
                probe_cmd = [
                    'ffprobe', '-v', 'quiet',
                    '-show_entries', 'format=duration',
                    '-print_format', 'json',
                    str(wav_path)
                ]
                try:
                    result = subprocess.run(probe_cmd, capture_output=True, text=True)
                    import json
                    data = json.loads(result.stdout)
                    audio_duration = float(data.get('format', {}).get('duration', 0))
                    
                    # 如果音频时长很短（小于2秒），说明是静音音频
                    if audio_duration < 2:
                        logger.warning("视频无音频内容")
                        text = "[该视频无语音内容]"
                        duration = 0.0
                except:
                    pass
            
            if not text or text == "":
                raise HTTPException(
                    status_code=500, 
                    detail="文案提取失败，无法获取视频语音内容。请确保视频有语音内容。"
                )
        
        # 更新数据库
        session = get_platform_session(request.platform)
        ModelClass = PLATFORM_MODELS.get(request.platform)
        if ModelClass:
            try:
                record = session.query(ModelClass).filter_by(video_id=video_id).first()
                if record:
                    record.transcript = text
                    record.transcript_duration = duration
                    if thumbnail_url:
                        record.thumbnail_url = thumbnail_url
                    session.commit()
                    logger.info(f"数据库更新成功: transcript={len(text)}字, thumbnail={thumbnail_url}")
            finally:
                session.close()
        
        # 操作成功后才扣除积分
        if current_user.role != UserRole.ADMIN:
            CreditManager.deduct_credits(
                current_user.id, 
                10,
                TransactionType.TRANSCRIPT,
                description=f"提取文案: {video_id}",
                related_id=video_id
            )
        
        return {
            "video_id": video_id,
            "text": text,
            "duration": duration,
            "thumbnail_url": thumbnail_url,
        }
        
    except Exception as e:
        logger.error(f"提取文案失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# 批量任务存储（简化版，实际生产应使用Redis或数据库）
batch_tasks = {}

@app.post("/api/transcript/batch")
async def create_batch_transcript(
    request: BatchTranscriptRequest,
    current_user: User = Depends(get_current_user)
):
    """批量提取文案"""
    require_license()
    
    import uuid
    from concurrent.futures import ThreadPoolExecutor
    
    # 检查积分
    total_credits_needed = len(request.video_urls) * 10
    if current_user.role != UserRole.ADMIN:
        if not CreditManager.check_credits(current_user.id, total_credits_needed):
            raise HTTPException(
                status_code=403,
                detail=f"积分不足，需要 {total_credits_needed} 积分，请前往财务页面充值。"
            )
    
    task_id = str(uuid.uuid4())
    batch_tasks[task_id] = {
        "status": "running",
        "total": len(request.video_urls),
        "processed": 0,
        "success_count": 0,
        "failed": 0,
        "results": [],
        "user_id": current_user.id,
    }
    
    crawler = get_crawler(request.platform, current_user.id)
    
    async def process_batch():
        from config import get_settings
        settings = get_settings()
        
        per_video_timeout = request.timeout or 90
        
        for i, video_url in enumerate(request.video_urls):
            try:
                logger.info(f"批量提取 [{i+1}/{len(request.video_urls)}]: {video_url}")
                
                async def process_single_video():
                    # 先尝试从URL提取video_id，检查本地视频是否存在
                    video_id = None
                    if "douyin.com" in video_url:
                        match = re.search(r'/video/(\d+)', video_url)
                        if match:
                            video_id = match.group(1)
                    elif "xiaohongshu" in video_url:
                        # 小红书URL格式：
                        # https://www.xiaohongshu.com/explore/67a6b784000000000102abcd
                        # https://www.xiaohongshu.com/discovery/item/67a6b784000000000102abcd
                        match = re.search(r'/(explore|discovery/item)/([a-zA-Z0-9]+)', video_url)
                        if match:
                            video_id = match.group(2)
                    
                    video_path = None
                    thumbnail_url = ""
                    
                    # 检查本地视频是否存在
                    if video_id:
                        existing_video_path = settings.VIDEOS_DIR / f"{video_id}.mp4"
                        if existing_video_path.exists():
                            logger.info(f"本地视频已存在，跳过网页流程: {existing_video_path}")
                            video_path = existing_video_path
                            existing_thumb = settings.THUMBNAIL_DIR / f"{video_id}.jpg"
                            if existing_thumb.exists():
                                thumbnail_url = f"/thumbnails/{existing_thumb.name}"
                    
                    # 如果本地视频不存在，需要访问网页
                    if not video_path:
                        await crawler.ensure_browser()
                        video_info = await crawler.get_video_detail(video_url, timeout=per_video_timeout)
                        video_id = video_info.video_id
                        
                        # 再次检查本地视频
                        existing_video_path = settings.VIDEOS_DIR / f"{video_id}.mp4"
                        if existing_video_path.exists():
                            logger.info(f"视频已存在，跳过下载: {existing_video_path}")
                            video_path = existing_video_path
                            existing_thumb = settings.THUMBNAIL_DIR / f"{video_id}.jpg"
                            if existing_thumb.exists():
                                thumbnail_url = f"/thumbnails/{existing_thumb.name}"
                        elif video_info.video_stream_url:
                            # 视频不存在，下载
                            from crawler.downloader import VideoDownloader
                            downloader = VideoDownloader()
                            
                            video_path = await downloader.download_video_with_audio(
                                video_url=video_info.video_stream_url,
                                audio_url=video_info.audio_url or "",
                                filename=video_id
                            )
                            logger.info(f"视频下载完成: {video_path}")
                            
                            if video_path:
                                try:
                                    thumb_path = downloader.extract_thumbnail(video_path)
                                    thumbnail_url = f"/thumbnails/{thumb_path.name}"
                                    logger.info(f"缩略图提取完成: {thumbnail_url}")
                                except Exception as e:
                                    logger.warning(f"缩略图提取失败: {e}")
                    
                    # 提取音频和转写文案
                    text = ""
                    duration = 0.0
                    
                    if video_path and video_path.exists():
                        from crawler.downloader import VideoDownloader
                        downloader = VideoDownloader()
                        
                        audio_path = downloader.extract_audio(video_path)
                        wav_path = downloader.convert_to_wav(audio_path)
                        
                        from asr import get_asr_engine
                        asr = get_asr_engine()
                        result = asr.transcribe(str(wav_path))
                        text = result.get("text", "")
                        duration = result.get("segments", [{}])[-1].get("end", 0.0) if result.get("segments") else 0.0
                    else:
                        return {
                            "video_url": video_url,
                            "error": "视频不存在且无法下载",
                            "success": False,
                        }
                    
                    session = get_platform_session(request.platform)
                    ModelClass = PLATFORM_MODELS.get(request.platform)
                    if ModelClass:
                        try:
                            record = session.query(ModelClass).filter_by(video_id=video_id).first()
                            if record:
                                record.transcript = text
                                record.transcript_duration = duration
                                if thumbnail_url:
                                    record.thumbnail_url = thumbnail_url
                                session.commit()
                        finally:
                            session.close()
                    
                    if current_user.role != UserRole.ADMIN:
                        CreditManager.deduct_credits(
                            current_user.id, 
                            10,
                            TransactionType.TRANSCRIPT,
                            description=f"批量提取文案: {video_id}",
                            related_id=video_id
                        )
                    
                    return {
                        "video_id": video_id,
                        "text": text,
                        "duration": duration,
                        "success": True,
                    }
                
                try:
                    result = await asyncio.wait_for(process_single_video(), timeout=per_video_timeout)
                    if result.get("success"):
                        batch_tasks[task_id]["results"].append(result)
                        batch_tasks[task_id]["success_count"] += 1
                    else:
                        batch_tasks[task_id]["failed"] += 1
                        batch_tasks[task_id]["results"].append(result)
                except asyncio.TimeoutError:
                    logger.error(f"批量提取超时({per_video_timeout}s): {video_url}")
                    batch_tasks[task_id]["failed"] += 1
                    batch_tasks[task_id]["results"].append({
                        "video_url": video_url,
                        "error": f"处理超时({per_video_timeout}秒)",
                        "success": False,
                    })
                    
            except Exception as e:
                logger.error(f"批量提取失败: {video_url}, {e}")
                batch_tasks[task_id]["failed"] += 1
                batch_tasks[task_id]["results"].append({
                    "video_url": video_url,
                    "error": str(e),
                    "success": False,
                })
            
            batch_tasks[task_id]["processed"] += 1
            logger.info(f"批量任务进度: {batch_tasks[task_id]['processed']}/{batch_tasks[task_id]['total']}")
        
        batch_tasks[task_id]["status"] = "completed"
        logger.info(f"批量任务完成: 成功 {batch_tasks[task_id]['success_count']}, 失败 {batch_tasks[task_id]['failed']}")
    
    asyncio.create_task(process_batch())
    
    return {"task_id": task_id, "total": len(request.video_urls)}


@app.get("/api/transcript/batch/{task_id}")
async def get_batch_transcript_status(
    task_id: str,
    current_user: User = Depends(get_current_user)
):
    """查询批量任务状态"""
    if task_id not in batch_tasks:
        raise HTTPException(status_code=404, detail="任务不存在")
    
    task = batch_tasks[task_id]
    return {
        "task_id": task_id,
        "status": task["status"],
        "total": task["total"],
        "completed": task["processed"],
        "success_count": task["success_count"],
        "failed": task["failed"],
        "results": task["results"] if task["status"] == "completed" else [],
    }


@app.delete("/api/transcript/batch/{task_id}")
async def delete_batch_task(
    task_id: str,
    current_user: User = Depends(get_current_user)
):
    """删除批量任务"""
    if task_id in batch_tasks:
        del batch_tasks[task_id]
    return {"message": "任务已删除"}


@app.get("/api/videos")
async def list_videos(
    keyword: Optional[str] = Query(None, description="按关键词筛选"),
    platform: Optional[str] = Query(None, description="按平台筛选"),
    content_type: Optional[str] = Query(None, description="内容类型: video/image，仅小红书有效"),
    limit: int = Query(50, ge=1, le=100, description="返回数量"),
    offset: int = Query(0, ge=0, description="偏移量"),
    current_user: User = Depends(get_current_user)
):
    """获取视频/图文列表"""
    all_videos = []
    total_count = 0

    # 根据platform和content_type决定查询哪些表
    if platform:
        if platform == "xiaohongshu" and content_type == "image":
            platforms_to_check = ["xiaohongshu_image"]
        elif platform == "douyin" and content_type == "image":
            platforms_to_check = ["douyin_image"]
        else:
            platforms_to_check = [platform]
    else:
        platforms_to_check = ["douyin", "douyin_image", "xiaohongshu", "xiaohongshu_image", "shipinhao"]

    for plat in platforms_to_check:
        session = get_platform_session(plat)
        ModelClass = PLATFORM_MODELS.get(plat)

        if ModelClass:
            try:
                # 构建基础查询
                base_query = session.query(ModelClass)

                # 数据隔离：管理员可见所有数据，普通用户只能看自己的数据
                if current_user.role != UserRole.ADMIN:
                    base_query = base_query.filter(ModelClass.user_id == current_user.id)

                if keyword:
                    from sqlalchemy import or_
                    base_query = base_query.filter(
                        or_(
                            ModelClass.keyword.contains(keyword),
                            ModelClass.description.contains(keyword)
                        )
                    )

                # 先获取总数
                platform_total = base_query.count()
                total_count += platform_total

                # 再查询分页数据
                records = base_query.order_by(ModelClass.created_at.desc()).offset(offset).limit(limit).all()

                for r in records:
                    video_data = {
                        "video_id": r.video_id,
                        "video_url": r.video_url,
                        "author_name": r.author_name,
                        "author_id": r.author_id,
                        "fans_count": r.fans_count,
                        "liked_count": r.liked_count,
                        "description": r.description,
                        "tags": json.loads(r.tags) if r.tags else [],
                        "likes": r.likes,
                        "comments": r.comments,
                        "collects": r.collects,
                        "shares": r.shares,
                        "thumbnail_url": r.thumbnail_url,
                        "transcript": r.transcript,
                        "duration": r.duration if hasattr(r, 'duration') else 0.0,
                        "platform": plat,
                        "overall_score": r.overall_score if hasattr(r, "overall_score") else None,
                        "created_at": r.created_at.isoformat() if r.created_at else None
                    }
                    # 图文记录：添加图片列表
                    if hasattr(r, 'images') and r.images:
                        video_data["images"] = r.images
                        try:
                            img_count = len(json.loads(r.images))
                            logger.debug(f"返回图片列表: {r.video_id}, 图片数量: {img_count}")
                        except:
                            logger.debug(f"返回图片列表: {r.video_id}, images字段有数据")
                    all_videos.append(video_data)
            finally:
                session.close()

    return {
        "total": total_count,
        "videos": all_videos,
    }

@app.delete("/api/videos/{video_id}")
async def delete_video(
    video_id: str, 
    platform: str = "douyin",
    force: bool = False,
    current_user: User = Depends(get_current_user)
):
    """删除视频（同时删除本地视频、音频、缩略图文件）"""
    from crawler.models import get_user_session, DeletedVideoRecord
    from config import get_settings
    
    settings = get_settings()
    
    session = get_platform_session(platform)
    ModelClass = PLATFORM_MODELS.get(platform)
    
    if not ModelClass:
        raise HTTPException(status_code=400, detail=f"不支持的平台: {platform}")
    
    try:
        record = session.query(ModelClass).filter_by(video_id=video_id).first()
        if record:
            user_session = get_user_session()
            try:
                deleted_record = DeletedVideoRecord(
                    user_id=current_user.id,
                    video_id=record.video_id,
                    platform=platform,
                    author_name=record.author_name,
                    description=record.description,
                    likes=record.likes or 0,
                    comments=record.comments or 0,
                    shares=record.shares or 0,
                    collects=record.collects or 0,
                    had_transcript=bool(record.transcript),
                )
                user_session.add(deleted_record)
                user_session.commit()
            except Exception as e:
                logger.error(f"记录删除操作失败: {e}")
                user_session.rollback()
            finally:
                user_session.close()
            
            # 删除本地文件
            files_deleted = []
            
            # 删除视频文件
            video_path = settings.VIDEOS_DIR / f"{video_id}.mp4"
            if video_path.exists():
                video_path.unlink()
                files_deleted.append(f"视频: {video_path.name}")
                logger.info(f"已删除视频文件: {video_path}")
            
            # 删除音频文件
            audio_path = settings.AUDIO_DIR / f"{video_id}.wav"
            if audio_path.exists():
                audio_path.unlink()
                files_deleted.append(f"音频: {audio_path.name}")
                logger.info(f"已删除音频文件: {audio_path}")
            
            # 删除缩略图
            thumb_path = settings.THUMBNAIL_DIR / f"{video_id}.jpg"
            if thumb_path.exists():
                thumb_path.unlink()
                files_deleted.append(f"缩略图: {thumb_path.name}")
                logger.info(f"已删除缩略图: {thumb_path}")
            
            # 删除图片文件夹（小红书图文）
            images_dir = settings.DATA_DIR / "images" / video_id
            if images_dir.exists() and images_dir.is_dir():
                import shutil
                shutil.rmtree(images_dir)
                files_deleted.append(f"图片文件夹: {images_dir.name}")
                logger.info(f"已删除图片文件夹: {images_dir}")
            
            # 删除截图
            screenshot_path = settings.DATA_DIR / "screenshots" / f"{video_id}.png"
            if screenshot_path.exists():
                screenshot_path.unlink()
                files_deleted.append(f"截图: {screenshot_path.name}")
                logger.info(f"已删除截图: {screenshot_path}")
            
            session.delete(record)
            session.commit()
            
            return {
                "message": "删除成功",
                "files_deleted": files_deleted if files_deleted else None
            }
        else:
            raise HTTPException(status_code=404, detail="视频不存在")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"删除视频失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        session.close()


class BatchDeleteRequest(BaseModel):
    """批量删除请求"""
    video_ids: List[str]
    platform: str = "douyin"


@app.post("/api/videos/batch-delete")
async def batch_delete_videos(
    request: BatchDeleteRequest,
    current_user: User = Depends(get_current_user)
):
    """批量删除视频（同时删除本地文件）"""
    from crawler.models import get_user_session, DeletedVideoRecord
    from config import get_settings
    import shutil
    
    settings = get_settings()
    session = get_platform_session(request.platform)
    ModelClass = PLATFORM_MODELS.get(request.platform)
    
    if not ModelClass:
        raise HTTPException(status_code=400, detail=f"不支持的平台: {request.platform}")
    
    results = {
        "success": [],
        "failed": [],
        "total_files_deleted": 0
    }
    
    try:
        for video_id in request.video_ids:
            try:
                record = session.query(ModelClass).filter_by(video_id=video_id).first()
                if record:
                    # 记录删除操作
                    user_session = get_user_session()
                    try:
                        deleted_record = DeletedVideoRecord(
                            user_id=current_user.id,
                            video_id=record.video_id,
                            platform=request.platform,
                            author_name=record.author_name,
                            description=record.description,
                            likes=record.likes or 0,
                            comments=record.comments or 0,
                            shares=record.shares or 0,
                            collects=record.collects or 0,
                            had_transcript=bool(record.transcript),
                        )
                        user_session.add(deleted_record)
                        user_session.commit()
                    except Exception as e:
                        logger.error(f"记录删除操作失败: {e}")
                        user_session.rollback()
                    finally:
                        user_session.close()
                    
                    # 删除本地文件
                    files_count = 0
                    
                    # 删除视频文件
                    video_path = settings.VIDEOS_DIR / f"{video_id}.mp4"
                    if video_path.exists():
                        video_path.unlink()
                        files_count += 1
                    
                    # 删除音频文件
                    audio_path = settings.AUDIO_DIR / f"{video_id}.wav"
                    if audio_path.exists():
                        audio_path.unlink()
                        files_count += 1
                    
                    # 删除缩略图
                    thumb_path = settings.THUMBNAIL_DIR / f"{video_id}.jpg"
                    if thumb_path.exists():
                        thumb_path.unlink()
                        files_count += 1
                    
                    # 删除图片文件夹（小红书图文）
                    images_dir = settings.DATA_DIR / "images" / video_id
                    if images_dir.exists() and images_dir.is_dir():
                        shutil.rmtree(images_dir)
                        files_count += 1
                    
                    # 删除截图
                    screenshot_path = settings.DATA_DIR / "screenshots" / f"{video_id}.png"
                    if screenshot_path.exists():
                        screenshot_path.unlink()
                        files_count += 1
                    
                    results["total_files_deleted"] += files_count
                    
                    # 删除数据库记录
                    session.delete(record)
                    session.commit()
                    
                    results["success"].append(video_id)
                    logger.info(f"批量删除成功: {video_id}, 删除文件数: {files_count}")
                else:
                    results["failed"].append({"video_id": video_id, "reason": "不存在"})
            except Exception as e:
                results["failed"].append({"video_id": video_id, "reason": str(e)})
                logger.error(f"批量删除失败: {video_id}, {e}")
        
        return {
            "message": f"批量删除完成: 成功 {len(results['success'])}，失败 {len(results['failed'])}",
            "success_count": len(results["success"]),
            "failed_count": len(results["failed"]),
            "total_files_deleted": results["total_files_deleted"],
            "failed_items": results["failed"] if results["failed"] else None
        }
    except Exception as e:
        logger.error(f"批量删除视频失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        session.close()


@app.put("/api/videos/{video_id}")
async def update_video(
    video_id: str, 
    request: UpdateVideoRequest, 
    platform: str = "douyin",
    force: bool = False,
    current_user: User = Depends(get_current_user)
):
    """更新视频信息（编辑文案等）"""
    session = get_platform_session(platform)
    ModelClass = PLATFORM_MODELS.get(platform)
    
    if not ModelClass:
        raise HTTPException(status_code=400, detail=f"不支持的平台: {platform}")
    
    try:
        record = session.query(ModelClass).filter_by(video_id=video_id).first()
        if not record:
            raise HTTPException(status_code=404, detail="视频不存在")
        
        # 更新字段
        if request.transcript is not None:
            record.transcript = request.transcript
        if request.description is not None:
            record.description = request.description
        
        record.updated_at = datetime.now()
        session.commit()
        
        return {
            "message": "更新成功",
            "video_id": video_id,
            "transcript": record.transcript,
            "description": record.description,
        }
    finally:
        session.close()


@app.get("/api/videos/{video_id}")
async def get_video_detail(
    video_id: str, 
    platform: str = "douyin",
    force: bool = False,
    current_user: User = Depends(get_current_user)
):
    """获取单个视频详情"""
    session = get_platform_session(platform)
    ModelClass = PLATFORM_MODELS.get(platform)
    
    if not ModelClass:
        raise HTTPException(status_code=400, detail=f"不支持的平台: {platform}")
    
    try:
        record = session.query(ModelClass).filter_by(video_id=video_id).first()
        if not record:
            raise HTTPException(status_code=404, detail="视频不存在")
        
        return {
            "video_id": record.video_id,
            "video_url": record.video_url,
            "author_name": record.author_name,
            "author_id": record.author_id,
            "fans_count": record.fans_count,
            "liked_count": record.liked_count,
            "description": record.description,
            "tags": json.loads(record.tags) if record.tags else [],
            "likes": record.likes,
            "comments": record.comments,
            "collects": record.collects,
            "shares": record.shares,
            "thumbnail_url": record.thumbnail_url,
            "transcript": record.transcript,
            "platform": platform,
            "created_at": record.created_at.isoformat() if record.created_at else None,
            "updated_at": record.updated_at.isoformat() if record.updated_at else None,
        }
    finally:
        session.close()


# ============ 模板管理接口 ============

@app.post("/api/templates")
async def create_template(
    request: CreateTemplateRequest,
    current_user: User = Depends(get_current_user)
):
    """创建文案模板"""
    session = get_platform_session("douyin")  # 使用默认会话
    
    try:
        template = Template(
            user_id=current_user.id,
            name=request.name,
            content=request.content,
            source_video_id=request.source_video_id,
            source_platform=request.source_platform,
            category=request.category or "用户模板",
            tags=json.dumps(request.tags) if request.tags else None,
            description=request.description,
            parent_id=request.parent_id,
            version=request.version or 1,
            version_note=request.version_note,
        )
        session.add(template)
        session.commit()
        
        return {
            "message": "模板创建成功",
            "template_id": template.id,
            "name": template.name,
        }
    except Exception as e:
        logger.error(f"创建模板失败: {e}")
        raise HTTPException(status_code=500, detail=f"创建模板失败: {str(e)}")
    finally:
        session.close()


@app.get("/api/templates")
async def list_templates(
    keyword: Optional[str] = Query(None, description="搜索关键词(名称/内容/描述)"),
    category: Optional[str] = Query(None, description="按分类筛选"),
    limit: int = Query(50, ge=1, le=100, description="返回数量"),
    offset: int = Query(0, ge=0, description="偏移量"),
    current_user: User = Depends(get_current_user)
):
    """获取模板列表"""
    session = get_platform_session("douyin")
    
    try:
        query = session.query(Template)
        
        if current_user.role != UserRole.ADMIN:
            from sqlalchemy import or_
            query = query.filter(
                or_(
                    Template.user_id == current_user.id,
                    Template.user_id == None
                )
            )
        
        if category:
            query = query.filter(Template.category == category)
        
        if keyword:
            from sqlalchemy import or_
            query = query.filter(
                or_(
                    Template.name.contains(keyword),
                    Template.content.contains(keyword),
                    Template.description.contains(keyword)
                )
            )
        
        query = query.filter(Template.parent_id == None)
        
        total = query.count()
        records = query.order_by(Template.created_at.desc()).offset(offset).limit(limit).all()
        
        templates = []
        for r in records:
            version_count = session.query(Template).filter(
                (Template.id == r.id) | (Template.parent_id == r.id)
            ).count()
            templates.append({
                "id": r.id,
                "name": r.name,
                "content": r.content,
                "source_video_id": r.source_video_id,
                "source_platform": r.source_platform,
                "category": r.category,
                "tags": json.loads(r.tags) if r.tags else [],
                "description": r.description,
                "usage_count": r.usage_count,
                "created_at": r.created_at.isoformat() if r.created_at else None,
                "updated_at": r.updated_at.isoformat() if r.updated_at else None,
                "version": r.version,
                "version_count": version_count,
            })
        
        return {
            "total": total,
            "templates": templates,
        }
    finally:
        session.close()


@app.delete("/api/templates/{template_id}")
async def delete_template(
    template_id: int,
    current_user: User = Depends(get_current_user)
):
    """删除模板"""
    session = get_platform_session("douyin")
    
    try:
        template = session.query(Template).filter_by(id=template_id).first()
        if not template:
            raise HTTPException(status_code=404, detail="模板不存在")
        
        session.delete(template)
        session.commit()
        return {"message": "模板删除成功"}
    finally:
        session.close()


@app.get("/api/templates/{template_id}")
async def get_template(
    template_id: int,
    current_user: User = Depends(get_current_user)
):
    """获取单个模板详情"""
    session = get_platform_session("douyin")
    
    try:
        template = session.query(Template).filter_by(id=template_id).first()
        if not template:
            raise HTTPException(status_code=404, detail="模板不存在")
        
        if current_user.role != UserRole.ADMIN and template.user_id != current_user.id and template.user_id is not None:
            raise HTTPException(status_code=403, detail="无权访问此模板")
        
        return {
            "id": template.id,
            "name": template.name,
            "content": template.content,
            "source_video_id": template.source_video_id,
            "source_platform": template.source_platform,
            "category": template.category,
            "tags": json.loads(template.tags) if template.tags else [],
            "description": template.description,
            "usage_count": template.usage_count,
            "created_at": template.created_at.isoformat() if template.created_at else None,
            "updated_at": template.updated_at.isoformat() if template.updated_at else None,
            "parent_id": template.parent_id,
            "version": template.version,
            "version_note": template.version_note,
        }
    finally:
        session.close()


@app.get("/api/templates/{template_id}/versions")
async def get_template_versions(
    template_id: int,
    current_user: User = Depends(get_current_user)
):
    """获取模板的所有版本"""
    session = get_platform_session("douyin")
    
    try:
        template = session.query(Template).filter_by(id=template_id).first()
        if not template:
            raise HTTPException(status_code=404, detail="模板不存在")
        
        root_id = template.parent_id if template.parent_id else template.id
        
        versions = session.query(Template).filter(
            (Template.id == root_id) | (Template.parent_id == root_id)
        ).order_by(Template.version.asc()).all()
        
        result = []
        for v in versions:
            result.append({
                "id": v.id,
                "name": v.name,
                "content": v.content,
                "category": v.category,
                "tags": json.loads(v.tags) if v.tags else [],
                "description": v.description,
                "version": v.version,
                "version_note": v.version_note,
                "created_at": v.created_at.isoformat() if v.created_at else None,
            })
        
        return {"versions": result}
    finally:
        session.close()

@app.post("/api/templates/{template_id}/versions")
async def create_template_version(
    template_id: int,
    request: UpdateTemplateRequest,
    current_user: User = Depends(get_current_user)
):
    """创建模板新版本"""
    session = get_platform_session("douyin")

    try:
        # 获取原模板
        template = session.query(Template).filter_by(id=template_id).first()
        if not template:
            raise HTTPException(status_code=404, detail="模板不存在")

        if current_user.role != UserRole.ADMIN and template.user_id != current_user.id:
            raise HTTPException(status_code=403, detail="无权编辑此模板")

        # 计算新版本号
        root_id = template.parent_id if template.parent_id else template.id
        max_version = session.query(Template).filter(
            (Template.id == root_id) | (Template.parent_id == root_id)
        ).count()
        new_version = max_version + 1

        # 创建新版本
        new_template = Template(
            name=request.name or template.name,
            content=request.content if request.content is not None else template.content,
            category=request.category or template.category,
            tags=json.dumps(request.tags) if request.tags else template.tags,
            description=request.description if request.description is not None else template.description,
            user_id=current_user.id,
            parent_id=root_id,
            version=new_version,
            version_note=request.version_note if hasattr(request, "version_note") else None,
            created_at=datetime.now(),
            updated_at=datetime.now()
        )

        session.add(new_template)
        session.commit()
        session.refresh(new_template)

        return {
            "message": "新版本创建成功",
            "template_id": new_template.id,
            "version": new_version
        }
    finally:
        session.close()

@app.put("/api/templates/{template_id}")
async def update_template(
    template_id: int,
    request: UpdateTemplateRequest,
    current_user: User = Depends(get_current_user)
):
    """更新模板"""
    session = get_platform_session("douyin")
    
    try:
        template = session.query(Template).filter_by(id=template_id).first()
        if not template:
            raise HTTPException(status_code=404, detail="模板不存在")
        
        if current_user.role != UserRole.ADMIN and template.user_id != current_user.id:
            raise HTTPException(status_code=403, detail="无权编辑此模板")
        
        if request.name is not None:
            template.name = request.name
        if request.content is not None:
            template.content = request.content
        if request.category is not None:
            template.category = request.category
        if request.tags is not None:
            template.tags = json.dumps(request.tags)
        if request.description is not None:
            template.description = request.description
        
        template.updated_at = datetime.now()
        session.commit()
        
        return {
            "message": "模板更新成功",
            "template_id": template.id,
            "name": template.name,
            "content": template.content,
            "category": template.category,
            "tags": json.loads(template.tags) if template.tags else [],
            "description": template.description,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"更新模板失败: {e}")
        raise HTTPException(status_code=500, detail=f"更新模板失败: {str(e)}")
    finally:
        session.close()


# ============ AI创作接口 ============

class AICreateRequest(BaseModel):
    """AI创作请求"""
    content: str = Field(..., description="原始内容")
    style: Optional[str] = Field(None, description="风格偏好")
    custom_prompt: Optional[str] = Field(None, description="自定义提示词（包含{content}占位符）")


def call_zhipu_ai(prompt: str) -> str:
    """调用智谱AI接口"""
    import subprocess
    import json
    
    settings = get_settings()
    
    url = f"{settings.ZHIPU_BASE_URL}/chat/completions"
    
    data = {
        "model": settings.ZHIPU_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "stream": False
    }
    
    try:
        # 使用 curl 调用，避免编码问题
        json_str = json.dumps(data, ensure_ascii=False)
        cmd = [
            "curl", "-s", "-X", "POST",
            "-H", f"Authorization: Bearer {settings.ZHIPU_API_KEY}",
            "-H", "Content-Type: application/json",
            "-d", json_str,
            url
        ]
        
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120, encoding="utf-8")
        
        if result.returncode != 0:
            logger.error(f"智谱AI调用失败: {result.stderr}")
            raise Exception(f"智谱AI调用失败: {result.stderr}")
        
        response = json.loads(result.stdout)
        if "choices" in response:
            return response["choices"][0]["message"]["content"]
        else:
            logger.error(f"智谱AI响应异常: {response}")
            raise Exception(f"智谱AI响应异常: {response.get('error', {}).get('message', '未知错误')}")
            
    except subprocess.TimeoutExpired:
        logger.error("智谱AI调用超时")
        raise Exception("智谱AI调用超时")
    except Exception as e:
        logger.error(f"智谱AI调用失败: {e}")
        raise Exception(f"智谱AI调用失败: {str(e)}")


@app.post("/api/ai/expand")
async def ai_expand_content(
    request: AICreateRequest,
    current_user: User = Depends(get_current_user)
):
    """AI内容扩写润色 - 消耗5积分"""
    if current_user.role != UserRole.ADMIN:
        if not CreditManager.check_credits(current_user.id, 5):
            raise HTTPException(
                status_code=403,
                detail="积分不足，无法使用AI扩写。当前需要 5 积分，请前往财务页面充值。"
            )
    
    default_prompt = """你是一个专业的短视频文案创作专家。请对以下文案进行扩写和润色：

原始文案：
{content}

要求：
1. 保持原文的核心意思和风格
2. 适当增加细节描述，让内容更丰富
3. 优化语言表达，使其更流畅、更有感染力
4. 增加适当的修辞手法（比喻、排比等）
5. 保持短视频文案的节奏感，适合口播
6. 字数控制在原文的1.5-2倍

请直接输出扩写润色后的文案，不要添加任何解释或说明："""

    try:
        if request.custom_prompt:
            prompt = request.custom_prompt.replace("{content}", request.content)
        else:
            prompt = default_prompt.replace("{content}", request.content)

        result = call_zhipu_ai(prompt)
        
        if current_user.role != UserRole.ADMIN:
            CreditManager.deduct_credits(
                current_user.id,
                5,
                TransactionType.AI_CREATE,
                description="AI内容扩写润色"
            )
        
        return {"result": result}
        
    except Exception as e:
        logger.error(f"AI扩写失败: {e}")
        raise HTTPException(status_code=500, detail=f"AI扩写失败: {str(e)}")


@app.post("/api/ai/shrink")
async def ai_shrink_content(
    request: AICreateRequest,
    current_user: User = Depends(get_current_user)
):
    """AI内容缩写润色 - 消耗5积分"""
    if current_user.role != UserRole.ADMIN:
        if not CreditManager.check_credits(current_user.id, 5):
            raise HTTPException(
                status_code=403,
                detail="积分不足，无法使用AI缩写。当前需要 5 积分，请前往财务页面充值。"
            )
    
    default_prompt = """你是一个专业的短视频文案创作专家。请对以下文案进行缩写和润色：

原始文案：
{content}

要求：
1. 保留原文的核心信息和关键卖点
2. 删除冗余的描述和废话
3. 使语言更加精炼、有力
4. 保持短视频文案的节奏感
5. 字数控制在原文的50%-70%
6. 确保缩写后意思完整，逻辑通顺

请直接输出缩写润色后的文案，不要添加任何解释或说明："""

    try:
        if request.custom_prompt:
            prompt = request.custom_prompt.replace("{content}", request.content)
        else:
            prompt = default_prompt.replace("{content}", request.content)

        result = call_zhipu_ai(prompt)
        
        if current_user.role != UserRole.ADMIN:
            CreditManager.deduct_credits(
                current_user.id,
                5,
                TransactionType.AI_CREATE,
                description="AI内容缩写润色"
            )
        
        return {"result": result}
        
    except Exception as e:
        logger.error(f"AI缩写失败: {e}")
        raise HTTPException(status_code=500, detail=f"AI缩写失败: {str(e)}")


@app.post("/api/ai/titles")
async def ai_generate_titles(
    request: AICreateRequest,
    current_user: User = Depends(get_current_user)
):
    """AI生成爆款标题 - 消耗5积分"""
    if current_user.role != UserRole.ADMIN:
        if not CreditManager.check_credits(current_user.id, 5):
            raise HTTPException(
                status_code=403,
                detail="积分不足，无法生成爆款标题。当前需要 5 积分，请前往财务页面充值。"
            )
    
    default_prompt = """你是一个专业的短视频文案创作专家。请根据以下文案内容，生成5个爆款标题：

文案内容：
{content}

要求：
1. 标题要有吸引力，能激发用户点击欲望
2. 可以使用以下技巧：
   - 数字+结果（如：3天瘦5斤的秘密）
   - 悬念式（如：为什么99%的人都做错了？）
   - 对比反转（如：你以为的正确做法，其实是错的！）
   - 痛点直击（如：别再为XX发愁了）
   - 好奇心（如：这个方法太神奇了）
3. 标题长度控制在15-30字
4. 每个标题一行，共5个标题
5. 不要添加序号和额外说明

请直接输出5个标题，每个标题一行："""

    try:
        if request.custom_prompt:
            prompt = request.custom_prompt.replace("{content}", request.content)
        else:
            prompt = default_prompt.replace("{content}", request.content)

        result = call_zhipu_ai(prompt)
        
        if current_user.role != UserRole.ADMIN:
            CreditManager.deduct_credits(
                current_user.id,
                5,
                TransactionType.AI_CREATE,
                description="AI生成爆款标题"
            )
        
        return {"result": result}
        
    except Exception as e:
        logger.error(f"AI生成标题失败: {e}")
        raise HTTPException(status_code=500, detail=f"AI生成标题失败: {str(e)}")


@app.post("/api/ai/optimize")
async def ai_optimize_content(
    request: AICreateRequest,
    current_user: User = Depends(get_current_user)
):
    """AI内容优化润色（字数基本不变）- 消耗5积分"""
    if current_user.role != UserRole.ADMIN:
        if not CreditManager.check_credits(current_user.id, 5):
            raise HTTPException(
                status_code=403,
                detail="积分不足，无法使用AI优化。当前需要 5 积分，请前往财务页面充值。"
            )
    
    default_prompt = """你是一个专业的短视频文案创作专家。请对以下文案进行优化润色：

原始文案：
{content}

要求：
1. 保持原文的核心意思和关键信息完全不变
2. 优化语言表达，使其更流畅、更有感染力
3. 增强文案的节奏感和韵律感，适合口播
4. 适当使用修辞手法（比喻、排比、押韵等）
5. 字数严格控制在原文的±10%以内，基本保持不变
6. 保持原文的风格和调性
7. 删除冗余表达，让内容更精炼

请直接输出优化后的文案，不要添加任何解释或说明："""

    try:
        if request.custom_prompt:
            prompt = request.custom_prompt.replace("{content}", request.content)
        else:
            prompt = default_prompt.replace("{content}", request.content)

        result = call_zhipu_ai(prompt)
        
        if current_user.role != UserRole.ADMIN:
            CreditManager.deduct_credits(
                current_user.id,
                5,
                TransactionType.AI_CREATE,
                description="AI内容优化润色"
            )
        
        return {"result": result}
        
    except Exception as e:
        logger.error(f"AI优化失败: {e}")
        raise HTTPException(status_code=500, detail=f"AI优化失败: {str(e)}")


@app.post("/api/ai/tags")
async def ai_extract_tags(
    request: AICreateRequest,
    current_user: User = Depends(get_current_user)
):
    """AI提取文案标签 - 消耗5积分"""
    if current_user.role != UserRole.ADMIN:
        if not CreditManager.check_credits(current_user.id, 5):
            raise HTTPException(
                status_code=403,
                detail="积分不足，无法提取标签。当前需要 5 积分，请前往财务页面充值。"
            )
    
    default_prompt = """你是一个专业的短视频文案分析专家。请分析以下文案，提取3-5个标签：

文案内容：
{content}

要求：
1. 标签应该涵盖：主题、风格、场景、情感、类型等维度
2. 每个标签2-4个字，简洁准确
3. 标签要有代表性，能快速识别文案特点
4. 只输出标签，用逗号分隔，不要添加序号或其他说明
5. 示例格式：情感共鸣,故事叙述,正能量,生活技巧

请直接输出标签，用逗号分隔："""

    try:
        if request.custom_prompt:
            prompt = request.custom_prompt.replace("{content}", request.content)
        else:
            prompt = default_prompt.replace("{content}", request.content)

        result = call_zhipu_ai(prompt)
        
        tags = [tag.strip() for tag in result.replace('，', ',').replace('、', ',').split(',') if tag.strip()]
        tags = tags[:5]
        
        if current_user.role != UserRole.ADMIN:
            CreditManager.deduct_credits(
                current_user.id,
                5,
                TransactionType.AI_CREATE,
                description="AI提取文案标签"
            )
        
        return {"tags": tags}
        
    except Exception as e:
        logger.error(f"AI提取标签失败: {e}")
        raise HTTPException(status_code=500, detail=f"AI提取标签失败: {str(e)}")


# ============ 下载接口 ============

@app.get("/api/videos/{video_id}/download/thumbnail")
async def download_thumbnail(
    video_id: str, 
    platform: str = "douyin",
    force: bool = False,
    current_user: User = Depends(get_current_user)
):
    """下载视频/图文缩略图 - 消耗2积分"""
    # 检查积分（管理员跳过）
    if current_user.role != UserRole.ADMIN:
        if not CreditManager.check_credits(current_user.id, 2):
            raise HTTPException(
                status_code=403,
                detail="积分不足，无法下载。当前需要 2 积分，请前往财务页面充值。"
            )
    
    session = get_platform_session(platform)
    ModelClass = PLATFORM_MODELS.get(platform)
    
    if not ModelClass:
        raise HTTPException(status_code=400, detail=f"不支持的平台: {platform}")
    
    try:
        record = session.query(ModelClass).filter_by(video_id=video_id).first()
        if not record:
            raise HTTPException(status_code=404, detail="内容不存在")
        
        if not record.thumbnail_url:
            raise HTTPException(status_code=404, detail="缩略图不存在")
        
        from fastapi.responses import FileResponse
        import os
        import httpx
        from pathlib import Path
        
        # 构建缩略图路径
        settings = get_settings()
        thumbnail_path = settings.DATA_DIR / "thumbnails" / f"{video_id}.jpg"
        thumbnail_path.parent.mkdir(parents=True, exist_ok=True)
        
        # 如果本地文件不存在，但数据库中有URL，则下载
        if not os.path.exists(thumbnail_path):
            thumbnail_url = record.thumbnail_url
            
            # 检查是否是外部URL（以http开头）
            if thumbnail_url.startswith('http'):
                try:
                    # 从外部URL下载缩略图
                    async with httpx.AsyncClient() as client:
                        response = await client.get(thumbnail_url, timeout=30)
                        response.raise_for_status()
                        
                        # 保存到本地
                        with open(thumbnail_path, 'wb') as f:
                            f.write(response.content)
                        
                        logger.info(f"缩略图下载完成: {video_id} 从 {thumbnail_url[:50]}...")
                except Exception as e:
                    logger.error(f"下载缩略图失败: {e}")
                    raise HTTPException(status_code=500, detail=f"下载缩略图失败: {str(e)}")
            else:
                # 尝试从本地相对路径读取
                    local_path = settings.DATA_DIR / thumbnail_url.lstrip("/")
                    if local_path.exists():
                        import shutil
                        shutil.copy(local_path, thumbnail_path)
                        logger.info(f"缩略图从本地复制: {video_id}")
                    else:
                        raise HTTPException(status_code=404, detail=f"缩略图文件不存在: {thumbnail_url}")
        
        # 扣除积分
        if current_user.role != UserRole.ADMIN:
            CreditManager.deduct_credits(
                current_user.id, 
                2,
                TransactionType.DOWNLOAD,
                description=f"下载缩略图: {video_id}",
                related_id=video_id
            )
        
        return FileResponse(
            path=str(thumbnail_path),
            filename=f"{video_id}_thumbnail.jpg",
            media_type="image/jpeg"
        )
    finally:
        session.close()


@app.get("/api/videos/{video_id}/thumbnail")
async def get_thumbnail(
    video_id: str, 
    platform: str = "douyin",
    force: bool = False,
    current_user: User = Depends(get_current_user)
):
    """获取缩略图图片（用于显示，不下载，不消耗积分）"""
    from fastapi.responses import FileResponse
    import os
    
    # 构建缩略图路径
    settings = get_settings()
    thumbnail_path = settings.DATA_DIR / "thumbnails" / f"{video_id}.jpg"
    
    # 如果本地文件存在，直接返回
    if os.path.exists(thumbnail_path):
        return FileResponse(
            path=str(thumbnail_path),
            media_type="image/jpeg"
        )
    
    # 本地文件不存在，返回占位图
    placeholder_path = settings.DATA_DIR / "thumbnails" / "placeholder.jpg"
    if os.path.exists(placeholder_path):
        return FileResponse(
            path=str(placeholder_path),
            media_type="image/jpeg"
        )
    
    raise HTTPException(status_code=404, detail="缩略图不存在")


@app.get("/api/videos/{video_id}/download/video")
async def download_video_file(
    video_id: str, 
    watermark: bool = True, 
    platform: str = "douyin",
    force: bool = False,
    current_user: User = Depends(get_current_user)
):
    """下载视频文件 - 消耗2积分
    
    Args:
        video_id: 视频ID
        watermark: 是否包含水印（默认True，设为False下载无水印版本）
        platform: 平台
    """
    # 检查积分（管理员跳过）
    if current_user.role != UserRole.ADMIN:
        if not CreditManager.check_credits(current_user.id, 2):
            raise HTTPException(
                status_code=403,
                detail="积分不足，无法下载。当前需要 2 积分，请前往财务页面充值。"
            )
    
    session = get_platform_session(platform)
    ModelClass = PLATFORM_MODELS.get(platform)
    
    if not ModelClass:
        raise HTTPException(status_code=400, detail=f"不支持的平台: {platform}")
    
    try:
        record = session.query(ModelClass).filter_by(video_id=video_id).first()
        if not record:
            raise HTTPException(status_code=404, detail="视频不存在")
        
        from fastapi.responses import FileResponse
        from pathlib import Path
        import os
        
        settings = get_settings()
        videos_dir = settings.DATA_DIR / "videos"
        
        # 根据参数确定文件名
        if watermark:
            # 带水印版本（默认文件名）
            video_path = videos_dir / f"{video_id}.mp4"
            filename = f"{video_id}_watermark.mp4"
        else:
            # 无水印版本
            video_path = videos_dir / f"{video_id}_no_watermark.mp4"
            filename = f"{video_id}_no_watermark.mp4"
        
        if not os.path.exists(video_path):
            if watermark:
                # 如果连带水印版本都不存在
                raise HTTPException(status_code=404, detail="视频文件不存在，请先提取文案")
            else:
                # 无水印版本不存在
                raise HTTPException(status_code=404, detail="无水印视频不存在，请确保已成功提取文案并下载了无水印版本")
        
        # 扣除积分
        if current_user.role != UserRole.ADMIN:
            CreditManager.deduct_credits(
                current_user.id, 
                2,
                TransactionType.DOWNLOAD,
                description=f"下载视频: {video_id}",
                related_id=video_id
            )
        
        return FileResponse(
            path=str(video_path),
            filename=filename,
            media_type="video/mp4"
        )
    finally:
        session.close()


@app.get("/api/videos/{video_id}/download/audio")
async def download_audio_file(
    video_id: str, 
    platform: str = "douyin",
    force: bool = False,
    current_user: User = Depends(get_current_user)
):
    """下载音频文件 - 消耗2积分"""
    if current_user.role != UserRole.ADMIN:
        if not CreditManager.check_credits(current_user.id, 2):
            raise HTTPException(
                status_code=403,
                detail="积分不足，无法下载。当前需要 2 积分，请前往财务页面充值。"
            )
    
    session = get_platform_session(platform)
    ModelClass = PLATFORM_MODELS.get(platform)
    
    if not ModelClass:
        raise HTTPException(status_code=400, detail=f"不支持的平台: {platform}")
    
    try:
        record = session.query(ModelClass).filter_by(video_id=video_id).first()
        if not record:
            raise HTTPException(status_code=404, detail="视频不存在")
        
        from fastapi.responses import FileResponse
        from pathlib import Path
        import os
        
        settings = get_settings()
        audio_dir = settings.DATA_DIR / "audio"
        audio_path = audio_dir / f"{video_id}.wav"
        
        if not os.path.exists(audio_path):
            raise HTTPException(status_code=404, detail="音频文件不存在，请先提取文案")
        
        if current_user.role != UserRole.ADMIN:
            CreditManager.deduct_credits(
                current_user.id, 
                2,
                TransactionType.DOWNLOAD,
                description=f"下载音频: {video_id}",
                related_id=video_id
            )
        
        return FileResponse(
            path=str(audio_path),
            filename=f"{video_id}.wav",
            media_type="audio/wav"
        )
    finally:
        session.close()


# ============ 格式工厂接口 ============

import os
import tempfile
import uuid
from pathlib import Path

@app.post("/api/format/video-to-audio")
async def video_to_audio(
    file: UploadFile = File(...),
    output_format: str = Form("mp3"),
    quality: str = Form("high"),
    current_user: User = Depends(get_current_user)
):
    """视频转音频 - 消耗5积分"""
    require_license()
    
    if current_user.role != UserRole.ADMIN:
        if not CreditManager.check_credits(current_user.id, 5):
            raise HTTPException(
                status_code=403,
                detail="积分不足，需要 5 积分，请前往财务页面充值。"
            )
    
    settings = get_settings()
    temp_dir = settings.DATA_DIR / "temp"
    temp_dir.mkdir(parents=True, exist_ok=True)
    
    task_id = str(uuid.uuid4())
    temp_video_path = temp_dir / f"{task_id}_{file.filename}"
    temp_audio_path = temp_dir / f"{task_id}_output.{output_format}"
    
    try:
        with open(temp_video_path, "wb") as f:
            content = await file.read()
            f.write(content)
        
        logger.info(f"视频转音频: {file.filename} -> {output_format}")
        
        import subprocess
        
        quality_args = {
            "low": ["-b:a", "128k"],
            "medium": ["-b:a", "192k"],
            "high": ["-b:a", "320k"] if output_format == "mp3" else ["-ar", "48000"],
        }
        
        cmd = [
            "ffmpeg", "-y", "-i", str(temp_video_path),
            "-vn",
            *quality_args.get(quality, quality_args["high"]),
            str(temp_audio_path)
        ]
        
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        
        if result.returncode != 0:
            logger.error(f"FFmpeg error: {result.stderr}")
            raise HTTPException(status_code=500, detail=f"转换失败: {result.stderr[:200]}")
        
        if not temp_audio_path.exists():
            raise HTTPException(status_code=500, detail="转换失败：输出文件不存在")
        
        if current_user.role != UserRole.ADMIN:
            CreditManager.deduct_credits(
                current_user.id, 
                5,
                TransactionType.DOWNLOAD,
                description=f"视频转音频: {file.filename}",
                related_id=task_id
            )
        
        from fastapi.responses import FileResponse
        from starlette.background import BackgroundTask
        
        def cleanup():
            cleanup_temp_files(temp_video_path, temp_audio_path)
        
        return FileResponse(
            path=str(temp_audio_path),
            filename=f"{Path(file.filename).stem}.{output_format}",
            media_type="audio/mpeg" if output_format == "mp3" else f"audio/{output_format}",
            background=BackgroundTask(cleanup)
        )
        
    except subprocess.TimeoutExpired:
        raise HTTPException(status_code=500, detail="转换超时")
    except Exception as e:
        logger.error(f"视频转音频失败: {e}")
        if temp_video_path.exists():
            temp_video_path.unlink()
        if temp_audio_path.exists():
            temp_audio_path.unlink()
        raise HTTPException(status_code=500, detail=str(e))


def cleanup_temp_files(*files: Path):
    """清理临时文件"""
    for f in files:
        try:
            if f.exists():
                f.unlink()
        except Exception as e:
            logger.warning(f"清理临时文件失败: {e}")


# ============ 爆款视频分析接口 ============

@app.post("/api/videos/{video_id}/analyze")
async def analyze_viral_video(
    video_id: str,
    platform: str = "douyin",
    force: bool = False,
    current_user: User = Depends(get_current_user)
):
    """爆款视频AI分析 - 消耗20积分"""
    require_license()
    
    session = get_platform_session(platform)
    ModelClass = PLATFORM_MODELS.get(platform)
    
    if not ModelClass:
        raise HTTPException(status_code=400, detail=f"不支持的平台: {platform}")
    
    try:
        record = session.query(ModelClass).filter_by(video_id=video_id).first()
        if not record:
            raise HTTPException(status_code=404, detail="视频不存在")
        
        # 检查是否已有分析结果
        if not force and record.overall_score is not None and record.overall_score > 0:
            logger.info(f"返回已保存的分析结果: {video_id}")
            return {
                "video_id": video_id,
                "overall_score": record.overall_score,
                "viral_factors": json.loads(record.viral_factors) if record.viral_factors else {},
                "content_analysis": json.loads(record.content_analysis) if record.content_analysis else {},
                "title_analysis": json.loads(record.title_analysis) if record.title_analysis else {},
                "tag_analysis": json.loads(record.tag_analysis) if record.tag_analysis else {},
                "transcript_analysis": json.loads(record.transcript_analysis) if record.transcript_analysis else {},
                "interaction_analysis": json.loads(record.interaction_analysis) if record.interaction_analysis else {},
                "improvement_suggestions": json.loads(record.improvement_suggestions) if record.improvement_suggestions else [],
                "success_formula": record.success_formula or "",
                "cached": True,
            }
        
        # 检查积分（管理员跳过）
        if current_user.role != UserRole.ADMIN:
            if not CreditManager.check_credits(current_user.id, 20):
                raise HTTPException(
                    status_code=403,
                    detail="积分不足，无法分析视频。当前需要 20 积分，请前往财务页面充值。"
                )
        
        # 调用AI分析
        from analyzer import get_analyzer
        analyzer = get_analyzer()
        
        # 解析标签
        tags = json.loads(record.tags) if record.tags else []
        
        # 执行分析
        logger.info(f"开始分析视频: {video_id}")
        result = analyzer.analyze_viral_video(
            title=record.description or "",
            description=record.description or "",
            transcript=record.transcript or "",
            tags=tags,
            likes=record.likes or 0,
            comments=record.comments or 0,
            shares=record.shares or 0,
            collects=record.collects or 0,
            author_name=record.author_name or "",
            fans_count=record.fans_count or "0",
        )
        
        logger.info(f"分析完成: overall_score={result.overall_score}")
        
        # 检查分析结果是否有效
        if not result or result.overall_score == 0:
            logger.warning(f"AI分析结果无效: {result}")
            raise HTTPException(
                status_code=500, 
                detail="AI分析失败，请稍后重试"
            )
        
        # 保存分析结果到数据库
        record.overall_score = result.overall_score
        record.viral_factors = json.dumps(result.viral_factors, ensure_ascii=False)
        record.content_analysis = json.dumps(result.content_analysis, ensure_ascii=False)
        record.title_analysis = json.dumps(result.title_analysis, ensure_ascii=False)
        record.tag_analysis = json.dumps(result.tag_analysis, ensure_ascii=False)
        record.transcript_analysis = json.dumps(result.transcript_analysis, ensure_ascii=False)
        record.interaction_analysis = json.dumps(result.interaction_analysis, ensure_ascii=False)
        record.improvement_suggestions = json.dumps(result.improvement_suggestions, ensure_ascii=False)
        record.success_formula = result.success_formula
        session.commit()
        logger.info(f"分析结果已保存: {video_id}")
        
        # 操作成功后才扣除积分
        if current_user.role != UserRole.ADMIN:
            CreditManager.deduct_credits(
                current_user.id, 
                20,
                TransactionType.ANALYZE,
                description=f"爆款视频分析: {video_id}",
                related_id=video_id
            )
        
        return {
            "video_id": video_id,
            "overall_score": result.overall_score,
            "viral_factors": result.viral_factors,
            "content_analysis": result.content_analysis,
            "title_analysis": result.title_analysis,
            "tag_analysis": result.tag_analysis,
            "transcript_analysis": result.transcript_analysis,
            "interaction_analysis": result.interaction_analysis,
            "improvement_suggestions": result.improvement_suggestions,
            "success_formula": result.success_formula,
            "cached": False,
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"爆款视频分析失败: {e}")
        raise HTTPException(status_code=500, detail=f"分析失败: {str(e)}")
    finally:
        session.close()


@app.post("/api/videos/{video_id}/visual-analyze")
async def visual_analyze_viral_video(
    video_id: str,
    platform: str = "douyin",
    force: bool = False,
    num_frames: int = 3,
    current_user: User = Depends(get_current_user)
):
    """视觉增强型爆款分析：截帧+视觉模型+文案 → 爆点+创作提示词"""
    require_license()

    session = get_platform_session(platform)
    ModelClass = PLATFORM_MODELS.get(platform)
    if not ModelClass:
        raise HTTPException(status_code=400, detail=f"不支持的平台: {platform}")

    try:
        record = session.query(ModelClass).filter_by(video_id=video_id).first()
        if not record:
            raise HTTPException(status_code=404, detail="视频不存在")

        # === 爆款阈值检查 ===
        # 计算互动加权分数：点赞0.5 + 评论0.3 + 分享0.2 + 收藏0.1
        likes = record.likes or 0
        comments = record.comments or 0
        shares = record.shares or 0
        collects = record.collects or 0
        
        viral_score = (
            likes * 0.5 + 
            comments * 0.3 + 
            shares * 0.2 + 
            collects * 0.1
        )
        
        MIN_VIRAL_SCORE = 5000  # 最低爆款分数阈值
        
        if viral_score < MIN_VIRAL_SCORE :
            logger.info(f"视频 {video_id} 互动量不足: 分数={viral_score:.0f}")
            raise HTTPException(
                status_code=400, 
                detail=f"该视频互动量不足，不具备爆款分析价值。当前分数：{viral_score:.0f}，最低要求：{MIN_VIRAL_SCORE}。互动数据：点赞 {likes:,} | 评论 {comments:,} | 分享 {shares:,} | 收藏 {collects:,}"
            )
        
        logger.info(f"视频 {video_id} 通过爆款阈值检查: 分数={viral_score:.0f}")
        # === 阈值检查结束 ===

        # 已有结果且不强制重新分析
        if not force and record.viral_points:
            logger.info(f"返回已有视觉分析结果: {video_id}")
            return {
                "video_id": video_id,
                "cached": True,
                "has_video": record.frames_analyzed is not None and record.frames_analyzed > 0,
                "frames_analyzed": record.frames_analyzed or 0,
                "visual_result": {
                    "visual_description": record.visual_description or "",
                    "scene_types": json.loads(record.scene_types) if record.scene_types else [],
                    "visual_highlights": json.loads(record.visual_highlights) if record.visual_highlights else [],
                    "cinematography": record.cinematography or "",
                } if record.visual_description else None,
                "viral_points": json.loads(record.viral_points) if record.viral_points else [],
                "visual_hooks": json.loads(record.visual_hooks) if record.visual_hooks else [],
                "content_hooks": json.loads(record.content_hooks) if record.content_hooks else [],
                "emotion_triggers": json.loads(record.emotion_triggers) if record.emotion_triggers else [],
                "target_audience": record.target_audience or "",
                "creation_prompt": record.creation_prompt or "",
                "replication_tips": json.loads(record.replication_tips) if record.replication_tips else [],
            }

        # 积分检查（管理员跳过）
        if current_user.role != UserRole.ADMIN:
            if not CreditManager.check_credits(current_user.id, 20):
                raise HTTPException(status_code=403, detail="积分不足，视觉分析需要 20 积分")

        # 执行视觉增强分析
        from analyzer.visual_analyzer import VisualViralAnalyzer
        settings = get_settings()

        analyzer = VisualViralAnalyzer(
            api_key=settings.ZHIPU_API_KEY,
            base_url=settings.ZHIPU_BASE_URL or "https://open.bigmodel.cn/api/paas/v4",
            vision_model=settings.ZHIPU_MODEL or "GLM-4.1V-Thinking-Flash",
            text_model="glm-4-flash",
            videos_dir=str(settings.VIDEOS_DIR),
        )

        result = analyzer.analyze(
            video_id=video_id,
            title=record.description or "",
            transcript=record.transcript or "",
            likes=record.likes or 0,
            comments=record.comments or 0,
            shares=record.shares or 0,
            collects=record.collects or 0,
            author_name=record.author_name or "",
            fans_count=str(record.fans_count or "0"),
            platform=platform,
            num_frames=num_frames,
        )

        # 保存到数据库
        if result.get("visual_result"):
            vr = result["visual_result"]
            record.visual_description = vr.get("visual_description", "")
            record.scene_types = json.dumps(vr.get("scene_types", []), ensure_ascii=False)
            record.visual_highlights = json.dumps(vr.get("visual_highlights", []), ensure_ascii=False)
            record.cinematography = vr.get("cinematography", "")
        record.viral_points = json.dumps(result.get("viral_points", []), ensure_ascii=False)
        record.visual_hooks = json.dumps(result.get("visual_hooks", []), ensure_ascii=False)
        record.content_hooks = json.dumps(result.get("content_hooks", []), ensure_ascii=False)
        record.emotion_triggers = json.dumps(result.get("emotion_triggers", []), ensure_ascii=False)
        record.target_audience = result.get("target_audience", "")
        record.creation_prompt = result.get("creation_prompt", "")
        record.replication_tips = json.dumps(result.get("replication_tips", []), ensure_ascii=False)
        record.frames_analyzed = result.get("frames_analyzed", 0)
        session.commit()
        logger.info(f"视觉分析结果已保存: {video_id}")

        # 扣积分
        if current_user.role != UserRole.ADMIN:
            CreditManager.deduct_credits(
                current_user.id, 20, TransactionType.ANALYZE,
                description=f"视觉爆款分析: {video_id}", related_id=video_id
            )

        return {"video_id": video_id, "cached": False, **result}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"视觉爆款分析失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"分析失败: {str(e)}")
    finally:
        session.close()


@app.get("/api/videos/{video_id}/analysis")
async def get_video_analysis(
    video_id: str,
    platform: str = "douyin",
    force: bool = False,
    current_user: User = Depends(get_current_user)
):
    """获取视频分析结果"""
    session = get_platform_session(platform)
    ModelClass = PLATFORM_MODELS.get(platform)
    
    if not ModelClass:
        raise HTTPException(status_code=400, detail=f"不支持的平台: {platform}")
    
    try:
        record = session.query(ModelClass).filter_by(video_id=video_id).first()
        if not record:
            raise HTTPException(status_code=404, detail="视频不存在")
        
        if record.overall_score is None or record.overall_score == 0:
            raise HTTPException(status_code=404, detail="暂无分析结果，请先进行分析")
        
        return {
            "video_id": video_id,
            "overall_score": record.overall_score,
            "viral_factors": json.loads(record.viral_factors) if record.viral_factors else {},
            "content_analysis": json.loads(record.content_analysis) if record.content_analysis else {},
            "title_analysis": json.loads(record.title_analysis) if record.title_analysis else {},
            "tag_analysis": json.loads(record.tag_analysis) if record.tag_analysis else {},
            "transcript_analysis": json.loads(record.transcript_analysis) if record.transcript_analysis else {},
            "interaction_analysis": json.loads(record.interaction_analysis) if record.interaction_analysis else {},
            "improvement_suggestions": json.loads(record.improvement_suggestions) if record.improvement_suggestions else [],
            "success_formula": record.success_formula or "",
        }
    finally:
        session.close()


@app.get("/api/videos/{video_id}/latest-analysis")
async def get_latest_viral_analysis(
    video_id: str,
    platform: str = "douyin",
    force: bool = False,
    current_user: User = Depends(get_current_user)
):
    """获取最新一次爆款分析结果"""
    session = get_platform_session(platform)
    ModelClass = PLATFORM_MODELS.get(platform)
    
    if not ModelClass:
        raise HTTPException(status_code=400, detail=f"不支持的平台: {platform}")
    
    try:
        record = session.query(ModelClass).filter_by(
            video_id=video_id
        ).first()
        
        if not record:
            raise HTTPException(status_code=404, detail="视频不存在")
        
        if not record.overall_score:
            raise HTTPException(status_code=404, detail="暂无分析结果")
        
        return {
            "video_id": video_id,
            "overall_score": record.overall_score,
            "viral_factors": json.loads(record.viral_factors) if record.viral_factors else {},
            "content_analysis": json.loads(record.content_analysis) if record.content_analysis else {},
            "title_analysis": json.loads(record.title_analysis) if record.title_analysis else {},
            "tag_analysis": json.loads(record.tag_analysis) if record.tag_analysis else {},
            "transcript_analysis": json.loads(record.transcript_analysis) if record.transcript_analysis else {},
            "interaction_analysis": json.loads(record.interaction_analysis) if record.interaction_analysis else {},
            "improvement_suggestions": json.loads(record.improvement_suggestions) if record.improvement_suggestions else [],
            "success_formula": record.success_formula or "",
        }
    finally:
        session.close()



# ============ SPA前端服务 ============
from fastapi.responses import FileResponse
import os

WEB_DIST = Path("/root/lyx/web/dist")

@app.get("/")
async def serve_index():
    """服务前端首页"""
    index_file = WEB_DIST / "index.html"
    if index_file.exists():
        return FileResponse(index_file)
    return {"error": "前端未构建"}

# ============ 视频号抓包接口 ============

from crawler.shipinhao_sniffer import (
    ShipinhaoSniffer, CapturedVideo,
    get_sniffer, init_sniffer, MITMPROXY_AVAILABLE
)


class ShipinhaoVideoResponse(BaseModel):
    """视频号视频信息"""
    video_url: str
    video_id: str = ""
    title: str = ""
    author: str = ""
    author_id: str = ""
    cover_url: str = ""
    duration: float = 0.0
    captured_at: str


class ShipinhaoSnifferStatus(BaseModel):
    """抓包服务状态"""
    running: bool
    port: int
    captured_count: int
    mitmproxy_available: bool
    proxy_config: Optional[dict] = None


@app.get("/api/shipinhao/sniffer/status", response_model=ShipinhaoSnifferStatus)
async def get_shipinhao_sniffer_status(current_user: User = Depends(get_current_user)):
    """
    获取视频号抓包服务状态
    """
    sniffer = get_sniffer()
    
    if sniffer:
        return ShipinhaoSnifferStatus(
            running=sniffer.is_running(),
            port=sniffer.port,
            captured_count=len(sniffer.get_captured_videos()),
            mitmproxy_available=MITMPROXY_AVAILABLE,
            proxy_config=sniffer.get_proxy_config() if not sniffer.is_running() else None
        )
    
    return ShipinhaoSnifferStatus(
        running=False,
        port=8080,
        captured_count=0,
        mitmproxy_available=MITMPROXY_AVAILABLE,
        proxy_config=ShipinhaoSniffer.get_proxy_config() if MITMPROXY_AVAILABLE else None
    )


@app.post("/api/shipinhao/sniffer/start")
async def start_shipinhao_sniffer(
    port: int = Query(8080, description="代理端口"),
    current_user: User = Depends(get_current_user)
):
    """
    启动视频号抓包服务
    
    启动后需要：
    1. 安装 mitmproxy 证书（浏览器访问 http://mitm.it）
    2. 设置系统代理：127.0.0.1:端口
    3. 打开 PC 微信播放视频号视频
    """
    if not MITMPROXY_AVAILABLE:
        raise HTTPException(
            status_code=500,
            detail="mitmproxy 未安装，请在服务器上运行: pip install mitmproxy"
        )
    
    sniffer = get_sniffer()
    
    if sniffer and sniffer.is_running():
        return {"message": "抓包服务已在运行", "port": sniffer.port}
    
    # 初始化并启动
    sniffer = await init_sniffer(port=port)
    
    # 异步启动
    asyncio.create_task(sniffer.start())
    
    return {
        "message": "抓包服务启动中",
        "port": port,
        "proxy_config": sniffer.get_proxy_config()
    }


@app.post("/api/shipinhao/sniffer/stop")
async def stop_shipinhao_sniffer(current_user: User = Depends(get_current_user)):
    """停止视频号抓包服务"""
    sniffer = get_sniffer()
    
    if not sniffer:
        raise HTTPException(status_code=400, detail="抓包服务未初始化")
    
    if not sniffer.is_running():
        return {"message": "抓包服务未运行"}
    
    await sniffer.stop()
    return {"message": "抓包服务已停止"}


@app.get("/api/shipinhao/videos", response_model=List[ShipinhaoVideoResponse])
async def get_shipinhao_videos(
    limit: int = Query(50, ge=1, le=200, description="返回数量限制"),
    clear: bool = Query(False, description="是否清空已捕获记录"),
    current_user: User = Depends(get_current_user)
):
    """
    获取已捕获的视频号视频列表
    """
    sniffer = get_sniffer()
    
    if not sniffer:
        raise HTTPException(status_code=400, detail="抓包服务未初始化，请先启动服务")
    
    videos = sniffer.get_captured_videos(limit=limit)
    
    result = [
        ShipinhaoVideoResponse(
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


@app.delete("/api/shipinhao/videos")
async def clear_shipinhao_videos(current_user: User = Depends(get_current_user)):
    """清空已捕获的视频记录"""
    sniffer = get_sniffer()
    
    if not sniffer:
        raise HTTPException(status_code=400, detail="抓包服务未初始化")
    
    count = len(sniffer.get_captured_videos())
    sniffer.clear_captured_videos()
    
    return {"message": f"已清空 {count} 条记录"}


@app.get("/api/shipinhao/proxy-config")
async def get_shipinhao_proxy_config():
    """
    获取代理配置说明
    
    返回如何设置系统代理和安装证书的详细说明
    """
    if not MITMPROXY_AVAILABLE:
        raise HTTPException(
            status_code=500,
            detail="mitmproxy 未安装，请在服务器上运行: pip install mitmproxy"
        )
    
    return ShipinhaoSniffer.get_proxy_config()


@app.post("/api/shipinhao/parse-link")
async def parse_shipinhao_link(
    url: str = Body(..., embed=True, description="视频号链接")
):
    """
    解析视频号链接，提取视频ID

    支持格式：
    - https://weixin.qq.com/sph/VIDEO_ID
    - https://channels.weixin.qq.com/finder-preview/pages/sph?id=VIDEO_ID

    注意：视频号内容需要微信登录态才能访问，这里只解析链接格式
    """
    from crawler.shipinhao_link_parser import parse_shipinhao_link

    result = parse_shipinhao_link(url)

    if result.get("status") == "error":
        raise HTTPException(status_code=400, detail=result.get("error"))

    return result


@app.get("/shipinhao", response_class=HTMLResponse)
async def shipinhao_page():
    """视频号解析页面"""
    from pathlib import Path
    html_file = Path("/app/data/output/shipinhao.html")
    if html_file.exists():
        return HTMLResponse(content=html_file.read_text(encoding="utf-8"))
    return HTMLResponse(content="<h1>页面未找到</h1>", status_code=404)


@app.post("/api/shipinhao/parse-video")
async def parse_shipinhao_video(
    url: str = Body(..., embed=True, description="视频号链接")
):
    """
    解析视频号链接，尝试获取视频下载地址

    注意：
    - 视频号页面需要微信登录态才能访问
    - 如果无法获取下载链接，可能需要用户先在微信中打开视频
    - 或者使用抓包工具获取真实的下载链接
    """
    from crawler.shipinhao_video_parser import parse_shipinhao_video_sync
    import asyncio

    try:
        # 使用异步方式解析
        result = parse_shipinhao_video_sync(url, timeout=30)

        if result.get("status") == "error":
            # 如果是因为需要登录，返回更详细的提示
            if "登录" in result.get("error", ""):
                return {
                    "status": "auth_required",
                    "video_id": result.get("video_id"),
                    "original_url": result.get("original_url"),
                    "error": "需要微信登录才能获取视频信息",
                    "hint": "请在微信中打开此链接，然后使用抓包工具获取下载链接",
                    "api_endpoint": "/api/shipinhao/save-download-url"
                }
            raise HTTPException(status_code=400, detail=result.get("error"))

        return result

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


class ShipinhaoDownloadUrl(BaseModel):
    """视频号下载链接"""
    video_id: str
    download_url: str
    title: str = ""
    author: str = ""


# 存储下载链接的字典（实际应用中应使用数据库）
_shipinhao_download_urls = {}


@app.post("/api/shipinhao/save-download-url")
async def save_shipinhao_download_url(
    data: ShipinhaoDownloadUrl,
    current_user: dict = Depends(get_current_user)
):
    """
    保存视频号下载链接

    当用户通过微信或抓包工具获取到真实的下载链接后，
    可以通过此接口保存，以便后续使用
    """
    global _shipinhao_download_urls

    _shipinhao_download_urls[data.video_id] = {
        "video_id": data.video_id,
        "download_url": data.download_url,
        "title": data.title,
        "author": data.author,
        "saved_at": datetime.now().isoformat(),
        "user_id": current_user.get("user_id")
    }

    return {
        "status": "success",
        "message": "下载链接已保存",
        "video_id": data.video_id
    }


@app.get("/api/shipinhao/download-url/{video_id}")
async def get_shipinhao_download_url(
    video_id: str,
    current_user: dict = Depends(get_current_user)
):
    """
    获取已保存的视频号下载链接
    """
    global _shipinhao_download_urls

    if video_id in _shipinhao_download_urls:
        return _shipinhao_download_urls[video_id]

    raise HTTPException(status_code=404, detail="下载链接不存在")
@app.get("/{path:path}")
async def serve_spa(path: str):
    """SPA fallback - 所有非API路由返回index.html"""
    # API路由由FastAPI处理
    if path.startswith("api/") or path.startswith("thumbnails/") or path.startswith("images/") or path.startswith("uploads/"):
        # 让FastAPI继续处理
        raise HTTPException(status_code=404)
    
    # 尝试返回静态文件
    file_path = WEB_DIST / path
    if file_path.exists() and file_path.is_file():
        return FileResponse(file_path)
    
    # SPA fallback - 返回index.html
    index_file = WEB_DIST / "index.html"
    if index_file.exists():
        return FileResponse(index_file)
    
    raise HTTPException(status_code=404)

if __name__ == "__main__":
    import uvicorn
    settings = get_settings()
    uvicorn.run(app, host=settings.API_HOST, port=settings.API_PORT)


# ============ 兼容旧版前端下载接口 ============

@app.get("/api/download/thumbnail")
async def download_thumbnail_compat(
    url: str = Query(..., description="视频ID"),
    video_id: str = Query(None, description="视频ID（备用）"),
    platform: str = Query("douyin", description="平台"),
    current_user: User = Depends(get_current_user)
):
    """下载缩略图（兼容接口）"""
    logger.info(f"下载缩略图请求: url={url}, video_id={video_id}")
    # url 参数实际传递的是 video_id
    actual_video_id = url or video_id
    return await download_thumbnail(actual_video_id, platform, current_user)


@app.get("/api/download/video-watermark")
async def download_video_watermark_compat(
    url: str = Query(..., description="视频ID"),
    video_id: str = Query(None, description="视频ID（备用）"),
    platform: str = Query("douyin", description="平台"),
    current_user: User = Depends(get_current_user)
):
    """下载视频（兼容接口）"""
    actual_video_id = url or video_id
    return await download_video_file(actual_video_id, platform, current_user)


@app.get("/api/download/audio")
async def download_audio_compat(
    url: str = Query(..., description="视频ID"),
    video_id: str = Query(None, description="视频ID（备用）"),
    platform: str = Query("douyin", description="平台"),
    current_user: User = Depends(get_current_user)
):
    """下载音频（兼容接口）"""
    actual_video_id = url or video_id
    return await download_audio_file(actual_video_id, platform, current_user)


@app.get("/api/download/file")
async def download_file_compat(
    url: str = Query(..., description="文件URL或视频ID"),
    current_user: User = Depends(get_current_user)
):
    """下载文件（兼容接口）"""
    from fastapi.responses import StreamingResponse
    import httpx
    
    # 如果是视频ID格式，尝试下载视频
    if not url.startswith("http"):
        return await download_video_watermark_compat(url, None, "douyin", current_user)
    
    # 否则作为URL下载
    try:
        async with httpx.AsyncClient(timeout=120) as client:
            response = await client.get(url, follow_redirects=True)
            response.raise_for_status()
            
            content_type = response.headers.get("content-type", "application/octet-stream")
            filename = url.split("/")[-1].split("?")[0] or "download"
            
            return StreamingResponse(
                iter([response.content]),
                media_type=content_type,
                headers={"Content-Disposition": f"attachment; filename={filename}"}
            )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"下载失败: {str(e)}")




# ===== 公开 API (无需认证) =====

@app.post("/api/public/transcript")
async def public_transcript(
    platform: str,
    video_url: str,
    timeout: int = 30
):
    """
    公开的文案提取接口（无需认证）
    用于 AI 视频创作平台集成
    """
    import logging
    logger = logging.getLogger(__name__)
    
    logger.info(f"公开API - 提取文案: platform={platform}, url={video_url}")
    
    try:
        if platform == "douyin":
            from crawler.douyin import DouyinCrawler
            crawler = DouyinCrawler()
            result = await crawler.get_video_transcript(video_url)
        elif platform == "xiaohongshu":
            from crawler.xiaohongshu import XiaohongshuCrawler
            crawler = XiaohongshuCrawler()
            result = await crawler.get_video_transcript(video_url)
        elif platform == "shipinhao":
            from crawler.shipinhao import ShipinhaoCrawler
            crawler = ShipinhaoCrawler()
            result = await crawler.get_video_transcript(video_url)
        else:
            return {"success": False, "error": f"不支持的平台: {platform}"}
        
        return {
            "success": True,
            "transcript": result.get("transcript", ""),
            "video_info": result.get("video_info", {})
        }
    except Exception as e:
        logger.error(f"提取文案失败: {e}")
        return {"success": False, "error": str(e)}
