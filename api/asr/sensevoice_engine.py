"""
SenseVoice 语音识别引擎（阿里通义实验室）
优势：中文识别准确率高、推理速度快、模型小（约230MB）
"""
from __future__ import annotations
from pathlib import Path
from typing import Union, Dict, List, Optional
from loguru import logger

from .base_engine import BaseASREngine


class SenseVoiceEngine(BaseASREngine):
    """SenseVoice 语音识别引擎"""
    
    def __init__(
        self,
        model_name: str = "iic/SenseVoiceSmall-onnx",
        device: str = "cpu",
        use_cache: bool = True,
    ):
        """
        初始化 SenseVoice 引擎
        
        Args:
            model_name: 模型名称，默认 iic/SenseVoiceSmall-onnx
            device: 设备，cuda / cpu
            use_cache: 是否缓存模型
        """
        self.model_name = model_name
        self.device = device
        self.use_cache = use_cache
        self._model = None
        
        # 直接检查本地模型，如果存在则使用本地路径
        from pathlib import Path
        local_path = Path(__file__).parent.parent / "data" / "models" / "iic" / "SenseVoiceSmall"
        if local_path.exists():
            self.model_name = str(local_path)
            logger.info(f"找到本地模型，使用本地路径: {self.model_name}")
        else:
            logger.info(f"本地模型不存在，使用在线模型: {model_name}")
    
    def _get_model_dir(self) -> Path:
        """获取模型缓存目录"""
        from config import get_settings
        settings = get_settings()
        model_dir = settings.DATA_DIR / "models" / "sensevoice"
        model_dir.mkdir(parents=True, exist_ok=True)
        return model_dir
    
    def load_model(self):
        """加载模型"""
        if self._model is not None:
            return self._model
        
        logger.info(f"加载 SenseVoice 模型: {self.model_name}, device: {self.device}")
        
        try:
            from funasr import AutoModel
            
            # 同时加载标点恢复模型
            self._model = AutoModel(
                model=self.model_name,
                punc_model="ct-punc-c",
                device=self.device,
                disable_update=True,
            )
            
            logger.info("SenseVoice 模型加载完成（含标点恢复）")
            return self._model
            
        except ImportError as e:
            logger.error(f"请安装 funasr: pip install funasr modelscope. 错误: {e}")
            raise
        except Exception as e:
            logger.error(f"模型加载失败: {e}")
            raise
    
    def transcribe(
        self,
        audio_path: Union[str, Path],
        language: str = "zh",
        **kwargs
    ) -> Dict:
        """
        语音转文字（支持长音频分段处理）
        
        Args:
            audio_path: 音频文件路径
            language: 语言代码（SenseVoice 自动检测）
        
        Returns:
            {
                "text": "完整文本",
                "segments": [{"start": 0.0, "end": 5.0, "text": "片段"}]
            }
        """
        if self._model is None:
            self.load_model()
        
        audio_path = Path(audio_path)
        logger.info(f"SenseVoice 开始识别: {audio_path}")
        
        # 获取音频时长
        try:
            from pydub import AudioSegment
            audio = AudioSegment.from_wav(audio_path)
            duration = len(audio) / 1000  # 转换为秒
            logger.info(f"音频时长: {duration:.2f} 秒")
            
            # 如果音频超过 60 秒，分段处理
            if duration > 60:
                return self._transribe_long_audio(audio, duration)
            else:
                return self._transribe_single(audio_path)
                
        except Exception as e:
            logger.warning(f"无法获取音频时长，直接处理: {e}")
            return self._transribe_single(audio_path)
    
    def _transribe_single(self, audio_path: Path) -> Dict:
        """识别单个音频文件"""
        try:
            result = self._model.generate(
                input=str(audio_path),
                cache={},
                is_final=True,
                return_raw_text=False,
                use_itn=True,
            )
            
            if result and len(result) > 0:
                text = result[0].get("text", "")
                import re
                text = re.sub(r'<\|[^|]+\|>', '', text).strip()
                
                return {
                    "text": text,
                    "segments": [{"start": 0.0, "end": 0.0, "text": text}]
                }
            else:
                return {"text": "", "segments": []}
                
        except Exception as e:
            logger.error(f"SenseVoice 识别失败: {e}")
            raise
    
    def _transribe_long_audio(self, audio: AudioSegment, duration: float) -> Dict:
        """分段处理长音频"""
        logger.info(f"长音频分段处理，总时长: {duration:.2f} 秒")
        
        # 每段 60 秒（1分钟）
        segment_length = 60 * 1000  # 毫秒
        all_texts = []
        
        for i in range(0, len(audio), segment_length):
            segment = audio[i:i + segment_length]
            segment_path = Path(f"/tmp/sensevoice_segment_{i}.wav")
            segment.export(segment_path, format="wav")
            
            try:
                result = self._model.generate(
                    input=str(segment_path),
                    cache={},
                    is_final=True,
                    return_raw_text=False,
                    use_itn=True,
                )
                
                if result and len(result) > 0:
                    text = result[0].get("text", "")
                    import re
                    text = re.sub(r'<\|[^|]+\|>', '', text).strip()
                    all_texts.append(text)
                    
                logger.info(f"第 {i//segment_length + 1} 段识别完成")
                
            finally:
                # 清理临时文件
                if segment_path.exists():
                    segment_path.unlink()
        
        full_text = " ".join(all_texts)
        logger.info(f"长音频识别完成，总文本长度: {len(full_text)}")
        
        return {
            "text": full_text,
            "segments": [{"start": 0.0, "end": duration, "text": full_text}]
        }
    
    def transcribe_with_timestamps(
        self,
        audio_path: Union[str, Path],
        language: str = "zh",
    ) -> List[Dict]:
        """
        带时间戳的语音识别
        
        注意：SenseVoiceSmall 不直接支持时间戳，这里返回整体文本
        如需时间戳，建议使用 Whisper 或 FunASR 的 Paraformer 模型
        """
        result = self.transcribe(audio_path, language=language)
        return result["segments"]
    
    def transcribe_batch(
        self,
        audio_paths: List[Union[str, Path]],
        language: str = "zh",
    ) -> List[Dict]:
        """
        批量语音识别（更高效）
        
        Args:
            audio_paths: 音频文件路径列表
            language: 语言代码
        
        Returns:
            识别结果列表
        """
        if self._model is None:
            self.load_model()
        
        logger.info(f"SenseVoice 批量识别: {len(audio_paths)} 个文件")
        
        results = []
        for audio_path in audio_paths:
            try:
                result = self.transcribe(audio_path, language=language)
                results.append(result)
            except Exception as e:
                logger.error(f"识别失败 {audio_path}: {e}")
                results.append({"text": "", "segments": [], "error": str(e)})
        
        return results


def download_sensevoice_model(model_name: str = "iic/SenseVoiceSmall"):
    """
    预下载 SenseVoice 模型
    
    首次使用建议调用此函数预先下载模型（约 230MB）
    """
    logger.info(f"开始下载 SenseVoice 模型: {model_name}")
    
    try:
        from modelscope import snapshot_download
        
        model_dir = snapshot_download(
            model_name,
            revision="v2.0.4",
        )
        
        logger.info(f"模型下载完成: {model_dir}")
        return model_dir
        
    except ImportError:
        logger.error("请安装 modelscope: pip install modelscope")
        raise
    except Exception as e:
        logger.error(f"模型下载失败: {e}")
        raise


if __name__ == "__main__":
    engine = SenseVoiceEngine(device="cpu")
    result = engine.transcribe("test.wav")
    print(result["text"])