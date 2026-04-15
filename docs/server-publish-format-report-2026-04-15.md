# 服务器发布格式排查报告

日期: 2026-04-15

## 结论

这次“服务器上安装成功，但写文章时格式不对、平台不能正确渲染”的主因，不是登录链路，也不是发布接口本身失效，而是“发布前的格式转换层”和“服务器接入路径”之间存在认知差异：

1. CLI 的 `publish/pub` 命令会自动调用 `HeyBoxConverter`，把 Markdown、代码块、表格和图片处理成更适合小黑盒的 HTML。
2. SDK 的 `XiaoheiheClient.publish()` 不做任何转换，它要求调用方自己传入最终 HTML。
3. 因此，只要服务器侧不是直接使用 CLI，而是自己在 Python/服务端代码里调用 `client.publish(...)`，并把 Markdown 或未经处理的正文直接传进去，就会出现“格式不对、平台无法正确渲染”的现象。

这不是小黑盒账号、Cookie 或 Linux 纯命令行环境独有的问题，而是“接入层使用方式不同”导致的行为差异。

## 代码级证据

### 1. CLI 发布路径会做格式转换

在 [cli.py](/C:/Code/AI/xiaoheihe/cli.py:536) 的 `cmd_publish()` 中：

- `--markdown` 会显式调用 `HeyBoxConverter().convert(..., source_format="markdown")`
- `--content` 会调用 `source_format="auto"`
- `--html` 也会再次走统一规范化

也就是说，CLI 用户天然走了“转换后再发布”的安全路径。

### 2. SDK 发布路径不会做格式转换

在 [xiaoheihe/__init__.py](/C:/Code/AI/xiaoheihe/xiaoheihe/__init__.py:253) 的 `XiaoheiheClient.publish()` 中：

- 方法签名就是 `html_content: str`
- 它直接把 `html_content` 透传给 `api_client.publish_article()`
- 这里没有任何 Markdown 解析或兼容化处理

因此，SDK 语义一直是“发布已经准备好的 HTML”，而不是“接收原始 Markdown 并自动发布”。

### 3. 后端发布接口会把传入内容原样当作 HTML 发出

在 `api_client.publish_article()` 中，正文会被序列化为：

```json
[{"text": html_content, "type": "html"}]
```

这意味着如果上层传进来的是 Markdown 原文、未转换代码块、未处理表格，最终小黑盒拿到的就是“伪装成 HTML 的原始文本”，渲染自然会出问题。

### 4. 格式转换依赖存在“降级路径”

在 [markdown_converter.py](/C:/Code/AI/xiaoheihe/markdown_converter.py:48) 的 `_check_deps()` 和以下方法中：

- [markdown_converter.py](/C:/Code/AI/xiaoheihe/markdown_converter.py:156) `_render_markdown_html`
- [markdown_converter.py](/C:/Code/AI/xiaoheihe/markdown_converter.py:732) `_render_code_image`
- [markdown_converter.py](/C:/Code/AI/xiaoheihe/markdown_converter.py:776) `_render_table_image`

如果服务器缺少这些依赖：

- `markdown`
- `Pillow`
- `pygments`

转换器会退化为“基础段落包裹”或保留 `<pre><code>` 文本块，而不是生成适配小黑盒的图片型代码块/表格。

所以服务器侧还有第二层风险：即使接入方式是正确的，如果依赖装得不完整，最终效果仍然会变差。

## 本次排查中的实际验证

### 1. 本地转换器验证

对 Markdown 示例进行转换后，能够稳定得到：

- `<h1>/<h2>/<p>/<ul>/<blockquote>` 等结构
- 代码块转 `data:image/...` 图片
- 表格转 `data:image/...` 图片

说明转换器本身在依赖齐全的环境下是工作的。

### 2. 服务器路径差异验证

从代码路径可以确认：

- `python cli.py pub ... -m ...` 会自动做格式转换
- `await client.publish(title, html_content=...)` 不会

这正是“本地命令行看起来正常，服务器自己接入后格式错乱”的典型原因。

## 为什么这会在服务器上更容易暴露

在服务器部署场景里，更常见的不是人手动敲 CLI，而是：

1. 外部程序生成 Markdown
2. Python 服务读取 Markdown
3. 直接调用 SDK 的 `publish()`

这里一旦少了 `HeyBoxConverter` 这一步，就会把原始 Markdown/半成品 HTML 直接发到小黑盒。

反过来说，如果你是本地一直使用：

```bash
python cli.py pub "标题" -m "$(cat article.md)"
```

那通常不会踩到这个坑，因为 CLI 已经帮你兜底了。

## 已做改进

为了降低服务器接入时的误用概率，这次新增了两个 SDK 入口：

- `XiaoheiheClient.render_article_content(content, source_format="auto")`
- `XiaoheiheClient.publish_markdown(title, markdown_content, ...)`

以及一个更通用的：

- `XiaoheiheClient.publish_content(title, content, source_format="auto", ...)`

这样服务器侧接入不需要手动记住转换步骤，直接调用新的 API 即可。

## 推荐用法

### 1. 服务器侧发布 Markdown

```python
async with XiaoheiheClient(headless=True) as client:
    result = await client.publish_markdown(
        title="服务器文章",
        markdown_content=markdown_text,
        draft=True,
    )
```

### 2. 服务器侧发布未知格式正文

```python
async with XiaoheiheClient(headless=True) as client:
    result = await client.publish_content(
        title="服务器文章",
        content=raw_content,
        source_format="auto",
        draft=True,
    )
```

### 3. 只有在你已经拿到最终 HTML 时，才使用原始 `publish()`

```python
await client.publish(title="标题", html_content=final_html, draft=True)
```

## 服务器上线前检查清单

建议在服务器上至少执行以下检查：

### 1. 检查关键依赖

```bash
python - <<'PY'
import markdown
import PIL
import pygments
print("deps ok")
PY
```

### 2. 检查转换器输出

```bash
python - <<'PY'
from xiaoheihe import XiaoheiheClient
html, stats = XiaoheiheClient.render_article_content("# 标题\\n\\n```python\\nprint(1)\\n```", "markdown")
print(stats)
print(html[:500])
PY
```

### 3. 检查真实发布时是否走了转换层

如果是你自己的服务端代码，确认它调用的是：

- `publish_markdown()`
- 或 `publish_content()`

而不是把 Markdown 直接传给：

- `publish()`

## 当前仍需注意的边界

### 1. 站外图片

当前项目已经尽量把本地图片和部分远程图片转成 `data:` 内嵌图，以减少渲染失败概率，但“小黑盒官方站内图片上传接口”仍未完全打通，后续还可以继续专项探索。

### 2. 依赖缺失时的退化

如果服务器环境安装不完整，代码块/表格会回退成较弱格式，即便调用路径正确，也可能出现“效果不够好”。

## 最终判断

这次服务器上的“格式不对”问题，优先级最高的根因是：

`服务器侧发布代码绕过了 CLI 的格式转换层，直接调用了只接受 HTML 的 SDK 发布接口。`

次级风险是：

`服务器环境如果缺少 markdown / Pillow / pygments，转换器会退化，导致最终渲染效果继续变差。`

这两个条件任何一个成立，都足以造成你看到的现象。
