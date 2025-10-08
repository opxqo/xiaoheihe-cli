"""
小黑盒爬虫API服务器
提供RESTful API接口，可通过HTTP请求访问
"""
from fastapi import FastAPI, HTTPException, Query, BackgroundTasks
from fastapi.responses import JSONResponse, FileResponse
from fastapi.middleware.cors import CORSMiddleware
from typing import List, Optional
from pydantic import BaseModel, Field
import uvicorn
from pathlib import Path
import asyncio
from contextlib import asynccontextmanager

from xiaoheihe_crawler import XiaoHeiHeCrawler
from models import Post, Comment

crawler = None

@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理 - 启动时打开浏览器，关闭时释放"""
    global crawler
    
    print("="*80)
    print("正在启动API服务器...")
    print("="*80)
    
    crawler = XiaoHeiHeCrawler(headless=False, silent=True)
    await crawler.init_browser()
    
    print("[OK] 浏览器已启动并保持连接（显示窗口模式，提高响应速度）")
    print("[OK] API服务器准备就绪")
    print("="*80)
    
    yield
    
    print("\n[INFO] 正在关闭浏览器...")
    await crawler.close_browser()
    print("[OK] 服务器已关闭")

app = FastAPI(
    title="小黑盒爬虫API",
    description="高性能小黑盒帖子和评论爬取服务 | 浏览器持久化连接",
    version="2.0.0",
    docs_url=None,
    redoc_url=None,
    lifespan=lifespan
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class PostResponse(BaseModel):
    """帖子响应模型"""
    success: bool = Field(..., description="是否成功")
    message: str = Field(..., description="消息")
    data: Optional[dict] = Field(None, description="帖子数据")

class CommentsResponse(BaseModel):
    """评论响应模型"""
    success: bool = Field(..., description="是否成功")
    message: str = Field(..., description="消息")
    data: Optional[dict] = Field(None, description="评论数据")
    total: int = Field(0, description="评论总数")

class BatchPostRequest(BaseModel):
    """批量爬取请求模型"""
    urls: List[str] = Field(..., description="帖子URL列表")

@app.get("/", tags=["首页"])
async def root():
    """API根路径"""
    return JSONResponse({
        "name": "小黑盒爬虫API",
        "version": "2.0.0",
        "status": "running",
        "browser": "persistent",
        "docs": "/docs",
        "endpoints": {
            "获取帖子": "GET /api/post/{post_id}",
            "获取评论": "GET /api/post/{post_id}/comments",
            "批量爬取": "POST /api/posts/batch",
            "登录二维码": "GET /api/qrcode"
        }
    })

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
            heading-text="小黑盒爬虫API - 浏览器持久化连接"
            show-method-in-nav-bar="as-colored-text"
            use-path-in-nav-bar="true"
        >
            <div slot="overview">
                <h2>🚀 高性能爬虫API</h2>
                <p>✅ 浏览器持久化连接 - 提高响应速度</p>
                <p>✅ 帖子和评论分离 - 灵活获取数据</p>
                <p>✅ 评论分页支持 - 处理大量数据</p>
                <p>✅ 纯JSON输出 - 标准RESTful API</p>
            </div>
        </rapi-doc>
    </body>
    </html>
    """
    from fastapi.responses import HTMLResponse
    return HTMLResponse(content=html)

@app.get("/api/post/{post_id}", response_model=PostResponse, tags=["帖子"])
async def get_post(
    post_id: str
):
    """
    获取单个帖子（不包含评论，API不下载图片）
    
    参数：
    - post_id: 帖子ID（例如：163964486）
    
    示例：
    - http://localhost:8000/api/post/163964486
    
    注意：API响应仅返回图片URL，不下载图片到服务器
    """
    try:
        url = f"https://www.xiaoheihe.cn/app/bbs/link/{post_id}"
        post = await crawler.crawl_post(url, download_images=False)
        
        if not post:
            raise HTTPException(status_code=404, detail="帖子不存在或爬取失败")
        
        post_dict = post.model_dump()
        post_dict['comments'] = []
        post_dict['comments_count'] = len(post.comments)
        
        return PostResponse(
            success=True,
            message="成功获取帖子",
            data=post_dict
        )
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"服务器错误: {str(e)}")

