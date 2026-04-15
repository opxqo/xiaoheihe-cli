"""
小黑盒 CLI v2.4 — 多子命令架构，服务器友好
支持守护模式、多种输出格式、Cookie 自动管理

用法:
    xiaoheihe get 179245676              # 获取帖子
    xiaoheihe get 179245676 --full       # 完整帖子（含所有评论）
    xiaoheihe comments 179245676         # 只看评论
    xiaoheihe batch ids.txt              # 批量爬取
    xiaoheihe serve                      # 启动守护进程
    xiaoheihe status                     # 查看守护进程状态
    xiaoheihe list                       # 查看我的文章列表
    xiaoheihe pub "标题" -c "<p>内容</p>" # 发布文章
    XHH_COOKIE="xxx" xiaoheihe list      # 环境变量 Cookie（服务器部署推荐）
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import json
import logging
import os
import sys
from typing import Optional, Any

# Windows 终端强制 UTF-8，避免 GBK 编码报错
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# 确保项目根目录在 path 中
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from utils import extract_link_id, format_number, truncate_text, get_daemon_pid_path
from xiaoheihe import XiaoheiheClient, DaemonServer
from config import get_cookie, save_cookie, has_stored_cookie

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

# ANSI 颜色
GREEN = "\033[32m"
YELLOW = "\033[33m"
CYAN = "\033[36m"
BOLD = "\033[1m"
DIM = "\033[2m"
RESET = "\033[0m"


def color(text: str, code: str = "") -> str:
    """添加颜色（非 TTY 自动去除）"""
    if not sys.stdout.isatty():
        return text
    return f"{code}{text}{RESET}"


# ==================== 辅助函数 ====================


def _type_label(link_type: Any, has_video: int) -> str:
    """内容类型标签"""
    if has_video or "video" in str(link_type).lower():
        return "视频"
    if "image_text" in str(link_type).lower():
        return "图文"
    if link_type:
        return "文章"
    return ""


def _format_time_abs(create_at: Optional[int]) -> str:
    """Unix 时间戳 → 绝对日期字符串"""
    if not create_at:
        return ""
    try:
        import time as _time
        return _time.strftime("%Y-%m-%d %H:%M", _time.localtime(create_at))
    except (ValueError, OSError):
        return ""


# ==================== 输出格式化器 ====================


class OutputFormatter:
    """数据输出格式化"""

    @staticmethod
    def json(data: Any, out_file: Optional[str] = None):
        text = json.dumps(data, ensure_ascii=False, indent=2, default=str)
        OutputFormatter._write(text, out_file)

    @staticmethod
    def table_post(post: dict):
        w = os.get_terminal_size().columns if sys.stdout.isatty() else 80
        print()
        print(color("═" * min(w, 70), CYAN))
        print(color(f"  {post.get('title', '无标题')}", BOLD))
        print(color("─" * min(w, 70), DIM))

        author = post.get("author", {})
        stats = post.get("stats", {})
        post_time = post.get("time", "")
        abs_time = _format_time_abs(post.get("create_at"))

        time_display = ""
        if abs_time and post_time:
            time_display = f"\U0001f4c5 {abs_time} ({post_time})"
        elif post_time:
            time_display = f"\U0001f4c5 {post_time}"

        print(
            f"  {color(author.get('name', '?'), GREEN)}"
            f"  {color(author.get('level', ''), DIM)} "
            f"|  \u2b50 {format_number(stats.get('views', 0))}"
            f"  \u2764\ufe0f {format_number(stats.get('likes', 0))}"
            f"  \u2b50 {format_number(stats.get('favorites', 0))}"
            f"  \U0001f4ac{stats.get('comments', 0)}"
        )
        if time_display:
            print(f"  {color(time_display, DIM)}")

        content = post.get("content", "")
        if content:
            print(f"\n  {truncate_text(content, max_len=200)}")

        tags = post.get("tags", [])
        if tags:
            tag_str = " ".join(f"[{t}]" for t in tags[:5])
            print(f"\n  {color(tag_str, YELLOW)}")

        video = post.get("video")
        if video and video.get("url"):
            print(
                f"\n  \U0001f3ac 视频: {video.get('duration', '?')}s  "
                f"({video.get('width', '?')}x{video.get('height', '?')})"
            )
            print(f"     {video.get('url', '')}")

        comments = post.get("comments", [])
        if comments:
            print(f"\n  {color(f'--- 评论 ({len(comments)} 条) ---', DIM)}")
            for c in comments[:10]:
                ca = c.get("author", {})
                print(
                    f"  #{c.get('floor_num', '?'):>4} "
                    f"{color(ca.get('name', '?'), GREEN):<16} "
                    f"{truncate_text(c.get('content', ''), max_len=50)}"
                )
            if len(comments) > 10:
                print(f"  ... 还有 {len(comments) - 10} 条评论")

        print(color("═" * min(w, 70), CYAN))
        print()

    @staticmethod
    def table_comments(data: dict):
        comments = data.get("comments", [])
        page = data.get("page", 1)

        print()
        print(color(f"  评论 (第{page}页, 共{len(comments)}条)", BOLD))
        print(color("─" * 60, DIM))

        for c in comments:
            ca = c.get("author", {})
            print(
                f"  #{c.get('floor_num', '?'):>4} "
                f"{color(ca.get('name', '?'), GREEN):<14} "
                f"{c.get('time', ''):>8} "
                f"\u2764\ufe0f{c.get('likes', 0):>4}  "
                f"{truncate_text(c.get('content', ''), max_len=45)}"
            )

            for cc in c.get("child_comments", [])[:3]:
                cca = cc.get("author", {})
                reply_to = cc.get("reply_to", {})
                print(
                    f"       \u2192 {color(cca.get('name', '?'), DIM):<12} "
                    f"回复 @{reply_to.get('name', '?')}: "
                    f"{truncate_text(cc.get('content', ''), max_len=35)}"
                )

            children = c.get("child_comments", [])
            if len(children) > 3:
                print(f"          ... 还有 {len(children) - 3} 条子评论")

        print(color("─" * 60, DIM))
        print()

    @staticmethod
    def table_batch(results: list):
        print()
        print(color(f"  批量结果 ({len(results)} 个帖子)", BOLD))
        print(color("─" * 78, DIM))

        for i, r in enumerate(results):
            if "error" in r:
                print(f"  {i + 1}. \u274c {r.get('post_id', '?')}: {r['error']}")
                continue

            stats = r.get("stats", {})
            author = r.get("author", {})
            abs_time = _format_time_abs(r.get("create_at"))
            time_short = abs_time.split()[0] if abs_time else r.get("time", "")

            print(
                f"  {i + 1}. {color(truncate_text(r.get('title', '(无标题)'), max_len=30), BOLD)}"
                f"  {color(author.get('name', '?'), GREEN)}"
                f"  \u2b50{format_number(stats.get('views', 0))}"
                f"  \u2764\ufe0f{stats.get('likes', 0)}"
                f"  \U0001f4ac{stats.get('comments', 0)}"
                f"  {color(time_short, DIM) if time_short else ''}"
            )

        print(color("─" * 78, DIM))
        print()

    @staticmethod
    def table_creator(data: dict):
        article = data.get("article", {})
        stat = article.get("statistic_data", {})

        w = os.get_terminal_size().columns if sys.stdout.isatty() else 80
        print()
        print(color("\U0001f4ca 创作者数据面板", BOLD))
        print(color(f"  {article.get('title', '(无标题)')}", DIM))
        print(color("─" * min(w, 70), DIM))

        print(
            f"  \U0001f441 浏览量:     {format_number(article.get('click', 0))}"
            f"    \U0001f3af 曝光量:   {format_number(article.get('exposure_count', 0))}"
        )
        print(
            f"  \U0001f4d6 有效阅读:   {format_number(article.get('view_time_count', 0))}"
            f"    \u23f1 阅读时长: {article.get('avg_time', 0)}s"
        )

        def _stat(key):
            item = stat.get(key, {})
            return item.get("total") if isinstance(item, dict) else (item or 0)

        print(color("─" * min(w, 50), DIM))
        print(
            f"  \u2764\ufe0f 点赞 {_stat('award'):>6}  ({_stat('award_rate')})"
            f"    \u2b50 收藏 {_stat('favour'):>6}  ({_stat('favour_rate')})"
        )
        print(
            f"  \U0001f4ac 评论 {_stat('comment'):>6}  ({_stat('comment_rate')})"
            f"    \U0001f517 分享 {_stat('share'):>6}  ({_stat('share_rate')})"
        )

        new_fans = article.get("new_follow", 0)
        if new_fans:
            print(f"  \U0001f195 新增粉丝: {new_fans}")

        sources = article.get("flow_source_info", [])
        if sources:
            src_str = "  ".join(f"{s['text']}:{s['value']}%" for s in sources)
            print(f"\n  \U0001f4cd 流量来源: {src_str}")

        trends = data.get("data_trends", [])
        if trends and len(trends) > 1:
            print(f"\n  \U0001f4c8 近 {len(trends)} 日趋势:")
            print(f"  {'日期':<12} {'浏览':>6} {'评论':>5} {'分享':>5} {'收藏':>5} {'点赞':>5}")
            for t in trends[-7:]:
                ts = t.timestamp
                date_str = _format_time_abs(ts)[:10] if ts else "?"
                print(
                    f"  {date_str:<12} {t.click:>6} "
                    f"{t.comment:>5} {t.share:>5} "
                    f"{t.favour:>5} {t.award:>5}"
                )

        print(color("═" * min(w, 70), CYAN))
        print()

    @staticmethod
    def table_article_list(data: dict):
        articles = data.get("articles", [])
        total = data.get("total", len(articles))

        if not articles:
            print("\n  暂无已发布的文章")
            return

        w = os.get_terminal_size().columns if sys.stdout.isatty() else 100
        print()
        print(color(f"  \U0001f4dd 我的文章 ({total} 篇)", BOLD))
        print(color("─" * min(w, 85), DIM))

        total_views = sum(a.get("click", 0) for a in articles)
        total_likes = sum(a.get("thumbs", 0) for a in articles)
        total_comments = sum(a.get("comment", 0) for a in articles)
        total_reads = sum(a.get("raw_view_time_count", 0) for a in articles)

        print(
            f"  总浏览: {format_number(total_views)}"
            f"  | 总点赞: {total_likes}"
            f"  | 总评论: {total_comments}"
            f"  | 有效阅读: {format_number(total_reads)}"
        )
        print(color("─" * min(w, 85), DIM))

        header = (
            f"  {'#':<3} "
            f"{'标题':<30} "
            f"{'类型':<4} "
            f"{'浏览':>7} "
            f"{'点赞':>4} "
            f"{'评论':>4} "
            f"{'阅读':>5} "
            f"{'发布时间':<14}"
        )
        print(header)
        print(color("─" * min(w, 85), DIM))

        for i, art in enumerate(articles):
            title = truncate_text(art.get("title", "(无标题)"), max_len=28)
            link_type = _type_label(art.get("link_type"), art.get("has_video", 0))
            abs_time = _format_time_abs(art.get("create_at"))
            print(
                f"  {i + 1:<3} "
                f"{color(title, BOLD):<30} "
                f"{link_type:<4} "
                f"{format_number(art.get('click', 0)):>7} "
                f"{art.get('thumbs', 0):>4} "
                f"{art.get('comment', 0):>4} "
                f"{format_number(art.get('raw_view_time_count', 0)):>5} "
                f"{abs_time or '':<14}"
            )

        print(color("─" * min(w, 85), DIM))
        print()

    @staticmethod
    def csv_output(results: list, out_file: Optional[str] = None):
        fieldnames = [
            "post_id", "title", "author_name", "views", "likes",
            "favorites", "comments", "time", "url",
        ]

        rows = []
        for r in results:
            if "error" in r:
                rows.append({"post_id": r.get("post_id", ""), "error": r["error"]})
            else:
                a = r.get("author", {})
                s = r.get("stats", {})
                rows.append({
                    "post_id": r.get("post_id", ""),
                    "title": r.get("title", ""),
                    "author_name": a.get("name", ""),
                    "views": s.get("views", 0),
                    "likes": s.get("likes", 0),
                    "favorites": s.get("favorites", 0),
                    "comments": s.get("comments", 0),
                    "time": r.get("time", ""),
                    "url": r.get("url", ""),
                })

        if out_file:
            with open(out_file, "w", encoding="utf-8-sig", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=fieldnames)
                writer.writeheader()
                writer.writerows(rows)
            logger.info("CSV 已保存到 %s", out_file)
        else:
            writer = csv.DictWriter(sys.stdout, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)

    @staticmethod
    def _write(text: str, out_file: Optional[str] = None):
        if out_file:
            with open(out_file, "w", encoding="utf-8") as f:
                f.write(text)
            logger.info("已保存到 %s", out_file)
        else:
            print(text)


# ==================== 统一客户端上下文管理器 ====================


class _ClientCtx:
    """
    统一客户端上下文管理器 —— 自动处理 Cookie 加载 + 生命周期。

    Cookie 优先级: --cookie 参数 > XHH_COOKIE 环境变量 > ~/.xhh_cookie 文件
    用法: async with _ClientCtx(args) as c: ...
    """

    def __init__(self, args):
        self.args = args
        self._cookie = get_cookie(getattr(args, "cookie", None))
        self._use_daemon = _check_daemon() if not self._cookie else False
        self._client: Optional[XiaoheiheClient] = None

    async def __aenter__(self) -> XiaoheiheClient:
        if self._cookie:
            self._client = XiaoheiheClient(headless=self.args.headless)
            await self._client.connect_with_cookies(self._cookie)
        elif self._use_daemon:
            self._client = XiaoheiheClient(headless=self.args.headless, daemon=True)
            await self._client.connect()
        else:
            self._client = XiaoheiheClient(headless=self.args.headless)
            await self._client.connect()
        return self._client

    async def __aexit__(self, exc_type, exc_val, tb):
        if self._client:
            await self._client.close()
            self._client = None
        return False


# ==================== 命令处理器 ====================


async def cmd_get(args):
    async with _ClientCtx(args) as c:
        data = await c.get_post(args.post_id, full=args.full)
        _output(data, args.format, args.output, "post")


async def cmd_comments(args):
    async with _ClientCtx(args) as c:
        data = await c.get_comments(args.post_id, page=args.page, page_size=args.page_size)
        _output(data, args.format, args.output, "comments")


async def cmd_batch(args):
    ids: list[str] = []
    if args.ids:
        ids = [extract_link_id(i) for i in args.ids]
    elif args.file:
        with open(args.file, "r", encoding="utf-8") as f:
            ids = [extract_link_id(line) for line in f if line.strip()]

    if not ids:
        logger.error("未提供任何帖子ID")
        sys.exit(1)

    logger.info("准备爬取 %d 个帖子", len(ids))
    async with _ClientCtx(args) as c:
        results = await c.batch_get(ids, full=args.full)
        _batch_output(results, args)


async def cmd_creator(args):
    async with _ClientCtx(args) as c:
        data = await c.get_creator_data(args.post_id)
        _output(data, args.format, args.output, "creator")


async def cmd_list(args):
    async with _ClientCtx(args) as c:
        data = await c.get_my_articles()
        _output(data, args.format, args.output, "article_list")


async def cmd_publish(args):
    """发布文章命令"""
    html_content = None
    convert_stats = None

    if getattr(args, "markdown", None):
        # Markdown 输入 → 自动转换为小黑盒兼容格式
        from markdown_converter import HeyBoxConverter
        converter = HeyBoxConverter()
        html_content = converter.convert(args.markdown, source_format="markdown")
        convert_stats = converter.stats.summary()
        logger.info("Markdown 已转换为小黑盒兼容格式: %s", convert_stats)

    elif args.content:
        # 检测是否包含需要转换的内容（代码块/表格等）
        raw = args.content
        needs_convert = any(marker in raw for marker in ("```", "<pre", "<table", "`code`", "~~"))
        if needs_convert:
            from markdown_converter import HeyBoxConverter
            converter = HeyBoxConverter()
            html_content = converter.convert(raw)
            convert_stats = converter.stats.summary()
            if convert_stats != "无需转换":
                logger.info("内容已优化为小黑盒兼容格式: %s", convert_stats)
        else:
            html_content = raw

    elif args.html:
        try:
            with open(args.html, "r", encoding="utf-8") as f:
                html_content = f.read()
        except FileNotFoundError:
            logger.error("HTML 文件不存在: %s", args.html)
            sys.exit(1)

    if not html_content:
        html_content = f"<p>{args.title}</p>"

    async with _ClientCtx(args) as c:
        result = await c.publish(
            title=args.title,
            html_content=html_content,
            link_tag=args.link_tag,
            draft=not args.do_publish,
        )

    # 输出结果
    action = "草稿" if not args.do_publish else "正式发布"
    w = os.get_terminal_size().columns if sys.stdout.isatty() else 80

    print()
    print(color(f"  {'='*min(w, 50)}", CYAN))
    status_icon = GREEN + "[OK]" + RESET if result.get("success") else YELLOW + "[FAIL]" + RESET
    print(f"  {status_icon} {action}: {result.get('title', args.title)}")
    if convert_stats and convert_stats != "无需转换":
        print(color(f"  格式转换: {convert_stats}", DIM))
    print(color(f"  {'─'*min(w, 50)}", DIM))

    if result.get("success"):
        print(f"  link_id: {result.get('link_id')}")
        print(f"  消息:     {result.get('message', '成功')}")
    else:
        print(f"  错误:     {result.get('message', '未知错误')}")

    print(color(f"  {'='*min(w, 50)}", CYAN))
    print()

    # JSON 模式额外输出完整数据
    if args.format == "json":
        OutputFormatter.json(result, args.output)


async def cmd_login(args):
    """
    手机号验证码登录。
    登录成功后 Cookie 自动保存，后续命令无需再登录。
    支持服务器 headless 模式（通过 stdin 输入验证码）。
    """
    from browser_manager import BrowserManager

    phone = getattr(args, "phone", "")
    if not phone:
        print(color("  ❌ 请提供手机号: --phone +8613800138000", RED))
        return

    bm = BrowserManager(headless=args.headless)
    try:
        success = await bm.login_with_phone(phone)
    finally:
        await bm.close()

    if success:
        print(f"\n{color('  ✅ 登录完成！Cookie 已保存。', GREEN)}")
        print(f"   后续所有命令可直接使用:")
        print(f"   python cli.py get <id>")
        print(f"   python cli.py pub '标题' -c '<p>内容</p>'\n")

        # 输出 JSON 结果
        result = {
            "status": "ok",
            "message": "登录成功",
            "heybox_id": bm.heybox_id,
            "headless": args.headless,
            "phone": phone[:len(phone) - 4] + "****",
        }
        if args.format == "json":
            OutputFormatter.json(result, args.output)
    else:
        print(f"\n{color('  ❌ 登录失败', RED)}")
        print("   可尝试: python cli.py login --phone <号码> (不带 --headless 弹出浏览器)")
        result = {
            "status": "error",
            "message": "登录失败",
            "phone": phone[:len(phone) - 4] + "****",
        }
        if args.format == "json":
            OutputFormatter.json(result, args.output)


async def cmd_serve(args):
    print()
    print(color("  小黑盒守护进程", BOLD))
    print(color("  ────────────────────────────────", DIM))
    print(f"  端口: {args.port}")
    print(f"  无头: {args.headless}")
    print(f"  PID 文件: {get_daemon_pid_path()}")
    print(color("  ────────────────────────────────", DIM))
    print()
    print(color("  按 Ctrl+C 停止", DIM))
    print()

    pid_path = get_daemon_pid_path()
    try:
        with open(pid_path, "w") as f:
            f.write(str(os.getpid()))
    except Exception:
        pass

    server = DaemonServer(port=args.port, headless=args.headless)
    try:
        await server.start()
    except KeyboardInterrupt:
        print("\n正在停止守护进程...")
        await server.stop()
    finally:
        if os.path.exists(pid_path):
            os.remove(pid_path)


def cmd_status(args):
    pid_path = get_daemon_pid_path()
    if not os.path.exists(pid_path):
        print(color("  ● 守护进程未运行", YELLOW))
        print(f"     启动: xiaoheihe serve")
        return

    try:
        with open(pid_path) as f:
            pid = int(f.read().strip())
        os.kill(pid, 0)

        print(color("  ● 守护进程运行中", GREEN))
        print(f"     PID: {pid}")

        try:
            import socket as _sock
            sock = _sock.socket(_sock.AF_INET, _sock.SOCK_STREAM)
            sock.settimeout(2)
            sock.connect(("127.0.0.1", 19810))
            sock.sendall(json.dumps({"action": "health"}).encode() + b"\n")
            data = sock.recv(4096).decode().strip()
            sock.close()
            health = json.loads(data)
            info = health.get("data", {})
            print(f"     Heybox ID: {info.get('heybox_id', '?')}")
            print(f"     Cookie 有效: {'是' if info.get('cookies_valid') else '否'}")
        except Exception:
            pass
    except (ProcessLookupError, OSError, PermissionError):
        print(color("  ● 守护进程僵尸（PID文件残留）", YELLOW))
        print(f"     清理: del {pid_path}")


# ==================== 工具函数 ====================


def _check_daemon() -> bool:
    pid_path = get_daemon_pid_path()
    if not os.path.exists(pid_path):
        return False
    try:
        with open(pid_path) as f:
            pid = int(f.read().strip())
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, OSError, PermissionError, ValueError):
        return False


def _batch_output(results, args):
    """批量结果统一输出"""
    if args.format == "csv":
        OutputFormatter.csv_output(results, args.output)
    elif args.format == "table":
        OutputFormatter.table_batch(results)
    else:
        OutputFormatter.json(results if len(results) > 1 else results[0], args.output)


def _output(data: dict, fmt: str, out_file: Optional[str] = None, data_type: str = "post"):
    dispatch = {
        "post": OutputFormatter.table_post,
        "comments": OutputFormatter.table_comments,
        "creator": OutputFormatter.table_creator,
        "article_list": OutputFormatter.table_article_list,
    }
    handler = dispatch.get(data_type)

    if fmt == "table" and handler:
        handler(data)
    elif fmt == "csv":
        OutputFormatter.csv_output([data], out_file)
    else:
        OutputFormatter.json(data, out_file)


# ==================== 主入口 ====================


_VERSION = "v2.4"

_CMD_ALIASES = {
    "g": "get",
    "c": "comments",
    "b": "batch",
    "s": "serve",
    "st": "status",
    "cr": "creator",
    "ls": "list",
    "pub": "publish",
    "li": "login",
}

_HANDLERS = {
    "get": cmd_get,
    "comments": cmd_comments,
    "batch": cmd_batch,
    "creator": cmd_creator,
    "list": cmd_list,
    "publish": cmd_publish,
    "serve": cmd_serve,
    "status": cmd_status,
    "login": cmd_login,
}


def main():
    parser = argparse.ArgumentParser(
        prog="xiaoheihe",
        description=color(f"小黑盒 CLI {_VERSION} — 社区数据 & 发布工具 (Agent友好)", BOLD),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=f"""\
