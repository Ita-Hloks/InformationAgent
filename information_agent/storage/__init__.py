"""SQLite 粗处理结果持久化接口。"""

from .models import PersistedCollection
from .store import SQLiteCollectionStore, default_database_path

__all__ = ["PersistedCollection", "SQLiteCollectionStore", "default_database_path"]
