"""
数据库模型 - 多平台支持 + 用户系统
"""
from datetime import datetime
from sqlalchemy import Column, Integer, String, Text, DateTime, Float, Boolean, ForeignKey, Enum, create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, Session, relationship
from config import get_settings
import enum

Base = declarative_base()


class UserRole(enum.Enum):
    """用户角色"""
    USER = "user"
    ADMIN = "admin"


class TransactionType(enum.Enum):
    """交易类型"""
    SEARCH = "search"           # 搜索视频
    TRANSCRIPT = "transcript"   # 提取文案
    DOWNLOAD = "download"       # 下载文案
    ANALYZE = "analyze"         # 爆款视频分析
    AI_CREATE = "ai_create"     # AI创作
    RECHARGE = "recharge"       # 充值
    REFUND = "refund"           # 退款


class TicketStatus(enum.Enum):
    """工单状态"""
    PENDING = "pending"         # 待审批
    APPROVED = "approved"       # 已通过
    REJECTED = "rejected"       # 已拒绝


class User(Base):
    """用户表"""
    __tablename__ = "users"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    username = Column(String(50), unique=True, nullable=False, comment="用户名")
    email = Column(String(100), unique=True, nullable=False, comment="邮箱")
    phone = Column(String(20), unique=True, nullable=False, comment="手机号")
    hashed_password = Column(String(255), nullable=False, comment="密码哈希")
    role = Column(Enum(UserRole), default=UserRole.USER, comment="用户角色")
    credits = Column(Integer, default=0, comment="当前积分")
    is_active = Column(Boolean, default=True, comment="是否激活")
    created_at = Column(DateTime, default=datetime.now, comment="创建时间")
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now, comment="更新时间")
    
    # 关联关系
    transactions = relationship("CreditTransaction", back_populates="user", cascade="all, delete-orphan")
    tickets = relationship("Ticket", foreign_keys="Ticket.user_id", back_populates="user", cascade="all, delete-orphan")


class CreditTransaction(Base):
    """积分交易记录表"""
    __tablename__ = "credit_transactions"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, comment="用户ID")
    amount = Column(Integer, nullable=False, comment="金额(正为充值，负为消耗)")
    type = Column(Enum(TransactionType), nullable=False, comment="交易类型")
    description = Column(String(500), comment="描述")
    related_id = Column(String(100), comment="关联ID(如视频ID)")
    created_at = Column(DateTime, default=datetime.now, comment="创建时间")
    
    # 关联关系
    user = relationship("User", back_populates="transactions")


class Ticket(Base):
    """工单表（充值申请）"""
    __tablename__ = "tickets"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, comment="用户ID")
    type = Column(String(20), default="recharge", comment="工单类型")
    amount = Column(Integer, nullable=False, comment="申请积分数量")
    payment_method = Column(String(20), nullable=False, comment="支付方式(alipay/wechat)")
    payment_proof = Column(String(500), comment="支付凭证图片路径")
    status = Column(Enum(TicketStatus), default=TicketStatus.PENDING, comment="状态")
    admin_note = Column(Text, comment="管理员备注")
    processed_by = Column(Integer, ForeignKey("users.id"), comment="处理人ID")
    created_at = Column(DateTime, default=datetime.now, comment="创建时间")
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now, comment="更新时间")
    
    # 关联关系
    user = relationship("User", foreign_keys=[user_id], back_populates="tickets")
    processor = relationship("User", foreign_keys=[processed_by])


class DeletedVideoRecord(Base):
    """已删除视频记录表（用于统计）"""
    __tablename__ = "deleted_videos"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, comment="删除操作的用户ID")
    video_id = Column(String(100), nullable=False, comment="视频ID")
    platform = Column(String(20), nullable=False, comment="平台")
    author_name = Column(String(100), comment="博主昵称")
    description = Column(Text, comment="视频描述")
    likes = Column(Integer, default=0, comment="点赞数")
    comments = Column(Integer, default=0, comment="评论数")
    shares = Column(Integer, default=0, comment="分享数")
    collects = Column(Integer, default=0, comment="收藏数")
    had_transcript = Column(Boolean, default=False, comment="是否有文案")
    deleted_at = Column(DateTime, default=datetime.now, comment="删除时间")


