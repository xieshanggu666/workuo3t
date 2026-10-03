import os

PORT = int(os.getenv("PORT", "8072"))
DB_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "data", "game.db")
DB_PATH = os.path.abspath(DB_PATH)
