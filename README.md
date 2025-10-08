# 小黑盒爬虫 API

专业的小黑盒社区帖子爬虫，支持提取帖子、评论、图片、视频、表情、勋章等完整数据。

##  特性

-  完整数据提取：帖子、作者、评论、图片、视频、表情、勋章
-  视频支持：提取视频URL、封面图、尺寸、时长
-  表情资源：精准提取表情雪碧图位置，支持前端渲染
-  勋章系统：用户勋章列表和佩戴状态
-  嵌套评论：完整的子评论数据，包含用户头像和图片
-  RESTful API：FastAPI 提供高性能接口
-  浏览器持久化：复用浏览器实例，响应速度快

## 快速开始

### 1. 安装依赖

```bash
pip install -r requirements.txt
playwright install chromium
```

### 2. 启动API服务器

```bash
python api_server.py
```

服务器启动后：
- API文档: http://localhost:8010/docs
- 备用文档: http://localhost:8010/redoc
- 健康检查: http://localhost:8010/health

## API端点

### 1. 获取帖子（不含评论）

```bash
GET /api/post/{post_id}
```

**示例**：
```bash
curl "http://localhost:8010/api/post/977e70c3b33f"
```

### 2. 获取评论（分页）

```bash
GET /api/post/{post_id}/comments?page=1&page_size=20
```

### 3. 获取完整帖子（含所有评论）

```bash
GET /api/post/{post_id}/full
```

### 4. 批量爬取

```bash
POST /api/posts/batch
Content-Type: application/json

{
  "urls": ["https://www.xiaoheihe.cn/app/bbs/link/xxxxx"],
  "download_images": false
}
```

## 数据结构

### 帖子数据

```json
{
  "post_id": "977e70c3b33f",
  "title": "帖子标题",
  "content": "帖子内容",
  "author": {
    "name": "用户名",
    "level": "Lv.9",
    "user_id": "12345678",
    "avatar_url": "https://...",
    "medals": [...],
    "wearing_medal": {...}
  },
  "tags": ["标签1", "标签2"],
  "images": [...],
  "video": {
    "url": "https://videoheybox.max-c.com/...",
    "poster": "https://imgheybox.max-c.com/...",
    "width": 1728,
    "height": 1080,
    "duration": 101.216
  },
  "stats": {
    "likes": 66,
    "favorites": 12,
    "comments": 31
  },
  "comments": [...]
}
```

### 评论数据

```json
{
  "comment_id": "721454406",
  "author": {
    "user_id": "83709585",
    "name": "用户名",
    "level": "Lv.13",
    "avatar_url": "https://...",
    "medals": [...],
    "wearing_medal": {...}
  },
  "content": "评论内容",
  "emojis": [
    {
      "name": "cube_笑cry",
      "emoji_id": "32",
      "sprite_url": "https://static.max-c.com/heybox_web/emoji/cube/cube_emoji_v19.png",
      "background_position": "70px 154px"
    }
  ],
  "time": "1小时前",
  "location": "四川",
  "likes": 0,
  "floor_num": 11,
  "images": [
    {
      "url": "https://...",
      "thumb": "https://...",
      "width": 1920,
      "height": 1080
    }
  ],
  "reply_to": {...},
  "child_comments": [...]
}
```

### 表情数据说明

表情使用CSS Sprite技术，返回的数据包含：
- `sprite_url`: 雪碧图完整URL
- `background_position`: 精确位置（如 "70px 154px"）
- 前端可直接使用这些数据渲染表情

## Python调用示例

```python
import requests

response = requests.get("http://localhost:8010/api/post/977e70c3b33f/full")
data = response.json()

print(f"标题: {data['data']['title']}")
print(f"作者: {data['data']['author']['name']}")

if data['data'].get('video'):
    print(f"视频: {data['data']['video']['url']}")

for comment in data['data']['comments']:
    print(f"{comment['author']['name']}: {comment['content']}")
    
    for emoji in comment.get('emojis', []):
        print(f"  表情: {emoji['name']} @ {emoji['background_position']}")
```

## 技术架构

- **Playwright**: 无头浏览器，处理客户端渲染
- **FastAPI**: 高性能异步API框架
- **Pydantic**: 数据验证和序列化
- **浏览器持久化**: 复用浏览器实例，提升响应速度

## 项目结构

```
xiaoheihe_pybu/
├── xiaoheihe_crawler.py    # 爬虫核心
├── api_server.py           # API服务器
├── models.py               # 数据模型
├── requirements.txt        # 依赖列表
├── README.md              # 本文件
└── data/                  # 输出目录
```

## 注意事项

1. 服务器不会下载图片到本地（`download_images=False`）
2. 图片URL直接返回给客户端使用
3. 表情位置为精确像素值，可直接用于CSS background-position
4. 视频元数据（宽高、时长）在视频未加载时可能为空
5. 建议合理控制请求频率

## 免责声明

### 法律声明

本项目（xiaoheihe-crawler-api）仅供学习、研究和技术交流使用。使用本项目时，您必须遵守以下条款：

#### 1. 使用限制

- ✅ **允许**：个人学习、技术研究、非商业用途
- ❌ **禁止**：商业用途、大规模数据采集、侵犯他人权益
- ❌ **禁止**：用于任何违反法律法规的行为

#### 2. 数据使用规范

- 爬取的数据仅供个人学习使用
- 不得传播、出售或用于商业目的
- 必须尊重原网站的版权和用户隐私
- 不得对目标网站造成负担或损害

#### 3. 用户责任

使用本项目时，用户应当：
- 遵守中华人民共和国相关法律法规
- 遵守《网络安全法》、《数据安全法》、《个人信息保护法》
- 遵守小黑盒平台的服务条款和robots.txt协议
- 合理控制请求频率，避免对服务器造成压力

#### 4. 免责条款

- 本项目作者不对使用本工具产生的任何后果负责
- 因使用本工具导致的任何法律纠纷与作者无关
- 用户使用本工具的一切行为由用户本人承担全部责任
- 本工具按"原样"提供，不提供任何明示或暗示的保证

#### 5. 知识产权

- 小黑盒及其数据的所有权归其所有者
- 本项目代码采用MIT License开源协议
- 使用本项目不代表获得小黑盒数据的任何权利

#### 6. 合规建议

建议用户：
- 仅在测试环境使用
- 设置合理的请求间隔（建议≥3秒）
- 不要在生产环境大规模部署
- 定期检查并遵守目标网站的最新条款

### 风险提示

⚠️ **重要提示**：
- 网络爬虫可能违反网站服务条款
- 过度爬取可能导致IP被封禁
- 数据采集需遵守相关法律法规
- 商业用途需获得平台授权

**使用本项目即表示您已阅读、理解并同意遵守以上所有条款。如不同意，请立即停止使用。**

## License

MIT License

Copyright (c) 2025 Li Fangyu

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
