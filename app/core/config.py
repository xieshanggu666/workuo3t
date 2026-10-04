import os

PORT = int(os.getenv("PORT", "8072"))
_DEFAULT_DB = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "data", "game.db"))
# 允许通过环境变量覆盖数据库位置（测试/部署隔离）
DB_PATH = os.path.abspath(os.environ.get("DB_PATH", _DEFAULT_DB))
