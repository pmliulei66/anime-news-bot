"""
飞书 Webhook 推送模块
将筛选后的新闻封装成 Markdown 格式推送到飞书群
"""

import json
import logging
from datetime import datetime
from typing import Optional

import requests

from config import Config
from fetcher import NewsItem

logger = logging.getLogger(__name__)

# 飞书 API 请求头
FEISHU_HEADERS = {
    "Content-Type": "application/json; charset=utf-8",
}


def _build_feishu_card(items: list[NewsItem]) -> dict:
    """
    构建飞书消息卡片（Interactive Card）

    使用飞书新版消息卡片格式，支持 Markdown 渲染
    """
    # 构建新闻列表 Markdown 内容
    news_lines = []
    for i, item in enumerate(items, 1):
        score_emoji = "🔥" if item.score >= 9 else "⭐" if item.score >= 8 else "📌"
        source_label = {
            "ann": "ANN",
            "crunchyroll": "CR",
            "bgm": "BGM",
            "mal": "MAL",
        }.get(item.source, item.source.upper())

        # 构建单条新闻内容 - 标题+内容形式（使用 AI 翻译的中文标题）
        display_title = item.ai_title if item.ai_title else item.title
        line = f"### {i}. {display_title}\n\n"
        line += f"{score_emoji} **评分**: {item.score}/10  |  **来源**: {source_label}\n\n"

        # 添加 AI 介绍（50-100字）
        if item.ai_intro:
            line += f"📝 {item.ai_intro}\n\n"

        # 添加原文链接
        line += f"🔗 [查看原文]({item.link})"

        news_lines.append(line)

    content_md = "\n\n---\n\n".join(news_lines)

    # 飞书消息卡片 JSON
    card = {
        "msg_type": "interactive",
        "card": {
            "header": {
                "title": {
                    "tag": "plain_text",
                    "content": f"🎬 动漫新闻速递 ({datetime.now().strftime('%m-%d %H:%M')})",
                },
                "template": "blue",
            },
            "elements": [
                {
                    "tag": "markdown",
                    "content": content_md,
                },
                {
                    "tag": "note",
                    "elements": [
                        {
                            "tag": "plain_text",
                            "content": (
                                f"共 {len(items)} 条高质量新闻 | "
                                f"评分阈值 ≥ {Config.SCORE_THRESHOLD} | "
                                f"Powered by DeepSeek AI"
                            ),
                        }
                    ],
                },
            ],
        },
    }

    return card


def _build_feishu_text(items: list[NewsItem]) -> dict:
    """
    构建飞书富文本消息（备用方案，兼容性更好）
    """
    lines = [
        f"🎬 **动漫新闻速递** ({datetime.now().strftime('%m-%d %H:%M')})\n",
    ]

    for i, item in enumerate(items, 1):
        score_emoji = "🔥" if item.score >= 9 else "⭐" if item.score >= 8 else "📌"
        source_label = {
            "ann": "ANN",
            "crunchyroll": "CR",
            "bgm": "BGM",
        }.get(item.source, item.source.upper())

        lines.append(
            f"**{i}. {item.title}**\n"
            f"{score_emoji} 评分: {item.score}/10 | 来源: {source_label}\n"
            f"💬 {item.ai_summary}\n"
            f"🔗 [查看原文]({item.link})\n"
        )

    content = "\n---\n".join(lines)

    return {
        "msg_type": "interactive",
        "card": {
            "elements": [
                {
                    "tag": "markdown",
                    "content": content,
                }
            ]
        },
    }


def send_to_feishu(items: list[NewsItem],
                   webhook_url: Optional[str] = None,
                   use_card: bool = True) -> bool:
    """
    推送新闻到飞书

    Args:
        items: 待推送的 NewsItem 列表
        webhook_url: 飞书 Webhook URL（默认从配置读取）
        use_card: 是否使用卡片格式（false 则使用富文本）

    Returns:
        是否推送成功
    """
    if not items:
        logger.info("没有需要推送的新闻，跳过")
        return True

    webhook_url = webhook_url or Config.FEISHU_WEBHOOK_URL
    if not webhook_url:
        logger.error("飞书 Webhook URL 未配置")
        return False

    # 构建消息体
    if use_card:
        payload = _build_feishu_card(items)
    else:
        payload = _build_feishu_text(items)

    try:
        logger.info(f"正在推送 {len(items)} 条新闻到飞书...")
        resp = requests.post(
            webhook_url,
            headers=FEISHU_HEADERS,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            timeout=15,
        )
        resp.raise_for_status()

        result = resp.json()
        if result.get("code", -1) == 0:
            logger.info("飞书推送成功 ✓")
            return True
        else:
            logger.error(f"飞书推送失败: {result.get('msg', '未知错误')}")
            return False

    except requests.RequestException as e:
        logger.error(f"飞书推送请求异常: {e}")
        return False
