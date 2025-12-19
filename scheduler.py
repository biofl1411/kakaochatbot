"""
스케줄러 모듈
- 매일 지정된 시간에 크롤링 실행
"""
import time
import threading
import logging
from datetime import datetime, timedelta

from config import CRAWL_HOUR, CRAWL_MINUTE, LOG_FILE, LOG_FORMAT
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
    """크롤링 스케줄러 (매일 지정 시간 실행)"""

    def __init__(self, hour: int = CRAWL_HOUR, minute: int = CRAWL_MINUTE):
        self.hour = hour
        self.minute = minute
        self._stop_event = threading.Event()
        self._thread = None

    def _get_next_run_time(self) -> datetime:
        """다음 크롤링 시간 계산"""
        now = datetime.now()
        next_run = now.replace(hour=self.hour, minute=self.minute, second=0, microsecond=0)

        # 오늘 예정 시간이 이미 지났으면 내일로
        if next_run <= now:
            next_run += timedelta(days=1)

        return next_run

    def _run_loop(self):
        """스케줄러 루프"""
        logger.info(f"스케줄러 시작 (매일 {self.hour:02d}:{self.minute:02d} 실행)")

        while not self._stop_event.is_set():
            next_run = self._get_next_run_time()
            wait_seconds = (next_run - datetime.now()).total_seconds()

            logger.info(f"다음 크롤링: {next_run.strftime('%Y-%m-%d %H:%M')} ({wait_seconds/3600:.1f}시간 후)")

            # 다음 크롤링 시간까지 대기
            if self._stop_event.wait(wait_seconds):
                break  # 중지 요청됨

            # 크롤링 실행
            try:
                logger.info(f"[{datetime.now()}] 예약 크롤링 시작")
                count = run_crawler()
                logger.info(f"[{datetime.now()}] 예약 크롤링 완료: {count}개 데이터")
            except Exception as e:
                logger.error(f"크롤링 오류: {e}")

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
