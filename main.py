#!/usr/bin/env python3
"""
动漫新闻自动抓取 & AI 筛选 & 飞书推送 - 主程序入口

用法:
    python main.py                  # 手动运行一次
    python main.py --interval 60    # 每 60 分钟定时运行
    python main.py --once           # 等同于无参数（运行一次）
"""

import argparse
import logging
import signal
import sys
import time
from datetime import datetime

from config import Config
from fetcher import fetch_all_sources, NewsItem
from filter import AIFilter
from notifier import send_to_feishu
from storage import NewsStorage

# ---------- 日志配置 ----------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("anime-news-bot")

# 全局标志，用于优雅退出
_running = True


def _signal_handler(signum, frame):
    """处理中断信号"""
    global _running
    logger.info(f"收到退出信号 ({signum})，正在停止...")
    _running = False


def run_once(dry_run: bool = False) -> dict:
    """
    执行一次完整的抓取 → 去重 → AI 筛选 → 推送 流程

    Args:
        dry_run: 为 True 时只抓取展示，不调用 AI、不推送、不保存数据库

    Returns:
        本次运行的统计信息
    """
    stats = {
        "fetched": 0,
        "new": 0,
        "kept": 0,
        "pushed": 0,
        "errors": 0,
    }

    mode_str = "【试运行模式】" if dry_run else ""
    logger.info("=" * 50)
    logger.info(f"开始执行抓取任务 {mode_str} - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    logger.info("=" * 50)

    # 1. 初始化存储（dry_run 时跳过数据库）
    storage = None if dry_run else NewsStorage()
    ai_filter = None if dry_run else AIFilter()

    try:
        # 2. 抓取所有源
        all_items = fetch_all_sources()
        stats["fetched"] = len(all_items)

        if not all_items:
            logger.info("未抓取到任何新闻，本次任务结束")
            return stats

        # 试运行模式：直接展示抓取结果
        if dry_run:
            logger.info(f"\n📰 共抓取到 {len(all_items)} 条新闻（试运行模式，跳过 AI 筛选和推送）\n")
            for i, item in enumerate(all_items[:10], 1):  # 只展示前10条
                source_emoji = {"ann": "🇺🇸", "crunchyroll": "🎬", "bgm": "🇨🇳"}.get(item.source, "📰")
                logger.info(f"{i}. {source_emoji} [{item.source.upper()}] {item.title}")
                logger.info(f"   🔗 {item.link}")
                if item.summary:
                    summary = item.summary[:100] + "..." if len(item.summary) > 100 else item.summary
                    logger.info(f"   📝 {summary}")
                logger.info("")
            if len(all_items) > 10:
                logger.info(f"... 还有 {len(all_items) - 10} 条新闻未展示")
            return stats

        # 3. 去重过滤
        new_items = storage.filter_new(all_items)
        stats["new"] = len(new_items)

        if not new_items:
            logger.info("没有新新闻，本次任务结束")
            return stats

        # 4. AI 筛选评分 - 双阈值过滤
        # - Score >= 8: 自动流，直接保留并推送
        # - Score == 7: 待定流，发送到飞书询问
        # - Score < 7: 丢弃流
        kept_items, pending_items = ai_filter.filter_news(new_items)
        stats["kept"] = len(kept_items)
        stats["pending"] = len(pending_items)

        # 5. 推送到飞书
        # 5.1 自动流（Score >= 8）
        if kept_items:
            success = send_to_feishu(kept_items, title="🎬 动漫新闻速递（自动通过）")
            stats["pushed"] = len(kept_items) if success else 0
        
        # 5.2 待定流（Score == 7）
        if pending_items:
            send_to_feishu(pending_items, title="⏳ 动漫新闻待确认（Score 7）")
            logger.info(f"{len(pending_items)} 条新闻待人工确认")

        if not kept_items and not pending_items:
            logger.info("没有达到评分阈值的新闻，不推送")

        # 6. 标记所有新新闻为已处理（包括被过滤的、待定的）
        for item in new_items:
            storage.mark_processed(
                entry_id=item.entry_id,
                title=item.title,
                link=item.link,
                source=item.source,
                score=item.score,
                kept=item in kept_items,
                pending=item in pending_items,
                ai_title=item.ai_title,
                ai_intro=item.ai_intro,
                image_url=item.image_url,
                reason=item.reason,
                published=item.published,
            )

        # 6.1 标记已推送到飞书的新闻
        pushed_ids = [i.entry_id for i in kept_items] + [i.entry_id for i in pending_items]
        if pushed_ids:
            storage.mark_pushed(pushed_ids)

        # 7. 打印统计
        db_stats = storage.get_stats()
        logger.info("-" * 50)
        logger.info(f"本次统计: 抓取 {stats['fetched']} | "
                     f"新增 {stats['new']} | "
                     f"保留 {stats['kept']} | "
                     f"推送 {stats['pushed']}")
        logger.info(f"累计统计: 已处理 {db_stats['total_processed']} | "
                     f"已推送 {db_stats['total_kept']}")
        logger.info("-" * 50)

    except Exception as e:
        logger.error(f"任务执行异常: {e}", exc_info=True)
        stats["errors"] += 1
    finally:
        if storage:
            storage.close()

    return stats


def run_loop(interval_minutes: int):
    """
    定时循环执行

    Args:
        interval_minutes: 执行间隔（分钟）
    """
    global _running

    logger.info(f"启动定时模式，间隔: {interval_minutes} 分钟")
    logger.info("按 Ctrl+C 退出")

    while _running:
        run_once()

        if not _running:
            break

        logger.info(f"下次执行: {interval_minutes} 分钟后")
        # 分段等待，以便及时响应退出信号
        wait_seconds = interval_minutes * 60
        for _ in range(wait_seconds):
            if not _running:
                break
            time.sleep(1)

    logger.info("定时模式已停止")


def main():
    """主入口"""
    parser = argparse.ArgumentParser(
        description="动漫新闻自动抓取 & AI 筛选 & 飞书推送",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python main.py                  # 运行一次（需要配置 API Key）
  python main.py --dry-run        # 试运行：只抓取展示，不调用 AI
  python main.py --interval 60    # 每 60 分钟运行一次
  python main.py -i 30            # 每 30 分钟运行一次
        """,
    )
    parser.add_argument(
        "--interval", "-i",
        type=int,
        default=None,
        help="定时运行间隔（分钟），不指定则只运行一次",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        default=False,
        help="只运行一次（默认行为）",
    )
    parser.add_argument(
        "--dry-run", "-d",
        action="store_true",
        default=False,
        help="试运行模式：只抓取展示，不调用 AI、不推送、不保存数据库",
    )

    args = parser.parse_args()

    # 注册信号处理
    signal.signal(signal.SIGINT, _signal_handler)
    signal.signal(signal.SIGTERM, _signal_handler)

    # 试运行模式跳过配置验证
    if not args.dry_run:
        missing = Config.validate()
        if missing:
            logger.error(f"缺少必要配置项: {', '.join(missing)}")
            logger.error("请复制 .env.example 为 .env 并填入对应的值")
            logger.error("或使用 --dry-run 模式先测试抓取功能")
            sys.exit(1)

        # 打印配置信息
        logger.info(f"AI 提供商: {Config.AI_PROVIDER}")
        logger.info(f"评分阈值: ≥ {Config.SCORE_THRESHOLD}")
        logger.info(f"Bangumi: {'启用' if Config.ENABLE_BGM else '禁用'}")

    # 执行
    if args.interval:
        run_loop(args.interval)
    else:
        run_once(dry_run=args.dry_run)


if __name__ == "__main__":
    main()
