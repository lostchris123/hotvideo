"""
PaddleOCR-VL 分析器 - 已废弃，改用智谱AI

使用智谱AI GLM-4-Flash 进行爆款视频分析
"""
from __future__ import annotations
from typing import List, Dict
from dataclasses import dataclass
from loguru import logger
import json
import httpx

from config import get_settings


@dataclass
class AnalysisResult:
    """文案分析结果"""
    summary: str
    selling_points: List[str]
    emotions: List[str]
    structure: str
    template: str
    suggestions: List[str]


@dataclass
class ViralAnalysisResult:
    """爆款视频分析结果"""
    overall_score: int
    viral_factors: Dict[str, List[str]]
    content_analysis: Dict[str, str]
    title_analysis: Dict[str, str]
    tag_analysis: Dict[str, str]
    transcript_analysis: Dict[str, str]
    interaction_analysis: Dict[str, str]
    improvement_suggestions: List[str]
    success_formula: str


def call_zhipu_ai(prompt: str, max_length: int = 4096) -> str:
    """调用智谱AI接口"""
    settings = get_settings()
    
    url = f"{settings.ZHIPU_BASE_URL}/chat/completions"
    
    headers = {
        "Authorization": f"Bearer {settings.ZHIPU_API_KEY}",
        "Content-Type": "application/json"
    }
    
    data = {
        "model": settings.ZHIPU_MODEL,
        "messages": [
            {
                "role": "user",
                "content": prompt
            }
        ],
        "stream": False
    }
    
    try:
        with httpx.Client(timeout=60.0) as client:
            response = client.post(url, headers=headers, json=data)
            response.raise_for_status()
            result = response.json()
            return result["choices"][0]["message"]["content"]
    except Exception as e:
        logger.error(f"智谱AI调用失败: {e}")
        raise Exception(f"智谱AI调用失败: {str(e)}")


class PaddleOCRVLAnalyzer:
    """爆款视频分析器 - 使用智谱AI"""
    
    def __init__(self, model: str = None, device: str = None):
        # 保留参数兼容性，但实际使用智谱AI
        pass
    
    def _call_model(self, prompt: str, max_length: int = 4096) -> str:
        """调用模型 - 使用智谱AI"""
        return call_zhipu_ai(prompt, max_length)
    
    def analyze_copywriting(self, text: str) -> AnalysisResult:
        """分析爆款文案特点"""
        prompt = f"""你是一个专业的短视频文案分析师。请分析以下爆款视频文案的特点：

文案内容：
{text}

请返回JSON格式结果：
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
            result_text = self._call_model(prompt, max_length=2048)
            result = self._parse_json(result_text)
            
            return AnalysisResult(
                summary=result.get("summary", ""),
                selling_points=result.get("selling_points", []),
                emotions=result.get("emotions", []),
                structure=result.get("structure", ""),
                template=result.get("template", ""),
                suggestions=result.get("suggestions", []),
            )
            
        except Exception as e:
            logger.error(f"文案分析失败: {e}")
            return AnalysisResult(
                summary="", selling_points=[], emotions=[],
                structure="", template="", suggestions=[],
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
        """爆款视频多维度分析 - 使用智谱AI GLM-4.1V-Thinking-Flash"""
        
        # 截断过长的文案
        transcript_short = transcript[:300] if transcript else "暂无"
        tags_str = ', '.join(tags[:5]) if tags else '无'
        
        # 简化的prompt，避免输出被截断
        prompt = f'''分析短视频数据，返回JSON。

作者: {author_name} | 粉丝: {fans_count}
标签: {tags_str}
文案: {transcript_short}...
互动: 赞{likes} 评{comments} 藏{collects} 享{shares}

返回格式（简洁）:
{{
  "overall_score": 85,
  "viral_factors": {{
    "content_factors": ["原因1"],
    "title_factors": ["原因1"],
    "tag_factors": ["原因1"],
    "transcript_factors": ["原因1"],
    "interaction_factors": ["原因1"]
  }},
  "content_analysis": {{
    "information_value": "简短分析",
    "emotional_value": "简短分析",
    "entertainment_value": "简短分析"
  }},
  "title_analysis": {{
    "attractiveness": "简短分析",
    "pain_point": "简短分析",
    "curiosity": "简短分析"
  }},
  "tag_analysis": {{
    "popularity": "简短分析",
    "accuracy": "简短分析",
    "traffic_potential": "简短分析"
  }},
  "transcript_analysis": {{
    "opening": "简短分析",
    "rhythm": "简短分析",
    "golden_sentences": "简短分析",
    "call_to_action": "简短分析"
  }},
  "interaction_analysis": {{
    "interaction_design": "简短分析",
    "comment_trigger": "简短分析",
    "share_motivation": "简短分析"
  }},
  "improvement_suggestions": ["建议1", "建议2"],
  "success_formula": "一句话总结"
}}
'''
        
        try:
            result_text = self._call_model(prompt, max_length=4096)
            result = self._parse_json(result_text)
            
            # 如果解析失败，尝试从响应中提取关键信息
            if not result or 'overall_score' not in result:
                logger.warning(f"JSON解析失败，尝试简化解析")
                # 尝试提取 overall_score
                import re
                score_match = re.search(r'"overall_score"\s*:\s*(\d+)', result_text)
                if score_match:
                    result = {'overall_score': int(score_match.group(1))}
                else:
                    result = {}
            
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
            
        except Exception as e:
            logger.error(f"爆款视频分析失败: {e}")
            return self._get_empty_result()
    
    def _parse_json(self, text: str) -> dict:
        """解析JSON"""
        if not text:
            return {}
        
        # 清理markdown标记
        if "```json" in text:
            text = text.split("```json")[1].split("```")[0]
        elif "```" in text:
            # 尝試找到JSON塊
            parts = text.split("```")
            json_part = ""
            for i, part in enumerate(parts):
                if "{" in part and "}" in part:
                    json_part = part.strip()
                    break
            if not json_part and len(parts) > 1:
                # 尝試第二個塊（通常在markdown中是代碼塊後面）
                json_part = parts[1] if len(parts) > 1 else text
            
            text = json_part or text
        
        # 简化清理
        start = text.find('{')
        end = text.rfind('}') + 1
        if start != -1 and end != 0:
            text = text[start:end]
        
        try:
            return json.loads(text)
        except json.JSONDecodeError as e:
            logger.error(f"JSON解析失败: {e}")
            # 尝试修复JSON
            import re
            try:
                fixed = re.sub(r'"\s*"', '", "', text)
                result = json.loads(fixed)
                logger.info("JSON修复成功")
                return result
            except:
                pass
            # 正则提取
            result = {}
            m = re.search(r'"overall_score"\s*:\s*(\d+)', text)
            if m:
                result['overall_score'] = int(m.group(1))
            logger.info(f"正则提取: {result}")
            return result
    
    def _get_empty_result(self) -> ViralAnalysisResult:
        """返回空结果"""
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