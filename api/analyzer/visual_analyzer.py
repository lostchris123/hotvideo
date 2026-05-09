"""
视觉增强型爆款分析模块（v10）
- 修复 JSON 格式问题：模型返回字典格式需要更智能的转换
"""
from __future__ import annotations
import os
import json
import base64
import random
import subprocess
import tempfile
import re
from pathlib import Path
from typing import Optional, List
from dataclasses import dataclass
from loguru import logger
import httpx


@dataclass
class VisualAnalysisResult:
    frames_analyzed: int
    visual_description: str
    scene_types: List[str]
    visual_highlights: List[str]
    cinematography: str = ""


@dataclass
class ViralInsightResult:
    viral_points: List[str]
    visual_hooks: List[str]
    content_hooks: List[str]
    emotion_triggers: List[str]
    target_audience: str
    creation_prompt: str
    replication_tips: List[str]


def _fix_json_dict_format(data: dict) -> dict:
    """修复模型返回的字典格式问题"""
    fixed = {}
    
    for key, value in data.items():
        if isinstance(value, dict):
            # 如果值是字典，检查是否是 "标签": "内容" 格式
            dict_keys = list(value.keys())
            if len(dict_keys) == 1 and any(k in key for k in ["描述", "类型", "受众", "提示词"]):
                # 提取第一个值作为实际内容
                fixed[key] = next(iter(value.values()), "")
            else:
                # 转换为值数组
                fixed[key] = list(value.values()) if value else []
        elif isinstance(value, list):
            # 检查数组中的元素是否是字典
            new_list = []
            for item in value:
                if isinstance(item, dict) and len(item) == 1:
                    # 提取字典的值
                    new_list.append(next(iter(item.values()), ""))
                else:
                    new_list.append(item)
            fixed[key] = new_list
        else:
            fixed[key] = value
    
    return fixed


def _extract_json(text: str) -> dict:
    if not text:
        return None
    try:
        return json.loads(text)
    except:
        pass
    
    # 提取代码块
    code_match = re.search(r'```(?:json)?\s*([\s\S]*?)\s*```', text)
    if code_match:
        json_text = code_match.group(1).strip()
        try:
            data = json.loads(json_text)
            return _fix_json_dict_format(data)
        except:
            # 尝试修复格式错误
            json_text = json_text.rstrip()
            json_text = re.sub(r'"爆点\d+":\s*"([^"]+)"', r'"\1"', json_text)
            json_text = re.sub(r'"AI提示词":\s*"([^"]+)"', r'"\1"', json_text)
            json_text = re.sub(r'"前3秒画面":\s*"([^"]+)"', r'"\1"', json_text)
            json_text = re.sub(r'"叙事":\s*"([^"]+)"', r'"\1"', json_text)
            json_text = re.sub(r'"情绪":\s*"([^"]+)"', r'"\1"', json_text)
            json_text = re.sub(r'"复刻\d+":\s*"([^"]+)"', r'"\1"', json_text)
            try:
                data = json.loads(json_text)
                return _fix_json_dict_format(data)
            except:
                pass
    
    # 提取对象
    json_match = re.search(r'\{[\s\S]*\}', text)
    if json_match:
        json_text = json_match.group()
        try:
            data = json.loads(json_text)
            return _fix_json_dict_format(data)
        except:
            pass
    return None


