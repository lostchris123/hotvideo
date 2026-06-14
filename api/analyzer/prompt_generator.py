"""
多类型提示词生成器
根据视频分析结果生成4种类型的提示词：
1. 视频脚本提示词（script）- 完整的分镜脚本
2. 视频风格提示词（style）- 视觉风格描述
3. 场景提示词（scene）- 场景布置、道具等
4. 人物形象提示词（character）- 角色造型、服装等
"""

import os
import json
import logging
from typing import Dict, List, Optional
from zhipuai import ZhipuAI

logger = logging.getLogger(__name__)


class PromptGenerator:
    """提示词生成器"""
    
    def __init__(self):
        self.api_key = os.getenv("ZHIPU_API_KEY")
        self.base_url = os.getenv("ZHIPU_BASE_URL", "https://open.bigmodel.cn/api/paas/v4")
        self.model = os.getenv("ZHIPU_MODEL", "glm-4-flash")  # 文本生成用 glm-4-flash
        
        if self.api_key:
            self.client = ZhipuAI(api_key=self.api_key, base_url=self.base_url)
        else:
            self.client = None
            logger.warning("ZHIPU_API_KEY not set, prompt generation will be disabled")
    
    def generate_all_prompts(
        self, 
        visual_description: str,
        viral_points: List[str],
        visual_hooks: List[str],
        content_hooks: List[str],
        target_audience: str,
        transcript: Optional[str] = None,
        duration: Optional[float] = None
    ) -> Dict[str, str]:
        """
        生成所有类型的提示词
        
        Args:
            visual_description: 视觉描述
            viral_points: 爆点列表
            visual_hooks: 视觉钩子
            content_hooks: 内容钩子
            target_audience: 目标受众
            transcript: 文案内容（可选）
            duration: 视频时长（可选）
        
        Returns:
            Dict[str, str]: 四种类型的提示词 {"script": "...", "style": "...", "scene": "...", "character": "..."}
        """
        
        if not self.client:
            logger.warning("提示词生成功能未启用，请配置 ZHIPU_API_KEY")
            return {}
        
        prompts = {}
        
        # 1. 生成视频脚本提示词
        try:
            prompts["script"] = self._generate_script_prompt(
                visual_description, viral_points, content_hooks, target_audience, transcript, duration
            )
        except Exception as e:
            logger.error(f"生成视频脚本提示词失败: {e}")
            prompts["script"] = ""
        
        # 2. 生成视频风格提示词
        try:
            prompts["style"] = self._generate_style_prompt(visual_description, visual_hooks)
        except Exception as e:
            logger.error(f"生成视频风格提示词失败: {e}")
            prompts["style"] = ""
        
        # 3. 生成场景提示词
        try:
            prompts["scene"] = self._generate_scene_prompt(visual_description, viral_points)
        except Exception as e:
            logger.error(f"生成场景提示词失败: {e}")
            prompts["scene"] = ""
        
        # 4. 生成人物形象提示词
        try:
            prompts["character"] = self._generate_character_prompt(visual_description, target_audience)
        except Exception as e:
            logger.error(f"生成人物形象提示词失败: {e}")
            prompts["character"] = ""
        
        return prompts
    
    def _generate_script_prompt(
        self,
        visual_description: str,
        viral_points: List[str],
        content_hooks: List[str],
        target_audience: str,
        transcript: Optional[str],
        duration: Optional[float]
    ) -> str:
        """生成视频脚本提示词"""
        
        duration_text = f"视频时长约{int(duration)}秒" if duration else ""
        transcript_section = f"\n原视频文案：\n{transcript}\n" if transcript else ""
        
        prompt = f"""你是一个专业的短视频脚本策划师。基于以下爆款视频分析，生成一份完整的视频拍摄脚本提示词。

{duration_text}
目标受众：{target_audience}

视觉内容分析：
{visual_description}

核心爆点：
{chr(10).join([f'- {point}' for point in viral_points])}

内容钩子：
{chr(10).join([f'- {hook}' for hook in content_hooks])}
{transcript_section}

请生成一份详细的视频脚本提示词，包含：
1. 开场设计（前3秒如何抓住注意力）
2. 分镜脚本（按时间轴列出每个镜头的拍摄要点）
3. 台词/旁白建议（适合目标受众的语言风格）
4. 节奏把控（哪些地方需要加速/放慢）
5. 结尾设计（如何引导互动）

要求：
- 提示词应直接可用于AI视频生成工具（如Sora、Runway等）
- 保持原视频的爆款逻辑
- 适合{target_audience}的审美和接受度
- 总字数300-500字

请直接输出提示词内容，不要有开场白。"""
        
        response = self.client.chat.completions.create(
            model=self.model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.8,
            max_tokens=800
        )
        
        return response.choices[0].message.content.strip()
    
    def _generate_style_prompt(
        self,
        visual_description: str,
        visual_hooks: List[str]
    ) -> str:
        """生成视频风格提示词"""
        
        prompt = f"""你是一个专业的视频视觉设计师。基于以下视频分析，生成一份视觉风格提示词。

视觉内容：
{visual_description}

视觉钩子：
{chr(10).join([f'- {hook}' for hook in visual_hooks])}

请生成一份详细的视觉风格提示词，包含：
1. 整体风格定位（如：日系清新、赛博朋克、复古怀旧等）
2. 色彩搭配方案（主色调、辅色调、对比色）
3. 光影效果（自然光/人工光、光比、色调）
4. 镜头语言（景别、角度、运镜方式）
5. 特效建议（转场、滤镜、动态效果）
6. 参考风格（类似的电影/广告/艺术作品）

要求：
- 提示词应适合AI图像/视频生成工具
- 具体描述视觉元素和氛围
- 总字数200-300字

请直接输出提示词内容，不要有开场白。"""
        
        response = self.client.chat.completions.create(
            model=self.model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.8,
            max_tokens=600
        )
        
        return response.choices[0].message.content.strip()
    
    def _generate_scene_prompt(
        self,
        visual_description: str,
        viral_points: List[str]
    ) -> str:
        """生成场景提示词"""
        
        prompt = f"""你是一个专业的场景设计师。基于以下视频分析，生成一份场景布置提示词。

视觉内容：
{visual_description}

核心爆点：
{chr(10).join([f'- {point}' for point in viral_points])}

请生成一份详细的场景提示词，包含：
1. 场景类型（室内/室外、具体场所）
2. 空间布局（前景、中景、背景的元素安排）
3. 道具清单（关键道具及其摆放位置）
4. 环境氛围（天气、时间段、氛围感）
5. 拍摄机位（推荐的角度和位置）
6. 可替代方案（低成本/易获取的场景选择）

要求：
- 提示词应具体可执行
- 考虑成本可控和易实现性
- 总字数200-300字

请直接输出提示词内容，不要有开场白。"""
        
        response = self.client.chat.completions.create(
            model=self.model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.8,
            max_tokens=600
        )
        
        return response.choices[0].message.content.strip()
    
    def _generate_character_prompt(
        self,
        visual_description: str,
        target_audience: str
    ) -> str:
        """生成人物形象提示词"""
        
        prompt = f"""你是一个专业的形象设计师。基于以下视频分析，生成一份人物形象提示词。

视觉内容：
{visual_description}

目标受众：{target_audience}

请生成一份详细的人物形象提示词，包含：
1. 人物类型（年龄、性别、职业/身份）
2. 外貌特征（发型、妆容、五官特点）
3. 服装搭配（上装、下装、鞋子、配饰）
4. 肢体语言（站姿、手势、表情）
5. 整体气质（关键词描述）
6. 可替代方案（适合不同预算的造型建议）

要求：
- 提示词应适合AI图像生成工具
- 符合目标受众{target_audience}的审美
- 总字数200-300字

请直接输出提示词内容，不要有开场白。"""
        
        response = self.client.chat.completions.create(
            model=self.model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.8,
            max_tokens=600
        )
        
        return response.choices[0].message.content.strip()
    
    def regenerate_prompt(
        self,
        prompt_type: str,
        visual_description: str,
        viral_points: List[str],
        visual_hooks: List[str],
        content_hooks: List[str],
        target_audience: str,
        transcript: Optional[str] = None,
        duration: Optional[float] = None
    ) -> str:
        """重新生成指定类型的提示词"""
        
        generators = {
            "script": self._generate_script_prompt,
            "style": self._generate_style_prompt,
            "scene": self._generate_scene_prompt,
            "character": self._generate_character_prompt
        }
        
        if prompt_type not in generators:
            raise ValueError(f"不支持的提示词类型: {prompt_type}")
        
        generator = generators[prompt_type]
        
        if prompt_type == "script":
            return generator(visual_description, viral_points, content_hooks, target_audience, transcript, duration)
        elif prompt_type == "style":
            return generator(visual_description, visual_hooks)
        elif prompt_type == "scene":
            return generator(visual_description, viral_points)
        elif prompt_type == "character":
            return generator(visual_description, target_audience)
        
        return ""
