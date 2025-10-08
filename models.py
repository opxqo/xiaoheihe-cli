"""
小黑盒帖子数据模型
"""
from typing import List, Optional
from pydantic import BaseModel, Field
from datetime import datetime

class Emoji(BaseModel):
    """表情信息"""
    name: str = Field(..., description="表情名称")
    emoji_id: str = Field(..., description="表情ID")
    sprite_url: str = Field(..., description="雪碧图URL")
    background_position: str = Field(..., description="背景位置")

class Medal(BaseModel):
    """勋章信息"""
    medal_id: int = Field(..., description="勋章ID")
    name: str = Field(..., description="勋章名称")
    description: str = Field(default="", description="勋章描述")
    img_url: str = Field(default="", description="勋章图标URL")
    level: int = Field(default=0, description="勋章等级")
    achieved: int = Field(default=0, description="是否已获得(1=已获得,0=未获得)")
    wear: int = Field(default=0, description="是否佩戴(1=佩戴,0=未佩戴)")

class PostAuthor(BaseModel):
    """帖子作者信息"""
    name: str = Field(..., description="作者名称")
    level: str = Field(default="", description="用户等级")
    avatar_url: Optional[str] = Field(None, description="头像URL")
    user_id: Optional[str] = Field(None, description="用户ID")
    medals: List[Medal] = Field(default_factory=list, description="用户勋章列表")
    wearing_medal: Optional[Medal] = Field(None, description="当前佩戴的勋章")

class PostStats(BaseModel):
    """帖子统计信息"""
    likes: int = Field(default=0, description="点赞数")
    favorites: int = Field(default=0, description="收藏数")
    comments: int = Field(default=0, description="评论数")

class PostImage(BaseModel):
    """帖子图片信息"""
    url: str = Field(..., description="图片URL")
    local_path: Optional[str] = Field(None, description="本地保存路径")

class PostVideo(BaseModel):
    """帖子视频信息"""
    url: str = Field(..., description="视频URL")
    poster: Optional[str] = Field(None, description="视频封面图URL")
    width: Optional[int] = Field(None, description="视频宽度")
    height: Optional[int] = Field(None, description="视频高度")
    duration: Optional[float] = Field(None, description="视频时长（秒）")

class CommentImage(BaseModel):
    """评论图片信息（包含完整元数据）"""
    url: str = Field(..., description="图片URL")
    thumb: Optional[str] = Field(None, description="缩略图URL")
    width: Optional[int] = Field(None, description="图片宽度")
    height: Optional[int] = Field(None, description="图片高度")
    local_path: Optional[str] = Field(None, description="本地保存路径")

class CommentUser(BaseModel):
    """评论用户信息"""
    user_id: str = Field(..., description="用户ID")
    name: str = Field(..., description="用户名称")
    level: str = Field(default="", description="用户等级")
    avatar_url: Optional[str] = Field(None, description="头像URL")
    medals: List[Medal] = Field(default_factory=list, description="用户勋章列表")
    wearing_medal: Optional[Medal] = Field(None, description="当前佩戴的勋章")

class Comment(BaseModel):
    """评论信息"""
    comment_id: str = Field(..., description="评论ID")
    author: CommentUser = Field(..., description="评论作者")
    content: str = Field(..., description="评论内容")
    emojis: List[Emoji] = Field(default_factory=list, description="评论中的表情")
    time: str = Field(..., description="评论时间")
    location: str = Field(default="", description="评论地点")
    likes: int = Field(default=0, description="点赞数")
    floor_num: int = Field(default=0, description="楼层号")
    images: List[CommentImage] = Field(default_factory=list, description="评论图片")
    reply_to: Optional[CommentUser] = Field(None, description="回复的用户")
    child_comments: List["Comment"] = Field(default_factory=list, description="子评论")

class Post(BaseModel):
    """小黑盒帖子完整信息"""
    post_id: str = Field(..., description="帖子ID")
    url: str = Field(..., description="帖子URL")
    title: str = Field(..., description="帖子标题")
    content: str = Field(..., description="帖子正文内容")
    author: PostAuthor = Field(..., description="作者信息")
    tags: List[str] = Field(default_factory=list, description="帖子标签")
    images: List[PostImage] = Field(default_factory=list, description="帖子图片")
    video: Optional[PostVideo] = Field(None, description="帖子视频")
    stats: PostStats = Field(default_factory=PostStats, description="统计信息")
    time: str = Field(..., description="发布时间")
    location: str = Field(default="", description="发布地点")
    comments: List[Comment] = Field(default_factory=list, description="评论列表")
    crawled_at: datetime = Field(default_factory=datetime.now, description="爬取时间")

    class Config:
        json_encoders = {
            datetime: lambda v: v.isoformat()
        }

