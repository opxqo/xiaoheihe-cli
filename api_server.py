"""
小黑盒爬虫API服务器 - 重构版
使用浏览器获取 Cookie 持久化，直接调用后端 API
"""
from fastapi import FastAPI, HTTPException, Query, BackgroundTasks
from fastapi.responses import JSONResponse, HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from typing import List, Optional
from pydantic import BaseModel, Field
import uvicorn
import asyncio
import os
import logging
from contextlib import asynccontextmanager

from browser_manager import BrowserManager
from api_client import XiaoheiheAPIClient
from data_parser import DataParser
from models import Post, Comment

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

browser_manager: Optional[BrowserManager] = None
api_client: Optional[XiaoheiheAPIClient] = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理：启动时初始化浏览器和客户端"""
    global browser_manager, api_client

    headless = os.getenv("HEADLESS", "true").lower() == "true"
    port = os.getenv("PORT", "8010")

    logger.info("=" * 60)
    logger.info("小黑盒爬虫API服务器 v2.0")
    logger.info(f"HEADLESS={headless}, PORT={port}")
    logger.info("=" * 60)

    browser_manager = BrowserManager(headless=headless)
    await browser_manager.init()

    api_client = XiaoheiheAPIClient(page=browser_manager.api_page)
    api_client.set_heybox_id(browser_manager.heybox_id)

    logger.info("API服务器准备就绪")
    logger.info("=" * 60)

    yield

    logger.info("正在关闭...")
    if api_client:
        await api_client.close()
    if browser_manager:
        await browser_manager.close()
    logger.info("服务器已关闭")


app = FastAPI(
    title="小黑盒爬虫API",
    description="小黑盒帖子和评论爬取服务（Cookie持久化 + 后端API直连）",
    version="2.0.0",
    docs_url=None,
    redoc_url=None,
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class BatchPostRequest(BaseModel):
    urls: List[str] = Field(..., description="帖子URL或ID列表")


class PostResponse(BaseModel):
    success: bool = Field(..., description="是否成功")
    message: str = Field(..., description="消息")
    data: Optional[dict] = Field(None, description="帖子数据")


class CommentsResponse(BaseModel):
    success: bool = Field(..., description="是否成功")
    message: str = Field(..., description="消息")
    data: Optional[dict] = Field(None, description="评论数据")
    total: int = Field(0, description="评论总数")


def _extract_link_id(value: str) -> str:
    """从 URL 或纯文本中提取帖子 ID"""
    if value.startswith("http"):
        import re

        match = re.search(r"/link/(\d+)", value)
        if match:
            return match.group(1)
    return value


@app.get("/", tags=["首页"])
async def root():
    """API根路径"""
    return JSONResponse(
        {
            "name": "小黑盒爬虫API",
            "version": "2.0.0",
            "status": "running",
            "auth": "cookie-persistent",
            "docs": "/docs",
            "endpoints": {
                "获取帖子": "GET /api/post/{post_id}",
                "获取评论": "GET /api/post/{post_id}/comments",
                "完整帖子": "GET /api/post/{post_id}/full",
                "批量爬取": "POST /api/posts/batch",
                "刷新Cookie": "POST /api/cookies/refresh",
            },
        }
    )


@app.get("/docs", include_in_schema=False)
async def custom_docs():
    """自定义API文档页面（RapiDoc）"""
    html = """
    <!doctype html>
    <html>
    <head>
        <meta charset="utf-8">
        <title>小黑盒爬虫API文档</title>
        <script type="module" src="https://unpkg.com/rapidoc/dist/rapidoc-min.js"></script>
    </head>
    <body>
        <rapi-doc
            spec-url="/openapi.json"
            theme="dark"
            bg-color="#1a1a1a"
            text-color="#ffffff"
            primary-color="#4CAF50"
            nav-bg-color="#252525"
            nav-text-color="#ffffff"
            nav-hover-bg-color="#333333"
            render-style="view"
            layout="column"
            schema-style="table"
            show-header="true"
            allow-try="true"
            allow-server-selection="false"
            allow-authentication="false"
            heading-text="小黑盒爬虫API - Cookie持久化 + API直连"
            show-method-in-nav-bar="as-colored-text"
            use-path-in-nav-bar="true"
        >
            <div slot="overview">
                <h2>爬虫API v2.0</h2>
                <p>Cookie持久化：首次浏览器登录，之后复用</p>
                <p>API直连：绕过前端渲染，直接调用后端接口</p>
                <p>评论分页：支持大量评论的分页获取</p>
                <p>纯JSON输出：标准RESTful API</p>
            </div>
        </rapi-doc>
    </body>
    </html>
    """
    return HTMLResponse(content=html)


@app.get("/api/post/{post_id}", response_model=PostResponse, tags=["帖子"])
async def get_post(post_id: str):
    """
    获取单个帖子（评论作为摘要）

    参数：
    - post_id: 帖子ID（例如：179245676）或完整URL

    示例：
    - http://localhost:8010/api/post/179245676
    """
    link_id = _extract_link_id(post_id)

    try:
        result = await api_client.get_post_comments(link_id, page_num=1, limit=20)

        if not result:
            raise HTTPException(status_code=404, detail="帖子不存在或Cookie已过期")

        post_meta, comments = DataParser.parse_comments_response(result)
        post = DataParser.parse_post_from_comments(
            link_id,
            f"https://www.xiaoheihe.cn/app/bbs/link/{link_id}",
            comments,
            post_meta=post_meta,
        )

        return PostResponse(
            success=True,
            message="成功获取帖子",
            data=post.model_dump(),
        )

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"服务器错误: {str(e)}")


@app.get(
    "/api/post/{post_id}/comments", response_model=CommentsResponse, tags=["评论"]
)
async def get_post_comments(
    post_id: str,
    page: int = Query(1, ge=1, description="页码（从1开始）"),
    page_size: int = Query(20, ge=1, le=100, description="每页数量（最多100）"),
):
    """
    获取帖子的评论列表（分页）

    参数：
    - post_id: 帖子ID
    - page: 页码（默认1）
    - page_size: 每页数量（默认20，最多100）

    示例：
    - http://localhost:8010/api/post/179245676/comments
    - http://localhost:8010/api/post/179245676/comments?page=1&page_size=10
    """
    link_id = _extract_link_id(post_id)

    try:
        result = await api_client.get_post_comments(
            link_id, page_num=page, limit=page_size, is_first=1
        )

        if not result:
            raise HTTPException(status_code=404, detail="帖子不存在或Cookie已过期")

        _, comments = DataParser.parse_comments_response(result)

        total = len(comments)

        return CommentsResponse(
            success=True,
            message=f"成功获取评论（第{page}页）",
            data={
                "comments": [c.model_dump() for c in comments],
                "page": page,
                "page_size": page_size,
            },
            total=total,
        )

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"服务器错误: {str(e)}")


@app.get("/api/post/{post_id}/full", tags=["帖子"])
async def get_post_with_comments(post_id: str):
    """
    获取完整帖子（包含所有评论）

    会自动分页获取全部评论数据

    示例：
    - http://localhost:8010/api/post/179245676/full
    """
    link_id = _extract_link_id(post_id)

    try:
        result = await api_client.get_post_full(link_id)

        if not result:
            raise HTTPException(status_code=404, detail="帖子不存在或Cookie已过期")

        post_meta, comments = DataParser.parse_comments_response(result)
        post = DataParser.parse_post_from_comments(
            link_id,
            f"https://www.xiaoheihe.cn/app/bbs/link/{link_id}",
            comments,
            post_meta=post_meta,
        )

        return PostResponse(
            success=True,
            message=f"成功获取完整帖子（{len(comments)} 条评论）",
            data=post.model_dump(),
        )

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"服务器错误: {str(e)}")


@app.post("/api/posts/batch", tags=["帖子"])
async def batch_crawl_posts(
    request: BatchPostRequest, background_tasks: BackgroundTasks
):
    """
    批量爬取多个帖子

    请求体示例：
    ```json
    {
        "urls": [
            "179245676",
            "https://www.xiaoheihe.cn/app/bbs/link/179290771"
        ]
    }
    ```

    返回：任务已提交的信息（后台处理）
    """
    try:

        async def batch_task():
            for url in request.urls:
                link_id = _extract_link_id(url)
                await api_client.get_post_comments(link_id, page_num=1, limit=20)
                await asyncio.sleep(2.0)

        background_tasks.add_task(batch_task)

        return {
            "success": True,
            "message": f"已提交{len(request.urls)}个帖子的爬取任务",
            "task_count": len(request.urls),
            "note": "任务在后台执行",
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"服务器错误: {str(e)}")


@app.post("/api/cookies/refresh", tags=["系统"])
async def refresh_cookies():
    """
    刷新Cookie

    会重新打开浏览器，让用户重新通过验证码登录
    """
    global browser_manager, api_client

    try:
        if browser_manager:
            await browser_manager.refresh_cookies()

            api_client = XiaoheiheAPIClient(page=browser_manager.api_page)
            api_client.set_heybox_id(browser_manager.heybox_id)

            return JSONResponse(
                {
                    "success": True,
                    "message": "Cookie已刷新",
                    "heybox_id": browser_manager.heybox_id,
                }
            )
        else:
            raise HTTPException(status_code=500, detail="服务器未初始化")

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"服务器错误: {str(e)}")


@app.get("/health", tags=["系统"])
async def health_check():
    """健康检查"""
    return JSONResponse(
        {
            "status": "healthy",
            "service": "xiaoheihe-crawler-api",
            "version": "2.0.0",
            "cookies_valid": len(browser_manager.cookies) > 0 if browser_manager else False,
            "heybox_id": browser_manager.heybox_id if browser_manager else None,
        }
    )


if __name__ == "__main__":
    port = int(os.getenv("PORT", "8010"))

    logger.info("服务地址：")
    logger.info(f"  - API文档: http://localhost:{port}/docs")
    logger.info(f"  - 健康检查: http://localhost:{port}/health")
    logger.info(f"  - 获取帖子: http://localhost:{port}/api/post/179245676")
    logger.info(f"  - 刷新Cookie: curl -X POST http://localhost:{port}/api/cookies/refresh")

    uvicorn.run(app, host="0.0.0.0", port=port, log_level="info")