def extract_frames(video_path: str, num_frames: int = 3) -> List[str]:
    if not os.path.exists(video_path):
        return []
    try:
        probe_cmd = ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", video_path]
        probe_data = json.loads(subprocess.run(probe_cmd, capture_output=True, text=True, timeout=10).stdout)
        duration = float(probe_data["format"]["duration"])
    except:
        duration = 60.0

    margin = duration * 0.1
    safe_start, safe_end = margin, duration - margin
    if safe_end <= safe_start:
        safe_start, safe_end = 0, duration

    timestamps = sorted(random.sample(
        [safe_start + (safe_end - safe_start) * i / (num_frames + 1) for i in range(1, num_frames + 1)], k=min(num_frames, 3)
    ))
    timestamps = [max(0, min(duration - 1, t + random.uniform(-duration * 0.05, duration * 0.05))) for t in timestamps]

    frames_b64 = []
    with tempfile.TemporaryDirectory() as tmpdir:
        for i, ts in enumerate(timestamps):
            out_path = os.path.join(tmpdir, f"frame_{i}.jpg")
            try:
                subprocess.run(["ffmpeg", "-ss", str(ts), "-i", video_path, "-vframes", "1", "-q:v", "3", "-vf", "scale=720:-1", out_path, "-y", "-loglevel", "quiet"], timeout=15, capture_output=True)
                if os.path.exists(out_path):
                    with open(out_path, "rb") as f:
                        frames_b64.append(base64.b64encode(f.read()).decode())
            except:
                pass
    return frames_b64


def analyze_frames_with_vision(frames_b64: List[str], api_key: str, base_url: str = "https://open.bigmodel.cn/api/paas/v4", model: str = "GLM-4.1V-Thinking-Flash") -> Optional[VisualAnalysisResult]:
    if not frames_b64:
        return None

    content = [{
        "type": "text",
        "text": f"""你是专业的短视频视觉分析师。请深度分析这 {len(frames_b64)} 张截图。

**必须具体描述，不能写"美女跳舞"这种废话。**

请分析：
1. **人物**：性别、年龄感、颜值风格、身材特点、发型、妆容
2. **服装**：款式、颜色、材质、风格（如"玫红色露肩短裙"不是"红色裙子"）
3. **动作**：具体在做什么（如"街舞旋转跳跃"不是"跳舞"）
4. **场景**：具体地点、背景、道具
5. **灯光**：光源类型、色彩、氛围
6. **镜头**：景别、运镜方式、剪辑节奏

返回JSON：
{{"visual_description": "200字详细描述，包含人物/服装/动作/场景/灯光", "scene_types": ["室内舞台", "彩色激光灯"], "visual_highlights": ["玫红色服装配暗色背景形成强反差", "高难度旋转动作"], "cinematography": "快剪，每1.5秒切镜"}}"""
    }]
    for b64 in frames_b64:
        content.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}})

    try:
        with httpx.Client(timeout=60) as client:
            resp = client.post(f"{base_url}/chat/completions",
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json={"model": model, "messages": [{"role": "user", "content": content}], "temperature": 0.3, "max_tokens": 1000})
            
            resp_json = resp.json()
            if "choices" not in resp_json:
                logger.error(f"视觉API异常: {resp_json}")
                return None
            
            raw = resp_json["choices"][0]["message"]["content"]
            logger.info(f"视觉分析返回: {raw[:300]}")
            data = _extract_json(raw)
            if data:
                return VisualAnalysisResult(
                    frames_analyzed=len(frames_b64),
                    visual_description=data.get("visual_description", ""),
                    scene_types=data.get("scene_types", []),
                    visual_highlights=data.get("visual_highlights", []),
                    cinematography=data.get("cinematography", ""),
                )
    except Exception as e:
        logger.error(f"视觉分析失败: {e}")
    return None


