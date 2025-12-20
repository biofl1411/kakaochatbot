"""
크롤러 모듈
- 회사 홈페이지에서 검사항목/검사주기 정보 크롤링
- 주기적으로 실행되어 DB에 저장
"""
import re
import requests
from bs4 import BeautifulSoup
import logging
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
import traceback

from config import URL_MAPPING, INDUSTRY_MAPPING, ITEM_POPUP_MAPPING, NUTRITION_POPUP_MAPPING, GENERAL_POPUP_MAPPING, SECTION_FILTER, LOG_FILE, LOG_FORMAT
from models import (
    save_inspection_item,
    save_inspection_cycle,
    save_nutrition_info,
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
        검사항목 크롤링 (Selenium 사용 - 팝업 요소에서 데이터 추출)

        Args:
            category: "식품" 또는 "축산"
        Returns:
            저장된 항목 수
        """
        url = URL_MAPPING.get("검사항목", {}).get(category)
        if not url:
            logger.error(f"검사항목 URL을 찾을 수 없음: {category}")
            return 0

        target_id = ITEM_POPUP_MAPPING.get(category)
        if not target_id:
            logger.error(f"검사항목 팝업 ID를 찾을 수 없음: {category}")
            return 0

        try:
            driver = self._get_driver()
            logger.info(f"검사항목 크롤링 시작: {category} (팝업 ID: {target_id})")

            driver.get(url)
            WebDriverWait(driver, 10).until(
                EC.presence_of_element_located((By.TAG_NAME, "body"))
            )

            soup = BeautifulSoup(driver.page_source, "html.parser")

            # 팝업 요소에서 테이블 찾기
            target_element = soup.find("div", class_="needpopup answerPop", id=target_id)
            if not target_element:
                logger.warning(f"검사항목 팝업 요소를 찾을 수 없음: {category} (ID: {target_id})")
                return 0

            table = target_element.find("table")
            if not table:
                logger.warning(f"검사항목 테이블을 찾을 수 없음: {category}")
                return 0

            count = 0
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
            industries = ["축산물제조가공업", "축산물즉석판매제조가공업"]

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

    def crawl_nutrition_info(self) -> int:
        """
        영양성분검사 정보 크롤링 (Selenium 사용)

        Returns:
            저장된 항목 수
        """
        total_count = 0

        try:
            driver = self._get_driver()
            logger.info("영양성분검사 정보 크롤링 시작")

            for test_type, url in URL_MAPPING.get("영양성분검사", {}).items():
                target_id = NUTRITION_POPUP_MAPPING.get(test_type)
                if not target_id:
                    logger.warning(f"영양성분검사 팝업 ID를 찾을 수 없음: {test_type}")
                    continue

                logger.info(f"영양성분검사 크롤링: {test_type} (URL: {url})")

                driver.get(url)
                WebDriverWait(driver, 10).until(
                    EC.presence_of_element_located((By.TAG_NAME, "body"))
                )

                soup = BeautifulSoup(driver.page_source, "html.parser")

                # 팝업 요소에서 테이블 찾기
                target_element = soup.find("div", class_="needpopup answerPop", id=target_id)
                if not target_element:
                    logger.warning(f"영양성분검사 팝업 요소를 찾을 수 없음: {test_type} (ID: {target_id})")
                    continue

                table = target_element.find("table")
                if not table:
                    logger.warning(f"영양성분검사 테이블을 찾을 수 없음: {test_type}")
                    continue

                # 테이블 데이터 추출 (링크 포함)
                rows = table.find_all("tr")
                table_data = []

                for row in rows:
                    cells = row.find_all(["th", "td"])
                    if cells:
                        row_data = self._extract_cell_data_with_links(cells)
                        table_data.append(row_data)

                if table_data:
                    # 테이블 데이터를 텍스트로 변환
                    details = self._format_table_data(table_data)
                    save_nutrition_info("영양성분검사", test_type, details)
                    total_count += 1
                    logger.info(f"영양성분검사 저장 완료: {test_type}")

            logger.info(f"영양성분검사 크롤링 완료: {total_count}개 저장")
            return total_count

        except Exception as e:
            logger.error(f"영양성분검사 크롤링 실패: {e}")
            logger.error(traceback.format_exc())
            return 0

    def _extract_cell_data_with_links(self, cells) -> list:
        """셀에서 텍스트와 링크를 함께 추출

        Returns:
            list of (text, url) tuples or just text strings
        """
        row_data = []
        base_url = "https://www.biofl.co.kr"

        for cell in cells:
            # 셀 내의 모든 링크 찾기
            links = cell.find_all("a", href=True)

            # "자세히 보기" 링크가 있는 경우
            detail_links = [link for link in links if "자세히" in link.get_text()]

            if detail_links:
                # 링크 텍스트를 제외한 셀 텍스트 추출
                cell_text = cell.get_text(strip=True)
                # 첫 번째 "자세히 보기" 링크의 URL
                href = detail_links[0].get("href", "")

                # 상대 경로를 절대 경로로 변환
                if href and not href.startswith("http"):
                    if href.startswith("/"):
                        href = base_url + href
                    else:
                        href = base_url + "/" + href

                row_data.append((cell_text, href))
            else:
                # 링크가 없는 일반 텍스트
                row_data.append(cell.get_text(strip=True))

        return row_data

    def _format_table_data(self, table_data: list, section_filter: str = None) -> str:
        """테이블 데이터를 포맷팅된 텍스트로 변환

        Args:
            table_data: 테이블 데이터 리스트
            section_filter: 특정 섹션만 포함할 경우 해당 섹션 헤더 텍스트
        """
        result = []
        in_target_section = section_filter is None  # 필터 없으면 항상 True
        current_section_header = None

        for row in table_data:
            # 제목 행 제외 (Q로 시작하는 질문 번호)
            if row and re.match(r'^Q\d+\.', str(row[0])):
                continue

            if len(row) >= 2:
                header_text = str(row[0]) if not isinstance(row[0], tuple) else row[0][0]

                # 섹션 필터가 있는 경우, 섹션 헤더 체크
                if section_filter:
                    if section_filter in header_text:
                        in_target_section = True
                        current_section_header = header_text
                    elif current_section_header and not header_text.startswith(" "):
                        # 새로운 섹션 시작 (타겟 섹션이 아닌 경우)
                        if section_filter not in header_text:
                            in_target_section = False

                if not in_target_section:
                    continue

                # 첫 번째 열이 헤더인 경우
                cleaned_values = []
                for val in row[1:]:
                    # val이 (text, url) 튜플인 경우
                    if isinstance(val, tuple):
                        text, url = val
                        cleaned = self._clean_text(text)
                        if cleaned and url:
                            cleaned_values.append(f"{cleaned}{{{{URL:{url}}}}}")
                        elif cleaned:
                            cleaned_values.append(cleaned)
                    else:
                        cleaned = self._clean_text(val)
                        if cleaned:
                            cleaned_values.append(cleaned)
                if cleaned_values:
                    result.append(f"[{header_text}] {' | '.join(cleaned_values)}")
            elif len(row) == 1:
                val = row[0]
                if isinstance(val, tuple):
                    text, url = val
                    cleaned = self._clean_text(text)
                    if cleaned and not re.match(r'^Q\d+\.', cleaned):
                        if url:
                            result.append(f"{cleaned}{{{{URL:{url}}}}}")
                        else:
                            result.append(cleaned)
                else:
                    cleaned = self._clean_text(val)
                    if cleaned and not re.match(r'^Q\d+\.', cleaned):
                        result.append(cleaned)
        return "\n".join(result)

    def _clean_text(self, text: str) -> str:
        """텍스트에서 불필요한 요소 제거"""
        if not text:
            return ""

        # "자세히 보기" 및 관련 텍스트 제거
        text = re.sub(r'자세히\s*보기', '', text)
        # 앞뒤 하이픈, 공백 정리
        text = re.sub(r'^[-\s]+|[-\s]+$', '', text)
        # 중복 공백 제거
        text = re.sub(r'\s+', ' ', text)

        return text.strip()

    def _extract_items_from_text(self, text: str) -> str:
        """텍스트에서 항목들을 추출하여 포맷팅"""
        if not text:
            return ""

        # 제목 제거 (Q로 시작하는 줄)
        text = re.sub(r'Q\d+\.[^-]*', '', text)

        # "자세히 보기" 제거
        text = re.sub(r'자세히\s*보기', '', text)

        # 하이픈으로 항목 분리
        items = re.split(r'[-•*]\s*', text)

        result = []
        for item in items:
            item = item.strip()
            if item and len(item) > 1:  # 빈 항목 제외
                result.append(f"[항목] {item}")

        return "\n".join(result) if result else text

    def crawl_general_info(self) -> int:
        """
        일반 검사 정보 크롤링 (자가품질검사, 소비기한설정, 항생물질, 잔류농약, 방사능, 비건, 할랄, 동물DNA, 알레르기, 글루텐Free)

        Returns:
            저장된 항목 수
        """
        total_count = 0
        categories = [
            "자가품질검사", "소비기한설정",
            "항생물질", "잔류농약", "방사능",
            "비건", "할랄", "동물DNA",
            "알레르기", "글루텐Free"
        ]

        try:
            driver = self._get_driver()
            logger.info("일반 검사 정보 크롤링 시작")

            for category in categories:
                category_urls = URL_MAPPING.get(category, {})
                category_popups = GENERAL_POPUP_MAPPING.get(category, {})
                category_filters = SECTION_FILTER.get(category, {})

                for menu_type, url in category_urls.items():
                    target_id = category_popups.get(menu_type)
                    if not target_id:
                        logger.warning(f"{category} 팝업 ID를 찾을 수 없음: {menu_type}")
                        continue

                    # 섹션 필터 가져오기
                    section_filter = category_filters.get(menu_type)

                    logger.info(f"{category} 크롤링: {menu_type} (URL: {url})" + (f" [필터: {section_filter}]" if section_filter else ""))

                    driver.get(url)
                    WebDriverWait(driver, 10).until(
                        EC.presence_of_element_located((By.TAG_NAME, "body"))
                    )

                    soup = BeautifulSoup(driver.page_source, "html.parser")

                    # 팝업 요소에서 테이블 찾기
                    target_element = soup.find("div", class_="needpopup answerPop", id=target_id)
                    if not target_element:
                        logger.warning(f"{category} 팝업 요소를 찾을 수 없음: {menu_type} (ID: {target_id})")
                        continue

                    table = target_element.find("table")
                    if not table:
                        # 테이블이 없으면 전체 텍스트 추출 후 정제
                        raw_text = target_element.get_text(strip=True)
                        if raw_text:
                            details = self._extract_items_from_text(raw_text)
                            if details:
                                save_nutrition_info(category, menu_type, details)
                                total_count += 1
                                logger.info(f"{category} 저장 완료: {menu_type} (텍스트)")
                        continue

                    # 테이블 데이터 추출 (링크 포함)
                    rows = table.find_all("tr")
                    table_data = []

                    for row in rows:
                        cells = row.find_all(["th", "td"])
                        if cells:
                            row_data = self._extract_cell_data_with_links(cells)
                            table_data.append(row_data)

                    if table_data:
                        # 섹션 필터 적용하여 포맷팅
                        details = self._format_table_data(table_data, section_filter)
                        if details:  # 필터 적용 후 내용이 있는 경우만 저장
                            save_nutrition_info(category, menu_type, details)
                            total_count += 1
                            logger.info(f"{category} 저장 완료: {menu_type}")

            logger.info(f"일반 검사 정보 크롤링 완료: {total_count}개 저장")
            return total_count

        except Exception as e:
            logger.error(f"일반 검사 정보 크롤링 실패: {e}")
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

        # 영양성분검사 정보 크롤링
        count = self.crawl_nutrition_info()
        total += count

        # 일반 검사 정보 크롤링 (항생물질, 잔류농약, 방사능, 비건, 할랄, 동물DNA)
        count = self.crawl_general_info()
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
