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
        "식품": "https://www.biofl.co.kr/sub.jsp?code=7r9P7y94&question_229",
        "축산": "https://www.biofl.co.kr/sub.jsp?code=XN0Cd4r7&question_230"
    },
    "검사주기": {
        "식품": "https://www.biofl.co.kr/sub.jsp?code=7r9P7y94",
        "축산": "https://www.biofl.co.kr/sub.jsp?code=XN0Cd4r7"
    }
}

# 업종 매핑
INDUSTRY_MAPPING = {
    "식품제조가공업": "question_236",
    "즉석판매제조가공업": "question_239",
    "축산물제조가공업": "question_200",
    "식육즙판매가공업": "question_210"
}

# 스케줄러 설정 (크롤링 주기: 시간 단위)
CRAWL_INTERVAL_HOURS = 1

# 로깅 설정
LOG_FILE = os.path.join(LOG_DIR, "chatbot.log")
LOG_FORMAT = "%(asctime)s - %(levelname)s - %(message)s"
