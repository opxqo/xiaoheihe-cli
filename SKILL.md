# xiaoheihe-cli Skill

小黑盒（Heybox）社区数据获取 & 文章发布 CLI 工具。

专为 AI Agent 设计的命令行接口，支持 JSON 结构化输出、Cookie 自动管理、纯服务器部署。

---

## 快速开始

### 1. 安装依赖

```bash
pip install playwright httpx pydantic
playwright install chromium  # 仅需一次
```

### 2. 配置 Cookie（必须）

**方式 A：环境变量（推荐用于服务器/Agent）**

```bash
export XHH_COOKIE="你的完整cookie字符串"
```

**方式 B：配置文件**

```bash
echo "你的cookie字符串" > ~/.xhh_cookie
```

**方式 C：命令行参数（临时）**

```bash
python cli.py --cookie "你的cookie" get 123456
```

> Cookie 获取方式：浏览器登录 `https://www.xiaoheihe.cn` → F12 开发者工具 → Application → Cookies → 复制全部

### 3. 验证安装

```bash
python cli.py -f json list
# 应返回 JSON 格式的文章列表（空数组也正常）
```

---

## 命令参考

所有命令默认输出 **JSON**（Agent 友好），加 `-f table` 可切换为表格。

### 获取帖子

```bash
python cli.py get <post_id_or_url>           # 基础信息 + 前20条评论
python cli.py get <post_id_or_url> --full     # 完整帖子（含所有评论）
```

**JSON 输出字段：**
```json
{
  "post_id": "179245676",
  "url": "https://www.xiaoheihe.cn/app/bbs/link/179245676",
  "title": "文章标题",
  "author": {"name": "作者名", "level": "Lv.5", "id": "97769292"},
  "stats": {
    "views": 38000,
    "likes": 1100,
    "favorites": 3100,
    "comments": 375
  },
  "content": "正文摘要...",
  "tags": ["校园生活"],
  "time": "1天前",
  "create_at": 1744560000,
  "comments": [...],
  "video": null
}
```

### 获取评论

```bash
python cli.py comments <post_id>              # 第1页，20条/页
python cli.py comments <post_id> -p 2 -s 50   # 第2页，50条/页
```

### 批量爬取

```bash
python cli.py batch id1 id2 id3               # 指定ID
python cli.py batch --file ids.txt            # 从文件读取（每行一个ID）
python cli.py batch --file ids.txt --full     # 完整帖子
```

### 我的文章列表

```bash
python cli.py list                            # 已发布文章列表
python cli.py list -f table                   # 表格格式
```

### 发布文章

```bash
# 存草稿（默认）
python cli.py pub "我的标题" -c "<h2>HTML正文</h2><p>内容</p>"

# 从 HTML 文件读取
python cli.py pub "我的标题" --html article.html

# 正式发布（不是草稿）
python cli.py pub "标题" -c "<p>内容</p>" --publish

# 指定标签（默认11=校园生活）
python cli.py pub "标题" -c "<p>内容</p>" --tag 11
```

**发布结果 JSON：**
```json
{"success": true, "link_id": "179318019", "message": "", "is_draft": true}
```

### 创作者数据

```bash
python cli.py creator <post_id>
```

### 守护进程模式（可选）

```bash
# 终端A: 启动守护（保持浏览器常驻）
python cli.py serve                          # 默认 headless

# 终端B: 使用守护进程（自动连接）
python cli.py list                           # 自动复用已有浏览器
```

---

## Agent 集成指南

### 标准调用模式 (subprocess + JSON)

```python
import subprocess
import json

def xhh_get(post_id: str) -> dict:
    """获取帖子详情"""
    r = subprocess.run(
        ["python", "cli.py", "-f", "json", "get", post_id],
        capture_output=True, text=True, timeout=60
    )
    return json.loads(r.stdout)

def xhh_publish(title: str, html_content: str, publish: bool = False) -> dict:
    """发布文章"""
    cmd = ["python", "cli.py", "-f", "json", "pub", title, "-c", html_content]
    if publish:
        cmd.append("--publish")
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    return json.loads(r.stdout)

def xhh_list() -> dict:
    """获取我的文章列表"""
    r = subprocess.run(
        ["python", "cli.py", "-f", "json", "list"],
        capture_output=True, text=True, timeout=60
    )
    return json.loads(r.stdout)
```

### Python SDK 方式 (直接 import)

```python
import asyncio
from xiaoheihe import XiaoheiheClient

async def example():
    async with XiaoheiheClient(headless=True) as client:
        post = await client.get_post("179245676")
        print(post["title"], post["stats"]["views"])

        result = await client.publish(
            title="测试文章",
            html_content="<p>这是正文</p>",
            draft=True,
        )
        print(f"success={result['success']} link_id={result['link_id']}")

asyncio.run(example())
```

---

## 错误处理

| 场景 | 现象 | 解决 |
|------|------|------|
| Cookie 过期 | 返回 `"Cookie已过期"` | 重新设置 XHH_COOKIE |
| 验证码拦截 | `"验证码校验失败"` | 用 `--no-headless` 手动过一次验证码 |
| 帖子不存在 | `RuntimeError` | 检查 post_id |
| 无创作者权限 | `"无法获取文章列表"` | 账号需开通创作者权限 |
| 浏览器未安装 | `Executable doesn't exist` | `playwright install chromium` |

---

## 文件结构

```
xiaoheihe/
├── cli.py              # CLI v2.4 主入口
├── config.py           # Cookie 配置管理 (env/file/arg 三级优先)
├── api_client.py       # API 调用层 (Route拦截)
├── browser_manager.py  # Playwright 浏览器管理
├── data_parser.py      # 数据解析
├── models.py           # Pydantic 数据模型
├── utils.py            # 工具函数
└── xiaoheihe/__init__.py  # Python SDK
```
