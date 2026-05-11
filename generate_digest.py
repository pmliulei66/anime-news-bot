#!/usr/bin/env python3
"""
每日动漫新闻汇总生成工具

从数据库读取今日高评分新闻，生成公众号风格的 Markdown 汇总文章，
并可选上传到公众号草稿箱。

用法:
    python generate_digest.py                    # 生成今日汇总并预览
    python generate_digest.py --publish          # 生成并上传到公众号草稿箱
    python generate_digest.py --date 2026-05-09  # 指定日期
    python generate_digest.py --cover cover.jpg  # 指定封面图
"""

import argparse
import logging
import sys
from datetime import datetime

from storage import NewsStorage

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("generate-digest")


def generate_markdown(news_list: list[dict], date_str: str) -> str:
    """
    根据新闻列表生成公众号风格的 Markdown 汇总文章

    Args:
        news_list: 新闻字典列表（来自 storage.get_kept_news）
        date_str: 日期字符串

    Returns:
        完整的 Markdown 文章内容
    """
    if not news_list:
        logger.warning("没有新闻可生成汇总")
        return ""

    # 按评分排序（已由 SQL 保证），取前 15 条用于公众号
    news_list = news_list[:15]
    logger.info(f"取前 {len(news_list)} 条新闻生成汇总")

    # 构建文章（不再分组，直接按评分倒序排列）
    lines = []

    # 标题
    date_display = datetime.strptime(date_str, "%Y-%m-%d").strftime("%m月%d日")
    weekday_names = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
    weekday = weekday_names[datetime.strptime(date_str, "%Y-%m-%d").weekday()]
    lines.append(f"# 🎬 每日动漫资讯 | {date_display} {weekday}")
    lines.append("")
    lines.append(f"> 今日共精选 {len(news_list)} 条动漫新闻，快来看看有没有你关注的作品！")
    lines.append("")

    # 直接按评分倒序排列所有新闻（不分组）
    for i, news in enumerate(news_list, 1):
        title = news["ai_title"] or news["title"]
        intro = news["ai_intro"] or ""
        image_url = news.get("image_url", "")
        # 获取新闻日期（如果有）
        news_date = news.get("published", "") or news.get("created_at", "")
        if news_date:
            try:
                # 尝试解析日期
                if "T" in news_date:
                    news_date = datetime.fromisoformat(news_date.replace("Z", "+00:00")).strftime("%m月%d日")
                elif " " in news_date:
                    news_date = datetime.strptime(news_date[:10], "%Y-%m-%d").strftime("%m月%d日")
            except:
                news_date = ""

        # 标题（去掉中括号标注，保留作品名）
        display_title = title.replace("「", "").replace("」", "").replace("【", "").replace("】", "")
        lines.append(f"### {i}. {display_title}")
        lines.append("")

        # 正文（如果包含分段结构，保持格式）
        if intro:
            # 处理 AI 返回的 分段格式
            if "【" in intro and "】" in intro:
                # 保留原有分段结构
                lines.append(intro)
            else:
                # 没有分段，作为导语处理
                lines.append(f"{intro}")
            lines.append("")

        # 图片（放在正文之后）
        if image_url:
            lines.append(f"![{title}]({image_url})")
            lines.append("")

        # 只显示日期
        if news_date:
            lines.append(f"*📅 {news_date}*")
            lines.append("")

    # 底部
    lines.append("---")
    lines.append("")
    lines.append("*📝 本文由 AI 自动生成，内容来源于各动漫新闻平台*")
    lines.append("*🔔 关注我们，每日获取最新动漫资讯*")

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(
        description="生成每日动漫新闻汇总",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python generate_digest.py                    # 生成并预览
  python generate_digest.py --publish          # 生成并上传公众号
  python generate_digest.py --date 2026-05-09  # 指定日期
  python generate_digest.py -p -c cover.jpg    # 上传并指定封面
        """,
    )
    parser.add_argument("--date", "-d", help="日期 (YYYY-MM-DD)，默认今天")
    parser.add_argument("--publish", "-p", action="store_true", help="生成后上传到公众号草稿箱")
    parser.add_argument("--cover", "-c", help="封面图路径")
    parser.add_argument("--output", "-o", help="输出 Markdown 文件路径")

    args = parser.parse_args()

    # 确定日期
    if args.date:
        date_str = args.date
    else:
        date_str = datetime.now().strftime("%Y-%m-%d")

    # 从数据库读取
    storage = NewsStorage()
    try:
        news_list = storage.get_kept_news(date_str)
    finally:
        storage.close()

    if not news_list:
        logger.info(f"{date_str} 没有已保留的新闻，跳过")
        sys.exit(0)

    # 生成 Markdown
    md_content = generate_markdown(news_list, date_str)

    # 输出到文件
    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(md_content)
        logger.info(f"Markdown 已保存到: {args.output}")
    else:
        # 默认保存
        default_path = f"digest_{date_str}.md"
        with open(default_path, "w", encoding="utf-8") as f:
            f.write(md_content)
        logger.info(f"Markdown 已保存到: {default_path}")

    # 预览
    print("\n" + "=" * 50)
    print(md_content)
    print("=" * 50 + "\n")

    # 上传到公众号
    if args.publish:
        logger.info("正在上传到公众号草稿箱...")
        from publish_to_wechat import publish

        md_file = args.output or f"digest_{date_str}.md"
        media_id = publish(
            md_file_path=md_file,
            cover=args.cover,
            author="动漫资讯Bot",
        )
        logger.info(f"✅ 汇总文章已上传到草稿箱！media_id: {media_id}")


if __name__ == "__main__":
    main()
