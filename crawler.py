"""
크롤러 모듈
- 회사 홈페이지에서 검사항목/검사주기 정보 크롤링
- 주기적으로 실행되어 DB에 저장
"""
import requests
from bs4 import BeautifulSoup
import logging
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
import traceback

from config import URL_MAPPING, INDUSTRY_MAPPING, LOG_FILE, LOG_FORMAT
from models import (
    save_inspection_item,
    save_inspection_cycle,
    save_crawl_log,
    init_database
)

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


class Crawler:
    """웹 크롤러 클래스"""

    def __init__(self):
        self._driver = None

    def _get_driver(self):
        """Selenium WebDriver 생성 (필요할 때만)"""
        if self._driver is None:
            try:
                options = Options()
                options.add_argument("--headless=new")
                options.add_argument("--no-sandbox")
                options.add_argument("--disable-dev-shm-usage")
                options.add_argument("--disable-gpu")
                options.add_argument("--window-size=1920,1080")
                self._driver = webdriver.Chrome(options=options)
                logger.info("WebDriver 생성 완료")
            except Exception as e:
                logger.error(f"WebDriver 생성 실패: {e}")
                raise
        return self._driver

    def close(self):
        """WebDriver 종료"""
        if self._driver:
            self._driver.quit()
            self._driver = None
            logger.info("WebDriver 종료")

    def crawl_inspection_items(self, category: str) -> int:
        """
        검사항목 크롤링 (requests 사용 - 가벼움)

        Args:
            category: "식품" 또는 "축산"
        Returns:
            저장된 항목 수
        """
        url = URL_MAPPING.get("검사항목", {}).get(category)
        if not url:
            logger.error(f"검사항목 URL을 찾을 수 없음: {category}")
            return 0

        try:
            logger.info(f"검사항목 크롤링 시작: {category}")
            response = requests.get(url, timeout=30)
            response.raise_for_status()

            soup = BeautifulSoup(response.content, "html.parser")
            tables = soup.find_all("table")

            count = 0
            for table in tables:
                rows = table.find_all("tr")[1:]  # 헤더 제외
                for row in rows:
                    columns = row.find_all("td", recursive=False)
                    if len(columns) >= 3:
                        food_type = columns[1].get_text(strip=True)
                        items = columns[2].get_text(strip=True)
                        if food_type and items:
                            save_inspection_item(category, food_type, items)
                            count += 1

            logger.info(f"검사항목 크롤링 완료: {category}, {count}개 저장")
            return count

        except Exception as e:
            logger.error(f"검사항목 크롤링 실패: {category}, {e}")
            logger.error(traceback.format_exc())
            return 0

    def crawl_inspection_cycles(self, category: str) -> int:
        """
        검사주기 크롤링 (Selenium 사용 - 동적 페이지)

        Args:
            category: "식품" 또는 "축산"
        Returns:
            저장된 항목 수
        """
        url = URL_MAPPING.get("검사주기", {}).get(category)
        if not url:
            logger.error(f"검사주기 URL을 찾을 수 없음: {category}")
            return 0

        # 카테고리에 맞는 업종 필터링
        if category == "식품":
            industries = ["식품제조가공업", "즉석판매제조가공업"]
        else:
            industries = ["축산물제조가공업", "식육즙판매가공업"]

        total_count = 0

        try:
            driver = self._get_driver()
            logger.info(f"검사주기 크롤링 시작: {category}")

            driver.get(url)
            WebDriverWait(driver, 10).until(
                EC.presence_of_element_located((By.TAG_NAME, "body"))
            )

            soup = BeautifulSoup(driver.page_source, "html.parser")

            for industry in industries:
                target_id = INDUSTRY_MAPPING.get(industry)
                if not target_id:
                    continue

                target_element = soup.find("div", class_="needpopup answerPop", id=target_id)
                if not target_element:
                    logger.warning(f"검사주기 요소를 찾을 수 없음: {industry}")
                    continue

                table = target_element.find("table")
                if not table:
                    continue

                rows = table.find_all("tr")[1:]  # 헤더 제외

                for row in rows:
                    columns = row.find_all("td", recursive=False)
                    if len(columns) >= 4:
                        food_group = columns[1].get_text(strip=True)
                        food_type_text = columns[2].get_text(strip=True)
                        cycle = columns[3].get_text(strip=True)

                        # 여러 식품 유형이 콤마로 구분된 경우 각각 저장
                        food_types = [ft.strip() for ft in food_type_text.split(',')]
                        for food_type in food_types:
                            if food_type and cycle:
                                save_inspection_cycle(category, industry, food_group, food_type, cycle)
                                total_count += 1

            logger.info(f"검사주기 크롤링 완료: {category}, {total_count}개 저장")
            return total_count

        except Exception as e:
            logger.error(f"검사주기 크롤링 실패: {category}, {e}")
            logger.error(traceback.format_exc())
            return 0

    def crawl_all(self):
        """모든 데이터 크롤링"""
        logger.info("=" * 50)
        logger.info("전체 크롤링 시작")
        logger.info("=" * 50)

        total = 0

        # 검사항목 크롤링 (requests - 가벼움)
        for category in ["식품", "축산"]:
            count = self.crawl_inspection_items(category)
            total += count

        # 검사주기 크롤링 (Selenium - 동적 페이지)
        for category in ["식품", "축산"]:
            count = self.crawl_inspection_cycles(category)
            total += count

        # WebDriver 종료
        self.close()

        # 크롤링 로그 저장
        if total > 0:
            save_crawl_log("all", "success", f"총 {total}개 데이터 저장")
            logger.info(f"전체 크롤링 완료: 총 {total}개 데이터 저장")
        else:
            save_crawl_log("all", "failed", "데이터 저장 실패")
            logger.error("크롤링 실패: 저장된 데이터 없음")

        return total


def run_crawler():
    """크롤러 실행 (외부에서 호출용)"""
    init_database()
    crawler = Crawler()
    try:
        return crawler.crawl_all()
    finally:
        crawler.close()


if __name__ == "__main__":
    run_crawler()
