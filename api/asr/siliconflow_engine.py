"""
SiliconFlow API 语音识别引擎
使用云端模型，支持长音频、高准确率
"""
from __future__ import annotations
from pathlib import Path
from typing import Union, Dict, List, Optional
import requests
import json
import time
from loguru import logger
import io

from .base_engine import BaseASREngine


class SiliconFlowEngine(BaseASREngine):
    """SiliconFlow 语音识别引擎"""
    
    def __init__(
        self,
        api_key: str,
        model: str = "FunAudioLLM/SenseVoiceSmall",
        base_url: str = "https://api.siliconflow.cn/v1/audio/transcriptions",
        timeout: int = 120,
    ):
        """
        初始化 SiliconFlow 引擎
        
        Args:
            api_key: SiliconFlow API key
            model: 模型名称，默认 FunAudioLLM/SenseVoiceSmall
            base_url: API 基础 URL
            timeout: 超时时间（秒）
        """
        self.api_key = api_key
        self.model = model
        self.base_url = base_url
        self.timeout = timeout
        
        logger.info(f"初始化 SiliconFlow 引擎，模型: {model}")
    
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
            language: 语言代码（zh/en等）
        
        Returns:
            {
                "text": "完整文本",
                "segments": [{"start": 0.0, "end": 0.0, "text": "完整文本"}]
            }
        """
        audio_path = Path(audio_path)
        if not audio_path.exists():
            raise FileNotFoundError(f"音频文件不存在: {audio_path}")
        
        logger.info(f"SiliconFlow 开始识别: {audio_path}")
        
        try:
            # 获取音频时长
            from pydub import AudioSegment
            audio = AudioSegment.from_wav(str(audio_path))
            duration = len(audio) / 1000  # 转换为秒
            logger.info(f"音频时长: {duration:.2f} 秒")
            
            # 调用 API
            with open(audio_path, 'rb') as audio_file:
                files = {
                    "file": (audio_path.name, audio_file, 'audio/wav'),
                }
                data = {
                    "model": self.model,
                }
                
                headers = {
                    "Authorization": f"Bearer {self.api_key}",
                }
                
                logger.info(f"正在调用 SiliconFlow API: {self.base_url}")
                response = requests.post(
                    self.base_url,
                    headers=headers,
                    files=files,
                    data=data,
                    timeout=self.timeout
                )
                
                if response.status_code == 200:
                    result = response.json()
                    text = result.get("text", "").strip()
                    
                    logger.info(f"识别完成，文本长度: {len(text)}")
                    logger.debug(f"识别结果: {text[:200]}..." if len(text) > 200 else text)
                    
                    return {
                        "text": text,
                        "segments": [{"start": 0.0, "end": duration, "text": text}],
                        "duration": duration
                    }
                else:
                    error_msg = f"API 调用失败: {response.status_code} {response.text}"
                    logger.error(error_msg)
                    raise Exception(error_msg)
                    
        except requests.exceptions.Timeout:
            error_msg = f"SiliconFlow API 请求超时 ({self.timeout} 秒)"
            logger.error(error_msg)
            raise Exception(error_msg)
        except Exception as e:
            logger.error(f"SiliconFlow 识别失败: {e}")
            raise
    
    def transcribe_batch(
        self,
        audio_paths: List[Union[str, Path]],
        language: str = "zh",
    ) -> List[Dict]:
        """
        批量语音识别（串行）
        
        Args:
            audio_paths: 音频文件路径列表
            language: 语言代码
        
        Returns:
            识别结果列表
        """
        logger.info(f"SiliconFlow 批量识别: {len(audio_paths)} 个文件")
        
        results = []
        for i, audio_path in enumerate(audio_paths):
            try:
                logger.info(f"处理第 {i+1}/{len(audio_paths)} 个文件: {audio_path}")
                result = self.transcribe(audio_path, language=language)
                results.append(result)
            except Exception as e:
                logger.error(f"识别失败 {audio_path}: {e}")
                results.append({"text": "", "segments": [], "error": str(e)})
        
        return results
    
    def transcribe_with_timestamps(
        self,
        audio_path: Union[str, Path],
        language: str = "zh",
    ) -> List[Dict]:
        """
        带时间戳的语音识别
        
        Note: SiliconFlow API 不直接支持时间戳，这里返回单一段落
        """
        result = self.transcribe(audio_path, language=language)
        return result["segments"]


def test_siliconflow():
    """测试函数"""
    import tempfile
    from pydub import AudioSegment
    from pydub.generators import Sine
    
    # 创建测试音频
    audio = Sine(440).to_audio_segment(duration=3000)  # 3秒
    temp_wav = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
    audio.export(temp_wav.name, format="wav")
    
    print(f"测试音频保存到: {temp_wav.name}")
    
    # 需要设置 API key
    api_key = input("请输入 SiliconFlow API key: ").strip()
    
    engine = SiliconFlowEngine(api_key=api_key)
    result = engine.transcribe(temp_wav.name)
    
    print(f"识别结果: {result['text']}")
    
    # 清理
    import os
    os.unlink(temp_wav.name)


if __name__ == "__main__":
    test_siliconflow()