class BaseVideoRecord(Base):
    """视频记录基类"""
    __abstract__ = True
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    
    # 用户关联（数据隔离）
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True, comment="用户ID（NULL表示共享数据）")
    
    # 博主信息
    author_name = Column(String(100), comment="博主昵称")
    author_id = Column(String(100), comment="账号ID")
    ip_location = Column(String(50), comment="IP属地")
    author_signature = Column(String(500), comment="签名")
    fans_count = Column(String(50), default="0", comment="粉丝数")
    liked_count = Column(String(50), default="0", comment="获赞数")
    
    # 帖子信息
    video_id = Column(String(100), unique=True, nullable=False, index=True, comment="视频ID")
    description = Column(Text, comment="帖子描述")
    tags = Column(Text, comment="帖子标签(JSON)")
    video_url = Column(String(500), comment="帖子链接")
    video_stream_url = Column(String(1000), comment="视频流下载链接")
    thumbnail_url = Column(String(500), comment="缩略图路径")
    publish_date = Column(DateTime, comment="发布日期")
    
    # 互动数据
    likes = Column(Integer, default=0, comment="点赞数")
    comments = Column(Integer, default=0, comment="评论数")
    collects = Column(Integer, default=0, comment="收藏数")
    shares = Column(Integer, default=0, comment="分享数")
    
    # 文案内容（后续填充）
    transcript = Column(Text, comment="语音转文字内容")
    transcript_duration = Column(Float, comment="音频时长(秒)")
    duration = Column(Float, default=0.0, comment="视频时长(秒)")
    
    # AI分析结果（后续填充）
    summary = Column(String(500), comment="摘要")
    selling_points = Column(Text, comment="卖点(JSON)")
    emotions = Column(Text, comment="情绪点(JSON)")
    template = Column(Text, comment="可复用模板")
    
    # 爆款视频分析结果
    overall_score = Column(Integer, comment="爆款评分(0-100)")
    viral_factors = Column(Text, comment="爆款因素(JSON)")
    content_analysis = Column(Text, comment="内容分析(JSON)")
    title_analysis = Column(Text, comment="标题分析(JSON)")
    tag_analysis = Column(Text, comment="标签分析(JSON)")
    transcript_analysis = Column(Text, comment="文案分析(JSON)")
    interaction_analysis = Column(Text, comment="互动分析(JSON)")
    improvement_suggestions = Column(Text, comment="改进建议(JSON)")
    success_formula = Column(Text, comment="成功公式")

    # 视觉增强分析结果（新版）
    visual_description = Column(Text, comment="AI视觉描述")
    scene_types = Column(Text, comment="场景类型(JSON)")
    visual_highlights = Column(Text, comment="视觉亮点(JSON)")
    viral_points = Column(Text, comment="爆点列表(JSON)")
    visual_hooks = Column(Text, comment="视觉钩子(JSON)")
    content_hooks = Column(Text, comment="内容钩子(JSON)")
    emotion_triggers = Column(Text, comment="情绪触发点(JSON)")
    target_audience = Column(Text, comment="目标受众")
    creation_prompt = Column(Text, comment="创作提示词")
    replication_tips = Column(Text, comment="复刻建议(JSON)")
    frames_analyzed = Column(Integer, comment="分析帧数")
    frame_timestamps = Column(Text, comment="截帧时间戳列表(JSON)")
    
    # 元数据
    keyword = Column(String(100), comment="搜索关键词")
    created_at = Column(DateTime, default=datetime.now, comment="记录创建时间")
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now, comment="记录更新时间")
    
    @property
    def platform_name(self):
        """返回平台名称"""
        return self.__tablename__.replace('_videos', '')


