import os
import re
import time
import aiohttp

from amiyabot import Message, Chain
from core import AmiyaBotPluginInstance

curr_dir = os.path.dirname(__file__)


# Image URL cache: url -> (text, image_url_or_path, timestamp)
_url_cache: dict[str, tuple[str, str | None, float]] = {}
_cache_timeout = 300  # 5 minutes cache


def _get_cached(url: str) -> tuple[str, str | None] | None:
    """Get cached content if not expired."""
    if url in _url_cache:
        text, img, timestamp = _url_cache[url]
        if time.time() - timestamp < _cache_timeout:
            return text, img
        else:
            # Remove expired cache
            del _url_cache[url]
            # Clean up temp file
            if img and os.path.exists(img) and img.startswith('/tmp/'):
                try:
                    os.remove(img)
                except Exception:
                    pass
    return None


def _is_image_by_content(data: bytes) -> tuple[bool, str]:
    """Detect if content is an image by magic bytes.
    
    Returns:
        tuple: (is_image, extension)
    """
    if len(data) < 4:
        return False, ''
    # PNG, JPEG, GIF, WebP magic bytes
    signatures = [
        (b'\x89PNG\r\n\x1a\n', '.png'),
        (b'\xff\xd8\xff', '.jpg'),
        (b'GIF87a', '.gif'),
        (b'GIF89a', '.gif'),
        (b'RIFF', '.webp'),  # WebP starts with RIFF....WEBP
        (b'BM', '.bmp'),     # BMP
    ]
    for sig, ext in signatures:
        if data.startswith(sig):
            return True, ext
    return False, ''


def _is_image_by_url(url: str) -> tuple[bool, str]:
    """Detect if URL likely points to an image by extension."""
    image_exts = {'.png', '.jpg', '.jpeg', '.gif', '.webp', '.bmp', '.ico'}
    lower = url.lower()
    for ext in image_exts:
        if lower.endswith(ext) or ('?' in lower and lower.split('?')[0].endswith(ext)):
            return True, ext
    return False, ''


async def fetch_url_content(url: str) -> tuple[str, str | None]:
    """Fetch content from a URL with intelligent detection.
    
    Returns:
        tuple: (text content, image path if it's an image, else None)
    """
    # Check cache with expiration
    cached = _get_cached(url)
    if cached:
        return cached
    
    cache_path = f'/tmp/url_content_{abs(hash(url))}'
    
    try:
        # Always download and save content first
        async with aiohttp.ClientSession() as session:
            async with session.get(
                url,
                timeout=aiohttp.ClientTimeout(total=10),
                ssl=False  # Skip SSL verification for self-signed certificates
            ) as response:
                if response.status == 200:
                    data = await response.read()
                    
                    # Save raw content
                    with open(cache_path, 'wb') as f:
                        f.write(data)
                    
                    # Then check if it's an image by content
                    is_image, ext = _is_image_by_content(data)
                    
                    if is_image:
                        # Rename to proper extension
                        img_path = f'{cache_path}{ext}'
                        os.rename(cache_path, img_path)
                        _url_cache[url] = ('', img_path, time.time())
                        return '', img_path
                    
                    # Return text content
                    text = data.decode('utf-8', errors='ignore')
                    _url_cache[url] = (text, None, time.time())
                    return text, None
                    
    except Exception as e:
        print(f'[TalkingPlugin] Fetch URL error: {url}, error: {e}')
    
    _url_cache[url] = ('', None, time.time())
    return '', None


async def parse_reply_content(reply: str, data: Message) -> tuple[str, str | None]:
    """Parse reply content, handling URL expressions and nickname placeholder.
    
    Returns:
        tuple: (text content, image path if there's an image, else None)
    """
    image_path = None
    
    # Handle URL expression: {url:https://example.com}
    url_pattern = r'\{url:([^}]+)\}'
    
    for match in re.finditer(url_pattern, reply):
        url = match.group(1)
        content, img_path = await fetch_url_content(url)
        if img_path:
            image_path = img_path
            reply = reply.replace(match.group(0), '')
        elif content:
            reply = reply.replace(match.group(0), content)
    
    # Replace nickname placeholder
    return reply.replace('{nickname}', data.nickname), image_path


class TalkPluginInstance(AmiyaBotPluginInstance): ...


bot = TalkPluginInstance(
    name='自定义回复',
    version='1.7',
    plugin_id='amiyabot-talking',
    plugin_type='official',
    description='可以自定义一问一答的简单对话',
    document=f'{curr_dir}/README.md',
    instruction=f'{curr_dir}/README_USE.md',
    global_config_schema=f'{curr_dir}/config_schema.json',
    global_config_default=f'{curr_dir}/config_default.yaml',
)


async def check_talk(data: Message):
    configs: list = bot.get_config('configs')

    def set_reply(_item):
        return True, 1, [_item['reply'], _item['is_at']]

    for item in configs:
        direct = item.get('direct')
        if direct:
            if direct == '仅群聊' and data.is_direct:
                continue
            if direct == '仅私聊' and not data.is_direct:
                continue

        if item['keyword_type'] == '包含关键词':
            if item['keyword'] in data.text:
                return set_reply(item)
        if item['keyword_type'] == '等于关键词':
            if item['keyword'] == data.text:
                return set_reply(item)
        if item['keyword_type'] == '正则匹配':
            if re.search(re.compile(item['keyword']), data.text):
                return set_reply(item)


@bot.on_message(verify=check_talk, check_prefix=False, allow_direct=True)
async def _(data: Message):
    reply: str = data.verify.keypoint[0]
    is_at: bool = data.verify.keypoint[1]

    if os.path.exists(reply):
        return Chain(data, at=is_at).image(reply)

    reply, image_path = await parse_reply_content(reply, data)
    
    chain = Chain(data, at=is_at)
    if image_path:
        chain = chain.image(image_path)
    if reply.strip():
        chain = chain.text(reply)
    
    return chain
