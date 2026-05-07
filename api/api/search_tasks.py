"""
异步搜索任务管理器
"""
import asyncio
import json
import time
from datetime import datetime
from typing import Optional, Callable, Dict, Any
from loguru import logger

from crawler.models import SearchTask, get_user_session, PLATFORM_MODELS, get_platform_session
from config import get_settings

MAX_TASK_DURATION = 30 * 60  # 任务最大运行时间：30分钟


class SearchTaskManager:
    """异步搜索任务管理器"""
    
    _instance = None
    _running_tasks: Dict[int, asyncio.Task] = {}
    _cancel_flags: Dict[int, bool] = {}
    _task_start_times: Dict[int, float] = {}
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._cleanup_orphan_tasks()
        return cls._instance
    
    @classmethod
    def _cleanup_orphan_tasks(cls):
        """清理服务重启后遗留的运行中任务"""
        session = get_user_session()
        try:
            orphan_tasks = session.query(SearchTask).filter(
                SearchTask.status == "running"
            ).all()
            for task in orphan_tasks:
                task.status = "interrupted"
                task.error_message = "服务重启，任务被中断，请重新搜索"
                task.completed_at = datetime.now()
                logger.info(f"清理孤儿任务: id={task.id}, keyword={task.keyword}")
            session.commit()
            if orphan_tasks:
                logger.info(f"共清理 {len(orphan_tasks)} 个孤儿任务")
        except Exception as e:
            logger.error(f"清理孤儿任务失败: {e}")
        finally:
            session.close()
    
    def create_task(
        self,
        user_id: int,
        keyword: str,
        platform: str,
        content_type: str = "video",
        limit: int = 20,
        min_likes: int = 0
    ) -> SearchTask:
        """创建搜索任务"""
        session = get_user_session()
        try:
            task = SearchTask(
                user_id=user_id,
                keyword=keyword,
                platform=platform,
                content_type=content_type,
                limit=limit,
                min_likes=min_likes,
                status="pending",
                progress=json.dumps({
                    "stage": "pending",
                    "collected_links": 0,
                    "visited_pages": 0,
                    "qualified_count": 0,
                    "message": "任务已创建，等待执行"
                })
            )
            session.add(task)
            session.commit()
            session.refresh(task)
            logger.info(f"创建搜索任务: id={task.id}, keyword={keyword}, platform={platform}")
            return task
        finally:
            session.close()
    
    def start_task(self, task_id: int, crawler_func: Callable, **kwargs) -> bool:
        """启动异步任务"""
        session = get_user_session()
        try:
            task = session.query(SearchTask).filter_by(id=task_id).first()
            if not task:
                logger.error(f"任务不存在: {task_id}")
                return False
            
            if task.status != "pending":
                logger.warning(f"任务状态不是pending: {task.status}")
                return False
            
            task.status = "running"
            task.started_at = datetime.now()
            session.commit()
            
            self._cancel_flags[task_id] = False
            self._task_start_times[task_id] = time.time()
            
            async def run_task():
                try:
                    await asyncio.wait_for(
                        self._run_search_task(task_id, crawler_func, **kwargs),
                        timeout=MAX_TASK_DURATION
                    )
                except asyncio.TimeoutError:
                    logger.error(f"任务超时: {task_id}")
                    self._update_task_status(
                        task_id, 
                        "failed", 
                        f"任务超时（超过{MAX_TASK_DURATION//60}分钟），请缩小搜索范围后重试"
                    )
                    self.cleanup_task(task_id)
                except Exception as e:
                    logger.error(f"任务执行异常: {task_id}, {e}")
                    self._update_task_status(task_id, "failed", str(e))
                    self.cleanup_task(task_id)
            
            self._running_tasks[task_id] = asyncio.create_task(run_task())
            logger.info(f"任务已启动: {task_id}")
            return True
        finally:
            session.close()
    
    async def _run_search_task(
        self,
        task_id: int,
        crawler_func: Callable,
        **kwargs
    ):
        """执行搜索任务"""
        qualified_videos = []
        
        def progress_callback(progress_data: Dict[str, Any]):
            """进度回调函数"""
            self._update_task_progress(task_id, progress_data)
        
        def cancel_check():
            """取消检查函数"""
            return self._cancel_flags.get(task_id, False)
        
        try:
            videos = await crawler_func(
                progress_callback=progress_callback,
                cancel_check=lambda: self._cancel_flags.get(task_id, False),
                **kwargs
            )
            
            if self._cancel_flags.get(task_id, False):
                self._update_task_status(task_id, "cancelled", "用户取消")
                return
            
            qualified_videos = videos if videos else []
            
            session = get_user_session()
            try:
                task = session.query(SearchTask).filter_by(id=task_id).first()
                if task:
                    task.status = "completed"
                    task.result_count = len(qualified_videos)
                    task.completed_at = datetime.now()
                    task.progress = json.dumps({
                        "stage": "completed",
                        "collected_links": 0,
                        "visited_pages": 0,
                        "qualified_count": len(qualified_videos),
                        "message": f"搜索完成，找到 {len(qualified_videos)} 个符合条件的内容"
                    })
                    session.commit()
            finally:
                session.close()
            
            logger.info(f"任务完成: {task_id}, 结果数: {len(qualified_videos)}")
            
        except asyncio.CancelledError:
            # 任务被取消
            logger.info(f"任务被取消: {task_id}")
            self._update_task_status(task_id, "cancelled", "用户取消")
            
        except Exception as e:
            logger.error(f"任务执行失败: {task_id}, {e}")
            self._update_task_status(task_id, "failed", str(e))
    
    def _update_task_progress(self, task_id: int, progress_data: Dict[str, Any]):
        """更新任务进度"""
        session = get_user_session()
        try:
            task = session.query(SearchTask).filter_by(id=task_id).first()
            if task:
                task.progress = json.dumps(progress_data, ensure_ascii=False)
                session.commit()
        except Exception as e:
            logger.warning(f"更新任务进度失败: {task_id}, {e}")
        finally:
            session.close()
    
    def _update_task_status(self, task_id: int, status: str, error_message: str = None):
        """更新任务状态"""
        session = get_user_session()
        try:
            task = session.query(SearchTask).filter_by(id=task_id).first()
            if task:
                task.status = status
                if error_message:
                    task.error_message = error_message
                if status in ["completed", "failed", "cancelled"]:
                    task.completed_at = datetime.now()
                session.commit()
        except Exception as e:
            logger.error(f"更新任务状态失败: {task_id}, {e}")
        finally:
            session.close()
    
    def get_task(self, task_id: int) -> Optional[Dict[str, Any]]:
        """获取任务信息"""
        session = get_user_session()
        try:
            task = session.query(SearchTask).filter_by(id=task_id).first()
            if not task:
                return None
            
            return {
                "id": task.id,
                "user_id": task.user_id,
                "keyword": task.keyword,
                "platform": task.platform,
                "content_type": task.content_type,
                "limit": task.limit,
                "min_likes": task.min_likes,
                "status": task.status,
                "progress": json.loads(task.progress) if task.progress else {},
                "result_count": task.result_count,
                "error_message": task.error_message,
                "created_at": task.created_at.isoformat() if task.created_at else None,
                "started_at": task.started_at.isoformat() if task.started_at else None,
                "completed_at": task.completed_at.isoformat() if task.completed_at else None,
            }
        finally:
            session.close()
    
    def get_user_tasks(self, user_id: int, limit: int = 20, offset: int = 0) -> list:
        """获取用户任务列表"""
        session = get_user_session()
        try:
            tasks = session.query(SearchTask).filter_by(user_id=user_id).order_by(
                SearchTask.created_at.desc()
            ).offset(offset).limit(limit).all()
            
            return [
                {
                    "id": t.id,
                    "keyword": t.keyword,
                    "platform": t.platform,
                    "content_type": t.content_type,
                    "status": t.status,
                    "result_count": t.result_count,
                    "created_at": t.created_at.isoformat() if t.created_at else None,
                    "completed_at": t.completed_at.isoformat() if t.completed_at else None,
                }
                for t in tasks
            ]
        finally:
            session.close()
    
    def cancel_task(self, task_id: int, user_id: int = None) -> bool:
        """取消任务"""
        session = get_user_session()
        try:
            task = session.query(SearchTask).filter_by(id=task_id).first()
            if not task:
                return False
            
            if task.status not in ["pending", "running"]:
                return False
            
            # 1. 设置取消标志
            self._cancel_flags[task_id] = True
            
            # 2. 真正取消 asyncio 任务
            if task_id in self._running_tasks:
                running_task = self._running_tasks[task_id]
                if running_task and not running_task.done():
                    running_task.cancel()
                    logger.info(f"已发送取消信号到异步任务: {task_id}")
            
            task.status = "cancelled"
            task.completed_at = datetime.now()
            session.commit()
            
            # 清理任务资源
            self.cleanup_task(task_id)
            
            logger.info(f"任务已取消: {task_id}")
            return True
        finally:
            session.close()
    
    def has_running_task(self, user_id: int) -> bool:
        """检查用户是否有正在运行的任务"""
        session = get_user_session()
        try:
            running = session.query(SearchTask).filter_by(
                user_id=user_id,
                status="running"
            ).first()
            return running is not None
        finally:
            session.close()
    
    def get_running_task(self, user_id: int) -> Optional[Dict[str, Any]]:
        """获取用户正在运行的任务"""
        session = get_user_session()
        try:
            task = session.query(SearchTask).filter_by(
                user_id=user_id,
                status="running"
            ).first()
            if task:
                return self.get_task(task.id)
            return None
        finally:
            session.close()
    
    def cleanup_task(self, task_id: int):
        """清理任务资源"""
        if task_id in self._running_tasks:
            del self._running_tasks[task_id]
        if task_id in self._cancel_flags:
            del self._cancel_flags[task_id]
        if task_id in self._task_start_times:
            del self._task_start_times[task_id]
    
    def reset_stuck_tasks(self, user_id: int) -> int:
        """重置用户所有卡住的任务，返回重置数量"""
        session = get_user_session()
        try:
            stuck_tasks = session.query(SearchTask).filter(
                SearchTask.user_id == user_id,
                SearchTask.status.in_(["running", "pending"])
            ).all()
            
            count = 0
            for task in stuck_tasks:
                task.status = "interrupted"
                task.error_message = "用户手动中断"
                task.completed_at = datetime.now()
                self.cleanup_task(task.id)
                count += 1
                logger.info(f"重置卡住的任务: id={task.id}, keyword={task.keyword}")
            
            session.commit()
            return count
        finally:
            session.close()


_task_manager: Optional[SearchTaskManager] = None


def get_task_manager() -> SearchTaskManager:
    """获取任务管理器单例"""
    global _task_manager
    if _task_manager is None:
        _task_manager = SearchTaskManager()
    return _task_manager