def generate_viral_insight(
    visual_result: Optional[VisualAnalysisResult], title: str, transcript: str,
    likes: int, comments: int, shares: int, collects: int,
    author_name: str, fans_count: str, platform: str,
    api_key: str, base_url: str = "https://open.bigmodel.cn/api/paas/v4", model: str = "glm-4-flash",
) -> ViralInsightResult:
    platform_name = {"douyin": "抖音", "xiaohongshu": "小红书"}.get(platform, platform)
    
    visual_context = ""
    if visual_result and visual_result.visual_description:
        visual_context = f"""
## 视频画面细节（必须融入创作提示词）
- **画面描述**：{visual_result.visual_description}
- **场景标签**：{', '.join(visual_result.scene_types) if visual_result.scene_types else '无'}
- **视觉亮点**：{', '.join(visual_result.visual_highlights) if visual_result.visual_highlights else '无'}
- **镜头特点**：{visual_result.cinematography if visual_result.cinematography else '无'}

⚠️ **重要**：创作提示词必须包含上述视觉细节中的关键元素！
"""
    else:
        visual_context = "\n## 视频画面\n无视频文件，基于文案推断\n"

    prompt = f"""你是短视频爆款研究专家。

## 视频信息
- 平台：{platform_name}
- 作者：{author_name}（{fans_count}粉）
- 文案：{title or '无'}
- 数据：赞{likes:,} | 评{comments:,} | 转{shares:,} | 收{collects:,}
{visual_context}
## 语音内容
{transcript or '无'}

---

## 任务

### 1. 爆点分析
分析这个视频为什么能火，必须是**具体机制**：
- ❌ "美女跳舞" → 废话
- ✅ "玫红色服装与暗色背景形成强反差，第一秒抓住注意力；高难度旋转动作在节拍高潮处精准卡点，制造视觉冲击"

### 2. 创作提示词（最重要！）
给 AI 视频生成工具（Sora/可灵/即梦）使用的**完整视频描述**。

**必须是一个可以直接输入 AI 生成类似视频的详细描述**，不是抽象指导！

**格式要求**：
```
【人物设定】
- 外貌：具体的颜值风格、年龄感、发型、妆容
- 服装：具体款式、颜色、材质（如"玫红色露肩短裙"不是"红色裙子"）
- 气质：自信/俏皮/性感/高冷

【场景设定】
- 地点：具体场景（如"专业舞台"不是"室内"）
- 灯光：具体光源、色彩、氛围
- 背景：具体道具、装饰

【镜头语言】
- 景别：近景/中景/全景
- 运镜：固定/推拉/跟随/环绕
- 剪辑节奏：快剪（X秒切镜）还是长镜头

【动作设计】
- 开场（前3秒）：具体动作画面
- 中段高潮：核心动作
- 结尾：收尾方式

【音乐与氛围】
- 音乐风格
- 整体情绪基调
```

**长度**：300-500字

**示例**：
❌ 错误："创建一个有吸引力的视频，使用独特的视觉元素..."
✅ 正确："一位20岁左右的女生，穿玫红色露肩短裙，黑色长发披肩，妆容精致。在暗色调专业舞台上，背景有多彩激光灯和烟雾效果。女生表演劲爆街舞，动作以高难度旋转和跳跃为主，每次音乐节拍重音时有快速切镜特写。镜头采用中景到近景的快速切换，平均每1.5秒切一次。灯光以舞台追光为主，配合彩色激光营造动感氛围。音乐为快节奏电子舞曲，整体情绪燃且性感。前3秒以一个高难度旋转动作开场，抓住观众注意力。"

---

**返回JSON格式（严格遵循）**：
```json
{{
  "viral_points": [
    "具体爆点1：机制 + 为什么有效",
    "具体爆点2：机制 + 为什么有效",
    "具体爆点3：机制 + 为什么有效"
  ],
  "visual_hooks": [
    "前3秒画面设计",
    "关键视觉记忆点"
  ],
  "content_hooks": [
    "叙事/悬念/反转设计"
  ],
  "emotion_triggers": [
    "触发的情绪类型"
  ],
  "target_audience": "精准受众描述",
  "creation_prompt": "完整的AI视频生成提示词（300-500字，必须包含视觉细节）",
  "replication_tips": [
    "可直接复制的做法1",
    "可直接复制的做法2",
    "需要注意的避坑点"
  ]
}}
```

**注意**：所有数组字段（viral_points、visual_hooks、content_hooks、emotion_triggers、replication_tips）必须是字符串数组，不能是字典。creation_prompt 必须是字符串，不能是字典。target_audience 必须是字符串。"""

    try:
        with httpx.Client(timeout=90) as client:
            resp = client.post(f"{base_url}/chat/completions",
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json={"model": model, "messages": [{"role": "user", "content": prompt}], "temperature": 0.7, "max_tokens": 3000})
            
            resp_json = resp.json()
            if "choices" not in resp_json:
                logger.error(f"文本API异常: status={resp.status_code}, body={resp.text[:500]}")
                raise ValueError(f"API异常: {resp_json.get('error', resp_json)}")
            
            raw = resp_json["choices"][0]["message"]["content"]
            logger.info(f"爆款洞察返回长度: {len(raw)}")
            
            data = _extract_json(raw)
            if data:
                # 确保所有字段类型正确
                viral_points = data.get("viral_points", [])
                if not isinstance(viral_points, list):
                    viral_points = [str(viral_points)] if viral_points else []
                
                visual_hooks = data.get("visual_hooks", [])
                if not isinstance(visual_hooks, list):
                    visual_hooks = [str(visual_hooks)] if visual_hooks else []
                
                content_hooks = data.get("content_hooks", [])
                if not isinstance(content_hooks, list):
                    content_hooks = [str(content_hooks)] if content_hooks else []
                
                emotion_triggers = data.get("emotion_triggers", [])
                if not isinstance(emotion_triggers, list):
                    emotion_triggers = [str(emotion_triggers)] if emotion_triggers else []
                
                replication_tips = data.get("replication_tips", [])
                if not isinstance(replication_tips, list):
                    replication_tips = [str(replication_tips)] if replication_tips else []
                
                target_audience = data.get("target_audience", "")
                if not isinstance(target_audience, str):
                    target_audience = str(target_audience)
                
                creation_prompt = data.get("creation_prompt", "")
                if not isinstance(creation_prompt, str):
                    creation_prompt = str(creation_prompt)
                
                return ViralInsightResult(
                    viral_points=viral_points,
                    visual_hooks=visual_hooks,
                    content_hooks=content_hooks,
                    emotion_triggers=emotion_triggers,
                    target_audience=target_audience,
                    creation_prompt=creation_prompt,
                    replication_tips=replication_tips,
                )
            logger.error(f"JSON提取失败: {raw[:500]}")
            raise ValueError("JSON解析失败")
    except Exception as e:
        logger.error(f"爆款洞察失败: {e}")
        raise