@app.get("/api/post/{post_id}/comments", response_model=CommentsResponse, tags=["评论"])
async def get_post_comments(
    post_id: str,
    page: int = Query(1, ge=1, description="页码（从1开始）"),
    page_size: int = Query(20, ge=1, le=100, description="每页数量（最多100）")
):
    """
    获取帖子的评论列表（分页）
    
    参数：
    - post_id: 帖子ID
    - page: 页码（默认1）
    - page_size: 每页数量（默认20，最多100）
    
    示例：
    - http://localhost:8000/api/post/165574066/comments
    - http://localhost:8000/api/post/165574066/comments?page=1&page_size=10
    """
    try:
        url = f"https://www.xiaoheihe.cn/app/bbs/link/{post_id}"
        post = await crawler.crawl_post(url, download_images=False)
        
        if not post:
            raise HTTPException(status_code=404, detail="帖子不存在或爬取失败")
        
        total = len(post.comments)
        start = (page - 1) * page_size
        end = start + page_size
        comments_page = post.comments[start:end]
        
        import json
        comments_dict = []
        for c in comments_page:
            c_dict = c.model_dump()
            comments_dict.append(c_dict)
        
        return CommentsResponse(
            success=True,
            message=f"成功获取评论（第{page}页）",
            data={
                "comments": comments_dict,
                "page": page,
                "page_size": page_size,
                "has_next": end < total
            },
            total=total
        )
        
    except HTTPException:
        raise
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"服务器错误: {str(e)}")

@app.post("/api/posts/batch", tags=["帖子"])
async def batch_crawl_posts(request: BatchPostRequest, background_tasks: BackgroundTasks):
    """
    批量爬取多个帖子
    
    请求体示例：
    ```json
    {
        "urls": [
            "https://www.xiaoheihe.cn/app/bbs/link/163964486",
            "https://www.xiaoheihe.cn/app/bbs/link/165445708"
        ],
        "download_images": true
    }
    ```
    
    返回：任务已提交的信息（后台处理）
    """
    try:
        async def batch_task():
            results = []
            for url in request.urls:
                post = await crawler.crawl_post(url, download_images=False)
                if post:
                    results.append(post)
                await asyncio.sleep(2.0)
        
        background_tasks.add_task(batch_task)
        
        return {
            "success": True,
            "message": f"已提交{len(request.urls)}个帖子的爬取任务",
            "task_count": len(request.urls),
            "note": "任务在后台执行，请查看data目录获取结果"
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"服务器错误: {str(e)}")

@app.get("/api/qrcode", tags=["登录"])
async def get_login_qrcode():
    """
    获取登录二维码
    
    返回：二维码图片（PNG格式）
    
    示例：
    - http://localhost:8000/api/qrcode
    """
    try:
        qr_path = await crawler.get_login_qrcode()
        
        if not qr_path or not Path(qr_path).exists():
            raise HTTPException(status_code=500, detail="二维码生成失败")
        
        return FileResponse(
            qr_path,
            media_type="image/png",
            filename="login_qrcode.png"
        )
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"服务器错误: {str(e)}")

@app.get("/api/post/{post_id}/full", tags=["帖子"])
async def get_post_with_comments(
    post_id: str
):
    """
    获取完整帖子（包含所有评论，不下载图片）
    
    注意：如果评论很多，建议使用分页接口
    
    示例：
    - http://localhost:8000/api/post/163964486/full
    
    注意：API响应仅返回图片URL，不下载图片到服务器
    """
    try:
        url = f"https://www.xiaoheihe.cn/app/bbs/link/{post_id}"
        post = await crawler.crawl_post(url, download_images=False)
        
        if not post:
            raise HTTPException(status_code=404, detail="帖子不存在或爬取失败")
        
        return PostResponse(
            success=True,
            message="成功获取完整帖子",
            data=post.model_dump()
        )
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"服务器错误: {str(e)}")

@app.get("/health", tags=["系统"])
async def health_check():
    """健康检查"""
    return JSONResponse({
        "status": "healthy",
        "service": "xiaoheihe-crawler-api",
        "version": "2.0.0",
        "browser_ready": crawler.browser is not None if crawler else False
    })

if __name__ == "__main__":
    print("="*80)
    print("小黑盒爬虫API服务器")
    print("="*80)
    print("\n服务地址：")
    print("  - API文档: http://localhost:8000/docs")
    print("  - 备用文档: http://localhost:8000/redoc")
    print("  - 健康检查: http://localhost:8000/health")
    print("\nAPI端点示例：")
    print("  - 获取帖子: http://localhost:8000/api/post/163964486")
    print("  - 获取评论: http://localhost:8000/api/post/165574066/comments")
    print("  - 登录二维码: http://localhost:8000/api/qrcode")
    print("\n按 Ctrl+C 停止服务器")
    print("="*80)
    
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=8010,
        log_level="info"
    )

