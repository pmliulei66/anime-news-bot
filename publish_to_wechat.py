#!/usr/bin/env python3
"""
微信公众号草稿箱发布工具

将本地 Markdown 文件一键上传到公众号草稿箱，支持：
- 自动下载并上传图片到微信素材库
- Markdown 转 HTML + 动漫资讯风 CSS 排版
- 自动提取标题和封面图

用法:
    python publish_to_wechat.py article.md
    python publish_to_wechat.py article.md --title "自定义标题" --author "作者" --cover cover.jpg
"""

import argparse
import io
import logging
import os
import re
import sys
from typing import Optional

import markdown2
import requests
from wechatpy import WeChatClient

from config import Config

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("publish-to-wechat")

# 微信图片域名（已上传的图片无需重复处理）
WECHAT_IMG_DOMAIN = "mmbiz.qpic.cn"

# 内联样式映射（微信公众号不支持 <style> 标签，必须用内联样式）
INLINE_STYLES = {
    "section": "font-family: 'PingFang SC', 'Microsoft YaHei', sans-serif; color: #333; line-height: 1.8; font-size: 15px; padding: 10px;",
    "h1": "font-size: 22px; color: #ff4500; border-bottom: 2px solid #ff4500; padding-bottom: 8px; margin-bottom: 20px; font-weight: bold;",
    "h2": "font-size: 18px; border-bottom: 2px solid #ff4500; color: #ff4500; padding-bottom: 5px; margin-top: 25px; font-weight: bold;",
    "h3": "font-size: 16px; color: #333; margin-top: 20px; font-weight: bold;",
    "p": "margin: 10px 0; text-align: justify;",
    "blockquote": "border-left: 4px solid #ff4500; background: #fff5f2; padding: 10px 15px; margin: 10px 0; border-radius: 4px; color: #555;",
    "img": "width: 100%; border-radius: 8px; margin-top: 10px; display: block;",
    "a": "color: #ff4500; text-decoration: none;",
    "strong": "color: #ff4500; font-weight: bold;",
    "code": "background: #f5f5f5; padding: 2px 6px; border-radius: 3px; font-size: 13px;",
    "ul": "padding-left: 20px; margin: 10px 0;",
    "ol": "padding-left: 20px; margin: 10px 0;",
    "li": "margin: 5px 0;",
    "hr": "border: none; border-top: 1px solid #eee; margin: 20px 0;",
}


def _add_inline_styles(html: str) -> str:
    """
    为 HTML 元素添加内联样式
    
    微信公众号不支持 <style> 标签，必须为每个元素单独添加 style 属性
    """
    for tag, style in INLINE_STYLES.items():
        # 匹配开始标签，添加 style 属性
        # <h1> -> <h1 style="...">
        # <h1 class="x"> -> <h1 class="x" style="...">
        pattern = re.compile(rf'<({tag})(\s[^>]*)?>', re.IGNORECASE)
        
        def add_style(match):
            full_tag = match.group(0)
            # 如果已有 style 属性，跳过
            if 'style=' in full_tag.lower():
                return full_tag
            # 添加 style 属性
            attrs = match.group(2) or ''
            return f'<{tag}{attrs} style="{style}">'
        
        html = pattern.sub(add_style, html)
    
    return html


def _extract_title(md_content: str) -> str:
    """
    从 Markdown 内容中提取标题
    优先匹配第一行 # 标题，其次匹配第一个 <h1> 标签
    """
    # 匹配 Markdown 标题
    match = re.search(r"^#\s+(.+)$", md_content, re.MULTILINE)
    if match:
        return match.group(1).strip()

    # 匹配 HTML 标题
    match = re.search(r"<h1[^>]*>(.+?)</h1>", md_content, re.IGNORECASE)
    if match:
        return re.sub(r"<[^>]+>", "", match.group(1)).strip()

    return "未命名文章"


