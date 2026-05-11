"""
AI 筛选评分模块
支持 Gemini 和 OpenAI 两种 API，对新闻进行智能评分和摘要
"""

import json
import logging
import re
from typing import Optional

from config import Config
from content_filter import ContentFilter
from fetcher import NewsItem

logger = logging.getLogger(__name__)

# 系统提示词 - 专业媒体版：专业标题撰写+正文结构化
SYSTEM_PROMPT = """# 任务：动漫资讯专业撰稿人

你是一位拥有10年经验的资深动漫媒体编辑。请对以下资讯进行专业分析和撰写。

## 一、评分标准（1-10分）

| 分数 | 类别 | 标准 |
|------|------|------|
| 9-10 | 重磅 | 全球首发、超人气IP续作、名监督新作 |
| 7-8 | 重要 | 正式定档、核心CAST/Staff公布、剧场版 |
| 5-6 | 一般 | 播出完毕、常规活动、声优日常 |
| 1-4 | 低价值 | 手游联动、抽奖、普通周边 |

## 二、标题撰写规范

### 结构公式
`[作品名（中括号标注）] + [核心事件] + [关键细节]`

### 专业用词对照

| 场景 | 禁止用词 | 推荐用词 |
|------|----------|----------|
| 定档 | 宣布制作、可能播出 | **定档**、**公布**、确定开播 |
| 声优 | 声优决定 | **追加CAST**、声優配置 |
| PV | 预告片发布 | **PV公开**、预告解禁 |
| 制作 | 开始做 | **制作决定**、承制确定 |
| 完结 | 动画结束了 | **播出完毕**、TV动画落幕 |
| 续作 | 第二季来了 | **续篇制作**、系列新作 |

### 标题示例

- 原: "New Bocchi the Rock anime announced"
  优: 「孤独摇滚」新作动画制作决定，讲述后藤一里组的全新故事

- 原: "Attack on Titan Final Season release date"
  优: 「进击的巨人」最终季Part3定档2023年3月

- 原: "Frieren new visual"
  优: 「葬送的芙莉莲」追加CAST，辛美尔确定为内山昂辉配音

## 三、正文撰写规范

### 结构模板

```
【导语】：时间 + 官方动作 + 核心事件（30-50字）
【背景】：原作/公司/监督/声优简介（20-40字）
【详情】：具体内容 + 看点/期待点（30-60字）
```

### 导语撰写

必须包含：何时 + 何事 + 核心信息

- ✅ 「孤独摇滚」第二季正式定档2024年10月开播
- ✅ MAPPA正式承制「链锯人」剧场版，首支PV解禁
- ❌ 今天公开了新的动画消息

### 背景补充

| 场景 | 补充内容 |
|------|----------|
| 漫画改编 | 原作者、连载杂志、累计发行量 |
| 续作 | 前作播出时间、口碑/数据 |
| 知名公司 | 代表作品、监督风格 |
| 声优首配 | 角色简介、此前演绎风格 |

## 四、必杀关键词

以下内容直接 keep=false：
- 手游、游戏内活动、抽奖、周边预订
- 联动周边、期间限定店、手游生放送
- 裸露、色情、辱华、政治敏感内容

## 五、全文中文翻译（强制要求）

**所有输出内容必须使用简体中文，禁止夹杂日文、英文。**

- 作品名：翻译为中文，如「進撃の巨人」→「进击的巨人」
- 人名：翻译为中文，如「佐藤龍雄」→「佐藤龙雄」
- 公司名：翻译为中文，如「京都アニメーション」→「京都动画」
- 声优/STAFF：翻译为中文，如「声優」→「声优」
- 作品名如果已有公认中文译名，使用公认译名

## 六、输出格式

```json
{
  "score": [1-10数字],
  "keep": [true/false],
  "title_cn": "[作品名]核心事件+关键细节（20-40字，中括号标注作品名）",
  "intro_cn": "第一段导语内容。第二段背景内容。第三段详情内容。",
  "reason": "评分理由（专业术语，如：超人气IP新作定档、Ufotable制作监督执导等）"
}
```"""

USER_PROMPT_TEMPLATE = """请分析以下动漫资讯，用专业媒体人的标准进行撰写：

【原文标题】
{title}

【原文摘要】
{summary}

【来源】
{source}

请按JSON格式返回分析结果。"""


