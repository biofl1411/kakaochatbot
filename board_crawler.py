"""
게시판 크롤러 모듈
- 홈페이지 게시판에서 제목과 내용 추출
- 자연어 처리를 위한 데이터 수집
"""
import re
import time
import logging
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, NoSuchElementException

from config import LOG_FILE, LOG_FORMAT
from models import save_board_mapping, init_database

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

# 게시판 카테고리별 URL 및 question 번호 매핑
BOARD_CONFIG = {
    "표시기준": {
        "base_url": "https://www.biofl.co.kr/sub.jsp?code=EJ2GKW3",
        "questions": [
            "question_161", "question_162", "question_163", "question_164",
            "question_165", "question_166", "question_167", "question_168",
            "question_169", "question_170", "question_171", "question_172",
            "question_173", "question_174", "question_175", "question_176",
            "question_177"
        ]
    },
    "잔류농약_항생물질": {
        "base_url": "https://www.biofl.co.kr/sub.jsp?code=MKJ9PKO0",
        "questions": [
            "question_82", "question_83", "question_84", "question_85",
            "question_86", "question_87", "question_88", "question_89",
            "question_90", "question_91", "question_92", "question_93"
        ]
    },
    "방사능": {
        "base_url": "https://www.biofl.co.kr/sub.jsp?code=HY5KJJJI",
        "questions": [
            "question_37", "question_38", "question_39", "question_40",
            "question_41", "question_42", "question_43", "question_44"
        ]
    },
    "영양성분": {
        "base_url": "https://www.biofl.co.kr/sub.jsp?code=JEKb3KXA",
        "questions": [
            "question_68", "question_69", "question_70", "question_71",
            "question_72", "question_73", "question_74", "question_75",
            "question_76", "question_77", "question_78", "question_79",
            "question_80", "question_81", "question_207", "question_208",
            "question_209", "question_210", "question_211", "question_212",
            "question_213", "question_214", "question_215", "question_216",
            "question_217"
        ]
    },
    "소비기한": {
        "base_url": "https://www.biofl.co.kr/sub.jsp?code=PXXBybSV",
        "questions": [
            "question_94", "question_95", "question_96", "question_97",
            "question_98"
        ]
    },
    "알레르기": {
        "base_url": "https://www.biofl.co.kr/sub.jsp?code=G7K3Y2F9",
        "questions": [
            "question_26", "question_27", "question_28", "question_29",
            "question_30", "question_31", "question_32", "question_33",
            "question_34", "question_35", "question_36", "question_176",
            "question_177", "question_178", "question_179", "question_180",
            "question_181", "question_182", "question_183"
        ]
    },
    "이물": {
        "base_url": "https://www.biofl.co.kr/sub.jsp?code=H5R6T8B3",
        "questions": [
            "question_122", "question_123", "question_124", "question_125",
            "question_126", "question_127", "question_128", "question_138",
            "question_159", "question_187", "question_188"
        ]
    },
    "비건_할랄_동물DNA": {
        "base_url": "https://www.biofl.co.kr/sub.jsp?code=D4P8L2M7",
        "questions": [
            "question_52", "question_53", "question_54", "question_55",
            "question_56", "question_57", "question_58", "question_59",
            "question_60", "question_61", "question_62"
        ]
    },
    "축산": {
        "base_url": "https://www.biofl.co.kr/sub.jsp?code=XN0Cd4r7",
        "questions": [
            "question_99", "question_100", "question_101", "question_102",
            "question_103", "question_104", "question_105", "question_106",
            "question_107", "question_108", "question_109", "question_110",
            "question_111", "question_112", "question_113", "question_114",
            "question_115", "question_116", "question_117", "question_118",
            "question_119", "question_120"
        ]
    },
    "식품": {
        "base_url": "https://www.biofl.co.kr/sub.jsp?code=7r9P7y94",
        "questions": [
            "question_48", "question_50", "question_51", "question_53",
            "question_56", "question_57", "question_60", "question_62",
            "question_63", "question_64", "question_65", "question_162",
            "question_191", "question_198", "question_199", "question_201",
            "question_208", "question_228", "question_229", "question_236",
            "question_239", "question_241"
        ]
    }
}


