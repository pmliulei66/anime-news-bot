#!/usr/bin/env python3
"""
简化测试：直接测试核心逻辑，不依赖 HTTP 服务器
"""

import sys
import os
from pathlib import Path

# 切换到项目目录
os.chdir(Path(__file__).parent)
sys.path.insert(0, str(Path(__file__).parent))

from storage import NewsStorage
from fetcher import NewsItem
from datetime import datetime, timedelta

def test_data_flow():
    """测试数据流转逻辑"""
    print("=" * 50)
    print("测试数据流转逻辑")
    print("=" * 50)
    
    # 1. 创建内存数据库
    storage = NewsStorage(":memory:")
    
    # 2. 模拟不同日期的新闻
    test_items = [
        # 今天的新闻（5-11）
        NewsItem(
            entry_id="test-1",
            title="「进击的巨人」最终季定档",
            link="http://example.com/1",
            summary="Test summary 1",
            source="test",
            published="2026-05-11T08:00:00Z",
            score=9,
            keep=True,
            ai_title="「进击的巨人」最终季定档2026年秋季",
            ai_intro="Test intro",
        ),
        NewsItem(
            entry_id="test-2",
            title="「孤独摇滚」第二季追加CAST",
            link="http://example.com/2",
            summary="Test summary 2",
            source="test",
            published="2026-05-11T06:30:00Z",
            score=8,
            keep=True,
            ai_title="「孤独摇滚」第二季追加CAST公开",
            ai_intro="Test intro",
        ),
        # 昨天的新闻（5-10）
        NewsItem(
            entry_id="test-3",
            title="「咒术回战」剧场版PV",
            link="http://example.com/3",
            summary="Test summary 3",
            source="test",
            published="2026-05-10T14:00:00Z",
            score=8,
            keep=True,
            ai_title="「咒术回战」剧场版最新PV公开",
            ai_intro="Test intro",
        ),
        # 低质量新闻（应该被过滤）
        NewsItem(
            entry_id="test-4",
            title="某手游联动活动",
            link="http://example.com/4",
            summary="Test summary 4",
            source="test",
            published="2026-05-11T09:00:00Z",
            score=3,
            keep=False,
            ai_title="",
            ai_intro="",
        ),
    ]
    
    # 3. 标记为已处理
    print("\n[1] 标记新闻为已处理...")
    for item in test_items:
        storage.mark_processed(
            entry_id=item.entry_id,
            title=item.title,
            link=item.link,
            source=item.source,
            score=item.score,
            kept=item.keep,
            pending=False,
            ai_title=item.ai_title,
            ai_intro=item.ai_intro,
            image_url="",
            reason="",
            published=item.published,
        )
    
    # 4. 模拟推送（标记 pushed）
    print("[2] 模拟飞书推送，标记 pushed...")
    pushed_ids = [item.entry_id for item in test_items if item.keep]
    storage.mark_pushed(pushed_ids)
    
    # 5. 验证结果
    print("\n[3] 验证数据库状态...")
    
    # 总记录
    c = storage._conn.execute("SELECT COUNT(*) FROM processed_news")
    print(f"  总记录: {c.fetchone()[0]} 条")
    
    # 保留记录
    c = storage._conn.execute("SELECT COUNT(*) FROM processed_news WHERE kept=1")
    print(f"  保留记录: {c.fetchone()[0]} 条")
    
    # 已推送
    c = storage._conn.execute("SELECT COUNT(*) FROM processed_news WHERE pushed=1")
    print(f"  已推送: {c.fetchone()[0]} 条")
    
    # 按日期分布
    c = storage._conn.execute("""
        SELECT DATE(published, '+8 hours'), COUNT(*), SUM(kept), SUM(pushed)
        FROM processed_news 
        GROUP BY DATE(published, '+8 hours')
        ORDER BY DATE(published, '+8 hours') DESC
    """)
    print("\n  按日期分布(北京时间):")
    print("  日期       | 总数 | 保留 | 已推送")
    print("  " + "-" * 35)
    for row in c.fetchall():
        print(f"  {row[0]} | {row[1]:4} | {row[2]:4} | {row[3]:4}")
    
    # 6. 模拟第二次运行（有新的今天新闻）
    print("\n[4] 模拟第二次运行（新增今天新闻）...")
    new_item = NewsItem(
        entry_id="test-5",
        title="「魔法少女小圆」剧场版定档",
        link="http://example.com/5",
        summary="Test summary 5",
        source="test",
        published="2026-05-11T12:00:00Z",
        score=9,
        keep=True,
        ai_title="「魔法少女小圆」剧场版定档8月",
        ai_intro="Test intro",
    )
    
    # 检查是否已处理
    if not storage.is_processed(new_item.entry_id):
        print(f"  新新闻: {new_item.ai_title[:30]}...")
        storage.mark_processed(
            entry_id=new_item.entry_id,
            title=new_item.title,
            link=new_item.link,
            source=new_item.source,
            score=new_item.score,
            kept=new_item.keep,
            pending=False,
            ai_title=new_item.ai_title,
            ai_intro=new_item.ai_intro,
            image_url="",
            reason="",
            published=new_item.published,
        )
        storage.mark_pushed([new_item.entry_id])
        print("  已推送")
    else:
        print("  已处理过，跳过")
    
    # 7. 检查重复新闻
    print("\n[5] 检查重复新闻（test-1）...")
    if storage.is_processed("test-1"):
        print("  test-1 已处理，跳过")
    
    # 8. 最终状态
    print("\n[6] 最终数据库状态:")
    c = storage._conn.execute("SELECT COUNT(*), SUM(kept), SUM(pushed) FROM processed_news")
    total, kept, pushed = c.fetchone()
    print(f"  总计: {total} 条, 保留: {kept}, 已推送: {pushed}")
    
    storage.close()
    print("\n" + "=" * 50)
    print("✅ 测试完成!")
    print("=" * 50)

if __name__ == "__main__":
    test_data_flow()