def _extract_first_image_url(html_content: str) -> Optional[str]:
    """从 HTML 中提取第一张图片的 URL"""
    match = re.search(r'<img[^>]+src=["\']([^"\']+)["\']', html_content)
    if match:
        return match.group(1)
    return None


def _download_image(url: str, timeout: int = 15) -> Optional[bytes]:
    """下载图片，返回二进制数据"""
    try:
        logger.info(f"正在下载图片: {url[:80]}...")
        resp = requests.get(url, timeout=timeout, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        })
        resp.raise_for_status()
        content_type = resp.headers.get("content-type", "")
        if "image" not in content_type and len(resp.content) < 1000:
            logger.warning(f"下载的内容可能不是图片: {url[:80]}")
            return None
        logger.info(f"图片下载成功 ({len(resp.content)} bytes)")
        return resp.content
    except Exception as e:
        logger.error(f"图片下载失败: {e}")
        return None


def _ensure_jpeg_or_png(data: bytes) -> tuple:
    """
    确保图片是 JPEG/PNG 格式，否则用 Pillow 转换
    
    Returns:
        (converted_data, filename, mime_type)
    """
    if data[:3] == b'\xff\xd8\xff':
        return data, "image.jpg", "image/jpeg"
    elif data[:4] == b'\x89PNG':
        return data, "image.png", "image/png"
    elif data[:4] == b'GIF8':
        return data, "image.gif", "image/gif"
    else:
        # 非 JPEG/PNG/GIF，尝试用 Pillow 转换
        try:
            from PIL import Image
            img = Image.open(io.BytesIO(data))
            buf = io.BytesIO()
            img.convert('RGB').save(buf, format='JPEG', quality=90)
            converted = buf.getvalue()
            logger.info(f"图片格式转换成功: {len(data)} -> {len(converted)} bytes (JPEG)")
            return converted, "image.jpg", "image/jpeg"
        except ImportError:
            logger.warning("Pillow 未安装，无法转换图片格式，尝试以原始数据上传")
            return data, "image.jpg", "image/jpeg"
        except Exception as e:
            logger.error(f"图片格式转换失败: {e}")
            return data, "image.jpg", "image/jpeg"


def _process_images(html_content: str, client: WeChatClient) -> str:
    """
    处理 HTML 中的图片：
    1. 正则匹配所有 <img src="...">
    2. 下载外部图片并上传到微信
    3. 替换为微信 CDN URL
    """
    img_pattern = re.compile(r'<img([^>]*?)src=["\']([^"\']+)["\']([^>]*?)>', re.IGNORECASE)

    def replace_img(match):
        prefix = match.group(1)
        src = match.group(2)
        suffix = match.group(3)

        # 跳过已经是微信图片
        if WECHAT_IMG_DOMAIN in src:
            logger.info(f"跳过微信图片: {src[:60]}...")
            return match.group(0)

        # 本地文件路径
        if os.path.isfile(src):
            try:
                with open(src, "rb") as f:
                    data = f.read()
                data, filename, mime = _ensure_jpeg_or_png(data)
                wechat_url = client.media.upload_image((filename, io.BytesIO(data), mime))
                logger.info(f"本地图片上传成功: {src}")
                return f'<img{prefix}src="{wechat_url}"{suffix}>'
            except Exception as e:
                logger.error(f"本地图片上传失败: {e}")
                return match.group(0)

        # 外部 URL
        img_data = _download_image(src)
        if img_data:
            try:
                img_data, filename, mime = _ensure_jpeg_or_png(img_data)
                wechat_url = client.media.upload_image((filename, io.BytesIO(img_data), mime))
                logger.info(f"图片上传微信成功")
                return f'<img{prefix}src="{wechat_url}"{suffix}>'
            except Exception as e:
                logger.error(f"图片上传微信失败: {e}")
                return match.group(0)

        return match.group(0)

    result = img_pattern.sub(replace_img, html_content)
    return result


