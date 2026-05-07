"""
配置管理模块
"""
from __future__ import annotations
from pathlib import Path
from typing import Optional
from pydantic_settings import BaseSettings, SettingsConfigDict
from functools import lru_cache


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        extra="ignore",
        env_file=".env",
        env_file_encoding="utf-8",
    )
    """应用配置"""
    
    # 项目路径
    BASE_DIR: Path = Path(__file__).parent.parent
    DATA_DIR: Path = BASE_DIR / "data"
    VIDEOS_DIR: Path = DATA_DIR / "videos"
    AUDIO_DIR: Path = DATA_DIR / "audio"
    OUTPUT_DIR: Path = DATA_DIR / "output"
    THUMBNAIL_DIR: Path = DATA_DIR / "thumbnails"
    LOGS_DIR: Path = DATA_DIR / "logs"
    BROWSER_DATA_DIR: Path = DATA_DIR / "browser_profiles"
    
    # Playwright 配置
    HEADLESS: bool = False  # 是否无头模式
    PROXY: Optional[str] = None  # 代理地址
    USER_DATA_DIR: Optional[str] = None  # 浏览器用户数据目录（已废弃，使用平台隔离）
    
    # VNC 配置
    HOST_IP: str = "localhost"
    VNC_PORT_OFFSET: int = 20000
    
    def get_browser_profile_dir(self, platform: str, user_id: int = None) -> Path:
        """获取浏览器配置目录（支持用户级隔离）
        
        Args:
            platform: 平台名称 (douyin/xiaohongshu/shipinhao)
            user_id: 用户ID（None表示共享目录）
        """
        if user_id is None:
            profile_dir = self.BROWSER_DATA_DIR / "shared" / platform
        else:
            profile_dir = self.BROWSER_DATA_DIR / f"user_{user_id}" / platform
        profile_dir.mkdir(parents=True, exist_ok=True)
        return profile_dir
    
    def get_user_cdp_port(self, user_id: int, platform: str) -> int:
        """计算用户专属CDP端口（固定映射）
        
        Args:
            user_id: 用户ID
            platform: 平台名称
            
        Returns:
            CDP调试端口
        """
        PLATFORM_OFFSET = {"douyin": 0, "xiaohongshu": 1, "shipinhao": 2}
        base_port = 9222 + (user_id - 1) * 3  # 每用户3个端口
        return base_port + PLATFORM_OFFSET.get(platform, 0)
    
    # ASR 配置
    ASR_ENGINE: str = "siliconflow"  # whisper / sensevoice / siliconflow
    WHISPER_MODEL: str = "medium"  # tiny/base/small/medium/large-v3
    WHISPER_DEVICE: str = "cpu"  # cuda / cpu
    
    # SenseVoice 配置
    SENSEVOICE_MODEL: str = "iic/SenseVoiceSmall"
    SENSEVOICE_DEVICE: str = "cpu"  # cuda / cpu
    
    # SiliconFlow 配置（云端语音识别 API）
    SILICONFLOW_API_KEY: str = ""
    SILICONFLOW_MODEL: str = "FunAudioLLM/SenseVoiceSmall"
    SILICONFLOW_BASE_URL: str = "https://api.siliconflow.cn/v1/audio/transcriptions"
    SILICONFLOW_TIMEOUT: int = 120  # 超时时间（秒）
    
    # LLM 配置 (可选)
    LLM_PROVIDER: str = "dashscope"  # openai / dashscope / paddleocr
    OPENAI_API_KEY: Optional[str] = None
    OPENAI_BASE_URL: str = "https://api.openai.com/v1"
    DASHSCOPE_API_KEY: Optional[str] = None
    
    # 智谱AI配置 (用于AI创作功能)
    ZHIPU_API_KEY: str = ""
    ZHIPU_BASE_URL: str = "https://open.bigmodel.cn/api/paas/v4"
    ZHIPU_MODEL: str = "GLM-4.1V-Thinking-Flash"
    
    # PaddleOCR-VL 配置
    PADDLEOCR_MODEL: str = "PaddleOCR/PaddleOCR-VL-1.5"  # ModelScope模型ID
    PADDLEOCR_DEVICE: str = "cpu"  # cuda / cpu
    
    # API 配置
    API_HOST: str = "0.0.0.0"
    API_PORT: int = 8000
    DEBUG: bool = True
    
    # 数据库配置
    DATABASE_URL: str = "sqlite+aiosqlite:///./data/douyin.db"
    


@lru_cache
def get_settings() -> Settings:
    """获取配置单例"""
    return Settings()


# 创建数据目录
def init_dirs():
    """初始化数据目录"""
    settings = get_settings()
    for dir_path in [settings.DATA_DIR, settings.VIDEOS_DIR, 
                     settings.AUDIO_DIR, settings.OUTPUT_DIR, 
                     settings.THUMBNAIL_DIR, settings.LOGS_DIR]:
        dir_path.mkdir(parents=True, exist_ok=True)


init_dirs()
