"""
SQLite 持久化去重模块
记录已处理的新闻 entry_id，确保同一条新闻不会重复处理
"""

import logging
import sqlite3
from datetime import datetime, timedelta
from typing import Optional

from config import Config

logger = logging.getLogger(__name__)

# 建表 SQL
_CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS processed_news (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    entry_id   TEXT    UNIQUE NOT NULL,
    title      TEXT,
    link       TEXT,
    source     TEXT,
    score      INTEGER DEFAULT 0,
    kept       INTEGER DEFAULT 0,
    pending    INTEGER DEFAULT 0,  -- Score == 7 时为待定状态
    pushed     INTEGER DEFAULT 0,  -- 是否已推送到飞书（0/1）
    ai_title   TEXT    DEFAULT '',
    ai_intro   TEXT    DEFAULT '',
    image_url  TEXT    DEFAULT '',
    reason     TEXT    DEFAULT '',  -- AI 评分理由
    published  TEXT    DEFAULT '',  -- 新闻发布日期
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_entry_id ON processed_news(entry_id);
"""


class NewsStorage:
    """新闻去重存储"""

    def __init__(self, db_path: Optional[str] = None):
        self.db_path = db_path or Config.DB_PATH
        self._conn: Optional[sqlite3.Connection] = None
        self._init_db()

    def _init_db(self):
        """初始化数据库和表"""
        try:
            self._conn = sqlite3.connect(self.db_path)
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.executescript(_CREATE_TABLE_SQL)
            # 兼容旧数据库：如果 pushed 列不存在则添加
            try:
                self._conn.execute("ALTER TABLE processed_news ADD COLUMN pushed INTEGER DEFAULT 0")
            except sqlite3.OperationalError:
                pass  # 列已存在
            self._conn.commit()
            logger.info(f"数据库初始化完成: {self.db_path}")
        except sqlite3.Error as e:
            logger.error(f"数据库初始化失败: {e}")
            raise

    def is_processed(self, entry_id: str) -> bool:
        """检查新闻是否已处理过"""
        try:
            cursor = self._conn.execute(
                "SELECT 1 FROM processed_news WHERE entry_id = ?",
                (entry_id,)
            )
            return cursor.fetchone() is not None
        except sqlite3.Error as e:
            logger.error(f"查询去重记录失败: {e}")
            return False

    def mark_processed(self, entry_id: str, title: str = "", link: str = "",
                       source: str = "", score: int = 0, kept: bool = False,
                       pending: bool = False, ai_title: str = "", ai_intro: str = "",
                       image_url: str = "", reason: str = "", published: str = ""):
        """标记新闻为已处理"""
        try:
            self._conn.execute(
                """INSERT OR IGNORE INTO processed_news 
                   (entry_id, title, link, source, score, kept, pending, ai_title, ai_intro, image_url, reason, published) 
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (entry_id, title, link, source, score, int(kept), int(pending), ai_title, ai_intro, image_url, reason, published)
            )
            self._conn.commit()
            # 清理旧数据：只保留评分最高的 30 条
            self._cleanup_old_records()
        except sqlite3.Error as e:
            logger.error(f"写入去重记录失败: {e}")

    def _cleanup_old_records(self, max_records: int = 30):
        """
        清理旧记录：每天新增的源数据只保留评分最高的 max_records 条
        """
        try:
            # 获取今天（北京时间）的所有记录数
            cursor = self._conn.execute(
                """SELECT COUNT(*) FROM processed_news 
                   WHERE DATE(created_at, '+8 hours') = DATE('now', '+8 hours')"""
            )
            count = cursor.fetchone()[0]
            
            if count > max_records:
                # 只删除今天的记录中评分最低的（保留前 max_records 条）
                self._conn.execute(
                    """DELETE FROM processed_news WHERE id IN (
                        SELECT id FROM processed_news 
                        WHERE DATE(created_at, '+8 hours') = DATE('now', '+8 hours')
                        ORDER BY score ASC
                        LIMIT ?
                    )""",
                    (count - max_records,)
                )
                deleted = count - max_records
                self._conn.commit()
                logger.info(f"清理今日记录: 删除 {deleted} 条，保留 {max_records} 条")
        except sqlite3.Error as e:
            logger.error(f"清理旧记录失败: {e}")

    def _is_similar_title(self, title: str, threshold: float = 0.9) -> bool:
        """
        检查标题是否与最近 24 小时内的记录相似
        
        Args:
            title: 待检查的标题
            threshold: 相似度阈值（默认 0.9 = 90%）
            
        Returns:
            如果存在相似标题返回 True
        """
        try:
            from rapidfuzz import fuzz
            
            # 查询最近 24 小时的标题
            since = (datetime.now() - timedelta(hours=24)).strftime("%Y-%m-%d %H:%M:%S")
            cursor = self._conn.execute(
                "SELECT title FROM processed_news WHERE created_at > ?",
                (since,)
            )
            recent_titles = [row[0] for row in cursor.fetchall() if row[0]]
            
            for recent_title in recent_titles:
                similarity = fuzz.ratio(title.lower(), recent_title.lower()) / 100.0
                if similarity >= threshold:
                    logger.info(f"标题相似度 {similarity:.2%}，跳过: {title[:50]}...")
                    return True
            
            return False
        except ImportError:
            logger.warning("rapidfuzz 未安装，跳过标题相似度检查")
            return False
        except sqlite3.Error as e:
            logger.error(f"标题相似度检查失败: {e}")
            return False

    def filter_new(self, items: list) -> list:
        """
        过滤出未处理过的新闻（支持标题模糊去重）

        Args:
            items: NewsItem 列表

        Returns:
            仅包含未处理条目的 NewsItem 列表
        """
        new_items = []
        skipped_entry = 0
        skipped_similar = 0
        
        for item in items:
            # 1. 检查 entry_id 是否已存在
            if self.is_processed(item.entry_id):
                logger.debug(f"跳过已处理: {item.title[:50]}")
                skipped_entry += 1
                continue
            
            # 2. 检查标题相似度（24小时内）
            if self._is_similar_title(item.title):
                skipped_similar += 1
                continue
            
            new_items.append(item)

        logger.info(
            f"去重过滤: {len(items)} 条中 {len(new_items)} 条为新新闻 "
            f"(跳过 entry_id: {skipped_entry}, 相似标题: {skipped_similar})"
        )
        return new_items

    def get_stats(self) -> dict:
        """获取统计信息"""
        try:
            cursor = self._conn.execute(
                "SELECT COUNT(*) as total, "
                "SUM(CASE WHEN kept = 1 THEN 1 ELSE 0 END) as kept_count "
                "FROM processed_news"
            )
            row = cursor.fetchone()
            return {
                "total_processed": row[0] or 0,
                "total_kept": row[1] or 0,
            }
        except sqlite3.Error as e:
            logger.error(f"获取统计信息失败: {e}")
            return {"total_processed": 0, "total_kept": 0}

    def mark_pushed(self, entry_ids: list[str]):
        """标记新闻为已推送到飞书"""
        try:
            self._conn.executemany(
                "UPDATE processed_news SET pushed = 1 WHERE entry_id = ?",
                [(eid,) for eid in entry_ids]
            )
            self._conn.commit()
            logger.info(f"标记 {len(entry_ids)} 条为已推送")
        except sqlite3.Error as e:
            logger.error(f"标记推送状态失败: {e}")

    def _is_pushed(self, entry_id: str) -> bool:
        """检查新闻是否已推送到飞书"""
        try:
            cursor = self._conn.execute(
                "SELECT pushed FROM processed_news WHERE entry_id = ?", (entry_id,)
            )
            row = cursor.fetchone()
            return bool(row and row[0])
        except sqlite3.Error:
            return False

    def get_unpushed_kept(self) -> list[dict]:
        """获取所有 kept=1 但未推送的新闻（按评分排序）"""
        try:
            cursor = self._conn.execute(
                """SELECT title, ai_title, ai_intro, score, source, link, image_url, published, created_at
                   FROM processed_news
                   WHERE kept = 1 AND pushed = 0
                   ORDER BY score DESC, created_at ASC"""
            )
            rows = cursor.fetchall()
            result = []
            for row in rows:
                result.append({
                    "title": row[0], "ai_title": row[1], "ai_intro": row[2],
                    "score": row[3], "source": row[4], "link": row[5],
                    "image_url": row[6], "published": row[7], "created_at": row[8],
                })
            return result
        except sqlite3.Error as e:
            logger.error(f"获取未推送新闻失败: {e}")
            return []

    def close(self):
        """关闭数据库连接"""
        if self._conn:
            self._conn.close()
            self._conn = None
            logger.info("数据库连接已关闭")

    def get_kept_news(self, date_str: Optional[str] = None) -> list[dict]:
        """
        获取指定日期的已保留新闻（用于生成每日汇总）

        Args:
            date_str: 日期字符串，格式 YYYY-MM-DD，默认今天（北京时间）

        Returns:
            新闻字典列表
        """
        from datetime import datetime, timedelta, timezone, tzinfo

        # 使用北京时间（UTC+8）
        class CST(tzinfo):
            def utcoffset(self, _):
                return timedelta(hours=8)
            def tzname(self, _):
                return "CST"
            def dst(self, _):
                return timedelta(0)

        if not date_str:
            # 默认获取最近24小时内的新闻
            now_cst = datetime.now(CST())
            date_str = now_cst.strftime("%Y-%m-%d")

        try:
            cursor = self._conn.execute(
                """SELECT title, ai_title, ai_intro, score, source, link, image_url, published, created_at
                   FROM processed_news
                   WHERE kept = 1 AND DATE(published, '+8 hours') = ?
                   ORDER BY score DESC, created_at ASC""",
                (date_str,)
            )
            rows = cursor.fetchall()
            result = []
            for row in rows:
                result.append({
                    "title": row[0],
                    "ai_title": row[1],
                    "ai_intro": row[2],
                    "score": row[3],
                    "source": row[4],
                    "link": row[5],
                    "image_url": row[6],
                    "published": row[7],
                    "created_at": row[8],
                })
            logger.info(f"查询到 {date_str} 的保留新闻: {len(result)} 条")
            return result
        except sqlite3.Error as e:
            logger.error(f"查询保留新闻失败: {e}")
            return []