class DouyinVideoRecord(BaseVideoRecord):
    """抖音视频记录表"""
    __tablename__ = "douyin_videos"


class DouyinImageRecord(BaseVideoRecord):
    """抖音图文记录表"""
    __tablename__ = "douyin_images"
    
    # 图文特有字段：多张图片URL列表
    images = Column(Text, comment="图片URL列表(JSON数组)")


class XiaohongshuVideoRecord(BaseVideoRecord):
    """小红书视频记录表"""
    __tablename__ = "xiaohongshu_videos"


class XiaohongshuImageRecord(BaseVideoRecord):
    """小红书图文记录表"""
    __tablename__ = "xiaohongshu_images"
    
    # 图文特有字段：多张图片URL列表
    images = Column(Text, comment="图片URL列表(JSON数组)")


class ShipinhaoVideoRecord(BaseVideoRecord):
    """视频号视频记录表"""
    __tablename__ = "shipinhao_videos"


class Template(Base):
    """用户保存的文案模板表"""
    __tablename__ = "templates"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True, comment="用户ID（NULL表示系统模板）")
    name = Column(String(200), nullable=False, comment="模板名称")
    content = Column(Text, nullable=False, comment="模板内容")
    source_video_id = Column(String(100), comment="来源视频ID")
    source_platform = Column(String(50), comment="来源平台")
    category = Column(String(50), default="用户模板", comment="模板分类")
    tags = Column(Text, comment="模板标签(JSON)")
    description = Column(Text, comment="模板描述")
    usage_count = Column(Integer, default=0, comment="使用次数")
    created_at = Column(DateTime, default=datetime.now, comment="创建时间")
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now, comment="更新时间")
    parent_id = Column(Integer, ForeignKey("templates.id"), nullable=True, comment="父模板ID（用于版本管理）")
    version = Column(Integer, default=1, comment="版本号")
    version_note = Column(String(500), comment="版本说明")


# 平台模型映射
PLATFORM_MODELS = {
    "douyin": DouyinVideoRecord,
    "douyin_image": DouyinImageRecord,
    "xiaohongshu": XiaohongshuVideoRecord,
    "xiaohongshu_image": XiaohongshuImageRecord,
    "shipinhao": ShipinhaoVideoRecord,
}


class SearchTask(Base):
    """异步搜索任务表"""
    __tablename__ = "search_tasks"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True, comment="用户ID")
    keyword = Column(String(100), nullable=False, comment="搜索关键词")
    platform = Column(String(20), default="douyin", comment="平台: douyin/xiaohongshu/shipinhao")
    content_type = Column(String(20), default="video", comment="内容类型: video/image")
    limit = Column(Integer, default=20, comment="目标数量")
    min_likes = Column(Integer, default=0, comment="最小点赞数")
    status = Column(String(20), default="pending", comment="状态: pending/running/completed/failed/cancelled")
    progress = Column(Text, comment="进度信息JSON: {stage, collected_links, visited_pages, qualified_count, message}")
    result_count = Column(Integer, default=0, comment="符合条件的结果数")
    error_message = Column(Text, comment="错误信息")
    created_at = Column(DateTime, default=datetime.now, comment="创建时间")
    started_at = Column(DateTime, comment="开始时间")
    completed_at = Column(DateTime, comment="完成时间")


class LicenseRecord(Base):
    """License授权记录表"""
    __tablename__ = "license_records"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    license_key = Column(String(64), unique=True, nullable=False, comment="License密钥")
    machine_code = Column(String(32), nullable=False, comment="绑定的机器码")
    expire_date = Column(String(20), nullable=False, comment="过期日期 YYYY-MM-DD")
    max_users = Column(Integer, default=10, comment="最大用户数")
    features = Column(Text, default="[]", comment="授权功能列表JSON")
    customer_name = Column(String(100), default="", comment="客户名称")
    is_active = Column(Boolean, default=True, comment="是否激活")
    created_at = Column(DateTime, default=datetime.now, comment="创建时间")
    activated_at = Column(DateTime, comment="激活时间")