class AIFilter:
    """AI 新闻筛选器"""

    def __init__(self):
        self.provider = Config.AI_PROVIDER
        self._client = None
        self._content_filter = ContentFilter()  # 内容安全过滤器
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
                    "max_output_tokens": 400,
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
                max_tokens=400,
            )
            text = response.choices[0].message.content.strip()
            return self._parse_json_response(text)
        except Exception as e:
            logger.error(f"OpenAI API 调用失败: {e}")
            return None

    def _parse_json_response(self, text: str) -> Optional[dict]:
        """
        解析 AI 返回的 JSON 结果
        兼容处理 markdown 代码块包裹、多行内容、截断等情况
        """
        # 清理文本
        text = text.strip()
        
        # 尝试提取 JSON 代码块
        json_match = re.search(r"```(?:json)?\s*(\{[\s\S]*?\})\s*```", text)
        if json_match:
            text = json_match.group(1)
        
        # 如果没有找到代码块，尝试提取整个 JSON 对象
        if not json_match:
            # 找到第一个 { 和最后一个 }
            first_brace = text.find('{')
            last_brace = text.rfind('}')
            if first_brace >= 0 and last_brace > first_brace:
                text = text[first_brace:last_brace + 1]
        
        # 尝试逐步修复不完整的 JSON
        for attempt in range(5):
            try:
                result = json.loads(text)
                # 验证必要字段
                result.setdefault("keep", False)
                result.setdefault("score", 0)
                result.setdefault("title_cn", "")
                result.setdefault("intro_cn", "")
                result.setdefault("reason", "")

                # 类型转换和范围校验
                result["keep"] = bool(result["keep"])
                result["score"] = max(1, min(10, int(result["score"])))

                # 截断标题（最大50字符）
                if len(result["title_cn"]) > 50:
                    result["title_cn"] = result["title_cn"][:47] + "..."

                # 截断正文（最大150字符）
                if len(result["intro_cn"]) > 150:
                    result["intro_cn"] = result["intro_cn"][:147] + "..."

                return result
            except json.JSONDecodeError as e:
                # 尝试修复常见的截断问题
                if attempt < 4:
                    # 移除末尾可能的截断残留
                    text = re.sub(r'[,\s]*\.\.\.\s*$', '', text)
                    # 移除末尾不完整的字符串值
                    text = re.sub(r',\s*"[^"]*$', '', text)
                    # 移除末尾逗号
                    text = re.sub(r',\s*$', '', text)
                    # 确保以 } 结尾
                    if not text.rstrip().endswith('}'):
                        text += '}'
                    continue
            except (ValueError, TypeError) as e:
                break
        
        logger.error(f"JSON 解析失败（已尝试修复）, 原始文本: {text[:200]}")
        return None

    def analyze(self, item: NewsItem) -> NewsItem:
        """
        分析单条新闻

        Args:
            item: 待分析的 NewsItem

        Returns:
            填充了 score、ai_summary、keep 字段的 NewsItem
        """
        # ===== 前置内容安全检查 =====
        # 1. 文本内容检查（辱华、敏感政治）
        text_passed, text_reason = self._content_filter.check_text(
            f"{item.title} {item.summary}"
        )
        if not text_passed:
            logger.warning(f"内容安全拦截[文本]: {text_reason} | {item.title[:40]}")
            item.score = 0
            item.keep = False
            item.ai_title = ""
            item.ai_summary = ""
            item.ai_intro = ""
            item.reason = f"内容安全拦截: {text_reason}"
            return item
        
        # 2. 图片内容检查（裸露检测）
        if item.image_url:
            image_passed, image_reason = self._content_filter.check_image(item.image_url)
            if not image_passed:
                logger.warning(f"内容安全拦截[图片]: {image_reason} | {item.title[:40]}")
                item.score = 0
                item.keep = False
                item.ai_title = ""
                item.ai_summary = ""
                item.ai_intro = ""
                item.reason = f"内容安全拦截: {image_reason}"
                # 清空图片URL，避免后续使用
                item.image_url = ""
                return item

        # ===== AI 评分分析 =====
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
            item.ai_title = result["title_cn"]  # 专业撰写的中文标题
            item.ai_intro = result["intro_cn"]   # 专业撰写的正文（包含导语/背景/详情）
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