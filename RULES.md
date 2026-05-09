# 动漫新闻 Bot 规则与逻辑文档

> 本文档是项目的核心规则手册，所有模块的开发和修改必须遵守本文档中的规则。

---

## 一、整体架构

```
RSS 抓取 → SQLite 去重 → AI 筛选评分 → 飞书推送（审核） → 每日汇总 → 公众号草稿箱
```

### 模块职责

| 模块 | 文件 | 职责 | 不可越权 |
|------|------|------|----------|
| 配置 | `config.py` | 读取 `.env`，提供全局配置 | 不含业务逻辑 |
| 抓取 | `fetcher.py` | 从 RSS/网页抓取新闻 | 不做筛选、不调 AI |
| 去重 | `storage.py` | SQLite 存储与去重查询 | 不改数据内容 |
| 筛选 | `filter.py` | AI 评分、翻译标题、生成介绍 | 不推送、不存储 |
| 推送 | `notifier.py` | 飞书 Webhook 推送 | 不改新闻内容 |
| 汇总 | `generate_digest.py` | 生成每日汇总 Markdown | 不抓取、不筛选 |
| 发布 | `publish_to_wechat.py` | Markdown → HTML → 公众号草稿 | 不改原文内容 |
| 主程序 | `main.py` | 编排上述模块的执行流程 | 不含具体实现 |

---

## 二、数据结构（NewsItem）

所有模块共享的数据结构，定义在 `fetcher.py`：

```
NewsItem:
  title       — 原始标题（外文）
  link        — 原文链接
  summary     — RSS 摘要（纯文本，≤500字）
  entry_id    — 唯一标识（默认 = link）
  source      — 来源标识：ann / crunchyroll / mal / bgm / bilibili / moegirl
  published   — 发布时间
  image_url   — 新闻配图 URL

  # AI 填充字段
  score       — 评分 1-10
  ai_title    — 中文翻译标题（≤100字符）
  ai_summary  — 中文简述（≤30字符）
  ai_intro    — 中文介绍（50-100字，≤120字符）
  keep        — 是否保留（bool）
```

---

## 三、RSS 抓取规则（fetcher.py）

### 3.1 抓取源优先级

1. **国内源**（稳定）：萌娘百科、BGM.tv
2. **RSSHub 镜像**（国内推荐）：Bilibili 动画区、Bangumi 每日放送
3. **国外源**（可能不稳定）：ANN、Crunchyroll、MyAnimeList

### 3.2 抓取参数

| 参数 | 值 | 说明 |
|------|-----|------|
| 超时时间 | 20 秒 | 单次请求 |
| 重试次数 | 2 次 | 指数退避（1s, 2s, 4s） |
| 最大条数 | 30 条/源 | 可配置 |
| User-Agent | Chrome 120 | 模拟浏览器 |

### 3.3 图片提取优先级

```
media_thumbnail → media_content → enclosures → summary 中的 <img> 标签
```

### 3.4 摘要处理

- 去除所有 HTML 标签
- 截断至 500 字符
- 空摘要保留，不丢弃条目

---

## 四、去重规则（storage.py）

### 4.1 去重逻辑

- 以 `entry_id`（默认 = link）为唯一标识
- 使用 `INSERT OR IGNORE`，已存在的记录不会覆盖
- 每次运行只处理**新增**新闻

### 4.2 数据库字段

```sql
processed_news (
    entry_id   TEXT UNIQUE,   -- 唯一标识
    title      TEXT,          -- 原始标题
    link       TEXT,          -- 原文链接
    source     TEXT,          -- 来源
    score      INTEGER,       -- AI 评分
    kept       INTEGER,       -- 是否保留（0/1）
    ai_title   TEXT,          -- 中文标题
    ai_intro   TEXT,          -- 中文介绍
    image_url  TEXT,          -- 配图 URL
    created_at TIMESTAMP      -- 处理时间
)
```

### 4.3 存储时机

- AI 筛选完成后，**所有**新新闻（包括被剔除的）都写入数据库
- `kept=1` 表示保留，`kept=0` 表示剔除
- AI 字段（ai_title、ai_intro、image_url）必须同时写入

---

## 五、AI 筛选规则（filter.py）

### 5.1 评分标准