示例:
  %(prog)s get 179245676                  获取帖子（JSON输出）
  %(prog)s get 179245676 -f table          获取帖子（表格显示）
  %(prog)s get 179245676 --full           获取完整帖子
  %(prog)s comments 179245676             查看评论
  %(prog)s batch ids.txt                  批量爬取
  %(prog)s creator 179245676              创作者数据（曝光量/转化率）
  %(prog)s list / ls                      查看我的文章列表
  %(prog)s pub "标题" -c "<p>HTML</p>"     发布文章（默认存草稿）
  %(prog)s pub "标题" -c "<p>HTML</p>" --publish   正式发布

服务器部署:
  export XHH_COOKIE="你的cookie字符串"
  %(prog)s -f json get 179245676           Agent 友好的 JSON 输出
        """,
    )
    parser.add_argument("--headless", action="store_true",
                        help="无头模式（默认关闭，交互命令自动弹出浏览器）")
    parser.add_argument("--no-headless", dest="headless", action="store_false",
                        help="关闭无头模式（同默认行为）")
    parser.add_argument("-f", "--format", choices=["json", "table", "csv"],
                        default="json", help="输出格式（默认json）")
    parser.add_argument("-o", "--output", default=None, help="输出文件路径")
    parser.add_argument("--cookie", default=None,
                        help="Cookie 字符串（优先级最高，覆盖环境变量和配置文件）")

    subparsers = parser.add_subparsers(dest="command", help="命令")

    # --- get ---
    p_get = subparsers.add_parser("get", aliases=["g"], help="获取帖子详情")
    p_get.add_argument("post_id", help="帖子ID或URL")
    p_get.add_argument("--full", action="store_true", help="包含全部评论")

    # --- comments ---
    p_com = subparsers.add_parser("comments", aliases=["c"], help="获取评论")
    p_com.add_argument("post_id", help="帖子ID或URL")
    p_com.add_argument("-p", "--page", type=int, default=1, help="页码（默认1）")
    p_com.add_argument("-s", "--page-size", type=int, default=20, dest="page_size",
                       help="每页数量（默认20）")

    # --- batch ---
    p_bat = subparsers.add_parser("batch", aliases=["b"], help="批量爬取")
    p_bat.add_argument("ids", nargs="*", help="帖子ID列表")
    p_bat.add_argument("--file", help="从文件读取ID（每行一个）")
    p_bat.add_argument("--full", action="store_true", help="完整帖子")

    # --- serve ---
    p_srv = subparsers.add_parser("serve", aliases=["s"], help="启动守护进程")
    p_srv.add_argument("--port", type=int, default=19810, help="监听端口（默认19810）")
    p_srv.add_argument("--headless", action="store_true", default=True,
                       help="无头模式（serve 默认开启）")

    # --- status ---
    subparsers.add_parser("status", aliases=["st"], help="查看守护进程状态")

    # --- creator ---
    p_cre = subparsers.add_parser("creator", aliases=["cr"], help="创作者后台数据")
    p_cre.add_argument("post_id", help="帖子ID或URL")

    # --- list ---
    subparsers.add_parser("list", aliases=["ls"], help="查看已发布文章列表")

    # --- login ---
    p_login = subparsers.add_parser("login", aliases=["li"], help="手机号验证码登录")
    p_login.add_argument("--phone", required=True,
                         help="手机号（含区号），如 +8613800138000 或 +49123456789")

    # --- publish ---
    p_pub = subparsers.add_parser("publish", aliases=["pub"], help="发布/保存草稿")
    p_pub.add_argument("title", help="文章标题（用引号包裹）")
    p_pub.add_argument("--html", help="HTML 正文文件路径")
    p_pub.add_argument("-c", "--content", default=None, help="HTML 正文内容（直接传入）")
    p_pub.add_argument("-m", "--markdown", default=None, help="Markdown 正文（自动转换为小黑盒兼容格式）")
    p_pub.add_argument("--tag", type=int, default=11, dest="link_tag",
                       help="标签ID（默认11=校园生活）")
    p_pub.add_argument("--publish", action="store_true", dest="do_publish",
                       help="正式发布（默认只存草稿箱）")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(0)

    cmd = _CMD_ALIASES.get(args.command, args.command)
    handler = _HANDLERS.get(cmd)
    if not handler:
        parser.print_help()
        sys.exit(1)

    # Cookie 提示（login 命令不需要 cookie）
    cookie = get_cookie(getattr(args, "cookie", None))
    if not cookie and cmd not in ("serve", "status", "login"):
        logger.debug("未提供 Cookie，将尝试守护模式或交互登录")

    if cmd == "serve":
        asyncio.run(handler(args))
    elif cmd == "status":
        handler(args)
    else:
        asyncio.run(handler(args))


if __name__ == "__main__":
    main()
