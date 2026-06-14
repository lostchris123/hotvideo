from sqlalchemy import Column, Integer, String, Text, Boolean, DateTime, UniqueConstraint
from sqlalchemy.ext.declarative import declarative_base
from datetime import datetime

Base = declarative_base()


class VideoPrompt(Base):
    __tablename__ = "video_prompts"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    video_id = Column(String(100), nullable=False, comment="video id")
    platform = Column(String(50), nullable=False, comment="platform: douyin, xiaohongshu, shipinhao")
    prompt_type = Column(String(30), nullable=False, comment="prompt type: script, style, scene, character")
    prompt_content = Column(Text, nullable=False, comment="prompt content")
    version = Column(Integer, default=1, comment="version number")
    is_edited = Column(Boolean, default=False, comment="whether edited by user")
    original_content = Column(Text, comment="original content before editing")
    created_at = Column(DateTime, default=datetime.now, comment="created time")
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now, comment="updated time")
    
    __table_args__ = (
        UniqueConstraint("video_id", "platform", "prompt_type", "version", name="unique_video_prompt"),
    )
    
    def to_dict(self):
        return {
            "id": self.id,
            "video_id": self.video_id,
            "platform": self.platform,
            "prompt_type": self.prompt_type,
            "prompt_content": self.prompt_content,
            "version": self.version,
            "is_edited": self.is_edited,
            "original_content": self.original_content,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None
        }


PROMPT_TYPES = {
    "script": "video script prompt",
    "style": "visual style prompt",
    "scene": "scene prompt",
    "character": "character prompt"
}
