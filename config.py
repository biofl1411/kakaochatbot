"""
카카오 챗봇 설정 파일
"""
import os

# 기본 경로
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
LOG_DIR = os.path.join(BASE_DIR, "logs")

# 디렉토리 자동 생성
os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(LOG_DIR, exist_ok=True)

# 데이터베이스 설정
DATABASE_PATH = os.path.join(DATA_DIR, "chatbot.db")

# 서버 설정
SERVER_HOST = "0.0.0.0"
SERVER_PORT = 5000

# 크롤링 대상 URL
URL_MAPPING = {
    "검사항목": {
        "식품": "https://www.biofl.co.kr/sub.jsp?code=7r9P7y94&question_241",
        "축산": "https://www.biofl.co.kr/sub.jsp?code=XN0Cd4r7&question_243"
    },
    "검사주기": {
        "식품": "https://www.biofl.co.kr/sub.jsp?code=7r9P7y94",
        "축산": "https://www.biofl.co.kr/sub.jsp?code=XN0Cd4r7"
    }
}

# 업종 매핑 (검사주기)
INDUSTRY_MAPPING = {
    "식품제조가공업": "question_236",
    "즉석판매제조가공업": "question_239",
    "축산물제조가공업": "question_240",
    "축산물즉석판매제조가공업": "question_246"
}

# 검사항목 팝업 ID 매핑
ITEM_POPUP_MAPPING = {
    "식품": "question_241",
    "축산": "question_243"
}

# 스케줄러 설정 (매일 크롤링 시간)
CRAWL_HOUR = 7  # 오전 7시
CRAWL_MINUTE = 0

# 로깅 설정
LOG_FILE = os.path.join(LOG_DIR, "chatbot.log")
LOG_FORMAT = "%(asctime)s - %(levelname)s - %(message)s"

# Google Vision API 설정
GOOGLE_VISION_CREDENTIALS = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS", "")
VISION_API_MONTHLY_LIMIT = 990  # 월별 API 호출 제한 (무료 1000건 중 여유분 제외)