class PlatformLoginStatus(Base):
    """平台登录状态表"""
    __tablename__ = "platform_login_status"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, comment="用户ID")
    platform = Column(String(20), nullable=False, comment="平台: douyin/xiaohongshu/shipinhao")
    is_logged_in = Column(Boolean, default=False, comment="是否已登录")
    nickname = Column(String(100), default="", comment="用户昵称")
    last_checked_at = Column(DateTime, default=datetime.now, comment="最后检查时间")
    created_at = Column(DateTime, default=datetime.now, comment="创建时间")
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now, comment="更新时间")


class BrowserSession(Base):
    """浏览器会话表 - 记录用户的浏览器登录会话"""
    __tablename__ = "browser_sessions"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, comment="用户ID")
    session_id = Column(String(100), unique=True, nullable=False, comment="会话ID")
    platform = Column(String(20), nullable=False, comment="平台: douyin/xiaohongshu/shipinhao")
    cdp_port = Column(Integer, comment="Chrome DevTools Protocol端口")
    vnc_port = Column(Integer, comment="VNC端口(可选)")
    websockify_port = Column(Integer, comment="WebSocket端口(可选)")
    status = Column(String(20), default="pending", comment="状态: pending/running/stopped/expired")
    login_status = Column(String(20), default="not_logged_in", comment="登录状态: not_logged_in/logged_in/expired")
    browser_profile = Column(String(500), comment="浏览器配置文件路径")
    expires_at = Column(DateTime, comment="过期时间")
    created_at = Column(DateTime, default=datetime.now, comment="创建时间")
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now, comment="更新时间")


# 平台数据库引擎缓存
_platform_engines = {}
_platform_session_factories = {}


def get_platform_session(platform: str) -> Session:
    """获取指定平台的数据库会话（单例模式，复用连接池）"""
    if platform not in _platform_engines:
        settings = get_settings()
        _platform_engines[platform] = create_engine(
            settings.DATABASE_URL.replace("+aiosqlite", "").replace("+asyncpg", ""),
            pool_size=5,
            max_overflow=10,
            pool_pre_ping=True,
            pool_recycle=3600
        )
        Base.metadata.create_all(_platform_engines[platform])
        _platform_session_factories[platform] = sessionmaker(bind=_platform_engines[platform])
    
    return _platform_session_factories[platform]()


def get_session():
    """获取数据库会话（默认抖音平台）"""
    return get_platform_session("douyin")


# 全局数据库引擎和会话工厂（单例模式）
_user_engine = None
_user_session_factory = None


def get_user_session():
    """获取用户数据库会话（单例模式，复用连接池）"""
    global _user_engine, _user_session_factory
    
    if _user_engine is None:
        settings = get_settings()
        # 使用连接池，提高性能
        _user_engine = create_engine(
            settings.DATABASE_URL.replace("+aiosqlite", "").replace("+asyncpg", ""),
            pool_size=10,
            max_overflow=20,
            pool_pre_ping=True,
            pool_recycle=3600
        )
        Base.metadata.create_all(_user_engine)
        _user_session_factory = sessionmaker(bind=_user_engine)
    
    return _user_session_factory()


# 初始化所有平台数据库
def init_all_platforms():
    """初始化所有平台数据库"""
    global _user_engine, _user_session_factory
    
    settings = get_settings()
    _user_engine = create_engine(
        settings.DATABASE_URL.replace("+aiosqlite", "").replace("+asyncpg", ""),
        pool_size=10,
        max_overflow=20,
        pool_pre_ping=True,
        pool_recycle=3600
    )
    Base.metadata.create_all(_user_engine)
    _user_session_factory = sessionmaker(bind=_user_engine)
    return _user_session_factory


init_all_platforms()