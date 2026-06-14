"""
增强版视觉分析器 - 集成提示词生成
"""

import os
import json
import logging
from typing import Dict, List, Optional
from analyzer.visual_analyzer_v11 import VisualAnalyzerV11
from analyzer.prompt_generator import PromptGenerator

logger = logging.getLogger(__name__)


class VisualAnalyzerWithPrompts(VisualAnalyzerV11):
    """集成提示词生成的视觉分析器"""
    
    def __init__(self):
        super().__init__()
        self.prompt_generator = PromptGenerator()
    
    async def analyze_and_generate_prompts(
        self,
        video_path: str,
        video_id: str,
        platform: str = "douyin",
        num_frames: int = 8,
        strategy: str = "smart"
    ) -> Dict:
        """
        执行视觉分析并生成提示词
        
        Args:
            video_path: 视频文件路径
            video_id: 视频ID
            platform: 平台
            num_frames: 截帧数量
            strategy: 截帧策略
        
        Returns:
            包含视觉分析结果和提示词的字典
        """
        
        # 1. 执行视觉分析
        analysis_result = await self.analyze_video(
            video_path=video_path,
            num_frames=num_frames,
            strategy=strategy
        )
        
        if "error" in analysis_result:
            return analysis_result
        
        # 2. 提取分析结果
        visual_description = analysis_result.get("visual_description", "")
        viral_points = analysis_result.get("viral_points", [])
        visual_hooks = analysis_result.get("visual_hooks", [])
        content_hooks = analysis_result.get("content_hooks", [])
        target_audience = analysis_result.get("target_audience", "普通用户")
        
        # 3. 生成提示词
        try:
            prompts = self.prompt_generator.generate_all_prompts(
                visual_description=visual_description,
                viral_points=viral_points,
                visual_hooks=visual_hooks,
                content_hooks=content_hooks,
                target_audience=target_audience
            )
            
            # 添加到返回结果
            analysis_result["prompts"] = prompts
            analysis_result["prompts_generated"] = True
            logger.info(f"视频 {video_id} 提示词生成成功，共 {len(prompts)} 种类型")
            
        except Exception as e:
            logger.error(f"视频 {video_id} 提示词生成失败: {e}", exc_info=True)
            analysis_result["prompts"] = {}
            analysis_result["prompts_generated"] = False
            analysis_result["prompts_error"] = str(e)
        
        return analysis_result
