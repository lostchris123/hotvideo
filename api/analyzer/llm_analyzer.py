"""
LLM 文案分析模块
"""
from __future__ import annotations
from typing import Optional, List, Dict
from dataclasses import dataclass
from loguru import logger

from config import get_settings


@dataclass
class AnalysisResult:
    """分析结果"""
    summary: str  # 文案摘要
    selling_points: List[str]  # 卖点
    emotions: List[str]  # 情绪触发点
    structure: str  # 结构特点
    template: str  # 可复用模板
    suggestions: List[str]  # 优化建议


@dataclass
class ViralAnalysisResult:
    """爆款视频分析结果"""
    overall_score: int  # 综合评分(0-100)
    viral_factors: Dict[str, List[str]]  # 爆款因素
    content_analysis: Dict[str, str]  # 内容分析
    title_analysis: Dict[str, str]  # 标题分析
    tag_analysis: Dict[str, str]  # 标签分析
    transcript_analysis: Dict[str, str]  # 文案分析
    interaction_analysis: Dict[str, str]  # 互动数据分析
    improvement_suggestions: List[str]  # 改进建议
    success_formula: str  # 成功公式总结


class LLMAnalyzer:
    """LLM 文案分析器"""
    
    def __init__(
        self,
        provider: str = None,
        api_key: str = None,
        base_url: str = None,
    ):
        settings = get_settings()
        self.provider = provider or settings.LLM_PROVIDER
        self.api_key = api_key or settings.OPENAI_API_KEY
        self.base_url = base_url or settings.OPENAI_BASE_URL
        self._client = None
    
    def _get_client(self):
        """获取 LLM 客户端"""
        if self._client:
            return self._client
        
        if self.provider == "openai":
            from openai import OpenAI
            self._client = OpenAI(
                api_key=self.api_key,
                base_url=self.base_url,
            )
        elif self.provider == "dashscope":
            import dashscope
            dashscope.api_key = self.api_key
            self._client = dashscope
        
        return self._client
    
    def analyze_copywriting(self, text: str) -> AnalysisResult:
        """
        分析爆款文案特点
        
        Args:
            text: 文案文本
        
        Returns:
            AnalysisResult
        """
        prompt = f"""你是一个专业的短视频文案分析师。请分析以下爆款视频文案的特点：

文案内容：
{text}

请从以下几个维度进行分析：

1. **核心卖点**：文案主要在推销什么？核心价值主张是什么？
2. **情绪触发点**：文案触发了哪些情绪？如何触发的？
3. **结构特点**：文案的结构是什么样的？（开头、中间、结尾）
4. **可复用模板**：提取一个可复用的文案模板
5. **优化建议**：如何让这个文案更好？

请以 JSON 格式返回结果：
{{
    "summary": "一句话总结",
    "selling_points": ["卖点1", "卖点2"],
    "emotions": ["情绪1", "情绪2"],
    "structure": "结构描述",
    "template": "可复用的模板",
    "suggestions": ["建议1", "建议2"]
}}
"""
        
        try:
            client = self._get_client()
            
            if self.provider == "openai":
                response = client.chat.completions.create(
                    model="gpt-4o-mini",
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0.7,
                )
                result_text = response.choices[0].message.content
            else:
                # 其他 provider 的处理
                result_text = ""
            
            # 解析结果
            import json
            result = json.loads(result_text)
            
            return AnalysisResult(
                summary=result.get("summary", ""),
                selling_points=result.get("selling_points", []),
                emotions=result.get("emotions", []),
                structure=result.get("structure", ""),
                template=result.get("template", ""),
                suggestions=result.get("suggestions", []),
            )
            
        except Exception as e:
            logger.error(f"LLM 分析失败: {e}")
            return AnalysisResult(
                summary="",
                selling_points=[],
                emotions=[],
                structure="",
                template="",
                suggestions=[],
            )
    
    def analyze_viral_video(
        self,
        title: str,
        description: str,
        transcript: str,
        tags: List[str],
        likes: int,
        comments: int,
        shares: int,
        collects: int,
        author_name: str = "",
        fans_count: str = "0",
    ) -> ViralAnalysisResult:
        """
        爆款视频多维度分析
        
        Args:
            title: 视频标题/文案
            description: 视频描述
            transcript: 视频文案（语音转文字）
            tags: 标签列表
            likes: 点赞数
            comments: 评论数
            shares: 分享数
            collects: 收藏数
            author_name: 作者名称
            fans_count: 粉丝数
            
        Returns:
            ViralAnalysisResult
        """
        prompt = f"""你是一个专业的短视频数据分析师，擅长分析爆款视频的传播机制。请对以下爆款视频进行全面分析：

## 视频基本信息
- 作者：{author_name}（粉丝数：{fans_count}）
- 标题/文案：{title}
- 描述：{description}
- 标签：{', '.join(tags) if tags else '无'}

## 视频文案（语音内容）
{transcript if transcript else '暂无文案内容'}

## 互动数据
- 点赞：{likes:,}
- 评论：{comments:,}
- 分享：{shares:,}
- 收藏：{collects:,}

---

请从以下维度深入分析这个视频为什么能火：

### 1. 内容价值分析
- 信息价值：是否提供了实用信息或知识？
- 情感价值：是否触发了特定情绪？
- 娱乐价值：是否有趣、好笑、令人放松？

### 2. 标题/文案分析
- 吸引力：标题是否足够吸引人？
- 痛点：是否击中目标用户的痛点？
- 好奇心：是否激发用户好奇心？

### 3. 标签策略分析
- 热度：是否使用了热门标签？
- 精准度：标签是否精准匹配内容？
- 流量：标签组合是否能带来流量？

### 4. 文案技巧分析
- 开头：前3秒是否足够吸引人？
- 节奏：文案节奏是否紧凑？
- 金句：是否有让人记住的金句？
- 引导：是否有明确的行动引导？

### 5. 互动设计分析
- 互动点：是否设计了互动环节？
- 评论引导：是否引导用户评论？
- 分享动机：是否让用户有分享冲动？

### 6. 爆款因素总结
- 核心原因：这个视频火的核心原因是什么？
- 可复制点：哪些是可以复制的？
- 风险提示：是否存在潜在风险？

---

请以 JSON 格式返回分析结果：
{{
    "overall_score": 85,
    "viral_factors": {{
        "content_factors": ["因素1", "因素2"],
        "title_factors": ["因素1", "因素2"],
        "tag_factors": ["因素1", "因素2"],
        "transcript_factors": ["因素1", "因素2"],
        "interaction_factors": ["因素1", "因素2"]
    }},
    "content_analysis": {{
        "information_value": "分析内容",
        "emotional_value": "分析内容",
        "entertainment_value": "分析内容"
    }},
    "title_analysis": {{
        "attractiveness": "分析内容",
        "pain_point": "分析内容",
        "curiosity": "分析内容"
    }},
    "tag_analysis": {{
        "popularity": "分析内容",
        "accuracy": "分析内容",
        "traffic_potential": "分析内容"
    }},
    "transcript_analysis": {{
        "opening": "分析内容",
        "rhythm": "分析内容",
        "golden_sentences": "分析内容",
        "call_to_action": "分析内容"
    }},
    "interaction_analysis": {{
        "interaction_design": "分析内容",
        "comment_trigger": "分析内容",
        "share_motivation": "分析内容"
    }},
    "improvement_suggestions": [
        "建议1",
        "建议2",
        "建议3"
    ],
    "success_formula": "总结这个视频的成功公式"
}}
"""
        
        try:
            client = self._get_client()
            
            if self.provider == "openai":
                response = client.chat.completions.create(
                    model="gpt-4o-mini",
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0.7,
                )
                result_text = response.choices[0].message.content
                
                # 清理可能的markdown代码块标记
                if result_text.startswith("```json"):
                    result_text = result_text[7:]
                if result_text.startswith("```"):
                    result_text = result_text[3:]
                if result_text.endswith("```"):
                    result_text = result_text[:-3]
                result_text = result_text.strip()
                
                # 解析结果
                import json
                result = json.loads(result_text)
                
                return ViralAnalysisResult(
                    overall_score=result.get("overall_score", 0),
                    viral_factors=result.get("viral_factors", {}),
                    content_analysis=result.get("content_analysis", {}),
                    title_analysis=result.get("title_analysis", {}),
                    tag_analysis=result.get("tag_analysis", {}),
                    transcript_analysis=result.get("transcript_analysis", {}),
                    interaction_analysis=result.get("interaction_analysis", {}),
                    improvement_suggestions=result.get("improvement_suggestions", []),
                    success_formula=result.get("success_formula", ""),
                )
            else:
                return self._get_empty_viral_result()
                
        except Exception as e:
            logger.error(f"爆款视频分析失败: {e}")
            return self._get_empty_viral_result()
    
    def _get_empty_viral_result(self) -> ViralAnalysisResult:
        """返回空的爆款分析结果"""
        return ViralAnalysisResult(
            overall_score=0,
            viral_factors={},
            content_analysis={},
            title_analysis={},
            tag_analysis={},
            transcript_analysis={},
            interaction_analysis={},
            improvement_suggestions=[],
            success_formula="",
        )
    
    def generate_similar_copy(self, template: str, topic: str) -> str:
        """
        根据模板生成类似文案
        
        Args:
            template: 文案模板
            topic: 新的主题
        
        Returns:
            生成的文案
        """
        prompt = f"""根据以下文案模板，为「{topic}」主题生成一个类似的爆款文案：

模板：
{template}

要求：
1. 保持模板的结构和节奏
2. 突出「{topic}」的核心卖点
3. 语言自然、有感染力
4. 适合短视频口播

请直接输出生成的文案：
"""
        
        try:
            client = self._get_client()
            
            if self.provider == "openai":
                response = client.chat.completions.create(
                    model="gpt-4o-mini",
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0.8,
                )
                return response.choices[0].message.content
            
        except Exception as e:
            logger.error(f"文案生成失败: {e}")
            return ""
    
    def extract_keywords(self, text: str) -> List[str]:
        """提取关键词"""
        import jieba.analyse
        
        keywords = jieba.analyse.extract_tags(text, topK=10)
        return list(keywords)