def _md_to_html(md_content: str) -> str:
    """
    将 Markdown 转为带内联样式的 HTML
    
    微信公众号不支持 <style> 标签，必须为每个元素单独添加 style 属性
    """
    # 使用 markdown2 转换
    extras = ["fenced-code-blocks", "tables", "strike", "task_list"]
    raw_html = markdown2.markdown(md_content, extras=extras)

    # 用 section 包裹
    html = f"<section>\n{raw_html}\n</section>"

    # 添加内联样式
    html = _add_inline_styles(html)

    return html


def _upload_cover(cover_path: Optional[str], html_content: str,
                  client: WeChatClient) -> str:
    """
    上传封面图，返回 thumb_media_id

    优先级：--cover 参数 > HTML 中第一张图片
    """
    img_data = None

    if cover_path and os.path.isfile(cover_path):
        with open(cover_path, "rb") as f:
            img_data = f.read()
        logger.info(f"使用指定封面图: {cover_path}")
    else:
        # 从 HTML 中提取第一张图片
        first_img_url = _extract_first_image_url(html_content)
        if first_img_url and WECHAT_IMG_DOMAIN not in first_img_url:
            # 如果是外部图片，先上传获取微信 URL，再下载用于封面
            # 注意：upload_image 返回的 URL 不能直接用于 thumb
            # 需要重新下载图片数据
            img_data = _download_image(first_img_url)
            if img_data:
                logger.info("使用文章第一张图片作为封面")
        elif first_img_url and WECHAT_IMG_DOMAIN in first_img_url:
            # 已经是微信图片，下载后上传为 thumb
            img_data = _download_image(first_img_url)
            if img_data:
                logger.info("使用文章第一张图片（已上传）作为封面")

    if not img_data:
        logger.error("未找到封面图！请通过 --cover 参数指定封面图片路径")
        sys.exit(1)

    try:
        # 确保图片数据是有效的 JPEG/PNG 格式
        img_bytes = img_data

        # 检测图片格式
        is_jpeg = img_bytes[:3] == b'\xff\xd8\xff'
        is_png = img_bytes[:4] == b'\x89PNG'

        if not is_jpeg and not is_png:
            logger.warning("图片格式不是 JPEG/PNG，尝试转换为 JPEG")
            try:
                from PIL import Image
                img = Image.open(io.BytesIO(img_data))
                buf = io.BytesIO()
                img.convert('RGB').save(buf, format='JPEG', quality=90)
                img_bytes = buf.getvalue()
                is_jpeg = True
            except ImportError:
                logger.error("需要安装 Pillow 库来转换图片格式: pip install Pillow")
                sys.exit(1)

        # 确定文件名和 MIME type（微信对格式检测严格）
        if is_jpeg:
            filename = "cover.jpg"
            mime_type = "image/jpeg"
        else:
            filename = "cover.png"
            mime_type = "image/png"

        # 上传为 thumb 类型（必须用 thumb，不能用 image）
        result = client.material.add(
            "thumb",
            (filename, io.BytesIO(img_bytes), mime_type)
        )
        thumb_media_id = result["media_id"]
        logger.info(f"封面图上传成功, thumb_media_id: {thumb_media_id}")
        return thumb_media_id
    except Exception as e:
        logger.error(f"封面图上传失败: {e}")
        sys.exit(1)


