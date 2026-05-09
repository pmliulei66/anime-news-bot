"""
SQLite 持久化去重模块
记录已处理的新闻 entry_id，确保同一条新闻不会重复处理
"""

import logging
import sqlite3
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
                       source: str = "", score: int = 0, kept: bool = False):
        """标记新闻为已处理"""
        try:
            self._conn.execute(
                """INSERT OR IGNORE INTO processed_news 
                   (entry_id, title, link, source, score, kept) 
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (entry_id, title, link, source, score, int(kept))
            )
            self._conn.commit()
        except sqlite3.Error as e:
            logger.error(f"写入去重记录失败: {e}")

    def filter_new(self, items: list) -> list:
        """
        过滤出未处理过的新闻

        Args:
            items: NewsItem 列表

        Returns:
            仅包含未处理条目的 NewsItem 列表
        """
        new_items = []
        for item in items:
            if not self.is_processed(item.entry_id):
                new_items.append(item)
            else:
                logger.debug(f"跳过已处理: {item.title[:50]}")

        logger.info(f"去重过滤: {len(items)} 条中 {len(new_items)} 条为新新闻")
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

    def close(self):
        """关闭数据库连接"""
        if self._conn:
            self._conn.close()
            self._conn = None
            logger.info("数据库连接已关闭")
