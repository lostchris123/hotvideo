"""
浏览器实例池管理器 - 全局内存管理
"""
import asyncio
import psutil
from datetime import datetime, timedelta
from typing import Dict, Optional
from loguru import logger


class BrowserPool:
    """全局浏览器实例池管理器"""
    
    _instance = None
    _initialized = False
    
    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance
    
    def __init__(self, max_instances: int = 5, idle_timeout_minutes: int = 60, memory_threshold: float = 0.8):
        if BrowserPool._initialized:
            return
        BrowserPool._initialized = True
        
        self.max_instances = max_instances
        self.idle_timeout = timedelta(minutes=idle_timeout_minutes)
        self.memory_threshold = memory_threshold
        
        self._instances: Dict[tuple, Dict] = {}
        self._lock = asyncio.Lock()
    
    async def register(self, user_id: int, platform: str, browser, context):
        async with self._lock:
            key = (user_id, platform)
            self._instances[key] = {
                "browser": browser,
                "context": context,
                "last_used": datetime.now(),
                "user_id": user_id,
                "platform": platform
            }
            logger.info(f"[BrowserPool] register: user={user_id}, platform={platform}, total={len(self._instances)}")
    
    async def get(self, user_id: int, platform: str):
        async with self._lock:
            key = (user_id, platform)
            if key in self._instances:
                self._instances[key]["last_used"] = datetime.now()
                return self._instances[key]
            return None
    
    async def unregister(self, user_id: int, platform: str):
        async with self._lock:
            key = (user_id, platform)
            if key in self._instances:
                del self._instances[key]
                logger.info(f"[BrowserPool] unregister: user={user_id}, platform={platform}, total={len(self._instances)}")
    
    async def check_capacity(self) -> bool:
        async with self._lock:
            await self._cleanup_idle()
            
            memory_percent = psutil.virtual_memory().percent / 100
            if memory_percent > self.memory_threshold:
                logger.warning(f"[BrowserPool] memory high: {memory_percent:.1%}")
                await self._cleanup_lru()
            
            if len(self._instances) >= self.max_instances:
                logger.warning(f"[BrowserPool] max instances reached: {self.max_instances}")
                await self._cleanup_lru()
            
            return len(self._instances) < self.max_instances
    
    async def _cleanup_idle(self):
        now = datetime.now()
        to_remove = []
        
        for key, info in self._instances.items():
            if now - info["last_used"] > self.idle_timeout:
                to_remove.append(key)
        
        for key in to_remove:
            await self._close_instance(key, "idle")
    
    async def _cleanup_lru(self):
        if not self._instances:
            return
        oldest_key = min(self._instances.keys(), key=lambda k: self._instances[k]["last_used"])
        await self._close_instance(oldest_key, "LRU")
    
    async def _close_instance(self, key: tuple, reason: str):
        info = self._instances.get(key)
        if not info:
            return
        
        try:
            uid = info.get("user_id")
            plat = info.get("platform")
            logger.info(f"[BrowserPool] close: user={uid}, platform={plat}, reason={reason}")
            
            context = info.get("context")
            browser = info.get("browser")
            
            if context:
                try:
                    await context.close()
                except:
                    pass
            if browser:
                try:
                    await browser.close()
                except:
                    pass
            
            del self._instances[key]
        except Exception as e:
            logger.error(f"[BrowserPool] close failed: {e}")
    
    def get_stats(self) -> Dict:
        memory = psutil.virtual_memory()
        return {
            "total_instances": len(self._instances),
            "max_instances": self.max_instances,
            "memory_percent": memory.percent,
            "memory_available_gb": round(memory.available / (1024**3), 2),
            "instances": [
                {
                    "user_id": info["user_id"],
                    "platform": info["platform"],
                    "last_used": info["last_used"].isoformat()
                }
                for info in self._instances.values()
            ]
        }

    async def cleanup_all(self):
        """清理所有浏览器实例"""
        async with self._lock:
            for key in list(self._instances.keys()):
                try:
                    instance = self._instances[key]
                    if instance.get("browser") and instance["browser"].is_connected():
                        await instance["browser"].close()
                except:
                    pass
            self._instances.clear()


browser_pool = BrowserPool(
    max_instances=10,
    idle_timeout_minutes=60,
    memory_threshold=0.9
)
