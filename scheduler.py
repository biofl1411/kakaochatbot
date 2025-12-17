"""
스케줄러 모듈
- 주기적으로 크롤링 실행
- 서버와 함께 실행하거나 별도로 실행 가능
"""
import time
import threading
import logging
from datetime import datetime

from config import CRAWL_INTERVAL_HOURS, LOG_FILE, LOG_FORMAT
from crawler import run_crawler
from models import init_database

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


class CrawlScheduler:
    """크롤링 스케줄러"""

    def __init__(self, interval_hours: int = CRAWL_INTERVAL_HOURS):
        self.interval_seconds = interval_hours * 3600
        self._stop_event = threading.Event()
        self._thread = None

    def _run_loop(self):
        """스케줄러 루프"""
        logger.info(f"스케줄러 시작 (주기: {self.interval_seconds // 3600}시간)")

        while not self._stop_event.is_set():
            try:
                logger.info(f"[{datetime.now()}] 크롤링 시작")
                count = run_crawler()
                logger.info(f"[{datetime.now()}] 크롤링 완료: {count}개 데이터")
            except Exception as e:
                logger.error(f"크롤링 오류: {e}")

            # 다음 크롤링까지 대기
            logger.info(f"다음 크롤링: {self.interval_seconds // 3600}시간 후")
            self._stop_event.wait(self.interval_seconds)

    def start(self, run_immediately: bool = True):
        """스케줄러 시작 (백그라운드 스레드)"""
        if self._thread and self._thread.is_alive():
            logger.warning("스케줄러가 이미 실행 중입니다")
            return

        # 즉시 한 번 실행
        if run_immediately:
            logger.info("초기 크롤링 실행")
            try:
                run_crawler()
            except Exception as e:
                logger.error(f"초기 크롤링 실패: {e}")

        # 백그라운드 스레드 시작
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()
        logger.info("스케줄러 백그라운드 스레드 시작")

    def stop(self):
        """스케줄러 중지"""
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=5)
        logger.info("스케줄러 중지됨")


def run_once():
    """크롤링 1회 실행"""
    init_database()
    logger.info("=" * 50)
    logger.info("수동 크롤링 실행")
    logger.info("=" * 50)
    count = run_crawler()
    logger.info(f"크롤링 완료: {count}개 데이터 저장")
    return count


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1 and sys.argv[1] == "--once":
        # 1회만 실행
        run_once()
    else:
        # 스케줄러 실행
        init_database()
        scheduler = CrawlScheduler()
        scheduler.start(run_immediately=True)

        try:
            # 메인 스레드 유지
            while True:
                time.sleep(60)
        except KeyboardInterrupt:
            logger.info("스케줄러 종료 요청")
            scheduler.stop()