| 分数 | 类别 | 示例 |
|------|------|------|
| 9-10 | 重大业界新闻 | 新企划公布、知名导演新作、重要人事变动 |
| 7-8 | 动画制作动态 | 定档、新预告、重要声优 cast |
| 5-6 | 一般性新闻 | 普通采访、小规模活动、常规 BD 发售 |
| 3-4 | 周边商品 | 手办售卖、手游活动、普通联名 |
| 1-2 | 琐碎信息 | 与动漫核心内容无关 |

### 5.2 保留阈值

- **默认阈值**：`SCORE_THRESHOLD = 7`（可在 .env 配置）
- `score >= 7` 且 `keep = true` 才保留
- AI 调用失败的条目默认**不保留**（score=0, keep=false）

### 5.3 AI 输出格式

必须返回严格 JSON：
```json
{
  "keep": true/false,
  "score": 1-10,
  "title_cn": "中文标题（翻译原标题，保留作品名原名）",
  "summary_cn": "中文简述（30字以内）",
  "intro_cn": "中文介绍（50-100字，适合二次元爱好者阅读）"
}
```

### 5.4 字段截断规则

| 字段 | 最大长度 | 截断方式 |
|------|----------|----------|
| title_cn | 100 字符 | 截断 + "..." |
| summary_cn | 30 字符 | 硬截断 |
| intro_cn | 120 字符 | 截断 + "..." |
| score | 1-10 | clamp |

### 5.5 AI 调用参数

| 参数 | 值 | 说明 |
|------|-----|------|
| temperature | 0.3 | 低随机性，保证评分一致性 |
| max_tokens | 200 | 限制输出长度 |

---

## 六、飞书推送规则（notifier.py）

### 6.1 消息格式

使用飞书 Interactive Card + Markdown 渲染：

```
### 1. {中文标题}

🔥 评分: 9/10  |  来源: MAL

📝 {50-100字中文介绍}

🔗 [查看原文]({link})
```

### 6.2 评分 emoji

| 评分 | emoji |
|------|-------|
| ≥ 9 | 🔥 |
| ≥ 8 | ⭐ |
| ≥ 7 | 📌 |

### 6.3 来源缩写

| source | 显示 |
|--------|------|
| ann | ANN |
| crunchyroll | CR |
| bgm | BGM |
| mal | MAL |
| 其他 | 原始值大写 |

### 6.4 编码要求

- 必须使用 `data=json.dumps(payload, ensure_ascii=False).encode("utf-8")`
- Content-Type: `application/json; charset=utf-8`
- **禁止**使用 `requests.post(json=...)`（中文编码问题）

### 6.5 图片限制

- 飞书 Webhook **不支持**外部图片 URL
- 飞书仅作为**审核工作台**，图片在公众号环节处理

---

## 七、每日汇总规则（generate_digest.py）

### 7.1 文章结构

```markdown
# 🎬 每日动漫资讯 | {MM月DD日} {周X}

> 今日共精选 {N} 条动漫新闻，快来看看有没有你关注的作品！

---

## 🔥 重磅新闻（评分 9+）

### 1. {中文标题}

{50-100字介绍}

![{标题}]({图片URL})

*📌 来源：{SOURCE} | 评分：{score}/10*
*🔗 [查看原文]({link})*

---

## ⭐ 热门资讯（评分 7-8）

（同上格式）

---

*📝 本文由 AI 自动生成，内容来源于各动漫新闻平台*
*🔔 关注我们，每日获取最新动漫资讯*
```

### 7.2 分组规则

| 分组 | 评分范围 | 标题 |
|------|----------|------|
| 🔥 重磅新闻 | ≥ 9 | `## 🔥 重磅新闻` |
| ⭐ 热门资讯 | 7-8 | `## ⭐ 热门资讯` |

### 7.3 排序规则

- 组内按 `score DESC`（高分在前）
- 同分按 `created_at ASC`（先抓取的在前）

### 7.4 图片规则

- 有 `image_url` 的新闻**必须**配图
- 无图片的新闻保持纯文字，不占位
- 图片语法：`![{标题}]({image_url})`

### 7.5 标题规则

- 优先使用 `ai_title`（AI 翻译的中文标题）
- `ai_title` 为空时回退到原始 `title`

---

## 八、公众号发布规则（publish_to_wechat.py）

### 8.1 微信 API 限制

