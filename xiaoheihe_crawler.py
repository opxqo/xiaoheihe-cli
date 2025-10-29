import json
import asyncio
import re
from pathlib import Path
from typing import Optional, List
import httpx
from playwright.async_api import async_playwright, Page, Browser, BrowserContext
from models import Post, PostAuthor, PostStats, PostImage, PostVideo, Comment, CommentUser, CommentImage, Medal, Emoji

class XiaoHeiHeCrawler:
   
    
    def __init__(self, output_dir: str = "data", headless: bool = False, silent: bool = False):
        """
        初始化爬虫
        
        Args:
            output_dir: 数据输出目录
            headless: 是否使用无头模式（True=后台运行，不显示浏览器）
            silent: 是否静默模式（不打印日志，用于API服务器）
        """
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(exist_ok=True)
        self.images_dir = self.output_dir / "images"
        self.images_dir.mkdir(exist_ok=True)
        self.headless = headless
        self.silent = silent
        
        self.playwright = None
        self.browser: Optional[Browser] = None
        self.context: Optional[BrowserContext] = None
        
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Referer': 'https://www.xiaoheihe.cn/',
        }
    
    async def init_browser(self):
        """初始化浏览器（持久化模式，用于API服务器）"""
        if self.browser:
            return
        
        self.playwright = await async_playwright().start()
        self.browser = await self.playwright.chromium.launch(headless=self.headless)
        self.context = await self.browser.new_context()
    
    async def close_browser(self):
        """关闭浏览器（API服务器关闭时调用）"""
        if self.context:
            await self.context.close()
        if self.browser:
            await self.browser.close()
        if self.playwright:
            await self.playwright.stop()
        
        self.context = None
        self.browser = None
        self.playwright = None
    
    async def get_post_data(self, url: str) -> Optional[dict]:
        """
        从页面获取帖子数据
        
        Args:
            url: 帖子URL
            
        Returns:
            帖子数据字典
        """
        use_persistent = self.browser and self.context
        
        page = None
        browser = None
        context = None
        playwright = None
        
        try:
            if use_persistent:
                page = await self.context.new_page()
            else:
                if not self.silent:
                    print(f"[INFO] 正在启动浏览器...")
                
                playwright = await async_playwright().start()
                browser = await playwright.chromium.launch(headless=self.headless)
                context = await browser.new_context(
                    user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
                )
                page = await context.new_page()
                
            await page.route("**/*.{woff,woff2}", lambda route: route.abort())
            
            await page.goto(url, wait_until='networkidle', timeout=30000)
            
            try:
                await page.wait_for_function(
                    """() => {
                        return window.__NUXT__ && 
                               window.__NUXT__.data && 
                               Object.keys(window.__NUXT__.data).length > 0;
                    }""",
                    timeout=10000
                )
            except:
                pass
            
            try:
                reply_buttons = await page.query_selector_all('button[class*="load-all"], button[class*="reply"]')
                for button in reply_buttons:
                    button_text = await button.text_content()
                    if (button_text and ('回复' in button_text or '条' in button_text)):
                        try:
                            await button.click()
                            await page.wait_for_timeout(300)
                        except:
                            pass
            except:
                pass
            
            post_data = await page.evaluate("""() => {
                const nuxtData = window.__NUXT__;
                if (!nuxtData || !nuxtData.data) {
                    return null;
                }
                
                // 找到包含result的key（动态生成的key）
                const dataObj = nuxtData.data;
                let result = null;
                for (let key in dataObj) {
                    const value = dataObj[key];
                    if (value && value.result && value.status === 'ok') {
                        result = value.result;
                        break;
                    }
                }
                
                if (!result) {
                    return null;
                }
                
                
                // 策略：取连续的图片（domIndex间隔不超过2），过滤相似内容
                const allImgs = Array.from(document.querySelectorAll('img'));
                const realImages = [];
                const candidateImages = [];
                
                
                document.querySelectorAll('img[src*="imgheybox"]').forEach(img => {
                    if (img.src && 
                        !img.src.includes('avatar') && 
                        !img.src.includes('emoji') &&
                        !img.src.includes('/oa/') &&
                        !img.src.includes('/dev/bbs/') &&
                        !img.src.includes('/game/header/') &&
                        img.src.includes('/bbs/') &&
                        img.naturalWidth > 400) {
                        
                       
                        let element = img;
                        let isInRelated = false;
                        let depth = 0;
                        
                        while (element && element !== document.body && depth < 10) {
                            const text = element.textContent || '';
                            if (text.includes('相似内容')) {
                                isInRelated = true;
                                break;
                            }
                            element = element.parentElement;
                            depth++;
                        }
                        
                        if (!isInRelated) {
                            candidateImages.push({
                                src: img.src,
                                domIndex: allImgs.indexOf(img)
                            });
                        }
                    }
                });
                
                // 2. 按domIndex排序
                candidateImages.sort((a, b) => a.domIndex - b.domIndex);
                
                
                if (candidateImages.length > 0) {
                    realImages.push(candidateImages[0].src);
                    
                    for (let i = 1; i < candidateImages.length; i++) {
                        const gap = candidateImages[i].domIndex - candidateImages[i-1].domIndex;
                        if (gap <= 2) {
                            realImages.push(candidateImages[i].src);
                        } else {
                // 间隔太大，停止
                            break;
                        }
                    }
                }
                
                
                result.realImages = realImages;
                
                // 不再从DOM提取子评论，直接使用__NUXT__中的comment数组
                // 每个评论对象的comment字段包含完整的子评论数据（用户头像、图片等）
                
                /
                const emojiMap = {};
                const CUBE_EMOJI_SPRITE_URL = 'https://static.max-c.com/heybox_web/emoji/cube/cube_emoji_v19.png';
                
                const allEmojis = document.querySelectorAll('[data-emoji]');
                
                allEmojis.forEach((el, index) => {
                    const emojiName = el.getAttribute('data-emoji');
                    const className = el.className;
                    const style = window.getComputedStyle(el);
                    const match = className.match(/hb-emoji-cube_(\\d+)/);
                    const emojiId = match ? match[1] : '';
                    
                    if (emojiName && !emojiMap[emojiName]) {
                        // 使用backgroundPositionX和backgroundPositionY组合
                        const bgX = style.backgroundPositionX;
                        const bgY = style.backgroundPositionY;
                        let bgPosition = style.backgroundPosition;
                        
                        // 使用X/Y值
                        if (bgX && bgY) {
                            bgPosition = `${bgX} ${bgY}`;
                        }
                        
                        emojiMap[emojiName] = {
                            name: emojiName,
                            id: emojiId,
                            spriteUrl: CUBE_EMOJI_SPRITE_URL,
                            backgroundPosition: bgPosition || '0% 0%',
                            backgroundSize: style.backgroundSize || '84px 182px'
                        };
                    }
                });
                
                result.emojiMap = emojiMap;
                
                const videoElements = document.querySelectorAll('video');
                let videoData = null;
                
                if (videoElements.length > 0) {
                    const video = videoElements[0];
                    const sources = video.querySelectorAll('source');
                    const videoSrc = sources.length > 0 ? sources[0].src : video.src;
                    
                    if (videoSrc) {
                        videoData = {
                            url: videoSrc,
                            poster: video.poster || null,
                            width: video.videoWidth || video.width || null,
                            height: video.videoHeight || video.height || null,
                            duration: video.duration || null
                        };
                    }
                }
                
                result.videoData = videoData;
                
                return result;
            }""")
            
            if page:
                await page.close()
            
            if not use_persistent:
                if browser:
                    try:
                        await browser.close()
                    except:
                        pass
                if playwright:
                    try:
                        await playwright.stop()
                    except:
                        pass
            
            return post_data if post_data else None
            
        except Exception as e:
            if page:
                try:
                    await page.close()
                except:
                    pass
            if not use_persistent:
                if browser:
                    try:
                        await browser.close()
                    except:
                        pass
                if playwright:
                    try:
                        await playwright.stop()
                    except:
                        pass
            return None
    
    def parse_post(self, post_data: dict, url: str) -> Post:
        """解析帖子数据"""
        link_data = post_data.get('link', {})
        comments_data = post_data.get('comments', [])
        
        user_data = link_data.get('user', {})
        level_info = user_data.get('level_info', {})
        
        medals = []
        wearing_medal = None
        medals_data = user_data.get('medals', [])
        for medal_item in medals_data:
            if isinstance(medal_item, dict):
                medal = Medal(
                    medal_id=medal_item.get('medal_id', 0),
                    name=medal_item.get('name', ''),
                    description=medal_item.get('description', ''),
                    img_url=medal_item.get('img_url', ''),
                    level=medal_item.get('level', 0),
                    achieved=medal_item.get('achieved', 0),
                    wear=medal_item.get('wear', 0)
                )
                medals.append(medal)
                if medal.wear == 1:
                    wearing_medal = medal
        
        author = PostAuthor(
            name=user_data.get('username', '未知用户'),
            level=f"Lv.{level_info.get('level', 0)}" if level_info.get('status') == 1 else '',
            avatar_url=user_data.get('avatar', ''),
            user_id=str(user_data.get('userid', '')),
            medals=medals,
            wearing_medal=wearing_medal
        )
        
        images = []
        
        real_images = post_data.get('realImages', [])
        if real_images:
            for img_url in real_images:
                base_url = img_url.split('?')[0]
                images.append(PostImage(url=base_url))
        else:
            text_content = link_data.get('text', '')
            if isinstance(text_content, str):
                try:
                    text_list = json.loads(text_content)
                    for item in text_list:
                        if isinstance(item, dict) and item.get('type') == 'img':
                            img_url = item.get('url', '')
                            if img_url:
                                base_url = img_url.split('?')[0]
                                images.append(PostImage(url=base_url))
                except:
                    pass
        
        video_data = post_data.get('videoData')
        video = None
        if video_data and video_data.get('url'):
            video = PostVideo(
                url=video_data.get('url'),
                poster=video_data.get('poster'),
                width=video_data.get('width'),
                height=video_data.get('height'),
                duration=video_data.get('duration')
            )
        
        tags = []
        topics = link_data.get('topics', [])
        for topic in topics:
            tags.append(topic.get('name', ''))
        
        stats = PostStats(
            likes=link_data.get('up', 0),
            favorites=link_data.get('favour_count', 0),
            comments=link_data.get('comment_num', 0)
        )
        
        emoji_map = post_data.get('emojiMap', {})
        
        comments = []
        for comment_group in comments_data:
            comment_array = comment_group.get('comment', [])
            if len(comment_array) > 0:
                main_comment_data = comment_array[0]
                child_comments_data = comment_array[1:] if len(comment_array) > 1 else []
                
                main_comment_data['_child_comments_data'] = child_comments_data
                
                comment = self._parse_comment(main_comment_data, emoji_map)
                if comment:
                    comments.append(comment)
        
        post_id = str(link_data.get('linkid', ''))
        if not post_id:
            match = re.search(r'/link/([a-zA-Z0-9]+)', url)
            if match:
                post_id = match.group(1)
        
        post = Post(
            post_id=post_id,
            url=url,
            title=link_data.get('title', ''),
            content=link_data.get('description', ''),
            author=author,
            tags=tags,
            images=images,
            video=video,
            stats=stats,
            time=self._format_timestamp(link_data.get('create_at', 0)),
            location=link_data.get('ip_location', ''),
            comments=comments
        )
        
        return post
    
    def _parse_comment(self, comment_data: dict, emoji_map: dict = None) -> Optional[Comment]:
        """解析单条评论（包含勋章和子评论）"""
        try:
            user_data = comment_data.get('user', {})
            level_info = user_data.get('level_info', {})
            
            medals = []
            wearing_medal = None
            medals_data = user_data.get('medals', [])
            for medal_item in medals_data:
                if isinstance(medal_item, dict):
                    medal = Medal(
                        medal_id=medal_item.get('medal_id', 0),
                        name=medal_item.get('name', ''),
                        description=medal_item.get('description', ''),
                        img_url=medal_item.get('img_url', ''),
                        level=medal_item.get('level', 0),
                        achieved=medal_item.get('achieved', 0),
                        wear=medal_item.get('wear', 0)
                    )
                    medals.append(medal)
                    if medal.wear == 1:
                        wearing_medal = medal
            
            author = CommentUser(
                user_id=str(user_data.get('userid', '')),
                name=user_data.get('username', '匿名用户'),
                level=f"Lv.{level_info.get('level', 0)}" if level_info.get('status') == 1 else '',
                avatar_url=user_data.get('avatar', ''),
                medals=medals,
                wearing_medal=wearing_medal
            )
            
            comment_images = []
            imgs_data = comment_data.get('imgs', [])
            if imgs_data and isinstance(imgs_data, list):
                for img_item in imgs_data:
                    if isinstance(img_item, dict):
                        img_url = img_item.get('url', '')
                        if img_url:
                            comment_images.append(CommentImage(
                                url=img_url.split('?')[0],
                                thumb=img_item.get('thumb', ''),
                                width=img_item.get('width'),
                                height=img_item.get('height')
                            ))
            
            text_content = comment_data.get('text', '')
            
            emojis = []
            if emoji_map and text_content:
                emoji_pattern = r'\[cube_[^\]]+\]'
                emoji_matches = re.findall(emoji_pattern, text_content)
                for emoji_text in emoji_matches:
                    emoji_name = emoji_text[1:-1]
                    if emoji_name in emoji_map:
                        emoji_data = emoji_map[emoji_name]
                        emojis.append(Emoji(
                            name=emoji_name,
                            emoji_id=emoji_data.get('id', ''),
                            sprite_url=emoji_data.get('spriteUrl', ''),
                            background_position=emoji_data.get('backgroundPosition', '')
                        ))
            
            reply_to = None
            reply_user_data = comment_data.get('replyuser', {})
            if reply_user_data:
                reply_level_info = reply_user_data.get('level_info', {})
                
                reply_medals = []
                reply_wearing_medal = None
                reply_medals_data = reply_user_data.get('medals', [])
                for medal_item in reply_medals_data:
                    if isinstance(medal_item, dict):
                        medal = Medal(
                            medal_id=medal_item.get('medal_id', 0),
                            name=medal_item.get('name', ''),
                            description=medal_item.get('description', ''),
                            img_url=medal_item.get('img_url', ''),
                            level=medal_item.get('level', 0),
                            achieved=medal_item.get('achieved', 0),
                            wear=medal_item.get('wear', 0)
                        )
                        reply_medals.append(medal)
                        if medal.wear == 1:
                            reply_wearing_medal = medal
                
                reply_to = CommentUser(
                    user_id=str(reply_user_data.get('userid', '')),
                    name=reply_user_data.get('username', ''),
                    level=f"Lv.{reply_level_info.get('level', 0)}" if reply_level_info.get('status') == 1 else '',
                    avatar_url=reply_user_data.get('avatar', ''),
                    medals=reply_medals,
                    wearing_medal=reply_wearing_medal
                )
            
            child_comments = []
            comment_id = str(comment_data.get('commentid', ''))
            child_comments_data = comment_data.get('_child_comments_data', [])
            
            for child_data in child_comments_data:
                try:
                    child_user_data = child_data.get('user', {})
                    child_level_info = child_user_data.get('level_info', {})
                    
                    child_medals = []
                    child_wearing_medal = None
                    child_medals_data = child_user_data.get('medals', [])
                    for medal_item in child_medals_data:
                        if isinstance(medal_item, dict):
                            medal = Medal(
                                medal_id=medal_item.get('medal_id', 0),
                                name=medal_item.get('name', ''),
                                description=medal_item.get('description', ''),
                                img_url=medal_item.get('img_url', ''),
                                level=medal_item.get('level', 0),
                                achieved=medal_item.get('achieved', 0),
                                wear=medal_item.get('wear', 0)
                            )
                            child_medals.append(medal)
                            if medal.wear == 1:
                                child_wearing_medal = medal
                    
                    child_author = CommentUser(
                        user_id=str(child_user_data.get('userid', '')),
                        name=child_user_data.get('username', ''),
                        level=f"Lv.{child_level_info.get('level', 0)}" if child_level_info.get('status') == 1 else '',
                        avatar_url=child_user_data.get('avatar', ''),
                        medals=child_medals,
                        wearing_medal=child_wearing_medal
                    )
                    
                    child_reply_to = None
                    child_reply_user_data = child_data.get('replyuser', {})
                    if child_reply_user_data:
                        child_reply_level_info = child_reply_user_data.get('level_info', {})
                        
                        child_reply_medals = []
                        child_reply_wearing_medal = None
                        child_reply_medals_data = child_reply_user_data.get('medals', [])
                        for medal_item in child_reply_medals_data:
                            if isinstance(medal_item, dict):
                                medal = Medal(
                                    medal_id=medal_item.get('medal_id', 0),
                                    name=medal_item.get('name', ''),
                                    description=medal_item.get('description', ''),
                                    img_url=medal_item.get('img_url', ''),
                                    level=medal_item.get('level', 0),
                                    achieved=medal_item.get('achieved', 0),
                                    wear=medal_item.get('wear', 0)
                                )
                                child_reply_medals.append(medal)
                                if medal.wear == 1:
                                    child_reply_wearing_medal = medal
                        
                        child_reply_to = CommentUser(
                            user_id=str(child_reply_user_data.get('userid', '')),
                            name=child_reply_user_data.get('username', ''),
                            level=f"Lv.{child_reply_level_info.get('level', 0)}" if child_reply_level_info.get('status') == 1 else '',
                            avatar_url=child_reply_user_data.get('avatar', ''),
                            medals=child_reply_medals,
                            wearing_medal=child_reply_wearing_medal
                        )
                    
                    child_images = []
                    child_imgs_data = child_data.get('imgs', [])
                    for img_item in child_imgs_data:
                        if isinstance(img_item, dict):
                            img_url = img_item.get('url', '')
                            if img_url:
                                child_images.append(CommentImage(
                                    url=img_url.split('?')[0],
                                    thumb=img_item.get('thumb', ''),
                                    width=img_item.get('width'),
                                    height=img_item.get('height')
                                ))
                    
                    child_text = child_data.get('text', '')
                    
                    child_emojis = []
                    if emoji_map and child_text:
                        emoji_pattern = r'\[cube_[^\]]+\]'
                        emoji_matches = re.findall(emoji_pattern, child_text)
                        for emoji_text in emoji_matches:
                            emoji_name = emoji_text[1:-1]
                            if emoji_name in emoji_map:
                                emoji_data = emoji_map[emoji_name]
                                child_emojis.append(Emoji(
                                    name=emoji_name,
                                    emoji_id=emoji_data.get('id', ''),
                                    sprite_url=emoji_data.get('spriteUrl', ''),
                                    background_position=emoji_data.get('backgroundPosition', '')
                                ))
                    
                    child_comment = Comment(
                        comment_id=str(child_data.get('commentid', '')),
                        author=child_author,
                        content=child_text,
                        emojis=child_emojis,
                        time=self._format_timestamp(child_data.get('create_at', 0)),
                        location=child_data.get('ip_location', ''),
                        likes=child_data.get('up', 0),
                        floor_num=child_data.get('floor_num', 0),
                        images=child_images,
                        reply_to=child_reply_to,
                        child_comments=[]
                    )
                    child_comments.append(child_comment)
                except Exception as e:
                    if not self.silent:
                        print(f"解析子评论出错: {str(e)}")
                    continue
            
            comment = Comment(
                comment_id=comment_id,
                author=author,
                content=text_content,
                emojis=emojis,
                time=self._format_timestamp(comment_data.get('create_at', 0)),
                location=comment_data.get('ip_location', ''),
                likes=comment_data.get('up', 0),
                floor_num=comment_data.get('floor_num', 0),
                images=comment_images,
                reply_to=reply_to,
                child_comments=child_comments
            )
            
            return comment
            
        except Exception as e:
            if not self.silent:
                print(f"[WARN] 解析评论失败: {e}")
            return None
    
    def _format_timestamp(self, timestamp: int) -> str:
        """格式化时间戳"""
        if not timestamp:
            return ''
        
        import time
        now = int(time.time())
        diff = now - timestamp
        
        if diff < 60:
            return '刚刚'
        elif diff < 3600:
            return f'{diff // 60}分钟前'
        elif diff < 86400:
            return f'{diff // 3600}小时前'
        elif diff < 2592000:
            return f'{diff // 86400}天前'
        else:
            return time.strftime('%Y-%m-%d', time.localtime(timestamp))
    
    async def download_image(self, url: str, filename: str) -> Optional[str]:
        """下载单个图片"""
        try:
            if not self.silent:
                print(f"[INFO] 正在下载图片: {url}")
            
            async with httpx.AsyncClient(headers=self.headers, timeout=30.0) as client:
                response = await client.get(url)
                response.raise_for_status()
                
                filepath = self.images_dir / filename
                with open(filepath, 'wb') as f:
                    f.write(response.content)
                
                if not self.silent:
                    print(f"[OK] 下载成功: {filename}")
                return str(filepath)
                
        except Exception as e:
            if not self.silent:
                print(f"[ERROR] 下载失败 {filename}: {e}")
            return None
    
    async def download_images(self, post: Post):
        """下载帖子中的所有图片"""
        if not post.images:
            if not self.silent:
                print("[INFO] 该帖子没有图片")
            return
        
        if not self.silent:
            print(f"\n[INFO] 开始下载 {len(post.images)} 张图片...")
        
        tasks = []
        for i, image in enumerate(post.images):
            ext = image.url.split('.')[-1].split('?')[0]
            if not ext or len(ext) > 5:
                ext = 'jpg'
            filename = f"{post.post_id}_img_{i+1}.{ext}"
            tasks.append(self.download_image(image.url, filename))
        
        paths = await asyncio.gather(*tasks)
        
        success_count = 0
        for i, path in enumerate(paths):
            if path and i < len(post.images):
                post.images[i].local_path = path
                success_count += 1
        
        if not self.silent:
            print(f"[INFO] 图片下载完成: {success_count}/{len(post.images)} 成功")
    
    def save_post(self, post: Post, format: str = 'json') -> str:
        """保存帖子数据"""
        if format == 'json':
            filepath = self.output_dir / f"{post.post_id}.json"
            with open(filepath, 'w', encoding='utf-8') as f:
                json.dump(post.model_dump(), f, ensure_ascii=False, indent=2, default=str)
            if not self.silent:
                print(f"[OK] 保存JSON: {filepath}")
            return str(filepath)
        
        elif format == 'txt':
            filepath = self.output_dir / f"{post.post_id}.txt"
            with open(filepath, 'w', encoding='utf-8') as f:
                f.write(f"标题: {post.title}\n")
                f.write(f"作者: {post.author.name} ({post.author.level})\n")
                f.write(f"时间: {post.time}\n")
                f.write(f"地点: {post.location}\n")
                f.write(f"标签: {', '.join(post.tags)}\n")
                f.write(f"\n正文:\n{post.content}\n")
                f.write(f"\n统计:\n")
                f.write(f"- 点赞: {post.stats.likes}\n")
                f.write(f"- 收藏: {post.stats.favorites}\n")
                f.write(f"- 评论: {post.stats.comments}\n")
                f.write(f"\n图片: {len(post.images)} 张\n")
                for i, img in enumerate(post.images):
                    f.write(f"  {i+1}. {img.url}\n")
                f.write(f"\n评论 ({len(post.comments)}):\n")
                for i, comment in enumerate(post.comments):
                    f.write(f"\n--- 评论 {i+1} ---\n")
                    f.write(f"{comment.author.name} ({comment.author.level})\n")
                    f.write(f"楼层{comment.floor_num} · {comment.time} · {comment.location}\n")
                    f.write(f"点赞: {comment.likes}\n")
                    if comment.reply_to:
                        f.write(f"回复 @{comment.reply_to.name}: ")
                    f.write(f"{comment.content}\n")
                    if comment.images:
                        f.write(f"  [图片] {len(comment.images)}张\n")
            if not self.silent:
                print(f"[OK] 保存TXT: {filepath}")
            return str(filepath)
    
    async def crawl_post(self, url: str, download_images: bool = True) -> Optional[Post]:
        """爬取单个帖子"""
        if not self.silent:
            print(f"\n{'='*80}")
        if not self.silent:
            print(f"开始爬取帖子: {url}")
        if not self.silent:
            print(f"{'='*80}\n")
        
        post_data = await self.get_post_data(url)
        if not post_data:
            if not self.silent:
                print("[ERROR] 获取帖子数据失败")
            return None
        
        post = self.parse_post(post_data, url)
        
        if download_images and post.images:
            await self.download_images(post)
        
        self.save_post(post, format='json')
        self.save_post(post, format='txt')
        
        if not self.silent:
            print(f"\n{'='*80}")
        if not self.silent:
            print("爬取完成！")
        if not self.silent:
            print(f"{'='*80}")
        if not self.silent:
            print(f"帖子ID: {post.post_id}")
        if not self.silent:
            print(f"标题: {post.title}")
        if not self.silent:
            print(f"作者: {post.author.name} ({post.author.level})")
        if not self.silent:
            print(f"内容: {post.content[:50]}...")
        if not self.silent:
            print(f"发布时间: {post.time}")
        if not self.silent:
            print(f"发布地点: {post.location}")
        if not self.silent:
            print(f"标签: {', '.join(post.tags)}")
        if not self.silent:
            print(f"\n统计信息:")
        if not self.silent:
            print(f"  - 点赞: {post.stats.likes}")
        if not self.silent:
            print(f"  - 收藏: {post.stats.favorites}")
        if not self.silent:
            print(f"  - 评论: {post.stats.comments}")
        if not self.silent:
            print(f"\n图片: {len(post.images)} 张")
        if not self.silent:
            print(f"评论: {len(post.comments)} 条")
        if not self.silent:
            print(f"{'='*80}\n")
        
        return post
    
    async def batch_crawl(self, urls: List[str], download_images: bool = True, delay: float = 3.0):
        """批量爬取帖子"""
        results = []
        for i, url in enumerate(urls, 1):
            if not self.silent:
                print(f"\n[{i}/{len(urls)}] 正在处理...")
            
            post = await self.crawl_post(url, download_images=download_images)
            if post:
                results.append(post)
            
            if i < len(urls):
                if not self.silent:
                    print(f"等待 {delay} 秒...")
                await asyncio.sleep(delay)
        
        if not self.silent:
            print(f"\n批量爬取完成！成功: {len(results)}/{len(urls)}")
        return results
    
    async def get_login_qrcode(self, save_path: Optional[str] = None, monitor_status: bool = False) -> Optional[str]:
        """
        获取登录二维码
        
        Args:
            save_path: 保存路径，如果为None则保存到默认位置
            monitor_status: 是否监听二维码状态（扫码/过期）
            
        Returns:
            二维码图片的保存路径（单个或列表）
        """
        try:
            if not self.silent:
                print(f"\n{'='*80}")
            if not self.silent:
                print("获取小黑盒登录二维码")
            if not self.silent:
                print(f"{'='*80}\n")
            
            if not self.silent:
                print("[INFO] 正在启动浏览器...")
            
            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True)
                context = await browser.new_context()
                page = await context.new_page()
                
            qr_status_requests = []
            if monitor_status:
                async def handle_response(response):
                    if 'login' in response.url or 'qrcode' in response.url or 'status' in response.url:
                        try:
                            json_data = await response.json()
                            qr_status_requests.append({
                                'url': response.url,
                                'status': response.status,
                                'data': json_data
                            })
                            if not self.silent:
                                print(f"[INFO] 二维码状态API: {response.url}")
                                print(f"[INFO] 响应: {json_data}")
                        except:
                            pass
                
                page.on('response', handle_response)
            
            if not self.silent:
                print("[INFO] 正在访问登录页面...")
            await page.goto("https://login.xiaoheihe.cn/?origin=heybox&redirect_url=https%3A%2F%2Fwww.xiaoheihe.cn%2Fhome", 
                               wait_until='networkidle', timeout=30000)
            
            if not self.silent:
                print("[INFO] 点击微信登录...")
            try:
                wechat_btn = await page.query_selector('.wechat-login, [class*="wechat"]')
                if wechat_btn:
                    await wechat_btn.click()
                    await asyncio.sleep(2)
                else:
                    if not self.silent:
                        print("[WARN] 未找到微信登录按钮，尝试直接提取...")
            except:
                if not self.silent:
                    print("[WARN] 点击微信登录失败，尝试直接提取...")
            
            await asyncio.sleep(1)
            
            qrcode_data = await page.evaluate("""() => {
                    // 查找两种二维码：
                    // 1. 微信登录二维码（弹窗中的真实二维码）
                    // 2. 客户端标识图标（静态PNG）
                const qrImages = {
                    wechat: null,
                    client_icon: null
                };
                
                    // 1. 查找客户端标识图标（fd777a7b3630248b8cf092dbc87979b1.png）
                const clientIcon = document.querySelector('img[src*="fd777a7b3630248b8cf092dbc87979b1"]');
                if (clientIcon) {
                    qrImages.client_icon = {
                        src: clientIcon.src,
                        width: clientIcon.width,
                        height: clientIcon.height,
                        type: 'client_icon'
                    };
                }
                
                    // 2. 查找微信二维码（微信open平台的二维码URL）
                const imgs = document.querySelectorAll('img');
                imgs.forEach(img => {
                        // 微信二维码：open.weixin.qq.com/connect/qrcode/
                    if (img.src && img.src.includes('open.weixin.qq.com/connect/qrcode') && img.width > 150) {
                        if (!qrImages.wechat) {
                            qrImages.wechat = {
                                src: img.src,
                                width: img.width,
                                height: img.height,
                                type: 'wechat_qr_url',
                                visible: img.offsetParent !== null
                            };
                        }
                    }
                    
                        // 也检查base64格式的二维码（备用）
                    if (!qrImages.wechat && img.src && img.src.startsWith('data:image') && img.width > 150) {
                        qrImages.wechat = {
                            src: img.src,
                            width: img.width,
                            height: img.height,
                            type: 'wechat_qr_base64',
                            visible: img.offsetParent !== null
                        };
                    }
                });
                
                    // 3. 查找canvas元素（二维码可能通过canvas渲染）
                const canvases = document.querySelectorAll('canvas');
                canvases.forEach(canvas => {
                    if (canvas.width > 150 && !qrImages.wechat) {
                        try {
                            const dataUrl = canvas.toDataURL('image/png');
                            qrImages.wechat = {
                                src: dataUrl,
                                width: canvas.width,
                                height: canvas.height,
                                type: 'wechat_qr_canvas',
                                isCanvas: true
                            };
                        } catch(e) {
                            // Canvas可能被污染
                        }
                    }
                });
                
                return qrImages;
            }""")
            
            await browser.close()
            
            if not qrcode_data:
                if not self.silent:
                    print("[ERROR] 未能获取二维码")
                return None
            
            saved_paths = []
            
            wechat_qr = qrcode_data.get('wechat')
            if wechat_qr and wechat_qr.get('src'):
                file_path = str(self.output_dir / "login_qrcode_wechat.png")
                qr_src = wechat_qr['src']
                
                if qr_src.startswith('data:image'):
                    import base64
                    base64_data = qr_src.split(',')[1] if ',' in qr_src else qr_src
                    img_data = base64.b64decode(base64_data)
                    
                    with open(file_path, 'wb') as f:
                        f.write(img_data)
                else:
                    async with httpx.AsyncClient(headers=self.headers, timeout=30.0) as client:
                        response = await client.get(qr_src)
                        response.raise_for_status()
                        
                        with open(file_path, 'wb') as f:
                            f.write(response.content)
                
                if not self.silent:
                    print(f"[OK] 微信二维码已保存: {file_path}")
                    print(f"[INFO] 尺寸: {wechat_qr.get('width')}x{wechat_qr.get('height')}")
                    print(f"[INFO] 类型: {wechat_qr.get('type')}")
                saved_paths.append(file_path)
            
            client_icon = qrcode_data.get('client_icon')
            if client_icon and client_icon.get('src'):
                file_path = str(self.output_dir / "login_client_icon.png")
                
                async with httpx.AsyncClient(headers=self.headers, timeout=30.0) as client:
                    response = await client.get(client_icon['src'])
                    response.raise_for_status()
                    
                    with open(file_path, 'wb') as f:
                        f.write(response.content)
                
                if not self.silent:
                    print(f"[OK] 客户端标识图标已保存: {file_path}")
                    print(f"[INFO] 尺寸: {client_icon.get('width')}x{client_icon.get('height')}")
                    print(f"[NOTE] 这是一个静态标识图标，不是真实的二维码")
            
            if monitor_status and qr_status_requests:
                if not self.silent:
                    print(f"\n[INFO] 监听到 {len(qr_status_requests)} 个二维码状态请求:")
                for req in qr_status_requests:
                    if not self.silent:
                        print(f"  - {req['url']}: {req['data']}")
            
            if not saved_paths:
                if not self.silent:
                    print("[ERROR] 未能获取微信二维码")
                return None
            
            return saved_paths[0] if len(saved_paths) == 1 else saved_paths
            
        except Exception as e:
            if not self.silent:
                print(f"[ERROR] 获取二维码失败: {e}")
            import traceback
            traceback.print_exc()
            return None

