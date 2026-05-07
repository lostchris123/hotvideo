"""
ASR 引擎抽象基类
"""
from __future__ import annotations
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Union, Dict, List


class BaseASREngine(ABC):
    """ASR 引擎抽象基类"""
    
    @abstractmethod
    def transcribe(
        self,
        audio_path: Union[str, Path],
        language: str = "zh",
        **kwargs
    ) -> Dict:
        """
        语音转文字
        
        Args:
            audio_path: 音频文件路径
            language: 语言代码
            **kwargs: 额外参数
        
        Returns:
            {
                "text": "完整文本",
                "segments": [{"start": 0.0, "end": 5.0, "text": "片段"}]
            }
        """
        pass
    
    @abstractmethod
    def transcribe_with_timestamps(
        self,
        audio_path: Union[str, Path],
        language: str = "zh",
    ) -> List[Dict]:
        """带时间戳的语音识别"""
        pass
    
    def get_text_only(
        self,
        audio_path: Union[str, Path],
        language: str = "zh",
    ) -> str:
        """仅获取文本内容"""
        result = self.transcribe(audio_path, language=language)
        return result["text"]