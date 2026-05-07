"""
解析视频号链接，获取视频信息
支持格式：https://weixin.qq.com/sph/xxx
"""
from urllib.parse import urlparse
from pydantic import BaseModel
from typing import Optional, Dict, Any


class ShipinhaoLinkInfo(BaseModel):
    """视频号链接信息"""
    original_url: str
    video_id: str
    platform: str = "shipinhao"
    finder_url: str
    status: str = "parsed"
    note: Optional[str] = None


def parse_shipinhao_link(url: str) -> Dict[str, Any]:
    """
    解析视频号链接

    Args:
        url: 视频号链接，如 https://weixin.qq.com/sph/AH2cIXqpzT

    Returns:
        解析结果字典
    """
    try:
        parsed = urlparse(url)

        # 检查是否是微信域名
        if 'weixin.qq.com' not in parsed.netloc and 'channels.weixin.qq.com' not in parsed.netloc:
            return {
                "error": "不是微信视频号链接",
                "original_url": url,
                "status": "error"
            }

        # 解析链接格式
        path_parts = parsed.path.strip('/').split('/')

        # 格式1: https://weixin.qq.com/sph/VIDEO_ID
        if 'sph' in path_parts:
            idx = path_parts.index('sph')
            if idx + 1 < len(path_parts):
                video_id = path_parts[idx + 1]
                return {
                    "original_url": url,
                    "video_id": video_id,
                    "platform": "shipinhao",
                    "finder_url": f"https://channels.weixin.qq.com/finder-preview/pages/sph?id={video_id}",
                    "status": "success",
                    "note": "链接解析成功，但视频内容需要微信登录态才能访问"
                }

        # 格式2: https://channels.weixin.qq.com/finder-preview/pages/sph?id=VIDEO_ID
        if 'channels.weixin.qq.com' in parsed.netloc:
            query_params = {}
            if parsed.query:
                for param in parsed.query.split('&'):
                    if '=' in param:
                        key, value = param.split('=', 1)
                        query_params[key] = value

            video_id = query_params.get('id')
            if video_id:
                return {
                    "original_url": url,
                    "video_id": video_id,
                    "platform": "shipinhao",
                    "finder_url": url,
                    "status": "success",
                    "note": "链接解析成功，但视频内容需要微信登录态才能访问"
                }

        return {
            "error": "不是有效的视频号链接格式",
            "original_url": url,
            "status": "error"
        }

    except Exception as e:
        return {
            "error": str(e),
            "original_url": url,
            "status": "error"
        }


# 测试
if __name__ == "__main__":
    test_urls = [
        "https://weixin.qq.com/sph/AH2cIXqpzT",
        "https://channels.weixin.qq.com/finder-preview/pages/sph?id=AH2cIXqpzT",
        "https://www.douyin.com/video/123",  # 非视频号链接
    ]

    import json
    for url in test_urls:
        print(f"\n解析: {url}")
        result = parse_shipinhao_link(url)
        print(json.dumps(result, indent=2, ensure_ascii=False))
