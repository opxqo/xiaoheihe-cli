"""
浏览器管理模块：Cookie 持久化、验证、登录流程

支持三种模式：
1. Cookie 注入（从浏览器复制或环境变量）— 最快
2. 密码登录（操作前端 JS 表单，自动 RSA 加密）— 推荐
3. 验证码登录（UI 自动化）— 备用

核心设计：
- 所有页面导航统一使用 /home 首页（不是 /app/bbs/home）
- 密码登录通过拦截前端 JS 提交的请求完成，不自己拼参数
- 登录成功后自动保存 Cookie，后续命令无需再登录
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from pathlib import Path
from typing import Optional, List

from playwright.async_api import async_playwright, Browser, BrowserContext

logger = logging.getLogger(__name__)

_BASE_URL = "https://www.xiaoheihe.cn"
_API_BASE = "https://api.xiaoheihe.cn"


class BrowserManager:
    """管理 Playwright 浏览器实例，负责 Cookie 的加载、验证和持久化"""

    COOKIES_FILE = "cookies.json"

    def __init__(self, headless: bool = False):
        self.headless = headless
        self._playwright = None
        self._browser: Optional[Browser] = None
        self._context: Optional[BrowserContext] = None
        self._api_page = None
        self._cookies: List[dict] = []
        self._heybox_id: Optional[str] = None

    @property
    def cookies(self) -> List[dict]:
        return self._cookies

    @property
    def heybox_id(self) -> Optional[str]:
        return self._heybox_id

    @property
    def api_page(self):
        return self._api_page

    # ==================== 初始化 ====================

    async def init(self):
        """初始化：尝试加载已保存的 Cookie，验证有效性"""
        saved_cookies = self._load_cookies()

        if saved_cookies:
            logger.info("已加载保存的 Cookie (%d 个)", len(saved_cookies))
            self._cookies = saved_cookies
            self._heybox_id = self._extract_heybox_id(saved_cookies)

            if await self._verify_cookies():
                logger.info("Cookie 有效，直接使用 API 模式")
                await self._ensure_api_page()
                return

            logger.warning("Cookie 已过期，需要重新登录")
            self._cookies = []
            self._heybox_id = None

        # 无有效 Cookie，打开浏览器让用户登录
        await self.open_browser_for_login()

    async def inject_cookies(self, cookie_string: str):
        """
        从浏览器 Cookie 字符串直接注入（跳过登录/验证码流程）。
        格式: "key1=val1; key2=val2; ..."
        """
        cookies = self._parse_cookie_string(cookie_string)
        if not cookies:
            raise ValueError("无法解析 Cookie 字符串")

        logger.info("已从字符串解析 %d 个 Cookie", len(cookies))
        self._cookies = cookies
        self._heybox_id = self._extract_heybox_id(cookies)

        if self._heybox_id:
            logger.info("用户 ID: %s", self._heybox_id)

        self._save_cookies(cookies)
        logger.info("Cookie 已保存到 %s", self.COOKIES_FILE)

    async def start_session(self):
        """启动会话：导航到首页后常驻，后续 API 全走 fetch() 不触发导航"""
        await self._ensure_api_page()
        try:
            await self._api_page.goto(
                f"{_BASE_URL}/home",
                wait_until="domcontentloaded",
                timeout=30000,
            )
            logger.info("会话就绪 (单页模式, heybox_id=%s)", self._heybox_id)
        except Exception as e:
            logger.warning("首页导航异常: %s", e)

    @staticmethod
    def _parse_cookie_string(cookie_string: str) -> List[dict]:
        from urllib.parse import unquote
        cookies = []
        for pair in cookie_string.split(";"):
            pair = pair.strip()
            if "=" not in pair:
                continue
            name, value = pair.split("=", 1)
            name, value = name.strip(), unquote(value.strip())
            if name:
                cookies.append({
                    "name": name, "value": value,
                    "domain": ".xiaoheihe.cn", "path": "/",
                    "sameSite": "Lax", "secure": False,
                })
        return cookies

    async def _verify_cookies(self) -> bool:
        """验证 Cookie 是否有效"""
        if not self._cookies:
            return False

        if not self._context:
            await self._launch_browser(headless=True)

        captured = [None]
        verified = asyncio.Event()

        async def on_response(response):
            if "/bbs/app/link/tree" in response.url:
                try:
                    body = await response.text()
                    captured[0] = json.loads(body)
                    verified.set()
                except Exception:
                    pass

        try:
            test_page = await self._context.new_page()
            test_page.on("response", on_response)
            try:
                await test_page.goto(
                    f"{_BASE_URL}/app/bbs/link/179245676",
                    wait_until="domcontentloaded", timeout=15000,
                )
                try:
                    await asyncio.wait_for(verified.wait(), timeout=8.0)
                except asyncio.TimeoutError:
                    await asyncio.sleep(2)
            finally:
                try:
                    test_page.remove_listener("response", on_response)
                except Exception:
                    pass
                await test_page.close()

            data = captured[0]
            return data is not None and data.get("status") == "ok"
        except Exception as e:
            logger.warning("Cookie 验证失败: %s", e)
            return False

    async def _ensure_api_page(self):
        """确保存在用于 API 请求的页面"""
        if self._api_page and not self._api_page.is_closed():
            return

        if not self._context:
            await self._launch_browser(headless=self.headless)

        self._api_page = await self._context.new_page()

    async def _launch_browser(self, headless: Optional[bool] = None):
        """统一浏览器启动逻辑，优先使用本机 Chrome，失败时回退 Playwright Chromium。"""
        if self._context:
            return

        if self._playwright is None:
            self._playwright = await async_playwright().start()

        launch_headless = self.headless if headless is None else headless
        try:
            self._browser = await self._playwright.chromium.launch(
                channel="chrome",
                headless=launch_headless,
            )
        except Exception as exc:
            logger.warning("Chrome 通道启动失败，回退 Playwright Chromium: %s", exc)
            self._browser = await self._playwright.chromium.launch(
                headless=launch_headless,
            )

        self._context = await self._browser.new_context()
        if self._cookies:
            await self._context.add_cookies(self._cookies)

    # ==================== 登录流程 ====================

    async def open_browser_for_login(self):
        """打开浏览器等待用户手动登录，轮询检测 Cookie"""
        print("\n" + "=" * 60)
        print("需要登录")
        print("正在打开浏览器，请在浏览器中完成登录...")
        print("=" * 60 + "\n")

        await self._launch_browser(headless=self.headless)

        page = await self._context.new_page()
        await page.goto(f"{_BASE_URL}/home", wait_until="domcontentloaded", timeout=30000)
        logger.info("浏览器已打开，等待用户登录...")

        max_wait, check_interval = 300, 3
        for elapsed in range(0, max_wait, check_interval):
            await asyncio.sleep(check_interval)
            captured = [None]

            async def on_check(response):
                if "/bbs/app/link/tree" in response.url:
                    try:
                        captured[0] = json.loads(await response.text())
                    except Exception:
                        pass

            check_page = await self._context.new_page()
            listener = check_page.on("response", on_check)
            try:
                await check_page.goto(f"{_BASE_URL}/app/bbs/link/179245676",
                                     wait_until="domcontentloaded", timeout=15000)
                await asyncio.sleep(3)
                status = (captured[0] or {}).get("status", "")
                if status == "ok":
                    logger.info("检测到有效 Cookie！")
                    self._cookies = await self._context.cookies()
                    self._heybox_id = self._extract_heybox_id(self._cookies)
                    self._save_cookies(self._cookies)
                    print(f"\n  ✅ 登录成功! 用户 ID: {self._heybox_id}")
                    await page.close()
                    await check_page.close()
                    await self._ensure_api_page()
                    return
            except Exception:
                pass
            finally:
                try:
                    check_page.remove_listener("response", on_check)
                except Exception:
                    pass
                await check_page.close()

        logger.warning("超时 (%ds)", max_wait)
        await page.close()

    async def api_login(self, phone: str, password: str) -> bool:
        """
        密码登录（通过前端 JS 表单提交）。

        策略：
        1. 导航到 /home 首页
        2. 点击登录按钮打开弹窗 → 切换到密码 tab
        3. 填入手机号+密码（前端 JS 自动 RSA 加密）
        4. 拦截 /account/login/ POST 响应获取结果
        5. 保存 Cookie 到本地

        所有签名参数(hkey/nonce/_time)、加密逻辑、额外字段均由前端原生处理。
        """
        print("\n" + "=" * 60)
        print(f"📱 小黑盒密码登录")
        print(f"   号码: {phone}")
        print("=" * 60)

        # 1. 启动浏览器
        await self._launch_browser(headless=self.headless)
        page = await self._context.new_page()

        # 2. 注册登录响应拦截器（在导航前！）
        login_result: list[Optional[dict]] = [None]
        login_done = asyncio.Event()

        async def _on_login_resp(response):
            if "/account/login/" in response.url and response.request.method == "POST":
                try:
                    text = await response.text()
                    logger.info("📥 登录响应: %s", text[:300] if len(text) > 300 else text)
                    login_result[0] = json.loads(text)
                except Exception as e:
                    login_result[0] = {"error": str(e)}
                finally:
                    login_done.set()

        page.on("response", _on_login_resp)

        try:
            # 3. 导航到登录页（直接用独立登录页，不走 /home 弹窗）
            await page.goto("https://login.xiaoheihe.cn/", wait_until="domcontentloaded", timeout=30000)
            await asyncio.sleep(2)

            # 4. 切换密码 tab
            await self._switch_pwd_tab(page)

            # 6. 填手机号
            phone_clean = re.sub(r'^[+]?86\s*', '', phone.strip())
            if not await self._fill_input(page, phone_clean,
                  ['input[type="tel"]', 'input[inputmode="tel"]', 'input[name="phone"]',
                   'input[type="text"]'], "手机号"):
                raise RuntimeError("未找到手机号输入框")

            # 7. 填密码
            if not await self._fill_input(page, password,
                  ['input[type="password"]', 'input[name="password"]',
                   'input[placeholder*="密码"]'], "密码"):
                raise RuntimeError("未找到密码输入框")

            # 8. 勾选协议
            await self._check_agreement(page)

            # 9. 点登录按钮
            if not await _click_submit_btn(page):
                raise RuntimeError("未找到登录提交按钮")

            # 10. 等待响应
            await asyncio.wait_for(login_done.wait(), timeout=15.0)

        except Exception as e:
            logger.error("登录流程异常: %s", e)
            print(f"\n  ❌ {e}")
        finally:
            try:
                page.remove_listener("response", _on_login_resp)
            except Exception:
                pass

        # 11. 处理结果
        data = login_result[0]
        if not data:
            print("\n  ❌ 未捕获到登录响应")
            return await self._fallback_manual(page)

        status = data.get("status", "")

        if status == "ok" or "account_detail" in data or "pkey" in data or "profile" in data:
            # 登录成功！等 Cookie 写入
            await asyncio.sleep(2)
            cookies = await self._context.cookies()
            self._cookies = cookies
            account_detail = data.get("account_detail", {})
            profile = data.get("profile", {})
            username = (account_detail.get("username") or profile.get("nickname") or "")
            self._heybox_id = (
                str(account_detail.get("userid", ""))
                or self._extract_heybox_id(cookies)
            )
            self._save_cookies(cookies)

            print(f"\n{'=' * 60}")
            print(f"  ✅ 登录成功!")
            print(f"  用户: {username} (ID: {self._heybox_id})")
            print(f"{'=' * 60}\n")

            await page.close()
            try:
                await self._ensure_api_page()
            except Exception as e:
                logger.warning("API 会话启动异常: %s", e)
            return True
        else:
            msg = data.get("msg") or data.get("message") or json.dumps(data)[:150]
            print(f"\n  ❌ 登录失败: {msg}")
            return await self._fallback_manual(page)

    async def login_with_phone(self, phone: str, code_callback=None, password=None) -> bool:
        """手机号登录入口（密码优先）"""
        if password:
            return await self.api_login(phone, password)
        return await self.login_with_phone_ui(phone, code_callback)

    async def login_with_phone_ui(self, phone: str, code_callback=None, password=None):
        """验证码 UI 登录（备用方案）"""
        mode = "密码" if password else "验证码"
        print(f"\n📱 小黑盒{mode}登录 (UI模式)\n   号码: {phone}\n")

        await self._launch_browser(headless=self.headless)
        page = await self._context.new_page()
        await page.goto(f"{_BASE_URL}/home", wait_until="domcontentloaded", timeout=30000)

        # 打开登录弹窗
        await self._click_login_entry(page)
        await asyncio.sleep(1.5)

        # 密码 tab
        if password:
            await self._switch_pwd_tab(page)

        # 填手机号
        phone_clean = re.sub(r'^[+]?86\s*', '', phone.strip())
        await self._fill_input(page, phone_clean,
            ['input[type="tel"]', 'input[inputmode="tel"]', 'input[name="phone"]', 'input[type="text"]'],
            "手机号", required=False)

        if password:
            await self._fill_input(page, password,
                ['input[type="password"]', 'input[name="password"]'], "密码", required=False)
        else:
            # 发验证码
            await self._click_send_code(page)
            code = (await code_callback() if code_callback else input("请输入验证码: ").strip()) or ""
            if len(code) < 4:
                print("❌ 验证码无效"); return False
            await self._fill_input(page, code,
                ['input[type="number"]', 'input[inputmode="numeric"]', 'input[name="code"]'],
                "验证码", required=False)

        await self._check_agreement(page)
        await _click_submit_btn(page)

        # 轮询检测结果
        for _ in range(20):
            await asyncio.sleep(3)
            cookies = await self._context.cookies()
            hid = self._extract_heybox_id(cookies)
            if hid:
                self._cookies, self._heybox_id = cookies, hid
                self._save_cookies(cookies)
                print(f"\n✅ 登录成功! ID: {hid}")
                await page.close(); await self._ensure_api_page(); return True

        print("\n❌ 登录超时")
        return False

    # ==================== UI 操作辅助方法 ====================

    @staticmethod
    async def _click_login_entry(page) -> bool:
        """点击页面上的登录按钮打开登录弹窗"""
        # 优先使用精确选择器（用户确认的 DOM 结构）
        for sel in ['button.login-btn', '[class*="login-btn"]',
                     'text="登录"', 'a[href*="login"]']:
            try:
                el = await page.wait_for_selector(sel, timeout=2000)
                if el and await el.is_visible():
                    await el.click(timeout=3000)
                    logger.info("✅ 点击登录入口: %s", sel)
                    await asyncio.sleep(1.5)
                    return True
            except Exception:
                continue
        logger.warning("未找到登录入口按钮")
        return False

    @staticmethod
    async def _switch_pwd_tab(page):
        """切换到密码登录 tab"""
        for sel in ['text="密码登录"', 'text="密码"',
                     ':has-text("密码"):not(:has-text("验证"))']:
            try:
                el = await page.query_selector(sel)
                if el and await el.is_visible():
                    await el.click(timeout=2000)
                    logger.info("切换到密码登录 tab")
                    await asyncio.sleep(1); return
            except Exception:
                continue

    @staticmethod
    async def _fill_input(page, value: str, selectors: list, label: str, required: bool = True) -> bool:
        """尝试用多个选择器填充输入框"""
        for sel in selectors:
            try:
                el = await page.wait_for_selector(sel, timeout=2000)
                if el and await el.is_visible():
                    await el.fill(value, timeout=3000)
                    logger.info("✅ 已填%s", label)
                    return True
            except Exception:
                continue
        if required:
            logger.warning("⚠️ 未找到%s输入框", label)
        return False

    @staticmethod
    async def _check_agreement(page):
        """勾选同意协议"""
        for sel in ['input[type="checkbox"]', '.el-checkbox__inner', 'input[name="agree"]',
                     '[class*="agree"] [class*="check"]', '[class*="check"]']:
            try:
                el = await page.query_selector(sel)
                if el and await el.is_visible():
                    checked = await el.evaluate("e => e.checked")
                    if not checked:
                        await el.click(timeout=2000)
                        logger.info("已勾选协议"); await asyncio.sleep(0.5)
                    return
            except Exception:
                continue

    async def _click_send_code(self, page):
        """点击发送验证码按钮"""
        for sel in ['text="获取验证码"', 'text="发送验证码"', 'text="获取"']:
            try:
                el = await page.query_selector(sel)
                if el and await el.is_visible():
                    await el.click(timeout=3000)
                    logger.info("已点击发送验证码"); return
            except Exception:
                continue
        # JS 兜底
        await page.evaluate("""() => {
            document.querySelectorAll('button').forEach(b => {
                if (/验证|发送|获取/.test(b.textContent)) b.click();
            });
        }""")

    async def _fallback_manual(self, page) -> bool:
        """回退：让用户手动完成登录"""
        print("\n  ⚠️ 自动登录未成功，请在浏览器中手动登录...")
        return await self._wait_for_login_result(page)

    async def _wait_for_login_result(self, page) -> bool:
        """等待用户手动登录完成"""
        for i in range(36):  # 3分钟
            await asyncio.sleep(5)
            cookies = await self._context.cookies()
            hid = self._extract_heybox_id(cookies)
            if hid:
                self._cookies, self._heybox_id = cookies, hid
                self._save_cookies(cookies)
                print(f"\n✅ 手动登录成功! ID: {hid}")
                await page.close(); await self._ensure_api_page(); return True
        print("\n❌ 等待超时"); return False

    # ==================== Cookie 刷新 & 关闭 ====================

    async def refresh_cookies(self):
        await self._shutdown()
        await self.open_browser_for_login()

    async def close(self):
        await self._shutdown()

    async def _shutdown(self):
        if self._api_page:
            try:
                await self._api_page.close()
            except Exception:
                pass
        self._api_page = None

        if self._context:
            try:
                await self._context.close()
            except Exception:
                pass
            self._context = None

        if self._browser:
            try:
                await self._browser.close()
            except Exception:
                pass
            self._browser = None

        if self._playwright:
            try:
                await self._playwright.stop()
            except Exception:
                pass
            self._playwright = None

    # ==================== Cookie 持久化 ====================

    def _load_cookies(self) -> List[dict]:
        p = Path(self.COOKIES_FILE)
        if p.exists():
            try:
                with open(p, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                logger.warning("加载 Cookie 失败: %s", e)
        return []

    def _save_cookies(self, cookies: List[dict]):
        p = Path(self.COOKIES_FILE)
        with open(p, "w", encoding="utf-8") as f:
            json.dump(cookies, f, ensure_ascii=False, indent=2)
        # 同时写入 ~/.xhh_cookie 供 CLI 命令（get/list/pub 等）复用登录态
        try:
            from config import save_cookie
            cookie_str = "; ".join(
                f"{c['name']}={c['value']}"
                for c in cookies if c.get("name") and c.get("value")
            )
            if cookie_str:
                save_cookie(cookie_str)
        except Exception as e:
            logger.warning("同步写入 ~/.xhh_cookie 失败: %s", e)

    @staticmethod
    def _extract_heybox_id(cookies: List[dict]) -> Optional[str]:
        for c in cookies:
            if c.get("name") in ("heybox_id", "user_heybox_id"):
                return c.get("value")
        return None


# ==================== 模块级辅助函数（供 api_login 使用）====================


async def _click_submit_btn(page) -> bool:
    """多层策略点击登录提交按钮"""
    # A: get_by_text 精确匹配
    try:
        btns = page.get_by_text("登录", exact=True)
        count = await btns.count()
        for i in range(count):
            btn = btns.nth(i)
            if await btn.is_visible():
                tag = await btn.evaluate("el => el.tagName")
                text = (await btn.inner_text()).strip()
                if tag in ("BUTTON", "A", "DIV", "SPAN") and len(text) <= 6:
                    await btn.click(force=True, timeout=3000)
                    logger.info("✅ 已点击提交 (get_by_text): '%s'", text)
                    return True
    except Exception:
        pass

    # B: CSS 选择器
    for sel in ['button[type="submit"]', '[class*="submit"]', '[class*="login-btn"]',
                 'button.el-button--primary']:
        try:
            el = await page.query_selector(sel)
            if el and await el.is_visible():
                await el.click(force=True, timeout=3000)
                logger.info("✅ 已点击提交 (CSS): %s", sel)
                return True
        except Exception:
            continue

    # C: JS 全量扫描
    try:
        found = await page.evaluate("""() => {
            const hits = [];
            document.querySelectorAll('button,[role="button"],a.btn,.btn').forEach(el => {
                const t = el.textContent.trim();
                if ((t.includes('登录') || t === '登 录') && el.offsetParent !== null) {
                    hits.push({tag: el.tagName, text: t});
                }
            });
            if (hits.length > 0) {
                document.querySelectorAll('button,[role="button"],a.btn,.btn').forEach(el => {
                    const t = el.textContent.trim();
                    if (t === hits[0].text) { el.click(); }
                });
            }
            return hits;
        }""")
        if found:
            logger.info("✅ 已点击提交 (JS扫描), 候选项=%d", len(found))
            return True
    except Exception as e:
        logger.error("JS 扫描失败: %s", e)

    logger.warning("❌ 未找到登录提交按钮")
    return False
