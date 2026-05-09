"""
RSS 抓取模块
支持从多个动漫新闻源抓取数据：
- 国内源：萌娘百科、BGM.tv
- 国外源：Anime News Network、Crunchyroll、MyAnimeList
- RSSHub 镜像（可自建）
"""

import logging
import re
from dataclasses import dataclass, field
from typing import Optional

import feedparser
import requests

from config import Config

logger = logging.getLogger(__name__)

# 请求头，模拟浏览器访问
DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9,zh-CN;q=0.8,zh;q=0.7",
}


@dataclass
class NewsItem:
    """新闻条目数据结构"""
    title: str
    link: str
    summary: str = ""
    entry_id: str = ""
    source: str = ""  # ann / crunchyroll / bgm
    published: str = ""
    image_url: str = ""  # 新闻图片

    # AI 筛选结果（后续填充）
    score: int = 0
    ai_title: str = ""  # AI 翻译的中文标题
    ai_summary: str = ""
    ai_intro: str = ""  # AI 生成的 50-100 字介绍
    keep: bool = False

    def __post_init__(self):
        # 如果没有 entry_id，用 link 作为唯一标识
        if not self.entry_id:
            self.entry_id = self.link


def fetch_rss(rss_url: str, source: str, max_entries: int = 30, retries: int = 2) -> list[NewsItem]:
    """
    从 RSS 源抓取新闻（带重试机制）

    Args:
        rss_url: RSS 订阅地址
        source: 来源标识 (ann / crunchyroll)
        max_entries: 最大抓取条数
        retries: 重试次数

    Returns:
        NewsItem 列表
    """
    items: list[NewsItem] = []
    last_error = None

    for attempt in range(retries + 1):
        try:
            logger.info(f"正在抓取 RSS [{source}]: {rss_url} (尝试 {attempt + 1}/{retries + 1})")

            # 使用 requests 先获取内容，再交给 feedparser 解析
            # 这样可以更好地处理网络错误和编码问题
            resp = requests.get(
                rss_url,
                headers=DEFAULT_HEADERS,
                timeout=20,
                allow_redirects=True
            )
            resp.raise_for_status()

            # 检查内容类型
            content_type = resp.headers.get('content-type', '').lower()

            # 处理可能的压缩内容
            content = resp.content

            # 用 feedparser 解析
            feed = feedparser.parse(content)

            if feed.bozo and not feed.entries:
                # 某些 RSS 有格式问题但仍有条目，继续处理
                if not feed.entries:
                    logger.warning(f"RSS 解析警告 [{source}]: {feed.bozo_exception}")
                    return items

            for entry in feed.entries[:max_entries]:
                # 提取摘要，去除 HTML 标签
                summary = entry.get("summary", "")
                summary = re.sub(r"<[^>]+>", "", summary).strip()
                # 截断过长的摘要
                if len(summary) > 500:
                    summary = summary[:500] + "..."

                # 提取图片 URL
                image_url = ""
                # 方式1: media_thumbnail (MyAnimeList 使用)
                if "media_thumbnail" in entry:
                    thumbnails = entry.get("media_thumbnail", [])
                    if thumbnails and isinstance(thumbnails, list):
                        image_url = thumbnails[0].get("url", "")
                # 方式2: media_content
                if not image_url and "media_content" in entry:
                    media = entry.get("media_content", [])
                    if media and isinstance(media, list):
                        image_url = media[0].get("url", "")
                # 方式3: enclosure
                if not image_url and "enclosures" in entry:
                    enclosures = entry.get("enclosures", [])
                    if enclosures:
                        image_url = enclosures[0].get("href", "")
                # 方式4: 从 summary 中提取第一张图片
                if not image_url and summary:
                    img_match = re.search(r'<img[^>]+src=["\']([^"\']+)["\']', entry.get("summary", ""))
                    if img_match:
                        image_url = img_match.group(1)

                item = NewsItem(
                    title=entry.get("title", "").strip(),
                    link=entry.get("link", ""),
                    summary=summary,
                    entry_id=entry.get("id", entry.get("link", "")),
                    source=source,
                    published=entry.get("published", ""),
                    image_url=image_url,
                )
                items.append(item)

            logger.info(f"[{source}] 抓取到 {len(items)} 条新闻")
            return items  # 成功，直接返回

        except requests.exceptions.ConnectionError as e:
            last_error = f"连接失败: {e}"
            logger.warning(f"RSS 连接失败 [{source}] (尝试 {attempt + 1}): {e}")
            if attempt < retries:
                import time
                time.sleep(2 ** attempt)  # 指数退避

        except requests.exceptions.Timeout as e:
            last_error = f"请求超时: {e}"
            logger.warning(f"RSS 请求超时 [{source}] (尝试 {attempt + 1}): {e}")
            if attempt < retries:
                import time
                time.sleep(2 ** attempt)

        except Exception as e:
            last_error = str(e)
            logger.error(f"RSS 抓取异常 [{source}] (尝试 {attempt + 1}): {e}")
            if attempt < retries:
                import time
                time.sleep(2 ** attempt)

    # 所有重试都失败了
    logger.error(f"RSS 抓取最终失败 [{source}]: {last_error}")
    return items