| 限制项 | 值 | 处理方式 |
|--------|-----|----------|
| 标题长度 | 64 字节 | UTF-8 截断至 63 字节 |
| 摘要长度 | 120 字节 | 取前 54 个字符 |
| 封面图类型 | thumb | 必须用 `material.add("thumb")` |
| 正文图片 | upload_image | `client.media.upload_image()` |
| CSS 样式 | **仅内联** | **禁止** `<style>` 标签 |

### 8.2 图片上传规则

- **必须**指定 MIME type：`(filename, BytesIO(data), mime_type)`
- 格式检测：JPEG（`\xff\xd8\xff`）、PNG（`\x89PNG`）、GIF（`GIF8`）
- 非 JPEG/PNG 自动尝试 Pillow 转换
- 已是微信域名（`mmbiz.qpic.cn`）的图片跳过
- 下载超时：15 秒
- 下载失败：保留原链接，打印警告，不中断流程

### 8.3 封面图规则

优先级：
1. `--cover` 参数指定的本地文件
2. HTML 中第一张图片（需下载后上传为 thumb）

### 8.4 编码规则

- **禁止** `requests.post(json=...)`
- **必须** `requests.post(data=payload.encode("utf-8"), headers={"Content-Type": "application/json; charset=utf-8"})`

### 8.5 排版样式（内联）

```css
section:  PingFang SC / Microsoft YaHei, 15px, 行高1.8, #333
h1:       22px, #ff4500, 底部2px橙红边框
h2:       18px, #ff4500, 底部2px橙红边框
h3:       16px, #333
p:        两端对齐
blockquote: 左4px橙红边框, #fff5f2背景, 圆角4px
img:      100%宽, 圆角8px
a:        #ff4500
strong:    #ff4500
hr:       1px #eee 实线
```

---

## 九、配置规则（config.py / .env）

### 9.1 必要配置

| 配置项 | 说明 | 默认值 |
|--------|------|--------|
| AI_PROVIDER | gemini / openai | gemini |
| OPENAI_API_KEY | DeepSeek API Key | — |
| OPENAI_BASE_URL | API 地址 | https://api.deepseek.com/v1 |
| OPENAI_MODEL | 模型名 | deepseek-chat |
| FEISHU_WEBHOOK_URL | 飞书 Webhook | — |
| WECHAT_APPID | 公众号 AppID | — |
| WECHAT_APPSECRET | 公众号 AppSecret | — |

### 9.2 可选配置

| 配置项 | 说明 | 默认值 |
|--------|------|--------|
| SCORE_THRESHOLD | 评分阈值 | 7 |
| INTERVAL_MINUTES | 定时间隔 | 60 |
| ENABLE_BGM | 启用 BGM.tv | false |
| RSSHUB_BASE | RSSHub 镜像 | rsshub.rssforever.com |

---

## 十、执行流程

### 10.1 新闻抓取流程（main.py）

```
1. fetch_all_sources()        → 抓取所有源
2. storage.filter_new()       → SQLite 去重，过滤新新闻
3. ai_filter.filter_news()    → AI 评分 + 翻译 + 介绍
4. send_to_feishu()           → 推送到飞书（仅保留的新闻）
5. storage.mark_processed()   → 所有新新闻写入数据库（含 AI 字段）
```

### 10.2 每日汇总流程（generate_digest.py）

```
1. storage.get_kept_news()    → 查询今日保留的新闻
2. generate_markdown()        → 生成汇总 Markdown（含图片）
3. publish_to_wechat.publish()→ 上传到公众号草稿箱
   ├── _md_to_html()          → Markdown 转 HTML + 内联样式
   ├── _process_images()      → 下载图片 → 上传微信 → 替换 URL
   ├── _upload_cover()        → 上传封面图（thumb 类型）
   └── draft.add API          → 创建草稿
```

---

## 十一、错误处理规则

| 场景 | 处理方式 |
|------|----------|
| RSS 抓取失败 | 重试 2 次，指数退避，最终失败跳过该源 |
| AI 调用失败 | 该条新闻标记为不保留（score=0），不中断流程 |
| JSON 解析失败 | 同上 |
| 图片下载失败 | 保留原链接，打印警告 |
| 图片上传微信失败 | 保留原链接，打印警告 |
| 封面图缺失 | 报错退出，提示用户指定 --cover |
| 飞书推送失败 | 打印错误，不重试 |
| 微信 API 错误 | 打印错误码和消息，退出 |
