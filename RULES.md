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
  pending     — Score == 7 时为待定状态（bool）
  reason      — AI 评分理由（简短中文）
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

1. **Entry ID 去重**：以 `entry_id`（默认 = link）为唯一标识，使用 `INSERT OR IGNORE`
2. **标题模糊去重**：使用 `rapidfuzz` 计算标题相似度，24 小时内相似度 > 90% 的标题视为重复

```
IF 标题相似度 > 90% AND 时间跨度 < 24h THEN 跳过
```

### 4.2 数据库字段

```sql
processed_news (
    entry_id   TEXT UNIQUE,   -- 唯一标识
    title      TEXT,          -- 原始标题
    link       TEXT,          -- 原文链接
    source     TEXT,          -- 来源
    score      INTEGER,       -- AI 评分
    kept       INTEGER,       -- 是否保留（0/1）
    pending    INTEGER,       -- 是否待定（0/1，Score == 7 时为 1）
    ai_title   TEXT,          -- 中文标题
    ai_intro   TEXT,          -- 中文介绍
    image_url  TEXT,          -- 配图 URL
    reason     TEXT,          -- AI 评分理由
    published  TEXT,          -- 新闻发布日期
    created_at TIMESTAMP      -- 处理时间
)
```

### 4.3 存储时机

- AI 筛选完成后，**所有**新新闻（包括被剔除的）都写入数据库
- `kept=1` 表示保留，`kept=0` 表示剔除
- AI 字段（ai_title、ai_intro、image_url）必须同时写入

### 4.4 数据保留规则

- 数据库**永久保留**所有历史数据，不删除
- **每天新增的数据**最多保留 30 条（按评分排序，删除当天评分最低的）
- 历史数据不受影响

### 4.5 推送与展示规则

| 渠道 | 数据范围 | 说明 |
|------|----------|------|
| 飞书推送 | **未推送过的** kept≥7 新闻 | 基于 `pushed` 字段去重，上次没推过这次就推 |
| 公众号汇总 | **仅今天**发布的新闻 | 最多取前 15 条 |
| 数据库存储 | **所有日期**的新闻 | 全部保存，不删除 |

---

## 五、AI 筛选规则（filter.py）

### 5.1 评分标准（内容价值 + 独特性）

| 分数 | 类别 | 示例 |
|------|------|------|
| 9-10 | 重磅 | 全球首发、超人气IP续作、名监督新作 |
| 7-8 | 必追 | 新番定档（含视觉图/PV）、核心Staff/声优变动 |
| 5-6 | 观察 | 动画完结感言、声优重大喜报、高水平幕后采访 |
| 1-4 | 垃圾 | 手游联动、抽奖活动、普通手办预售 |

### 5.2 加分关键词（Bonus）

满足以下关键词时**升档处理**：
- PV2（通常画质更稳）
- 制作决定（首发新闻）
- Staff公布、剧场版
- 知名IP的"定档"、"预告"、"特报" → 直接给 8 分以上

### 5.3 必杀关键词（Reject）

标题/摘要中包含以下关键词，直接 `keep=false`：
- 手游、游戏内活动、抽奖、周边预订
- 联动周边、期间限定店、手游生放送、游戏复刻

### 5.4 强制过滤（安全规则）

以下内容直接 `keep=false`：
- 涉及裸露、色情的内容
- 极端政治敏感内容
- 明显辱华倾向的番剧相关新闻

### 5.5 内容安全过滤（content_filter.py）

在 AI 评分之前，先进行前置内容安全检查：

#### 5.5.1 文本内容检查

使用关键词匹配检测以下内容：
- **辱华内容**：历史否认（南京大屠杀虚构、慰安妇自愿等）、分裂主义（台独、港独、藏独、疆独）
- **敏感政治**：领土主权争议、反华言论
- **色情内容**：裸露、性行为相关词汇

检测方式：`check_text_content(text)` 返回 `(是否通过, 原因)`

#### 5.5.2 图片内容检查

使用肤色检测算法识别裸露图片：
- 下载图片并分析肤色比例
- 肤色占比超过阈值（默认 30%）视为可疑
- 支持 JPEG、PNG、GIF 格式

检测方式：`check_image_content(image_url)` 返回 `(是否通过, 原因)`

#### 5.5.3 拦截处理

内容安全检查未通过时：
- `score = 0`, `keep = False`
- `reason = "内容安全拦截: {原因}"`
- 图片违规时清空 `image_url`
- 记录警告日志，不进入 AI 评分流程

### 5.6 双阈值过滤

| Score | 状态 | 处理方式 |
|-------|------|----------|
| ≥ 8 | 自动流 | 直接保留并推送飞书 |
| = 7 | 待定流 | 推送到飞书待确认区 |
| < 7 | 丢弃流 | 默默记录，不打扰 |

### 5.6 AI 输出格式（专业媒体版）

必须返回严格 JSON：
```json
{
  "score": 1-10,
  "keep": true/false,
  "title_cn": "[作品名]核心事件+关键细节（20-40字，中括号标注作品名）",
  "intro_cn": "【导语】（30-50字）\n\n【背景】（20-40字）\n\n【详情】（30-60字）",
  "reason": "评分理由（专业术语）"
}
```

### 5.7 标题撰写规范

| 元素 | 要求 |
|------|------|
| 结构 | `[作品名] + [核心事件] + [关键细节]` |
| 作品名 | 用「」或【】标注 |
| 长度 | 20-40 字 |
| 用词 | 专业术语（定档、追加CAST、PV公开等） |

### 5.8 正文撰写规范

| 部分 | 内容 | 字数 |
|------|------|------|
| 【导语】 | 时间 + 官方动作 + 核心事件 | 30-50字 |
| 【背景】 | 原作/公司/监督/声优简介 | 20-40字 |
| 【详情】 | 具体内容 + 看点/期待点 | 30-60字 |

### 5.9 字段截断规则

| 字段 | 最大长度 | 截断方式 |
|------|----------|----------|
| title_cn | 50 字符 | 截断 + "..." |
| intro_cn | 150 字符 | 截断 + "..." |
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

### 1. {中文标题}

{50-100字介绍}

![{标题}]({图片URL})

*📅 {新闻日期} | 🔗 [查看原文]({link})*

### 2. ...

---

*📝 本文由 AI 自动生成，内容来源于各动漫新闻平台*
*🔔 关注我们，每日获取最新动漫资讯*
```

### 7.2 排版规则

- **不显示评分**：移除评分显示
- **不显示来源**：移除来源显示
- **不分组**：不再区分"重磅新闻"和"热门资讯"，直接按评分倒序排列
- **显示日期**：每条新闻显示发布日期（📅 MM月DD日）
- **不显示原文链接**：移除"查看原文"链接

### 7.3 排序规则

- 组内按 `score DESC`（高分在前）
- 同分按 `created_at ASC`（先抓取的在前）
- **公众号最多取前 15 条**（按评分排序后截断）

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
2. storage.filter_new()       → SQLite 去重（entry_id + 标题模糊）
3. ai_filter.filter_news()    → AI 评分 + 双阈值过滤
   ├─ Score >= 8: 自动流 → kept_items
   ├─ Score == 7: 待定流 → pending_items
   └─ Score < 7: 丢弃流
4. send_to_feishu(kept_items, title="自动通过")
   send_to_feishu(pending_items, title="待确认") → 推送到飞书
5. storage.mark_processed()   → 所有新新闻写入数据库
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