def fetch_bgm_hot_topics(base_url: str = Config.BGM_BASE_URL,
                         max_entries: int = 20) -> list[NewsItem]:
    """
    抓取 Bangumi (bgm.tv) 热门讨论/条目

    通过解析网页获取热门讨论话题

    Args:
        base_url: bgm.tv 基础 URL
        max_entries: 最大抓取条数

    Returns:
        NewsItem 列表
    """
    items: list[NewsItem] = []
    try:
        url = f"{base_url}/subject/top"
        logger.info(f"正在抓取 Bangumi 热门: {url}")

        resp = requests.get(url, headers=DEFAULT_HEADERS, timeout=15)
        resp.raise_for_status()

        # 解析 HTML 提取热门条目
        # bgm.tv 的 top 页面包含热门动画条目
        html = resp.text

        # 匹配条目链接和标题
        # 格式: <a href="/subject/xxxxx" class="name">标题</a>
        pattern = r'<a\s+href="(/subject/\d+)"[^>]*class="[^"]*name[^"]*"[^>]*>([^<]+)</a>'
        matches = re.findall(pattern, html)

        for path, title in matches[:max_entries]:
            title = title.strip()
            if not title:
                continue

            item = NewsItem(
                title=title,
                link=f"{base_url}{path}",
                summary=f"Bangumi 热门条目: {title}",
                entry_id=f"bgm_{path}",
                source="bgm",
                published="",
            )
            items.append(item)

        # 如果 top 页面解析失败，尝试从首页获取热门讨论
        if not items:
            logger.info("尝试从 Bangumi 首页获取热门讨论...")
            resp = requests.get(base_url, headers=DEFAULT_HEADERS, timeout=15)
            resp.raise_for_status()
            html = resp.text

            # 匹配首页上的话题链接
            pattern = r'<a\s+href="(/subject/\d+|/topic/\d+)"[^>]*>([^<]{4,80})</a>'
            matches = re.findall(pattern, html)

            seen = set()
            for path, title in matches[:max_entries]:
                title = title.strip()
                if not title or title in seen:
                    continue
                seen.add(title)

                item = NewsItem(
                    title=title,
                    link=f"{base_url}{path}",
                    summary=f"Bangumi 热门: {title}",
                    entry_id=f"bgm_{path}",
                    source="bgm",
                    published="",
                )
                items.append(item)

        logger.info(f"[bgm] 抓取到 {len(items)} 条热门内容")

    except Exception as e:
        logger.error(f"Bangumi 抓取异常: {e}")

    return items


def fetch_rsshub_sources() -> list[NewsItem]:
    """
    从 RSSHub 抓取动漫新闻（国内可访问）
    """
    items: list[NewsItem] = []
    base = Config.RSSHUB_BASE.rstrip('/')

    # Bilibili 动画分区热门
    bilibili_url = f"{base}/bilibili/partion/rid/51"
    items.extend(fetch_rss(bilibili_url, source="bilibili", max_entries=20))

    # Bangumi 每日放送
    bangumi_calendar_url = f"{base}/bangumi/calendar/today"
    items.extend(fetch_rss(bangumi_calendar_url, source="bangumi-rss", max_entries=20))

    return items


def fetch_domestic_sources() -> list[NewsItem]:
    """
    从国内可访问的源抓取动漫新闻
    """
    items: list[NewsItem] = []

    # 1. 萌娘百科 Atom Feed
    items.extend(fetch_rss(
        "https://zh.moegirl.org.cn/special:recentchanges?type=feed&feedformat=atom",
        source="moegirl",
        max_entries=30
    ))

    # 2. BGM.tv 热门条目（原生抓取）
    if Config.ENABLE_BGM:
        items.extend(fetch_bgm_hot_topics())

    return items


def fetch_all_sources() -> list[NewsItem]:
    """
    从所有配置的源抓取新闻

    Returns:
        合并去重前的所有 NewsItem
    """
    all_items: list[NewsItem] = []

    # 优先级1: 国内源（稳定可访问）
    logger.info("正在抓取国内源...")
    items = fetch_domestic_sources()
    all_items.extend(items)
    logger.info(f"国内源抓取完成: {len(items)} 条")

    # 优先级2: RSSHub 镜像（国内推荐，备用）
    logger.info("正在从 RSSHub 抓取...")
    try:
        items = fetch_rsshub_sources()
        all_items.extend(items)
        logger.info(f"RSSHub 抓取完成: {len(items)} 条")
    except Exception as e:
        logger.warning(f"RSSHub 抓取失败（不影响其他源）: {e}")

    # 优先级3: 国外源（可能不稳定）
    logger.info("正在抓取国外源（可能不稳定）...")

    items = fetch_rss(Config.RSS_ANN, source="ann")
    all_items.extend(items)

    items = fetch_rss(Config.RSS_CRUNCHYROLL, source="crunchyroll")
    all_items.extend(items)

    # MyAnimeList 新闻（RSS 可用）
    items = fetch_rss("https://myanimelist.net/rss/news.xml", source="mal")
    all_items.extend(items)

    logger.info(f"所有源共抓取 {len(all_items)} 条新闻")
    return all_items
