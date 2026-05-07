"""
Whisper + 标点恢复引擎
Whisper 识别速度快，后接标点恢复模型添加标点
"""
from __future__ import annotations
from pathlib import Path
from typing import Optional, Union, List, Dict
from loguru import logger

from .base_engine import BaseASREngine


class WhisperPuncEngine(BaseASREngine):
    """Whisper + 标点恢复引擎"""
    
    def __init__(
        self,
        whisper_model: str = "base",
        device: str = "cpu",
    ):
        self.whisper_model = whisper_model
        self.device = device
        self._whisper = None
        self._punc_model = None
    
    def _load_whisper(self):
        """加载 Whisper 模型"""
        if self._whisper is None:
            import whisper
            logger.info(f"加载 Whisper 模型: {self.whisper_model}")
            self._whisper = whisper.load_model(self.whisper_model, device=self.device)
            logger.info("Whisper 模型加载完成")
        return self._whisper
    
    def _load_punc_model(self):
        """加载标点恢复模型"""
        if self._punc_model is None:
            try:
                from funasr import AutoModel
                logger.info("加载标点恢复模型: ct-punc-c")
                self._punc_model = AutoModel(
                    model="ct-punc-c",
                    device=self.device,
                    disable_update=True,
                )
                logger.info("标点恢复模型加载完成")
            except Exception as e:
                logger.warning(f"标点模型加载失败，将跳过标点恢复: {e}")
                self._punc_model = False  # 标记为不可用
        return self._punc_model if self._punc_model != False else None
    
    def load_model(self):
        """加载所有模型"""
        self._load_whisper()
        self._load_punc_model()
    
    def transcribe(
        self,
        audio_path: Union[str, Path],
        language: str = "zh",
        **kwargs
    ) -> Dict:
        """
        语音转文字（带标点）
        
        Args:
            audio_path: 音频文件路径
            language: 语言代码
        
        Returns:
            {
                "text": "完整文本（带标点）",
                "segments": [...]
            }
        """
        whisper = self._load_whisper()
        audio_path = Path(audio_path)
        
        logger.info(f"Whisper 开始识别: {audio_path}")
        
        # Step 1: Whisper 识别
        result = whisper.transcribe(
            str(audio_path),
            language=language,
            task="transcribe",
            verbose=False,
            no_speech_threshold=0.6,
        )
        
        raw_text = result["text"].strip()
        logger.info(f"Whisper 识别完成，文本长度: {len(raw_text)}")
        
        # Step 2: 标点恢复
        punc_model = self._load_punc_model()
        if punc_model and raw_text:
            try:
                punc_result = punc_model.generate(input=raw_text)
                if punc_result and len(punc_result) > 0:
                    text_with_punc = punc_result[0].get("text", raw_text)
                    logger.info(f"标点恢复完成")
                    return {
                        "text": text_with_punc,
                        "segments": [
                            {"start": seg["start"], "end": seg["end"], "text": seg["text"].strip()}
                            for seg in result.get("segments", [])
                        ]
                    }
            except Exception as e:
                logger.warning(f"标点恢复失败: {e}")
        
        # 如果标点恢复失败，返回原始文本
        return {
            "text": raw_text,
            "segments": [
                {"start": seg["start"], "end": seg["end"], "text": seg["text"].strip()}
                for seg in result.get("segments", [])
            ]
        }
    
    def transcribe_with_timestamps(
        self,
        audio_path: Union[str, Path],
        language: str = "zh",
    ) -> List[Dict]:
        """带时间戳的识别"""
        result = self.transcribe(audio_path, language=language)
        return result["segments"]
    
    def transcribe_batch(
        self,
        audio_paths: List[Union[str, Path]],
        language: str = "zh",
    ) -> List[Dict]:
        """批量识别"""
        results = []
        for audio_path in audio_paths:
            try:
                result = self.transcribe(audio_path, language=language)
                results.append(result)
            except Exception as e:
                logger.error(f"识别失败 {audio_path}: {e}")
                results.append({"text": "", "segments": [], "error": str(e)})
        return results


if __name__ == "__main__":
    engine = WhisperPuncEngine(whisper_model="base", device="cpu")
    result = engine.transcribe("test.wav")
    print(result["text"])