class BoardCrawler:
    """게시판 크롤러 클래스"""

    def __init__(self):
        self._driver = None

    def _get_driver(self):
        """Selenium WebDriver 생성"""
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

    def crawl_board_content(self, category: str, base_url: str, question_id: str) -> dict:
        """
        특정 게시판 팝업에서 제목과 내용 추출
        - answerPopOpen 링크를 클릭하여 팝업 내용 로드
        - answerWrap에서 실제 Q&A 내용 추출

        Args:
            category: 카테고리명
            base_url: 기본 URL
            question_id: question_XXX 형식의 ID

        Returns:
            {"title": 제목, "content": 내용} 또는 None
        """
        driver = self._get_driver()

        try:
            # 1. 기본 페이지 로드
            driver.get(base_url)
            time.sleep(1.5)

            # 2. 해당 question의 팝업 링크 찾기 및 클릭
            popup_link_selector = f'a[data-needpopup-show="#{question_id}"]'
            try:
                popup_link = driver.find_element(By.CSS_SELECTOR, popup_link_selector)

                # 링크 텍스트가 질문 제목
                link_text = popup_link.text.strip()

                # 클릭하여 팝업 열기
                driver.execute_script("arguments[0].click();", popup_link)
                time.sleep(1)  # 팝업 로딩 대기

            except NoSuchElementException:
                logger.warning(f"⚠️ {category}/{question_id}: 팝업 링크 없음")
                return None

            # 3. 팝업 내용 추출
            title = link_text  # 링크 텍스트가 제목
            content = None

            # answerWrap 내용 추출 시도
            content_selectors = [
                f"#{question_id} .answerWrap",
                f"#{question_id} .answerLayer",
                f"#{question_id}"
            ]

            for selector in content_selectors:
                try:
                    elem = driver.find_element(By.CSS_SELECTOR, selector)
                    if elem and elem.text.strip():
                        content = elem.text.strip()
                        break
                except NoSuchElementException:
                    continue

            # 4. 팝업 닫기 (다음 크롤링을 위해)
            try:
                close_btn = driver.find_element(By.CSS_SELECTOR, f"#{question_id} .close, #{question_id} .btn-close, .needpopup-close")
                driver.execute_script("arguments[0].click();", close_btn)
            except NoSuchElementException:
                # ESC 키로 닫기 시도
                from selenium.webdriver.common.keys import Keys
                driver.find_element(By.TAG_NAME, "body").send_keys(Keys.ESCAPE)
            time.sleep(0.3)

            if title or content:
                logger.info(f"✅ {category}/{question_id}: {title[:30] if title else 'N/A'}...")
                return {"title": title, "content": content}
            else:
                logger.warning(f"⚠️ {category}/{question_id}: 내용 추출 실패")
                return None

        except TimeoutException:
            logger.error(f"❌ {category}/{question_id}: 타임아웃")
            return None
        except Exception as e:
            logger.error(f"❌ {category}/{question_id}: {e}")
            return None

    def crawl_category(self, category: str) -> int:
        """
        특정 카테고리의 모든 게시판 크롤링

        Args:
            category: 카테고리명

        Returns:
            성공한 항목 수
        """
        if category not in BOARD_CONFIG:
            logger.error(f"알 수 없는 카테고리: {category}")
            return 0

        config = BOARD_CONFIG[category]
        base_url = config["base_url"]
        questions = config["questions"]

        success_count = 0
        for question_id in questions:
            result = self.crawl_board_content(category, base_url, question_id)

            if result:
                save_board_mapping(
                    question_id=question_id,
                    category=category,
                    base_url=base_url,
                    title=result.get("title"),
                    content=result.get("content")
                )
                success_count += 1

            time.sleep(0.5)  # 서버 부하 방지

        logger.info(f"📊 {category}: {success_count}/{len(questions)} 완료")
        return success_count

    def crawl_all(self) -> dict:
        """모든 카테고리 크롤링"""
        logger.info("=" * 50)
        logger.info("전체 게시판 크롤링 시작")
        logger.info("=" * 50)

        results = {}
        total_success = 0
        total_count = 0

        for category in BOARD_CONFIG:
            count = self.crawl_category(category)
            results[category] = count
            total_success += count
            total_count += len(BOARD_CONFIG[category]["questions"])

        logger.info("=" * 50)
        logger.info(f"전체 크롤링 완료: {total_success}/{total_count}")
        logger.info("=" * 50)

        return results


def main():
    """메인 실행 함수"""
    # DB 초기화
    init_database()

    crawler = BoardCrawler()
    try:
        results = crawler.crawl_all()

        print("\n📊 크롤링 결과:")
        print("-" * 40)
        for category, count in results.items():
            total = len(BOARD_CONFIG[category]["questions"])
            print(f"  {category}: {count}/{total}")
        print("-" * 40)

    finally:
        crawler.close()


if __name__ == "__main__":
    main()
