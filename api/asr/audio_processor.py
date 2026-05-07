"""
音频预处理模块
"""
from pathlib import Path
from typing import Optional
import subprocess
from loguru import logger


class AudioProcessor:
    """音频处理器"""
    
    @staticmethod
    def convert_format(
        input_path: Path,
        output_path: Path,
        sample_rate: int = 16000,
        channels: int = 1,
    ) -> Path:
        """
        转换音频格式
        
        Args:
            input_path: 输入文件
            output_path: 输出文件
            sample_rate: 采样率
            channels: 声道数
        """
        cmd = [
            'ffmpeg', '-y',
            '-i', str(input_path),
            '-ar', str(sample_rate),
            '-ac', str(channels),
            '-acodec', 'pcm_s16le',
            str(output_path)
        ]
        
        result = subprocess.run(cmd, capture_output=True, text=True)
        
        if result.returncode != 0:
            raise Exception(f"音频转换失败: {result.stderr}")
        
        return output_path
    
    @staticmethod
    def split_audio(
        audio_path: Path,
        chunk_duration: int = 600,  # 10分钟
        output_dir: Path = None,
    ) -> list[Path]:
        """
        分割长音频
        
        Args:
            audio_path: 音频文件路径
            chunk_duration: 每段时长（秒）
            output_dir: 输出目录
        
        Returns:
            分割后的文件列表
        """
        output_dir = output_dir or audio_path.parent / "chunks"
        output_dir.mkdir(parents=True, exist_ok=True)
        
        # 获取总时长
        duration = AudioProcessor.get_duration(audio_path)
        
        chunks = []
        start = 0
        index = 0
        
        while start < duration:
            output_path = output_dir / f"{audio_path.stem}_{index}.wav"
            
            cmd = [
                'ffmpeg', '-y',
                '-i', str(audio_path),
                '-ss', str(start),
                '-t', str(chunk_duration),
                '-acodec', 'pcm_s16le',
                str(output_path)
            ]
            
            result = subprocess.run(cmd, capture_output=True, text=True)
            
            if result.returncode == 0:
                chunks.append(output_path)
            
            start += chunk_duration
            index += 1
        
        logger.info(f"音频分割完成，共 {len(chunks)} 段")
        return chunks
    
    @staticmethod
    def get_duration(audio_path: Path) -> float:
        """获取音频时长"""
        cmd = [
            'ffprobe', '-v', 'error',
            '-show_entries', 'format=duration',
            '-of', 'default=noprint_wrappers=1:nokey=1',
            str(audio_path)
        ]
        
        result = subprocess.run(cmd, capture_output=True, text=True)
        
        if result.returncode == 0:
            return float(result.stdout.strip())
        return 0.0
    
    @staticmethod
    def remove_silence(
        input_path: Path,
        output_path: Path,
        silence_threshold: float = -35.0,
    ) -> Path:
        """
        移除静音部分
        
        Args:
            input_path: 输入文件
            output_path: 输出文件
            silence_threshold: 静音阈值（dB）
        """
        cmd = [
            'ffmpeg', '-y',
            '-i', str(input_path),
            '-af', f'silenceremove=stop_periods=-1:stop_duration=1:stop_threshold={silence_threshold}dB',
            str(output_path)
        ]
        
        result = subprocess.run(cmd, capture_output=True, text=True)
        
        if result.returncode != 0:
            logger.warning(f"静音移除失败，使用原文件: {result.stderr}")
            return input_path
        
        return output_path
    
    @staticmethod
    def normalize_volume(
        input_path: Path,
        output_path: Path,
        target_db: float = -20.0,
    ) -> Path:
        """
        音量标准化
        
        Args:
            input_path: 输入文件
            output_path: 输出文件
            target_db: 目标音量（dB）
        """
        cmd = [
            'ffmpeg', '-y',
            '-i', str(input_path),
            '-af', f'loudnorm=I={target_db}:TP=-1.5:LRA=11',
            str(output_path)
        ]
        
        result = subprocess.run(cmd, capture_output=True, text=True)
        
        if result.returncode != 0:
            raise Exception(f"音量标准化失败: {result.stderr}")
        
        return output_path