async def main():
    """主函数"""
    print("="*80)
    print("小黑盒爬虫 - 专业爬虫方案")
    print("\n请选择功能:")
    print("1. 爬取单个帖子")
    print("2. 批量爬取帖子")
    print("3. 获取登录二维码")
    
    choice = input("\n请输入选项 (1-3): ").strip()
    
    crawler = XiaoHeiHeCrawler(output_dir="data", headless=True)
    
    try:
        if choice == "1":
            url = input("\n请输入帖子URL: ").strip()
            if not url:
                url = "https://www.xiaoheihe.cn/app/bbs/link/163964486"
                print(f"使用默认URL: {url}")
            
            await crawler.crawl_post(url, download_images=True)
            
        elif choice == "2":
            print("\n请输入帖子URL（每行一个，输入空行结束）:")
            urls = []
            while True:
                url = input().strip()
                if not url:
                    break
                urls.append(url)
            
            if urls:
                await crawler.batch_crawl(urls, download_images=True, delay=3.0)
            else:
                print("未输入任何URL")
        
        elif choice == "3":
            qr_path = await crawler.get_login_qrcode()
            if qr_path:
                print(f"\n✓ 二维码获取成功！")
                print(f"文件位置: {qr_path}")
                print("\n使用小黑盒APP扫描二维码即可登录")
        
        else:
            print("无效的选项")
            
    except KeyboardInterrupt:
        print("\n\n用户中断")
    except Exception as e:
        print(f"\n发生错误: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(main())
