"""
API 数据模型
"""
from __future__ import annotations
from typing import Optional, List
from pydantic import BaseModel, Field
from datetime import datetime


class VideoInfoResponse(BaseModel):
    """视频信息响应"""
    video_id: str
    title: str
    author: str
    likes: int
    comments: int
    shares: int
    video_url: str
    description: str


class TranscriptResponse(BaseModel):
    """转录响应"""
    video_id: str
    text: str
    segments: List[dict] = Field(default_factory=list)
    duration: float = 0.0
    created_at: datetime = Field(default_factory=datetime.now)


class AnalysisResponse(BaseModel):
    """分析响应"""
    video_id: str
    summary: str
    selling_points: List[str]
    emotions: List[str]
    structure: str
    template: str
    suggestions: List[str]


class ProcessRequest(BaseModel):
    """处理请求"""
    url: str
    extract_audio: bool = True
    transcribe: bool = True
    analyze: bool = True


class ProcessResponse(BaseModel):
    """处理响应"""
    video_info: VideoInfoResponse
    transcript: Optional[TranscriptResponse] = None
    analysis: Optional[AnalysisResponse] = None