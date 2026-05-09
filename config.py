"""
配置加载模块
从 .env 文件读取所有配置项，提供统一的配置接口
"""

import os
from dotenv import load_dotenv

# 加载 .env 文件（优先当前目录，其次项目根目录）
load_dotenv()


class Config:
    """全局配置"""

    # ---------- AI API ----------
    AI_PROVIDER: str = os.getenv("AI_PROVIDER", "gemini").lower()

    GEMINI_API_KEY: str = os.getenv("GEMINI_API_KEY", "")
    GEMINI_MODEL: str = os.getenv("GEMINI_MODEL", "gemini-2.0-flash")

    OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "")
    OPENAI_MODEL: str = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
    OPENAI_BASE_URL: str = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")

    # ---------- RSS 源 ----------
    # 国外源（国内可能不稳定）
    RSS_ANN: str = os.getenv("RSS_ANN", "https://www.animenewsnetwork.com/all/rss.xml")
    RSS_CRUNCHYROLL: str = os.getenv("RSS_CRUNCHYROLL", "https://www.crunchyroll.com/news/rss")

    # RSSHub 镜像（国内推荐）
    RSSHUB_BASE: str = os.getenv("RSSHUB_BASE", "https://rsshub.rssforever.com")

    # Bilibili 动画分区 (rid=51 是动画区)
    RSS_BILIBILI_ANIME: str = os.getenv("RSS_BILIBILI_ANIME", "")

    # Bangumi 每日放送
    RSS_BANGUMI_CALENDAR: str = os.getenv("RSS_BANGUMI_CALENDAR", "")

    # 原生 Bangumi
    ENABLE_BGM: bool = os.getenv("ENABLE_BGM", "false").lower() == "true"
    BGM_BASE_URL: str = os.getenv("BGM_BASE_URL", "https://bgm.tv")

    # ---------- 飞书 ----------
    FEISHU_WEBHOOK_URL: str = os.getenv("FEISHU_WEBHOOK_URL", "")

    # ---------- 筛选 ----------
    SCORE_THRESHOLD: int = int(os.getenv("SCORE_THRESHOLD", "7"))

    # ---------- 运行 ----------
    INTERVAL_MINUTES: int = int(os.getenv("INTERVAL_MINUTES", "60"))

    # ---------- 内部 ----------
    DB_PATH: str = os.path.join(os.path.dirname(os.path.abspath(__file__)), "news.db")

    @classmethod
    def validate(cls) -> list[str]:
        """验证必要配置项，返回缺失项列表"""
        missing = []
        if cls.AI_PROVIDER == "gemini" and not cls.GEMINI_API_KEY:
            missing.append("GEMINI_API_KEY")
        elif cls.AI_PROVIDER == "openai" and not cls.OPENAI_API_KEY:
            missing.append("OPENAI_API_KEY")
        if not cls.FEISHU_WEBHOOK_URL:
            missing.append("FEISHU_WEBHOOK_URL")
        return missing
