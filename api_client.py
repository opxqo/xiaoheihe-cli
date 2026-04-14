"""
小黑盒后端 API 调用层：通过拦截页面自身请求获取数据
（服务器要求请求必须由页面自身的 JS 代码发起，自定义请求会被拒绝 "非法请求"）
发布接口使用 Route 拦截 + 页面内 fetch 方案复用签名参数。
"""
import asyncio
import json
import logging
from contextlib import asynccontextmanager
from typing import Optional

logger = logging.getLogger(__name__)

_BASE_URL = "https://www.xiaoheihe.cn"
_API_BASE = "https://api.xiaoheihe.cn"


class XiaoheiheAPIClient:
    """小黑盒后端 API 客户端（基于页面自身 JS 请求拦截）"""

    def __init__(self, page):
        self._page = page
        self._heybox_id: Optional[str] = None

    async def close(self):
        """关闭客户端（无需操作，页面由 BrowserManager 管理）"""

    def set_heybox_id(self, heybox_id: str):
        self._heybox_id = heybox_id

    # ==================== 公开 API ====================

    async def get_post_comments(
        self,
        link_id: str,
        page_num: int = 1,
        limit: int = 20,
        is_first: int = 1,
    ) -> Optional[dict]:
        """获取帖子评论树"""
        return await self._navigate_and_intercept(
            link_id=link_id,
            api_pattern="/bbs/app/link/tree",
        )

    async def get_post_full(
        self, link_id: str, page_size: int = 100
    ) -> Optional[dict]:
        """获取完整帖子数据（循环分页获取所有评论）"""
        all_comments = []
        page_num = 1
        result = None

        while True:
            result = await self.get_post_comments(
                link_id=link_id,
                page_num=page_num,
                limit=page_size,
                is_first=1 if page_num == 1 else 0,
            )

            if not result or "result" not in result:
                break

            result_data = result["result"]
            comments_data = result_data.get("comments", [])

            for comment_group in comments_data:
                if "comment" in comment_group:
                    all_comments.extend(comment_group["comment"])

            has_more = False
            if comments_data:
                last_comment = comments_data[-1].get("comment", [])
                if last_comment:
                    has_more = last_comment[0].get("has_more", 0) == 1

            if not has_more:
                break

            page_num += 1
            await asyncio.sleep(1.0)

        if result and "result" in result:
            result["result"]["_all_comments"] = all_comments

        return result

    async def get_emojis(self) -> Optional[dict]:
        """获取表情列表"""
        return await self._navigate_and_intercept(
            link_id="1",
            api_pattern="/bbs/app/api/emojis/list",
            url=f"{_BASE_URL}/app/bbs/home",
        )

    async def get_related_posts(self, link_id: str) -> Optional[dict]:
        """获取相似帖子推荐"""
        return await self._navigate_and_intercept(
            link_id=link_id,
            api_pattern="/bbs/app/link/related/recommend_web",
        )

    async def get_creator_data(self, link_id: str) -> Optional[dict]:
        """获取创作者后台文章详情"""
        return await self._navigate_and_intercept(
            link_id=link_id,
            api_pattern="/bbs/app/author/concept/article/data",
            url=f"{_BASE_URL}/creator/content_management/detail/{link_id}",
        )

    async def get_article_list(self) -> Optional[dict]:
        """获取创作者已发布的文章列表"""
        return await self._navigate_and_intercept(
            link_id="list",
            api_pattern="/bbs/app/author/concept/article/list",
            url=f"{_BASE_URL}/creator/content_management/home",
        )

    # ==================== 发布接口 ====================

    async def publish_article(
        self,
        title: str,
        html_content: str,
        link_tag: int = 11,
        draft: bool = True,
        edit_link_id: Optional[str] = None,
    ) -> Optional[dict]:
        """
        发布（或保存草稿）文章。

        核心思路：通过 Playwright Route 拦截页面发出的 API 请求，
        从中提取完整的签名参数（hkey/nonce/_time 等），
        然后在页面 JS 上下文中用 fetch 发送 POST 请求到发布接口。

        重要：不做页面导航（导航会销毁执行上下文），而是通过 JS fetch
        触发一个轻量 API 请求来激活路由拦截器。

        Args:
            title: 文章标题
            html_content: HTML 格式的正文内容
            link_tag: 标签ID（默认11=校园生活）
            draft: True=存草稿箱，False=正式发布
            edit_link_id: 编辑已有文章时传入 link_id

        Returns:
            API 响应 dict
        """
        import urllib.parse

        # 构造 POST body
        text_payload = json.dumps(
            [{"text": html_content, "type": "html"}],
            ensure_ascii=False,
        )
        post_body_dict = {
            "text": text_payload,
            "title": title,
            "desc": "",
            "post_type": "1",
            "view_limit": "1",
            "link_tag": str(link_tag),
            "words_count": str(len(html_content)),
            "original": "1",
            "declaration": "1",
            "extra_declaration": "-1",
            "draft": "1" if draft else "0",
        }
        if edit_link_id:
            post_body_dict["edit"] = "1"
            post_body_dict["link_id"] = edit_link_id
        post_body_raw = urllib.parse.urlencode(post_body_dict)

        result_holder: list[Optional[dict]] = [None]
        triggered = [False]
        nav_done = asyncio.Event()

        async def _intercept_and_publish(route):
            """拦截第一个 GET API 请求，提取签名参数，发 POST 发布请求"""
            url = route.request.url
            if (
                not triggered[0]
                and route.request.method == "GET"
                and "/bbs/app/" in url
            ):
                triggered[0] = True
                logger.info("拦截到API请求，复用签名参数...")

                # 先放行原始请求
                await route.continue_()

                # 从原始 URL 提取 query string（含 hkey/nonce/_time/h_src 等完整签名参数）
                parsed = urllib.parse.urlparse(url)
                qs = parsed.query
                post_url = f"{_API_BASE}/bbs/app/api/link/post?{qs}"

                try:
                    # 等待导航完成后再 evaluate（避免 Execution context destroyed）
                    await asyncio.sleep(1)
                    resp = await self._page.evaluate(
                        """async ([postUrl, rawBody]) => {
                            const r = await fetch(postUrl, {
                                method: 'POST',
                                headers: {
                                    'Content-Type': 'application/x-www-form-urlencoded;charset=utf-8'
                                },
                                body: rawBody,
                                credentials: 'include',
                            });
                            return { status: r.status, body: await r.text() };
                        }""",
                        [post_url, post_body_raw],
                    )
                    data = json.loads(resp["body"]) if isinstance(resp.get("body"), str) else {}
                    result_holder[0] = data
                    nav_done.set()
                    api_status = data.get("status", "?")
                    msg = data.get("msg", "")
                    if api_status == "ok":
                        link_id = (data.get("result") or {}).get("link_id")
                        action = "草稿" if draft else "发布"
                        logger.info("✅ %s成功! link_id=%s", action, link_id)
                    else:
                        logger.warning("❌ API返回: status=%s msg=%s", api_status, msg)
                except Exception as e:
                    logger.error("发布请求异常: %s", e)
                    nav_done.set()
            else:
                await route.continue_()

        # 注册路由拦截器
        await self._page.route(f"{_API_BASE}/**", _intercept_and_publish)

        try:
            # 用 JS fetch 触发一个轻量 API 请求（不导航，不销毁执行上下文）
            # 首页加载时会自动触发 /bbs/app/api/emojis/list 等 API 调用
            await self._page.evaluate("""async () => {
                // 触发表情列表等内部API调用，让路由拦截器捕获签名参数
                await fetch('/bbs/app/api/emojis/list?os_type=web&app=heybox&client_type=web', {
                    credentials: 'include'
                });
            }""")

            # 等待拦截完成（最多15秒）
            try:
                await asyncio.wait_for(nav_done.wait(), timeout=15.0)
            except asyncio.TimeoutError:
                if not triggered[0]:
                    logger.error("未触发拦截（可能页面未在有效状态或无API请求）")
                elif result_holder[0] is None:
                    logger.error("拦截已触发但发布请求未完成")
        except Exception as e:
            logger.error("发布流程异常: %s", e)
        finally:
            # 移除路由拦截器
            try:
                await self._page.unroute(f"{_API_BASE}/**")
            except Exception:
                pass

        return result_holder[0]

    # ==================== Fetch 模式（不导航，复用已有页面） ====================

    async def call_api(
        self,
        endpoint: str,
        *,
        method: str = "GET",
        body: Optional[dict] = None,
        base_url: str = "https://api.xiaoheihe.cn",
    ) -> Optional[dict]:
        """
        在当前页面内通过 JS fetch() 调用 API，**不触发页面导航，不会弹验证码**。

        Args:
            endpoint: API 路径（如 /bbs/app/link/tree）
            method: HTTP 方法 (GET / POST)
            body: POST 请求的 JSON body
            base_url: API 基础 URL
        Returns:
            解析后的 JSON dict 或 None
        """
        url = f"{base_url}{endpoint}"
        try:
            result = await self._page.evaluate("""async ({url, method, bodyStr}) => {
                const opts = {
                    method: method,
                    credentials: 'include',
                    headers: { 'Content-Type': 'application/json' },
                };
                if (method !== 'GET' && bodyStr) opts.body = bodyStr;
                const resp = await fetch(url, opts);
                return await resp.json();
            }""", {"url": url, "method": method.upper(), "bodyStr": json.dumps(body) if body else None})

            if isinstance(result, dict):
                status = result.get("status")
                if status == "show_captcha":
                    logger.error("Cookie 失效 (验证码): %s", endpoint)
                    return None
                if status != "ok":
                    logger.warning("API 返回异常 (status=%s): %s", status, endpoint)
                return result
            logger.warning("API 返回非 JSON: %s", type(result))
            return None

        except Exception as e:
            logger.error("fetch API 调用失败 (%s %s): %s", method, url, e)
            return None

    # ==================== 核心拦截逻辑 ====================

    @asynccontextmanager
    async def _intercept(self, api_pattern: str):
        """
        响应拦截上下文管理器 —— 进入/退出自动注册/移除监听器。

        Yields:
            list[Optional[dict]]: 长度为1的列表，拦截结果存入 [0]
        """
        captured: list[Optional[dict]] = [None]

        async def on_response(response):
            if api_pattern in response.url:
                try:
                    body = await response.text()
                    captured[0] = json.loads(body)
                except (json.JSONDecodeError, Exception):
                    pass

        listener = self._page.on("response", on_response)
        try:
            yield captured
        finally:
            try:
                self._page.remove_listener("response", on_response)
            except Exception:
                pass

    async def _navigate_and_intercept(
        self,
        link_id: str,
        api_pattern: str,
        url: Optional[str] = None,
    ) -> Optional[dict]:
        """导航到页面并拦截指定的 API 响应"""
        if url is None:
            url = f"{_BASE_URL}/app/bbs/link/{link_id}"

        try:
            async with self._intercept(api_pattern) as captured:
                await self._page.goto(url, wait_until="networkidle", timeout=30000)
                await asyncio.sleep(2)

            data = captured[0]
            if data is None:
                logger.error("未捕获到有效 API 响应 (url=%s)", url)
                return None

            status = data.get("status")
            if status == "ok":
                return data
            if status == "show_captcha":
                logger.error("验证码失效，需要重新登录")
                return None
            return data

        except Exception as e:
            logger.error("API 请求失败 (url=%s): %s", url, e)
            return None
