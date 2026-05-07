"""ASR 语音识别模块"""
from .base_engine import BaseASREngine
from .whisper_engine import WhisperEngine
from .whisper_punc_engine import WhisperPuncEngine
from .sensevoice_engine import SenseVoiceEngine
from .siliconflow_engine import SiliconFlowEngine
from .audio_processor import AudioProcessor


def get_asr_engine(engine_type: str = None, **kwargs):
    """
    获取 ASR 引擎实例
    
    Args:
        engine_type: 引擎类型 (whisper / whisper_punc / sensevoice / siliconflow)
        **kwargs: 引擎参数
    
    Returns:
        ASR 引擎实例
    """
    from config import get_settings
    settings = get_settings()
    
    engine_type = engine_type or settings.ASR_ENGINE
    
    if engine_type == "siliconflow":
        return SiliconFlowEngine(
            api_key=kwargs.get("api_key", settings.SILICONFLOW_API_KEY),
            model=kwargs.get("model", settings.SILICONFLOW_MODEL),
        )
    elif engine_type == "sensevoice":
        return SenseVoiceEngine(
            model_name=kwargs.get("model_name", settings.SENSEVOICE_MODEL),
            device=kwargs.get("device", settings.SENSEVOICE_DEVICE),
        )
    elif engine_type == "whisper_punc":
        return WhisperPuncEngine(
            whisper_model=kwargs.get("model_size", settings.WHISPER_MODEL),
            device=kwargs.get("device", settings.WHISPER_DEVICE),
        )
    else:
        return WhisperEngine(
            model_size=kwargs.get("model_size", settings.WHISPER_MODEL),
            device=kwargs.get("device", settings.WHISPER_DEVICE),
        )


__all__ = [
    "BaseASREngine",
    "WhisperEngine",
    "WhisperPuncEngine",
    "SenseVoiceEngine",
    "SiliconFlowEngine",
    "AudioProcessor",
    "get_asr_engine",
]