def publish(md_file_path: str, title: Optional[str] = None,
            author: Optional[str] = None,
            cover: Optional[str] = None,
            digest: Optional[str] = None) -> str:
    """
    发布 Markdown 文件到公众号草稿箱

    Args:
        md_file_path: Markdown 文件路径
        title: 自定义标题（默认从 Markdown 提取）
        author: 作者名
        cover: 封面图路径
        digest: 文章摘要

    Returns:
        草稿的 media_id
    """
    # 1. 读取 Markdown 文件
    if not os.path.isfile(md_file_path):
        logger.error(f"文件不存在: {md_file_path}")
        sys.exit(1)

    with open(md_file_path, "r", encoding="utf-8") as f:
        md_content = f.read()

    logger.info(f"读取文件: {md_file_path} ({len(md_content)} 字符)")

    # 2. 提取标题
    if not title:
        title = _extract_title(md_content)
    # 微信标题限制 64 字节（约 21 个中文字符）
    if len(title.encode('utf-8')) > 64:
        title = title.encode('utf-8')[:63].decode('utf-8', errors='ignore')
    logger.info(f"文章标题: {title}")

    # 3. Markdown 转 HTML
    html_content = _md_to_html(md_content)
    logger.info("Markdown 转 HTML 完成")

    # 4. 初始化微信客户端
    if not Config.WECHAT_APPID or not Config.WECHAT_APPSECRET:
        logger.error("请在 .env 中配置 WECHAT_APPID 和 WECHAT_APPSECRET")
        sys.exit(1)

    client = WeChatClient(Config.WECHAT_APPID, Config.WECHAT_APPSECRET)
    logger.info("微信客户端初始化成功")

    # 5. 处理图片（上传到微信）
    html_content = _process_images(html_content, client)
    logger.info("图片处理完成")

    # 6. 上传封面图
    thumb_media_id = _upload_cover(cover, html_content, client)

    # 7. 构建摘要（微信限制 120 字节）
    if not digest:
        plain_text = re.sub(r"<[^>]+>", "", html_content)
        plain_text = re.sub(r"\s+", " ", plain_text).strip()
        # 微信 digest 限制 120 字节，中文约 40 字，留余量取 54 字符
        digest = plain_text[:54]

    # 8. 创建草稿
    articles = [{
        "title": title,
        "author": author or "",
        "digest": digest,
        "content": html_content,
        "content_source_url": "",
        "thumb_media_id": thumb_media_id,
        "need_open_comment": False,
        "only_fans_can_comment": False,
    }]

    try:
        # wechatpy 1.8.x 可能没有 draft 模块，直接调用微信 API
        if hasattr(client, 'draft') and hasattr(client.draft, 'add'):
            result = client.draft.add(articles)
        else:
            # 直接调用微信草稿箱 API
            import json as _json
            access_token = client.access_token
            url = f"https://api.weixin.qq.com/cgi-bin/draft/add?access_token={access_token}"
            # 必须用 data + encode('utf-8') 确保中文编码正确
            # json= 参数在某些情况下会导致中文编码问题
            payload = _json.dumps({"articles": articles}, ensure_ascii=False).encode("utf-8")
            resp = requests.post(
                url,
                data=payload,
                headers={"Content-Type": "application/json; charset=utf-8"},
                timeout=15,
            )
            result = resp.json()
            if "errcode" in result and result["errcode"] != 0:
                raise Exception(f"Error code: {result['errcode']}, message: {result.get('errmsg', '')}")
        media_id = result["media_id"]
        logger.info(f"✅ 草稿创建成功！media_id: {media_id}")
        logger.info(f"请在公众号后台 → 草稿箱中查看和编辑")
        return media_id
    except Exception as e:
        logger.error(f"草稿创建失败: {e}")
        sys.exit(1)


def main():
    parser = argparse.ArgumentParser(
        description="将 Markdown 文件发布到微信公众号草稿箱",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python publish_to_wechat.py article.md
  python publish_to_wechat.py article.md --title "自定义标题"
  python publish_to_wechat.py article.md --author "动漫资讯" --cover cover.jpg
        """,
    )
    parser.add_argument("file", help="Markdown 文件路径")
    parser.add_argument("--title", "-t", help="自定义文章标题")
    parser.add_argument("--author", "-a", help="作者名")
    parser.add_argument("--cover", "-c", help="封面图路径（默认取文章第一张图片）")
    parser.add_argument("--digest", "-d", help="文章摘要（默认自动提取）")

    args = parser.parse_args()

    publish(
        md_file_path=args.file,
        title=args.title,
        author=args.author,
        cover=args.cover,
        digest=args.digest,
    )


if __name__ == "__main__":
    main()
