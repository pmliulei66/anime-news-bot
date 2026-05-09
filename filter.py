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

# 系统提示词 - 优化版：引入内容价值+独特性、加分/必杀关键词
SYSTEM_PROMPT = """# 任务：动漫资讯降噪专家

你现在是一名资深动漫博主，请对以下 RSS 资讯进行筛选。你的目标是选出能引起动漫爱好者讨论的"硬核资讯"。

## 筛选标准：
1. **内容权重评分 (0-10)：**
   - [9-10分]：全球首发、超人气IP新作、名监督（如汤浅、新海诚）新动作。
   - [7-8分]：新番正式定档（包含视觉图/PV）、核心Staff/声优表变动。
   - [5-6分]：动画完结感言、声优重大喜报、高水平的动画幕后采访。
   - [5分以下]：手游联动、抽奖活动、普通手办预售、不涉及动画本体的商业活动。

2. **加分关键词 (Bonus)：**
   - PV2（通常画质更稳）
   - 制作决定（首发新闻）
   - Staff公布、剧场版
   - 如果是知名IP的"定档"、"预告"、"特报"，直接给 8 分以上

3. **必杀关键词 (Reject)：**
   - 标题中包含"手游"、"游戏内活动"、"抽奖"、"周边预订"
   - 联动周边、期间限定店、手游生放送、游戏复刻
   - 以上内容直接标记 keep=false

4. **强制规则：**
   - 涉及裸露、色情、极端政治敏感的内容，直接标记 keep=false
   - 有明显辱华倾向的番剧相关新闻，直接标记 keep=false

## 输出格式：
请仅返回 JSON：
{
  "score": [数字],
  "keep": [true/false],
  "title_cn": "中文标题（翻译原标题，保留作品名原名）",
  "summary_cn": "中文简述（30字以内）",
  "intro_cn": "中文介绍（50-100字，适合二次元爱好者阅读）",
  "reason": "简短的中文字符串，说明为何给这个分"
}"""

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
            result.setdefault("reason", "")

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
            item.reason = result.get("reason", "")
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
            item.reason = ""
            logger.warning(f"AI 分析失败，跳过: {item.title[:40]}")

        return item

    def filter_news(self, items: list[NewsItem]) -> list[NewsItem]:
        """
        批量筛选新闻 - 双阈值过滤
        
        - Score >= 8: 自动流，直接保留
        - Score == 7: 待定流，需要人工确认
        - Score < 7: 丢弃流

        Args:
            items: 待筛选的 NewsItem 列表

        Returns:
            评分超过阈值且被 AI 标记为保留的 NewsItem 列表
        """
        kept_items = []
        pending_items = []

        for item in items:
            analyzed = self.analyze(item)
            if analyzed.keep and analyzed.score >= 8:
                # 自动流：Score >= 8
                kept_items.append(analyzed)
            elif analyzed.keep and analyzed.score == 7:
                # 待定流：Score == 7，标记为待定
                analyzed.pending = True
                pending_items.append(analyzed)

        logger.info(
            f"AI 筛选完成: {len(items)} 条中 {len(kept_items)} 条自动保留, "
            f"{len(pending_items)} 条待定 (Score 7)"
        )
        
        # 返回自动保留项，待定项由调用方单独处理
        return kept_items, pending_items