"""
카카오 챗봇 설정 파일
"""
import os

# 기본 경로
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# 서버 설정
SERVER_HOST = "0.0.0.0"
SERVER_PORT = 5000

# 데이터베이스 경로
DATABASE_PATH = os.path.join(BASE_DIR, "data", "chatbot.db")

# 로그 설정
LOG_FILE = os.path.join(BASE_DIR, "logs", "chatbot.log")
LOG_FORMAT = "%(asctime)s - %(levelname)s - %(message)s"

# Google Vision API 월별 호출 제한
VISION_API_MONTHLY_LIMIT = 1000

# Google Vision API 키 경로
GOOGLE_VISION_KEY_PATH = os.path.join(BASE_DIR, "google-vision-key.json", "credentials.json")
