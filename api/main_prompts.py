# 提示词相关API

from fastapi import HTTPException, Depends
from typing import Optional
from analyzer.prompt_generator import PromptGenerator
from crawler.models_prompts import VideoPrompt, PROMPT_TYPES

# 初始化提示词生成器
prompt_generator = PromptGenerator()


def register_prompt_routes(app, User, get_current_user, DouyinVideoRecord, XiaohongshuVideoRecord, ShipinhaoVideoRecord, json, logger):
    """注册提示词相关路由"""
    
    @app.post("/api/videos/{video_id}/generate-prompts")
    async def generate_prompts(
        video_id: str,
        platform: str = "douyin",
        current_user: User = Depends(get_current_user)
    ):
        from crawler.models import get_user_session
        session = get_user_session()
        
        try:
            # 根据平台选择模型
            if platform == "douyin":
                VideoRecord = DouyinVideoRecord
            elif platform == "xiaohongshu":
                VideoRecord = XiaohongshuVideoRecord
            elif platform == "shipinhao":
                VideoRecord = ShipinhaoVideoRecord
            else:
                raise HTTPException(status_code=400, detail=f"Unsupported platform: {platform}")
            
            # 查询视频记录
            video = session.query(VideoRecord).filter(VideoRecord.video_id == video_id).first()
            if not video:
                raise HTTPException(status_code=404, detail="Video not found")
            
            if not video.visual_description:
                raise HTTPException(status_code=400, detail="Please perform visual analysis first")
            
            # 解析JSON字段
            viral_points = json.loads(video.viral_points) if video.viral_points else []
            visual_hooks = json.loads(video.visual_hooks) if video.visual_hooks else []
            content_hooks = json.loads(video.content_hooks) if video.content_hooks else []
            
            # 生成提示词
            prompts = prompt_generator.generate_all_prompts(
                visual_description=video.visual_description,
                viral_points=viral_points,
                visual_hooks=visual_hooks,
                content_hooks=content_hooks,
                target_audience=video.target_audience or "ordinary users",
                transcript=video.transcript,
                duration=video.duration
            )
            
            # 保存到数据库
            saved_prompts = []
            for prompt_type, prompt_content in prompts.items():
                if prompt_content:
                    prompt_record = VideoPrompt(
                        video_id=video_id,
                        platform=platform,
                        prompt_type=prompt_type,
                        prompt_content=prompt_content,
                        version=1
                    )
                    session.add(prompt_record)
                    
                    saved_prompts.append({
                        "type": prompt_type,
                        "type_name": PROMPT_TYPES.get(prompt_type, prompt_type),
                        "content": prompt_content,
                        "version": 1
                    })
            
            session.commit()
            
            return {
                "success": True,
                "message": "Prompts generated successfully",
                "prompts": saved_prompts
            }
            
        except Exception as e:
            session.rollback()
            logger.error(f"Failed to generate prompts: {e}", exc_info=True)
            raise HTTPException(status_code=500, detail=f"Failed to generate prompts: {str(e)}")
        finally:
            session.close()
    
    @app.get("/api/prompt-types")
    async def get_all_prompt_types():
        return {
            "success": True,
            "prompt_types": PROMPT_TYPES
        }
    
    @app.get("/api/videos/{video_id}/prompts")
    async def get_prompts(
        video_id: str,
        platform: str = "douyin",
        prompt_type: Optional[str] = None,
        current_user: User = Depends(get_current_user)
    ):
        from crawler.models import get_user_session
        session = get_user_session()
        
        try:
            query = session.query(VideoPrompt).filter(
                VideoPrompt.video_id == video_id,
                VideoPrompt.platform == platform
            )
            
            if prompt_type:
                query = query.filter(VideoPrompt.prompt_type == prompt_type)
            
            prompts = query.order_by(VideoPrompt.prompt_type, VideoPrompt.version.desc()).all()
            
            return {
                "success": True,
                "prompts": [p.to_dict() for p in prompts],
                "total": len(prompts)
            }
        finally:
            session.close()
    
    return app