class VisualViralAnalyzer:
    def __init__(self, api_key: str, base_url: str, vision_model: str, text_model: str, videos_dir: str):
        self.api_key = api_key
        self.base_url = base_url
        self.vision_model = vision_model
        self.text_model = text_model
        self.videos_dir = Path(videos_dir)

    def analyze(self, video_id: str, title: str, transcript: str, likes: int, comments: int, shares: int, collects: int, author_name: str, fans_count: str, platform: str, num_frames: int = 3) -> dict:
        video_path = None
        for ext in [".mp4", ".mov", ".webm"]:
            candidate = self.videos_dir / f"{video_id}{ext}"
            if candidate.exists():
                video_path = str(candidate)
                break

        frames_b64 = extract_frames(video_path, num_frames) if video_path else []
        visual_result = analyze_frames_with_vision(frames_b64, self.api_key, self.base_url, self.vision_model) if frames_b64 else None
        insight = generate_viral_insight(visual_result, title, transcript, likes, comments, shares, collects, author_name, fans_count, platform, self.api_key, self.base_url, self.text_model)

        return {
            "has_video": video_path is not None,
            "frames_analyzed": len(frames_b64),
            "visual_result": {
                "visual_description": visual_result.visual_description,
                "scene_types": visual_result.scene_types,
                "visual_highlights": visual_result.visual_highlights,
                "cinematography": visual_result.cinematography,
            } if visual_result else None,
            "viral_points": insight.viral_points,
            "visual_hooks": insight.visual_hooks,
            "content_hooks": insight.content_hooks,
            "emotion_triggers": insight.emotion_triggers,
            "target_audience": insight.target_audience,
            "creation_prompt": insight.creation_prompt,
            "replication_tips": insight.replication_tips,
        }
