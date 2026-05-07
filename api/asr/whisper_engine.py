"""
Whisper 语音识别引擎
"""
from __future__ import annotations
from pathlib import Path
from typing import Optional, Union, List, Dict
from loguru import logger

from config import get_settings


class WhisperEngine:
    """Whisper 语音识别引擎"""
    
    def __init__(
        self,
        model_size: str = None,
        device: str = None,
    ):
        # 延迟导入 whisper 模块，避免在配置使用其他引擎时报错
        import whisper
        from whisper import Whisper
        self._whisper = whisper
        self._Whisper = Whisper
        
        settings = get_settings()
        self.model_size = model_size or settings.WHISPER_MODEL
        self.device = device or settings.WHISPER_DEVICE
        self._model = None
    
    def load_model(self):
        """加载模型"""
        if self._model:
            return self._model
        
        logger.info(f"加载 Whisper 模型: {self.model_size}, device: {self.device}")
        self._model = self._whisper.load_model(self.model_size, device=self.device)
        logger.info("Whisper 模型加载完成")
        return self._model
    
    def transcribe(
        self,
        audio_path: Union[str, Path],
        language: str = "zh",
        task: str = "transcribe",
        verbose: bool = False,
    ) -> Dict:
        """
        语音转文字
        
        Args:
            audio_path: 音频文件路径
            language: 语言代码 (zh/en)
            task: 任务类型 (transcribe/translate)
            verbose: 是否输出详细信息
        
        Returns:
            {
                "text": "完整文本",
                "segments": [
                    {"start": 0.0, "end": 5.0, "text": "片段文本"},
                    ...
                ]
            }
        """
        if not self._model:
            self.load_model()
        
        audio_path = Path(audio_path)
        logger.info(f"开始识别: {audio_path}")
        
        # 标点符号配置
        prepend_punc = "\u201c\u2018\uff08\u3010\u300a\u300c\u300e\u3014\u3008\u201c\u300a\u3010"
        append_punc = "\u3002,\uff0c!\uff01?\uff1f:\uff1a\u201d\u2019\uff09\u3011\u300b\u300f\u3015\u3009\u201d\u300b\u3011"
        
        result = self._model.transcribe(
            str(audio_path),
            language=language,
            task=task,
            verbose=verbose,
            prepend_punctuations=prepend_punc,
            append_punctuations=append_punc,
            word_timestamps=False,
            no_speech_threshold=0.6,
        )
        
        logger.info(f"识别完成，文本长度: {len(result[text])}")
        
        return {
            "text": result["text"].strip(),
            "segments": [
                {
                    "start": seg["start"],
                    "end": seg["end"],
                    "text": seg["text"].strip(),
                }
                for seg in result.get("segments", [])
            ]
        }
    
    def transcribe_with_timestamps(
        self,
        audio_path: Union[str, Path],
        language: str = "zh",
    ) -> List[Dict]:
        """
        带时间戳的语音识别
        
        Returns:
            [
                {"start": 0.0, "end": 5.0, "text": "片段1"},
                {"start": 5.0, "end": 10.0, "text": "片段2"},
                ...
            ]
        """
        result = self.transcribe(audio_path, language=language)
        return result["segments"]
    
    def get_text_only(
        self,
        audio_path: Union[str, Path],
        language: str = "zh",
    ) -> str:
        """
        仅获取文本内容
        """
        result = self.transcribe(audio_path, language=language)
        return result["text"]


if __name__ == "__main__":
    engine = WhisperEngine(model_size="medium", device="cuda")
    result = engine.transcribe("test.wav")
    print(result["text"])
