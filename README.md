# 动漫新闻自动抓取机器人

自动从多个 RSS 源抓取动漫新闻，通过 AI 智能筛选评分后推送到飞书。

## 功能

- 📡 RSS 抓取：多源聚合
  - **国内源**：萌娘百科、BGM.tv（稳定）
  - **国外源**：Anime News Network、Crunchyroll、MyAnimeList（可能不稳定）
  - **RSSHub 镜像**：Bilibili 动画区、Bangumi 放送（需自建或使用公共镜像）
- 🧠 AI 筛选：Gemini / OpenAI 智能评分，自动过滤低价值新闻
- 📨 飞书推送：Markdown 格式，评分 > 7 的高质量新闻自动推送
- 💾 去重机制：SQLite 持久化，同一条新闻不会重复处理
- ⏰ 定时运行：支持手动运行和定时循环两种模式

## 快速开始

```bash
# 1. 安装依赖
pip install -r requirements.txt

# 2. 配置环境变量
cp .env.example .env
# 编辑 .env，填入你的 API Key 和 Webhook URL

# 3. 手动运行一次
python main.py

# 4. 或启动定时循环（默认每 60 分钟）
python main.py --interval 60
```

## 配置说明

所有配置项均在 `.env` 文件中，详见 `.env.example`。

| 配置项 | 说明 | 默认值 |
|--------|------|--------|
| `AI_PROVIDER` | AI 提供商：`gemini` 或 `openai` | gemini |
| `GEMINI_API_KEY` | Gemini API Key | - |
| `OPENAI_API_KEY` | OpenAI API Key | - |
| `FEISHU_WEBHOOK_URL` | 飞书机器人 Webhook 地址 | - |
| `SCORE_THRESHOLD` | 推送评分阈值（1-10） | 7 |
| `INTERVAL_MINUTES` | 定时抓取间隔（分钟） | 60 |
| `ENABLE_BGM` | 是否启用 BGM.tv 抓取 | true |
| `RSSHUB_BASE` | RSSHub 镜像地址 | rsshub.rssforever.com |

## 项目结构

```
anime-news-bot/
├── main.py              # 主程序入口
├── config.py            # 配置加载
├── fetcher.py           # RSS 抓取模块
├── filter.py            # AI 筛选评分模块
├── notifier.py          # 飞书推送模块
├── storage.py           # SQLite 去重模块
├── .env.example         # 配置模板
├── requirements.txt     # 依赖列表
└── README.md
```
