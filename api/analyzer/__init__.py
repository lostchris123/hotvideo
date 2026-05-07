"""文案分析模块"""
from config import get_settings

def get_analyzer():
    """获取分析器（根据配置）"""
    settings = get_settings()
    
    if settings.LLM_PROVIDER == "paddleocr":
        from .paddleocr_analyzer import PaddleOCRVLAnalyzer
        return PaddleOCRVLAnalyzer()
    elif settings.LLM_PROVIDER == "openai":
        from .llm_analyzer import LLMAnalyzer
        return LLMAnalyzer(provider="openai")
    elif settings.LLM_PROVIDER == "dashscope":
        from .llm_analyzer import LLMAnalyzer
        return LLMAnalyzer(provider="dashscope")
    else:
        # 默认使用 PaddleOCR-VL
        from .paddleocr_analyzer import PaddleOCRVLAnalyzer
        return PaddleOCRVLAnalyzer()

__all__ = ["get_analyzer"]