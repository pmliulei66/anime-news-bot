"""
AI 筛选评分模块
支持 Gemini 和 OpenAI 两种 API，对新闻进行智能评分和摘要
"""

import json
import logging
import re
from typing import Optional

from config import Config
from fetcher import NewsItem

logger = logging.getLogger(__name__)

# 系统提示词
SYSTEM_PROMPT = """你是一位资深的动漫行业分析师。你的任务是分析动漫新闻的价值。

评分标准：
- 9-10分：重大业界新闻（新企划公布、知名导演/制作公司新作、重要人事变动）
- 7-8分：动画制作动态、定档信息、新预告片发布、重要声优 cast 公布
- 5-6分：一般性新闻（普通采访、小规模活动、常规 BD 发售）
- 3-4分：周边商品售卖、手游活动、普通联名
- 1-2分：与动漫核心内容无关的琐碎信息

保留规则：
- 评分 >= 7 的新闻保留并推送
- 评分 < 7 的新闻剔除

你必须严格按照以下 JSON 格式回复，不要包含任何其他文字：
{"keep": true/false, "score": 1-10, "title_cn": "中文标题（翻译原标题，保留作品名原名）", "summary_cn": "中文简述（30字以内）", "intro_cn": "中文介绍（50-100字，介绍新闻主要内容，适合二次元爱好者阅读）"}"""

USER_PROMPT_TEMPLATE = """请分析以下动漫新闻：

标题：{title}
摘要：{summary}
来源：{source}

请按 JSON 格式返回分析结果。"""


class AIFilter:
    """AI 新闻筛选器"""

    def __init__(self):
        self.provider = Config.AI_PROVIDER
        self._client = None
        self._init_client()

    def _init_client(self):
        """初始化 AI 客户端"""
        if self.provider == "gemini":
            self._init_gemini()
        elif self.provider == "openai":
            self._init_openai()
        else:
            raise ValueError(f"不支持的 AI 提供商: {self.provider}")

    def _init_gemini(self):
        """初始化 Gemini 客户端"""
        try:
            import google.generativeai as genai
            genai.configure(api_key=Config.GEMINI_API_KEY)
            self._client = genai.GenerativeModel(
                model_name=Config.GEMINI_MODEL,
                system_instruction=SYSTEM_PROMPT,
            )
            logger.info(f"Gemini 客户端初始化完成 (model: {Config.GEMINI_MODEL})")
        except ImportError:
            logger.error("请安装 google-generativeai: pip install google-generativeai")
            raise
        except Exception as e:
            logger.error(f"Gemini 初始化失败: {e}")
            raise

    def _init_openai(self):
        """初始化 OpenAI 客户端"""
        try:
            from openai import OpenAI
            self._client = OpenAI(
                api_key=Config.OPENAI_API_KEY,
                base_url=Config.OPENAI_BASE_URL,
            )
            logger.info(f"OpenAI 客户端初始化完成 (model: {Config.OPENAI_MODEL})")
        except ImportError:
            logger.error("请安装 openai: pip install openai")
            raise
        except Exception as e:
            logger.error(f"OpenAI 初始化失败: {e}")
            raise

    def _call_gemini(self, user_message: str) -> Optional[dict]:
        """调用 Gemini API"""
        try:
            response = self._client.generate_content(
                user_message,
                generation_config={
                    "temperature": 0.3,
                    "max_output_tokens": 200,
                },
            )
            text = response.text.strip()
            return self._parse_json_response(text)
        except Exception as e:
            logger.error(f"Gemini API 调用失败: {e}")
            return None

    def _call_openai(self, user_message: str) -> Optional[dict]:
        """调用 OpenAI API"""
        try:
            response = self._client.chat.completions.create(
                model=Config.OPENAI_MODEL,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": user_message},
                ],
                temperature=0.3,
                max_tokens=200,
            )
            text = response.choices[0].message.content.strip()
            return self._parse_json_response(text)
        except Exception as e:
            logger.error(f"OpenAI API 调用失败: {e}")
            return None

    def _parse_json_response(self, text: str) -> Optional[dict]:
        """
        解析 AI 返回的 JSON 结果
        兼容处理 markdown 代码块包裹的情况
        """
        # 尝试提取 JSON（可能被 ```json ... ``` 包裹）
        json_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
        if json_match:
            text = json_match.group(1)

        # 尝试直接提取 JSON 对象
        if not json_match:
            json_match = re.search(r"\{.*\}", text, re.DOTALL)
            if json_match:
                text = json_match.group(0)

        try:
            result = json.loads(text)
            # 验证必要字段
            result.setdefault("keep", False)
            result.setdefault("score", 0)
            result.setdefault("title_cn", "")
            result.setdefault("summary_cn", "")
            result.setdefault("intro_cn", "")

            # 类型转换和范围校验
            result["keep"] = bool(result["keep"])
            result["score"] = max(1, min(10, int(result["score"])))

            # 截断中文标题
            if len(result["title_cn"]) > 100:
                result["title_cn"] = result["title_cn"][:97] + "..."

            # 截断中文简述
            if len(result["summary_cn"]) > 30:
                result["summary_cn"] = result["summary_cn"][:30]

            # 截断中文介绍（50-100字）
            if len(result["intro_cn"]) > 120:
                result["intro_cn"] = result["intro_cn"][:117] + "..."

            return result
        except (json.JSONDecodeError, ValueError, TypeError) as e:
            logger.error(f"JSON 解析失败: {e}, 原始文本: {text[:200]}")
            return None

    def analyze(self, item: NewsItem) -> NewsItem:
        """
        分析单条新闻

        Args:
            item: 待分析的 NewsItem

        Returns:
            填充了 score、ai_summary、keep 字段的 NewsItem
        """
        user_message = USER_PROMPT_TEMPLATE.format(
            title=item.title,
            summary=item.summary or "无摘要",
            source=item.source,
        )

        if self.provider == "gemini":
            result = self._call_gemini(user_message)
        else:
            result = self._call_openai(user_message)

        if result:
            item.score = result["score"]
            item.keep = result["keep"]
            item.ai_title = result["title_cn"]  # AI 翻译的中文标题
            item.ai_summary = result["summary_cn"]
            item.ai_intro = result["intro_cn"]
            logger.info(
                f"[{item.source}] 评分: {item.score}, 保留: {item.keep} | "
                f"{item.ai_title[:40] if item.ai_title else item.title[:40]}"
            )
        else:
            # AI 调用失败时默认不保留
            item.score = 0
            item.keep = False
            item.ai_title = ""
            item.ai_summary = "AI 分析失败"
            item.ai_intro = ""
            logger.warning(f"AI 分析失败，跳过: {item.title[:40]}")

        return item

    def filter_news(self, items: list[NewsItem]) -> list[NewsItem]:
        """
        批量筛选新闻

        Args:
            items: 待筛选的 NewsItem 列表

        Returns:
            评分超过阈值且被 AI 标记为保留的 NewsItem 列表
        """
        kept_items = []

        for item in items:
            analyzed = self.analyze(item)
            if analyzed.keep and analyzed.score >= Config.SCORE_THRESHOLD:
                kept_items.append(analyzed)

        logger.info(
            f"AI 筛选完成: {len(items)} 条中 {len(kept_items)} 条被保留 "
            f"(阈值 >= {Config.SCORE_THRESHOLD})"
        )
        return kept_items
