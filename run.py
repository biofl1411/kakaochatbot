"""
통합 실행 스크립트
- 서버와 스케줄러를 함께 실행
"""
import logging
from waitress import serve

from config import SERVER_HOST, SERVER_PORT, LOG_FILE, LOG_FORMAT
from models import init_database
from app import app
from scheduler import CrawlScheduler

# 로깅 설정
logging.basicConfig(
    level=logging.INFO,
    format=LOG_FORMAT,
    handlers=[
        logging.FileHandler(LOG_FILE, encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)


def main():
    """메인 실행 함수"""
    logger.info("=" * 50)
    logger.info("카카오 챗봇 서버 시작")
    logger.info("=" * 50)

    # 데이터베이스 초기화
    init_database()
    logger.info("데이터베이스 초기화 완료")

    # 스케줄러 시작 (백그라운드)
    scheduler = CrawlScheduler()
    scheduler.start(run_immediately=True)
    logger.info("스케줄러 시작 완료")

    # 서버 시작 (waitress - 프로덕션용)
    logger.info(f"서버 시작: http://{SERVER_HOST}:{SERVER_PORT}")
    try:
        serve(app, host=SERVER_HOST, port=SERVER_PORT)
    except KeyboardInterrupt:
        logger.info("서버 종료 요청")
    finally:
        scheduler.stop()
        logger.info("서버 종료")


if __name__ == "__main__":
    main()
