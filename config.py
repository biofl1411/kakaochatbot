"""
서버 설정
"""

# 서버 설정
SERVER_HOST = "0.0.0.0"
SERVER_PORT = 5000

# 로깅 설정
LOG_FILE = "logs/chatbot.log"
LOG_FORMAT = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"

# 데이터베이스 설정
DB_PATH = "data/chatbot.db"

# Vision API 설정
VISION_API_MONTHLY_LIMIT = 100  # 월별 API 호출 제한
