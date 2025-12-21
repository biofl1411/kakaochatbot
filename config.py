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
    },
    "자가품질검사": {
        "검사주기알림": "https://www.biofl.co.kr/sub.jsp?code=7r9P7y94&question_198"
    },
    "영양성분검사": {
        "검사종류": "https://www.biofl.co.kr/sub.jsp?code=JEKb3KXA&question_241",
        "9대영양성분": "https://www.biofl.co.kr/sub.jsp?code=JEKb3KXA&question_193",
        "14대영양성분": "https://www.biofl.co.kr/sub.jsp?code=JEKb3KXA&question_192"
    },
    "소비기한설정": {
        "가속실험": "https://www.biofl.co.kr/sub.jsp?code=PXXBybSV&question_241",
        "실측실험": "https://www.biofl.co.kr/sub.jsp?code=PXXBybSV&question_241"
    },
    "항생물질": {
        "검사종류": "https://www.biofl.co.kr/sub.jsp?code=MKJ9PKO0&question_90"
    },
    "잔류농약": {
        "검사종류": "https://www.biofl.co.kr/sub.jsp?code=MKJ9PKO0&question_90"
    },
    "방사능": {
        "검사안내": "https://www.biofl.co.kr/sub.jsp?code=HY5KJJJI&question_90"
    },
    "비건": {
        "검사안내": "https://www.biofl.co.kr/sub.jsp?code=D4P8L2M7&question_185"
    },
    "할랄": {
        "검사안내": "https://www.biofl.co.kr/sub.jsp?code=D4P8L2M7&question_186"
    },
    "동물DNA": {
        "검사안내": "https://www.biofl.co.kr/sub.jsp?code=D4P8L2M7&question_127"
    },
    "알레르기": {
        "검사종류": "https://www.biofl.co.kr/sub.jsp?code=G7K3Y2F9&question_26",
        "RT-PCR": "https://www.biofl.co.kr/sub.jsp?code=G7K3Y2F9&question_151",
        "Elisa": "https://www.biofl.co.kr/sub.jsp?code=G7K3Y2F9&question_151"
    },
    "글루텐Free": {
        "Free기준": "https://www.biofl.co.kr/sub.jsp?code=EJ2GKW3&question_161"
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

# 영양성분검사 팝업 ID 매핑
NUTRITION_POPUP_MAPPING = {
    "검사종류": "question_241",
    "9대영양성분": "question_193",
    "14대영양성분": "question_192"
}

# 일반 검사 팝업 ID 매핑 (카테고리 > 메뉴 > 팝업ID)
GENERAL_POPUP_MAPPING = {
    "자가품질검사": {
        "검사주기알림": "question_198"
    },
    "소비기한설정": {
        "가속실험": "question_97",
        "실측실험": "question_97"
    },
    "항생물질": {
        "검사종류": "question_90"
    },
    "잔류농약": {
        "검사종류": "question_85"
    },
    "방사능": {
        "검사안내": "question_39"
    },
    "비건": {
        "검사안내": "question_185"
    },
    "할랄": {
        "검사안내": "question_186"
    },
    "동물DNA": {
        "검사안내": "question_127"
    },
    "알레르기": {
        "검사종류": "question_26",
        "RT-PCR": "question_151",
        "Elisa": "question_151"
    },
    "글루텐Free": {
        "Free기준": "question_161"
    }
}

# 섹션 필터 (특정 헤더만 포함)
# 같은 팝업에서 특정 섹션만 추출할 때 사용
SECTION_FILTER = {
    "소비기한설정": {
        "가속실험": "2) 가속실험",  # "2) 가속실험(3개월이상 제품)" 섹션만
        "실측실험": "1) 실측실험"   # "1) 실측실험 (3개월이내 제품)" 섹션만
    },
    "알레르기": {
        "RT-PCR": "RT-PCR Kit",    # "RT-PCR Kit" 섹션만
        "Elisa": "ELISA Kit"       # "ELISA Kit" 섹션만
    }
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
