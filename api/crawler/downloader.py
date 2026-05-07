"""
视频/音频下载模块
"""
import asyncio
import subprocess
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse
import aiohttp
import httpx
from loguru import logger

from config import get_settings


class VideoDownloader:
    """视频下载器"""
    
    def __init__(self, download_dir: Path = None):
        settings = get_settings()
        self.download_dir = download_dir or settings.VIDEOS_DIR
        self.audio_dir = settings.AUDIO_DIR
        self.download_dir.mkdir(parents=True, exist_ok=True)
        self.audio_dir.mkdir(parents=True, exist_ok=True)
    
    async def download_video(
        self,
        url: str,
        filename: str = None,
        timeout: int = 120,
        headers: dict = None,
    ) -> Path:
        """
        下载视频
        
        Args:
            url: 视频URL
            filename: 保存文件名（不含扩展名）
            timeout: 超时时间（秒）
            headers: 自定义请求头
        
        Returns:
            保存的文件路径
        """
        if not filename:
            # 从URL生成文件名
            parsed = urlparse(url)
            filename = parsed.path.split('/')[-1] or "video"
        
        # 确保文件名唯一
        video_path = self.download_dir / f"{filename}.mp4"
        counter = 1
        while video_path.exists():
            video_path = self.download_dir / f"{filename}_{counter}.mp4"
            counter += 1
        
        logger.info(f"开始下载视频: {url}")
        
        # 默认请求头（模拟浏览器）
        default_headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "*/*",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Referer": "https://www.douyin.com/",
            "Accept-Encoding": "identity",
        }
        if headers:
            default_headers.update(headers)
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    url, 
                    headers=default_headers,
                    timeout=aiohttp.ClientTimeout(total=timeout)
                ) as resp:
                    if resp.status == 200:
                        with open(video_path, 'wb') as f:
                            async for chunk in resp.content.iter_chunked(8192):
                                f.write(chunk)
                        logger.info(f"视频下载完成: {video_path}")
                        return video_path
                    else:
                        raise Exception(f"下载失败，状态码: {resp.status}")
        except Exception as e:
            logger.error(f"视频下载失败: {e}")
            raise
    
    def download_video_ffmpeg(self, url: str, output_path: Path = None) -> Path:
        """使用 ffmpeg 下载视频（支持 m3u8）"""
        if not output_path:
            output_path = self.download_dir / "video.mp4"
        
        cmd = [
            'ffmpeg', '-y',
            '-i', url,
            '-c', 'copy',
            str(output_path)
        ]
        
        logger.info(f"ffmpeg 下载视频: {url}")
        result = subprocess.run(cmd, capture_output=True, text=True)
        
        if result.returncode != 0:
            logger.error(f"ffmpeg 错误: {result.stderr}")
            raise Exception("ffmpeg 下载失败")
        
        logger.info(f"视频下载完成: {output_path}")
        return output_path
    
    def extract_audio(
        self,
        video_path: Path,
        audio_format: str = "wav",
        sample_rate: int = 16000,
    ) -> Path:
        """
        从视频中提取音频
        
        Args:
            video_path: 视频文件路径
            audio_format: 音频格式 (wav/mp3)
            sample_rate: 采样率
        
        Returns:
            音频文件路径
        """
        audio_path = self.audio_dir / f"{video_path.stem}.{audio_format}"
        
        logger.info(f"提取音频: {video_path} -> {audio_path}")
        
        # 先检查视频是否有音频流
        probe_cmd = [
            'ffprobe', '-v', 'quiet',
            '-show_streams', '-select_streams', 'a',  # 只检查音频流
            '-print_format', 'json',
            str(video_path)
        ]
        
        try:
            probe_result = subprocess.run(probe_cmd, capture_output=True, text=True)
            import json
            probe_data = json.loads(probe_result.stdout)
            streams = probe_data.get('streams', [])
            
            if not streams:
                logger.warning(f"视频无音频轨道: {video_path}")
                # 创建一个静音音频文件（1秒）
                silence_cmd = [
                    'ffmpeg', '-y',
                    '-f', 'lavfi',
                    '-i', 'anullsrc=r=16000:cl=mono',
                    '-t', '1',
                    '-acodec', 'pcm_s16le' if audio_format == 'wav' else 'libmp3lame',
                    str(audio_path)
                ]
                silence_result = subprocess.run(silence_cmd, capture_output=True, text=True)
                if silence_result.returncode == 0:
                    logger.info(f"已创建静音音频文件: {audio_path}")
                    return audio_path
                else:
                    logger.error(f"创建静音音频失败: {silence_result.stderr}")
                    raise Exception("视频无音频且创建静音音频失败")
        except json.JSONDecodeError:
            logger.warning("无法解析ffprobe输出，继续尝试提取音频")
        
        # 正常提取音频
        cmd = [
            'ffmpeg', '-y',
            '-i', str(video_path),
            '-vn',  # 不包含视频
            '-acodec', 'pcm_s16le' if audio_format == 'wav' else 'libmp3lame',
            '-ar', str(sample_rate),
            '-ac', '1',  # 单声道
            str(audio_path)
        ]
        
        result = subprocess.run(cmd, capture_output=True, text=True)
        
        if result.returncode != 0:
            logger.error(f"音频提取失败: {result.stderr}")
            raise Exception("音频提取失败")
        
        logger.info(f"音频提取完成: {audio_path}")
        return audio_path
    
    def extract_thumbnail(self, video_path: Path) -> Path:
        """
        从视频中提取首帧作为缩略图
        
        Args:
            video_path: 视频文件路径
        
        Returns:
            缩略图文件路径
        """
        from config import get_settings
        
        settings = get_settings()
        # 缩略图文件名与视频ID一致
        thumbnail_path = settings.THUMBNAIL_DIR / f"{video_path.stem}.jpg"
        thumbnail_path.parent.mkdir(parents=True, exist_ok=True)
        
        logger.info(f"提取缩略图: {video_path} -> {thumbnail_path}")
        
        cmd = [
            'ffmpeg', '-y',
            '-i', str(video_path),      # 输入视频
            '-ss', '00:00:01.000',      # 从第1秒选取帧
            '-vframes', '1',            # 只取1帧
            '-vf', 'scale=320:-1',      # 缩放宽度320，高度自适应
            '-q:v', '2',                # 图片质量
            str(thumbnail_path)          # 输出文件
        ]
        
        result = subprocess.run(cmd, capture_output=True, text=True)
        
        if result.returncode != 0:
            logger.warning(f"缩略图提取失败: {result.stderr[:200]}")
            raise Exception(f"缩略图提取失败")
        
        logger.info(f"缩略图提取完成: {thumbnail_path}")
        return thumbnail_path
    
    def get_thumbnail_url(self, video_filename: str) -> str:
        """
        构造缩略图URL
        
        Args:
            video_filename: 视频文件名（不含扩展名）
        
        Returns:
            缩略图相对路径
        """
        return f"/thumbnails/{video_filename}.jpg"
    
    async def download_video_and_extract_thumbnail(
        self,
        url: str,
        filename: str = None,
    ) -> tuple[Path, Path]:
        """
        下载视频并提取缩略图
        
        Returns:
            (视频路径, 缩略图路径)
        """
        # 诪先下载视频
        video_path = await self.download_video(url, filename)
        
        # 提取缩略图
        try:
            thumbnail_path = self.extract_thumbnail(video_path)
        except Exception as e:
            logger.error(f"提取缩略图失败: {e}")
            # 创建一个占位符图片
            thumbnail_path = await self.create_placeholder_image(video_path)
        
        return video_path, thumbnail_path
    
    async def create_placeholder_image(self, video_path: Path) -> Path:
        """
        创建占位符缩略图
        
        Args:
            video_path: 视频文件路径
        
        Returns:
            占位符图片路径
        """
        from config import get_settings
        import os
        
        settings = get_settings()
        placeholder_path = settings.THUMBNAIL_DIR / f"{video_path.stem}_placeholder.jpg"
        placeholder_path.parent.mkdir(parents=True, exist_ok=True)
        
        # 使用FFmpeg创建一个简单的占位符
        cmd = [
            'ffmpeg',
            '-f', 'lavfi', '-i', 'color=size=320x240:color=black',  # 黑色背景
            '-pix_fmt', 'yuv420p',
            '-vframes', '1',
            str(placeholder_path)
        ]
        
        result = subprocess.run(cmd, capture_output=True, text=True)
        
        if result.returncode != 0:
            logger.error(f"创建占位符图片失败: {result.stderr}")
            raise Exception("创建占位符图片失败")
        
        logger.info(f"占位符图片创建完成: {placeholder_path}")
        return placeholder_path
    
    async def get_video_duration(self, video_path: Path) -> float:
        """获取视频时长（秒）"""
        cmd = [
            'ffprobe', '-v', 'error',
            '-show_entries', 'format=duration',
            '-of', 'default=noprint_wrappers=1:nokey=1',
            str(video_path)
        ]
        
        result = subprocess.run(cmd, capture_output=True, text=True)
        
        if result.returncode == 0:
            return float(result.stdout.strip())
        return 0.0
    
    async def download_and_extract_audio(
        self,
        video_url: str,
        filename: str = None,
    ) -> tuple[Path, Path]:
        """
        下载视频并提取音频
        
        Returns:
            (视频路径, 音频路径)
        """
        # 下载视频
        video_path = await self.download_video(video_url, filename)
        
        # 提取音频
        audio_path = self.extract_audio(video_path)
        
        return video_path, audio_path
    
    async def download_audio_from_url(
        self,
        audio_url: str,
        filename: str = None,
        timeout: int = 120,
    ) -> Path:
        """
        直接从URL下载音频文件
        
        Args:
            audio_url: 音频URL
            filename: 保存文件名
            timeout: 超时时间
        
        Returns:
            音频文件路径
        """
        if not filename:
            filename = "audio"
        
        audio_path = self.audio_dir / f"{filename}.mp3"
        counter = 1
        while audio_path.exists():
            audio_path = self.audio_dir / f"{filename}_{counter}.mp3"
            counter += 1
        
        logger.info(f"下载音频: {audio_url}")
        
        headers = {
            "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) AppleWebKit/605.1.15",
            "Referer": "https://www.douyin.com/",
        }
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    audio_url, 
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=timeout)
                ) as resp:
                    if resp.status == 200:
                        with open(audio_path, 'wb') as f:
                            async for chunk in resp.content.iter_chunked(8192):
                                f.write(chunk)
                        logger.info(f"音频下载完成: {audio_path}")
                        return audio_path
                    else:
                        raise Exception(f"下载失败，状态码: {resp.status}")
        except Exception as e:
            logger.error(f"音频下载失败: {e}")
            raise
    
    def convert_to_wav(self, audio_path: Path, sample_rate: int = 16000) -> Path:
        """
        将音频转换为 WAV 格式（ASR 需要）
        
        Args:
            audio_path: 音频文件路径
            sample_rate: 采样率
        
        Returns:
            WAV 文件路径
        """
        wav_path = self.audio_dir / f"{audio_path.stem}.wav"
        
        if audio_path.suffix == ".wav":
            return audio_path
        
        logger.info(f"转换音频格式: {audio_path} -> {wav_path}")
        
        cmd = [
            'ffmpeg', '-y',
            '-i', str(audio_path),
            '-acodec', 'pcm_s16le',
            '-ar', str(sample_rate),
            '-ac', '1',
            str(wav_path)
        ]
        
        result = subprocess.run(cmd, capture_output=True, text=True)
        
        if result.returncode != 0:
            logger.error(f"音频转换失败: {result.stderr}")
            raise Exception("音频转换失败")
        
        return wav_path
    
    async def download_video_with_audio(
        self, 
        video_url: str, 
        audio_url: str = "", 
        filename: str = None,
        timeout: int = 120
    ) -> Path:
        """
        下载视频（自动处理音频）
        
        Args:
            video_url: 视频URL
            audio_url: 音频URL（可选）
            filename: 保存文件名（不含扩展名）
            timeout: 下载超时时间
        
        Returns:
            视频文件路径
        """
        if not filename:
            from urllib.parse import urlparse
            parsed = urlparse(video_url)
            filename = parsed.path.split('/')[-1] or "video"
        
        # 临时文件路径
        temp_video_path = self.download_dir / f"{filename}_temp.mp4"
        temp_audio_path = self.audio_dir / f"{filename}_temp.m4a"
        final_video_path = self.download_dir / f"{filename}.mp4"
        
        # 确保文件名唯一
        counter = 1
        while final_video_path.exists():
            final_video_path = self.download_dir / f"{filename}_{counter}.mp4"
            counter += 1
        
        logger.info(f"开始下载视频: {video_url[:80]}...")
        
        # 检测是否是小红书CDN
        is_xiaohongshu = 'xhscdn.com' in video_url
        referer = "https://www.xiaohongshu.com/" if is_xiaohongshu else "https://www.douyin.com/"
        
        # 默认请求头
        headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "*/*",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Referer": referer,
            "Accept-Encoding": "identity",
        }
        
        try:
            # 1. 下载视频文件
            await self._download_file(video_url, temp_video_path, headers, timeout)
            
            # 2. 检测视频轨道是否存在
            if not self._check_video_track(temp_video_path):
                self._cleanup_files([temp_video_path])
                raise Exception("下载的文件无视频轨道，可能不是有效的视频文件（可能是图片或图文内容）")
            
            # 3. 检测视频是否有音频轨道
            has_audio = self._check_audio_track(temp_video_path)
            
            if has_audio:
                # 情况A: 视频自带音频，直接重命名返回
                temp_video_path.rename(final_video_path)
                logger.info(f"视频已自带音频: {final_video_path}")
                return final_video_path
            
            # 4. 视频无音频，需要处理
            if audio_url:
                # 情况B: 有独立音频URL，下载并合并
                logger.info(f"下载独立音频: {audio_url[:80]}...")
                try:
                    await self._download_file(audio_url, temp_audio_path, headers, timeout)
                    
                    # 合并视频和音频
                    self._merge_video_audio(temp_video_path, temp_audio_path, final_video_path)
                    
                    # 清理临时文件
                    self._cleanup_files([temp_video_path, temp_audio_path])
                    
                    logger.info(f"视频音频合并完成: {final_video_path}")
                    return final_video_path
                    
                except Exception as e:
                    logger.warning(f"独立音频下载失败: {e}，尝试创建静音音频")
                    # 继续尝试情况C
            
            # 情况C: 无音频或音频下载失败，生成静音音频
            video_duration = await self.get_video_duration(temp_video_path)
            silence_path = self._create_silence_audio_file(filename, max(video_duration, 1.0))
            
            # 合并视频和静音音频
            self._merge_video_audio(temp_video_path, silence_path, final_video_path)
            
            # 清理临时文件
            self._cleanup_files([temp_video_path, silence_path])
            
            logger.info(f"已添加静音音频: {final_video_path}")
            return final_video_path
            
        except Exception as e:
            logger.error(f"视频下载处理失败: {e}")
            # 清理可能的临时文件
            self._cleanup_files([temp_video_path, temp_audio_path])
            raise

    async def _download_file(self, url: str, output_path: Path, headers: dict = None, timeout: int = 120):
        """下载文件到指定路径"""
        default_headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
            "Referer": "https://www.douyin.com/",
        }
        if headers:
            default_headers.update(headers)
        
        # 检测是否是小红书CDN，使用httpx
        is_xiaohongshu = 'xhscdn.com' in url
        
        if is_xiaohongshu:
            # 小红书使用httpx流式下载
            async with httpx.AsyncClient(timeout=httpx.Timeout(timeout, connect=30.0)) as client:
                async with client.stream('GET', url, headers=default_headers, follow_redirects=True) as response:
                    if response.status_code == 200:
                        output_path.parent.mkdir(parents=True, exist_ok=True)
                        with open(output_path, 'wb') as f:
                            async for chunk in response.aiter_bytes(chunk_size=8192):
                                f.write(chunk)
                        logger.info(f"文件下载完成(httpx): {output_path}")
                    else:
                        raise Exception(f"下载失败，状态码: {response.status_code}")
        else:
            # 其他平台使用aiohttp
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    url, 
                    headers=default_headers,
                    timeout=aiohttp.ClientTimeout(total=timeout)
                ) as resp:
                    if resp.status == 200:
                        output_path.parent.mkdir(parents=True, exist_ok=True)
                        with open(output_path, 'wb') as f:
                            async for chunk in resp.content.iter_chunked(8192):
                                f.write(chunk)
                        logger.info(f"文件下载完成(aiohttp): {output_path}")
                    else:
                        raise Exception(f"下载失败，状态码: {resp.status}")

    def _check_audio_track(self, video_path: Path) -> bool:
        """检查视频是否有音频轨道"""
        probe_cmd = [
            'ffprobe', '-v', 'quiet',
            '-show_streams', '-select_streams', 'a',
            '-print_format', 'json',
            str(video_path)
        ]
        
        try:
            import json
            result = subprocess.run(probe_cmd, capture_output=True, text=True)
            data = json.loads(result.stdout)
            streams = data.get('streams', [])
            
            has_audio = len(streams) > 0
            logger.info(f"音频轨道检测: {'有' if has_audio else '无'}")
            return has_audio
            
        except Exception as e:
            logger.warning(f"音频轨道检测失败: {e}")
            return False

    def _check_video_track(self, video_path: Path) -> bool:
        """检查视频是否有视频轨道"""
        probe_cmd = [
            'ffprobe', '-v', 'quiet',
            '-show_streams', '-select_streams', 'v',
            '-print_format', 'json',
            str(video_path)
        ]
        
        try:
            import json
            result = subprocess.run(probe_cmd, capture_output=True, text=True)
            data = json.loads(result.stdout)
            streams = data.get('streams', [])
            
            has_video = len(streams) > 0
            logger.info(f"视频轨道检测: {'有' if has_video else '无'}")
            return has_video
            
        except Exception as e:
            logger.warning(f"视频轨道检测失败: {e}")
            return False

    def _merge_video_audio(self, video_path: Path, audio_path: Path, output_path: Path):
        """合并视频和音频文件"""
        logger.info(f"合并视频音频: {video_path.name} + {audio_path.name}")
        
        if not self._check_video_track(video_path):
            logger.error(f"文件无视频轨道: {video_path}")
            raise Exception("文件无视频轨道，可能不是有效的视频文件")
        
        cmd = [
            'ffmpeg', '-y',
            '-i', str(video_path),
            '-i', str(audio_path),
            '-c:v', 'copy',
            '-c:a', 'aac',
            '-strict', 'experimental',
            '-map', '0:v:0',
            '-map', '1:a:0',
            str(output_path)
        ]
        
        result = subprocess.run(cmd, capture_output=True, text=True)
        
        if result.returncode != 0:
            logger.error(f"音视频合并失败: {result.stderr}")
            raise Exception("音视频合并失败")
        
        logger.info(f"合并完成: {output_path}")

    def _create_silence_audio_file(self, filename: str, duration: float) -> Path:
        """创建静音音频文件"""
        audio_path = self.audio_dir / f"{filename}_silence.m4a"
        
        logger.info(f"创建静音音频: {audio_path}, 时长: {duration}秒")
        
        cmd = [
            'ffmpeg', '-y',
            '-f', 'lavfi',
            '-i', f'anullsrc=r=16000:cl=mono',
            '-t', str(duration),
            '-acodec', 'aac',
            str(audio_path)
        ]
        
        result = subprocess.run(cmd, capture_output=True, text=True)
        
        if result.returncode != 0:
            logger.error(f"创建静音音频失败: {result.stderr}")
            raise Exception("创建静音音频失败")
        
        return audio_path

    def _cleanup_files(self, file_paths: list):
        """清理临时文件"""
        for path in file_paths:
            try:
                if path and path.exists():
                    path.unlink()
                    logger.debug(f"已清理临时文件: {path}")
            except Exception as e:
                logger.warning(f"清理文件失败: {path}, {e}")