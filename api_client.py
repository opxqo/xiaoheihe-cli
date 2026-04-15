"""
小黑盒后端 API 调用层。

读接口通过拦截页面真实请求获取数据；发布接口复用页面已生成的签名参数，
直接调用后端接口保存草稿或发布内容。
"""
from __future__ import annotations

import asyncio
import json
import logging
import urllib.parse
from contextlib import asynccontextmanager
from typing import Optional

import requests

logger = logging.getLogger(__name__)

_BASE_URL = "https://www.xiaoheihe.cn"
_API_BASE = "https://api.xiaoheihe.cn"


class XiaoheiheAPIClient:
    """小黑盒后端 API 客户端。"""

    def __init__(self, page):
        self._page = page
        self._heybox_id: Optional[str] = None

    async def close(self):
        """关闭客户端（页面生命周期由 BrowserManager 管理）。"""

    def set_heybox_id(self, heybox_id: str):
        self._heybox_id = heybox_id

    async def get_post_comments(
        self,
        link_id: str,
        page_num: int = 1,
        limit: int = 20,
        is_first: int = 1,
    ) -> Optional[dict]:
        # 当前帖子链路依赖页面自身请求，分页参数仍由前端实际行为决定。
        # page_num / limit 保留在接口层，避免破坏现有调用方签名。
        _ = (page_num, limit, is_first)
        return await self._navigate_and_intercept(
            link_id=link_id,
            api_pattern="/bbs/app/link/tree",
        )

    async def get_post_full(self, link_id: str, page_size: int = 100) -> Optional[dict]:
        all_comments = []
        seen_page_markers: set[str] = set()
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

            comment_groups = result["result"].get("comments", [])
            marker = ""
            if comment_groups and isinstance(comment_groups[0], dict):
                first_group = comment_groups[0].get("comment", [])
                if first_group and isinstance(first_group[0], dict):
                    marker = str(first_group[0].get("commentid", ""))

            if marker and marker in seen_page_markers:
                logger.warning("检测到评论分页未前进，提前停止 full 抓取: link_id=%s", link_id)
                break
            if marker:
                seen_page_markers.add(marker)

            for group in comment_groups:
                if "comment" in group:
                    all_comments.extend(group["comment"])

            last_comments = (comment_groups or [[]])[-1].get("comment", [])
            has_more = bool(last_comments and last_comments[0].get("has_more") == 1)
            if not has_more:
                break

            page_num += 1
            await asyncio.sleep(1.0)

        if result and "result" in result:
            result["result"]["_all_comments"] = all_comments
        return result

    async def get_emojis(self) -> Optional[dict]:
        return await self._navigate_and_intercept(
            link_id="1",
            api_pattern="/bbs/app/api/emojis/list",
            url=f"{_BASE_URL}/app/bbs/home",
        )

    async def get_related_posts(self, link_id: str) -> Optional[dict]:
        return await self._navigate_and_intercept(
            link_id=link_id,
            api_pattern="/bbs/app/link/related/recommend_web",
        )

    async def get_creator_data(self, link_id: str) -> Optional[dict]:
        return await self._navigate_and_intercept(
            link_id=link_id,
            api_pattern="/bbs/app/author/concept/article/data",
            url=f"{_BASE_URL}/creator/content_management/detail/{link_id}",
        )

    async def get_article_list(self) -> Optional[dict]:
        return await self._navigate_and_intercept(
            link_id="list",
            api_pattern="/bbs/app/author/concept/article/list",
            url=f"{_BASE_URL}/creator/content_management/home",
        )

    async def publish_article(
        self,
        title: str,
        html_content: str,
        link_tag: int = 11,
        draft: bool = True,
        edit_link_id: Optional[str] = None,
    ) -> Optional[dict]:
        """
        发布文章或保存草稿。

        做法：
        1. 读取浏览器里的登录 Cookie。
        2. 访问创作者后台，拦截一条已签名的 GET 请求，复用其 query 参数。
        3. 用相同会话直接 POST 到发布接口。
        """
        all_cookies = await self._page.context.cookies()
        cookie_dict = {
            c["name"]: c["value"]
            for c in all_cookies
            if c.get("name") and c.get("value")
        }
        if not cookie_dict:
            logger.error("无 Cookie，请先登录")
            return {"success": False, "message": "未登录，请先运行 login"}

        heybox_id = cookie_dict.get("user_heybox_id") or cookie_dict.get("heybox_id") or "0"
        device_id = cookie_dict.get("device_id") or "02f61cdb0c14c026f9e6865d7744b86e"

        text_json = json.dumps([{"text": html_content, "type": "html"}], ensure_ascii=False)
        post_body = {
            "text": text_json,
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
            "heybox_id": heybox_id,
        }
        if edit_link_id:
            post_body["edit"] = "1"
            post_body["link_id"] = edit_link_id

        signature_params = await self._get_signature_query_params(
            url=f"{_BASE_URL}/creator/content_management/home",
            api_pattern="/bbs/app/author/concept/article/list",
        )
        if not signature_params:
            logger.error("无法获取签名参数")
            return {"success": False, "message": "无法获取签名参数，请尝试重新登录"}

        query_params = {}
        for key in (
            "os_type",
            "app",
            "client_type",
            "version",
            "web_version",
            "x_client_type",
            "x_app",
            "x_os_type",
            "device_info",
            "device_id",
            "hkey",
            "_time",
            "nonce",
        ):
            if signature_params.get(key):
                query_params[key] = signature_params[key]

        query_params["heybox_id"] = signature_params.get("heybox_id") or heybox_id
        query_params.setdefault("device_id", device_id)
        query_params.setdefault("device_info", "Chrome")

        post_url = f"{_API_BASE}/bbs/app/api/link/post?{urllib.parse.urlencode(query_params)}"
        headers = {
            "Origin": "https://www.xiaoheihe.cn",
            "Referer": "https://www.xiaoheihe.cn/",
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36"
            ),
            "accept": "application/json",
            "content-type": "application/x-www-form-urlencoded;charset=utf-8",
            "DNT": "1",
            "Sec-Fetch-Dest": "empty",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Site": "same-site",
        }

        try:
            response = await asyncio.to_thread(
                requests.post,
                post_url,
                data=post_body,
                cookies=cookie_dict,
                headers=headers,
                timeout=15,
            )
            result = response.json()
        except Exception as e:
            logger.error("发布请求异常: %s", e)
            return {"success": False, "message": f"请求异常: {e}", "is_draft": draft}

        action = "草稿" if draft else "发布"
        if result.get("status") == "ok":
            link_id = result.get("link_id") or (result.get("result") or {}).get("link_id")
            logger.info("✅ %s成功! link_id=%s", action, link_id)
            return {
                "success": True,
                "link_id": link_id,
                "message": f"{action}成功",
                "is_draft": draft,
                "raw": result,
            }

        msg = result.get("msg") or result.get("message") or "发布失败"
        logger.warning("❌ %s失败: %s", action, msg)
        return {"success": False, "message": msg, "is_draft": draft, "raw": result}

    async def _get_signature_query_params(
        self,
        *,
        url: str,
        api_pattern: str = "/bbs/app/",
    ) -> Optional[dict]:
        """拦截一条已签名 GET 请求，提取完整 query 参数。"""
        captured = [None]
        ready = asyncio.Event()

        async def on_response(resp):
            if api_pattern in resp.url and resp.request.method == "GET":
                try:
                    parsed = urllib.parse.urlparse(resp.url)
                    qs = urllib.parse.parse_qs(parsed.query)
                    if qs.get("hkey") and qs.get("_time") and qs.get("nonce"):
                        captured[0] = {
                            key: values[0]
                            for key, values in qs.items()
                            if values
                        }
                        ready.set()
                except Exception:
                    pass

        self._page.on("response", on_response)
        try:
            await self._page.goto(url, wait_until="domcontentloaded", timeout=20000)
            try:
                await asyncio.wait_for(ready.wait(), timeout=8.0)
            except asyncio.TimeoutError:
                await asyncio.sleep(2)
        except Exception as e:
            logger.warning("签名获取导航异常: %s", e)
        finally:
            try:
                self._page.remove_listener("response", on_response)
            except Exception:
                pass

        return captured[0]

    async def call_api(
        self,
        endpoint: str,
        *,
        method: str = "GET",
        body: Optional[dict] = None,
        base_url: str = _API_BASE,
    ) -> Optional[dict]:
        url = f"{base_url}{endpoint}"
        try:
            result = await self._page.evaluate(
                """async ({url, method, bodyStr}) => {
                    const options = {
                        method,
                        credentials: 'include',
                        headers: { 'Content-Type': 'application/json' },
                    };
                    if (method !== 'GET' && bodyStr) {
                        options.body = bodyStr;
                    }
                    const resp = await fetch(url, options);
                    return await resp.json();
                }""",
                {
                    "url": url,
                    "method": method.upper(),
                    "bodyStr": json.dumps(body) if body else None,
                },
            )
            if isinstance(result, dict):
                if result.get("status") == "show_captcha":
                    logger.error("Cookie 失效 (验证码): %s", endpoint)
                    return None
                if result.get("status") != "ok":
                    logger.warning("API 返回异常 (status=%s): %s", result.get("status"), endpoint)
                return result
            return None
        except Exception as e:
            logger.error("fetch API 失败 (%s %s): %s", method, url, e)
            return None

    @asynccontextmanager
    async def _intercept(self, api_pattern: str):
        captured: list[Optional[dict]] = [None]
        received = asyncio.Event()

        async def on_response(response):
            if api_pattern in response.url:
                try:
                    captured[0] = json.loads(await response.text())
                    received.set()
                except Exception:
                    pass

        self._page.on("response", on_response)
        try:
            yield captured, received
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
        if url is None:
            url = f"{_BASE_URL}/app/bbs/link/{link_id}"

        try:
            async with self._intercept(api_pattern) as (captured, received):
                await self._page.goto(url, wait_until="domcontentloaded", timeout=30000)
                try:
                    await asyncio.wait_for(received.wait(), timeout=10.0)
                except asyncio.TimeoutError:
                    await asyncio.sleep(2)

            data = captured[0]
            if data is None:
                logger.error("未捕获到有效 API 响应 (url=%s)", url)
                return None
            if data.get("status") == "show_captcha":
                logger.error("Cookie 被验证码拦截，当前链路不可用: %s", url)
                return None
            return data
        except Exception as e:
            logger.error("API 请求失败 (url=%s): %s", url, e)
            return None
