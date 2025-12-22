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

    def _extract_general_text(self, text: str) -> str:
        """일반 텍스트 형식의 팝업 내용 추출 (자가품질검사 등)"""
        if not text:
            return ""

        # Q 제목 제거
        text = re.sub(r'Q\d+\.\s*[^\n]*', '', text)

        # "자세히 보기", "Close" 등 버튼 텍스트 제거
        text = re.sub(r'자세히\s*보기', '', text)
        text = re.sub(r'\bClose\b', '', text)

        # 공백 정리
        text = re.sub(r'\s+', ' ', text).strip()

        result = []

        # 주요 섹션 분리
        # 1. ※ 주의사항 분리
        parts = re.split(r'(※[^※관련]*)', text)

        for part in parts:
            part = part.strip()
            if not part:
                continue

            # ※ 주의사항
            if part.startswith('※'):
                result.append(f"\n\n⚠️ 주의사항")
                result.append(f"  {part}")
                continue

            # 관련 법령 자료 분리
            if '관련 법령' in part:
                legal_split = re.split(r'(관련 법령[^:]*:[^\.]+\.)', part)
                for legal_part in legal_split:
                    legal_part = legal_part.strip()
                    if not legal_part:
                        continue
                    if legal_part.startswith('관련 법령'):
                        result.append(f"\n\n📋 관련 법령")
                        # 법령 내용 정리
                        legal_content = legal_part.replace('관련 법령 자료 :', '').strip()
                        result.append(f"  {legal_content}")
                    else:
                        # 일반 문장 처리
                        self._format_general_sentences(legal_part, result)
            else:
                self._format_general_sentences(part, result)

        formatted = '\n'.join(result).strip()
        # 연속된 줄바꿈 정리 (3개 이상 -> 2개)
        formatted = re.sub(r'\n{3,}', '\n\n', formatted)
        return formatted

    def _format_general_sentences(self, text: str, result: list):
        """일반 문장들을 포맷팅하여 result 리스트에 추가"""
        # 문장 단위로 분리
        sentences = re.split(r'(?<=[다요음됩니다]\.)\s*', text)

        for sent in sentences:
            sent = sent.strip()
            if not sent or len(sent) < 5:
                continue

            # - 로 시작하는 항목
            if sent.startswith('-'):
                result.append(f"\n• {sent[1:].strip()}")
            # 번호로 시작하는 항목 (예: 3. 자가품질검사...)
            elif re.match(r'^\d+\.\s', sent):
                result.append(f"\n\n📌 {sent}")
            # 다만, 으로 시작하는 단서 조항
            elif sent.startswith('다만,'):
                result.append(f"\n  └ {sent}")
            # 예) 로 시작하는 예시
            elif sent.startswith('예)'):
                result.append(f"\n💡 {sent}")
            else:
                # 일반 문장 - 첫 문장이면 bullet point 추가
                if not result or result[-1].startswith('\n\n'):
                    result.append(f"\n• {sent}")
                else:
                    result.append(f"\n• {sent}")

    def _extract_section_text(self, text: str, section_filter: str) -> str:
        """텍스트에서 특정 섹션만 추출 (소비기한설정 등)"""
        if not text or not section_filter:
            return ""

        logger.info(f"섹션 필터 적용: {section_filter}")

        # "Close" 등 버튼 텍스트 제거
        text = re.sub(r'\bClose\b', '', text)

        # 공백 정리 (줄바꿈은 유지)
        text = re.sub(r'[ \t]+', ' ', text).strip()

        # 섹션 시작 위치 찾기 (예: "1) 실측실험" 또는 "2) 가속실험")
        start_match = re.search(rf'{re.escape(section_filter)}', text)
        if not start_match:
            logger.warning(f"섹션을 찾을 수 없음: {section_filter}")
            return ""

        # 섹션 시작부터 끝까지 추출
        section_start = start_match.start()

        # 다음 섹션 번호 찾기 (예: 현재 1)이면 2) 찾기, 현재 2)이면 끝까지)
        current_num = re.match(r'(\d)\)', section_filter)
        if current_num:
            next_num = int(current_num.group(1)) + 1
            # 다음 섹션 패턴 (공백 유무 상관없이 한글로 시작하는 섹션)
            next_pattern = rf'{next_num}\)\s*[가-힣]'
            next_match = re.search(next_pattern, text[section_start + 10:])
            if next_match:
                section_end = section_start + 10 + next_match.start()
            else:
                section_end = len(text)
        else:
            section_end = len(text)

        section_text = text[section_start:section_end].strip()
        logger.info(f"추출된 섹션 텍스트 길이: {len(section_text)}")

        if not section_text:
            return ""

        lines = []

        # 제목 추출 (예: "1) 실측실험 (3개월이내 제품)")
        title_match = re.match(r'(\d\)\s*[가-힣]+\s*\([^)]+\))', section_text)
        if title_match:
            title = title_match.group(1)
            # 제목 포맷팅
            if "실측실험" in title:
                lines.append("📋 실측실험 (3개월 이내 제품)")
            elif "가속실험" in title:
                lines.append("📋 가속실험 (3개월 이상 제품)")
            else:
                lines.append(f"📋 {title}")
            lines.append("")
            section_text = section_text[title_match.end():].strip()

        # 본문 설명과 예시 분리
        example_split = re.split(r'예\)\s*', section_text, maxsplit=1)

        # 본문 설명 추출
        if example_split[0].strip():
            description = example_split[0].strip()
            # 공백 정리
            description = re.sub(r'\s+', ' ', description)

            # 설명 텍스트 포맷팅
            lines.append("📌 설명")
            lines.append(f"  {description}")
            lines.append("")

        # 예시 추출
        if len(example_split) > 1 and example_split[1].strip():
            example = example_split[1].strip()
            # * 이후는 참고사항이므로 분리
            note_split = re.split(r'\s*\*\s*', example, maxsplit=1)
            example_text = note_split[0].strip()

            if example_text:
                # 공백 정리
                example_text = re.sub(r'\s+', ' ', example_text)
                lines.append("💡 예시")
                lines.append(f"  {example_text}")
                lines.append("")

            # 참고사항
            if len(note_split) > 1 and note_split[1].strip():
                note = note_split[1].strip()
                note = re.sub(r'\s+', ' ', note)
                lines.append("⚠️ 참고사항")
                lines.append(f"  * {note}")

        # 본문에서 참고사항 추출 (예시가 없는 경우)
        if len(example_split) == 1:
            notes = re.findall(r'\*\s*([^*]+)', section_text)
            if notes:
                lines.append("⚠️ 참고사항")
                for note in notes:
                    note = note.strip()
                    note = re.sub(r'\s+', ' ', note)
                    if note and len(note) > 3:
                        lines.append(f"  * {note}")

        result = '\n'.join(lines)
        # 연속된 빈 줄 정리
        result = re.sub(r'\n{3,}', '\n\n', result)
        logger.info(f"포맷팅된 결과 길이: {len(result)}")
        return result

    def _extract_allergy_kit_section(self, text: str, section_filter: str) -> str:
        """알레르기 키트 섹션 추출 (ELISA Kit 또는 RT-PCR Kit)"""
        if not text or not section_filter:
            return ""

        logger.info(f"알레르기 섹션 필터 적용: {section_filter}")

        # "Close" 등 버튼 텍스트 제거
        text = re.sub(r'\bClose\b', '', text)
        # Q 제목 제거
        text = re.sub(r'Q\d+\.[^Q]*?(?=ELISA|RT-PCR)', '', text, count=1)

        # 섹션 시작 위치 찾기
        start_match = re.search(rf'{re.escape(section_filter)}', text, re.IGNORECASE)
        if not start_match:
            logger.warning(f"알레르기 섹션을 찾을 수 없음: {section_filter}")
            return ""

        section_start = start_match.start()

        # 다음 섹션 찾기 (ELISA Kit 다음에 RT-PCR Kit, 또는 RT-PCR Kit이면 끝까지)
        if "ELISA" in section_filter.upper():
            # ELISA Kit 섹션 -> RT-PCR Kit까지
            next_match = re.search(r'RT-PCR\s*Kit', text[section_start + len(section_filter):], re.IGNORECASE)
            if next_match:
                section_end = section_start + len(section_filter) + next_match.start()
            else:
                section_end = len(text)
        else:
            # RT-PCR Kit 섹션 -> 끝까지
            section_end = len(text)

        section_text = text[section_start:section_end].strip()
        logger.info(f"추출된 알레르기 섹션 텍스트 길이: {len(section_text)}")

        if not section_text:
            return ""

        lines = []

        # 섹션 제목
        if "ELISA" in section_filter.upper():
            lines.append("🧪 ELISA Kit")
        else:
            lines.append("🧪 RT-PCR Kit")
        lines.append("")

        # 보유 Kit 추출
        kit_match = re.search(r'-\s*보유\s*[Kk]it\s*[:：]?\s*([^-]+?)(?=-\s*(?:별도|입고)|$)', section_text, re.DOTALL)
        if kit_match:
            kit_items = kit_match.group(1).strip()
            # 공백 정리
            kit_items = re.sub(r'\s+', ' ', kit_items)
            lines.append("📌 보유 Kit")
            lines.append(f"  {kit_items}")
            lines.append("")

        # 별도 문의 추출
        inquiry_match = re.search(r'-\s*별도\s*문의\s*[:：]?\s*([^-*]+?)(?=-|\*|$)', section_text, re.DOTALL)
        if inquiry_match:
            inquiry_items = inquiry_match.group(1).strip()
            inquiry_items = re.sub(r'\s+', ' ', inquiry_items)
            lines.append("📌 별도 문의")
            lines.append(f"  {inquiry_items}")
            lines.append("")

        # 입고 예정 추출
        incoming_match = re.search(r'-\s*입고\s*예정\s*[:：]?\s*([^-\[*]+?)(?=-|\[|\*|$)', section_text, re.DOTALL)
        if incoming_match:
            incoming_items = incoming_match.group(1).strip()
            incoming_items = re.sub(r'\s+', ' ', incoming_items)
            lines.append("📌 입고 예정")
            lines.append(f"  {incoming_items}")
            lines.append("")

        # 검출 가능 종 안내 (RT-PCR에만 있음)
        detect_match = re.search(r'\[검출\s*가능\s*종\s*안내\](.+?)(?=\*고객지원|$)', section_text, re.DOTALL)
        if detect_match:
            detect_content = detect_match.group(1).strip()
            lines.append("📌 검출 가능 종 안내")

            # 각 종별로 분리 (1) 오징어, 2) 게, 3) 새우)
            species_pattern = r'(\d+\))\s*([가-힣]+)\s*[:：]?\s*([^0-9]+?)(?=\d+\)|$)'
            species_matches = re.findall(species_pattern, detect_content)
            for num, name, detail in species_matches:
                detail = re.sub(r'\s+', ' ', detail).strip()
                if detail:
                    lines.append(f"  {num} {name}: {detail}")
            lines.append("")

        # * 안내사항 추출
        note_match = re.search(r'\*\s*(?:별도\s*문의하신|고객지원)[^*]+', section_text)
        if note_match:
            note = note_match.group(0).strip()
            note = re.sub(r'\s+', ' ', note)
            lines.append("⚠️ 안내사항")
            lines.append(f"  {note}")

        result = '\n'.join(lines)
        logger.info(f"포맷팅된 알레르기 결과 길이: {len(result)}")
        return result

    def _extract_items_from_text(self, text: str, category: str = None, section_filter: str = None) -> str:
        """텍스트에서 항목들을 추출하여 포맷팅"""
        if not text:
            return ""

        # 자가품질검사는 일반 텍스트 형식 사용
        if category == "자가품질검사":
            return self._extract_general_text(text)

        # 소비기한설정은 섹션 필터 적용
        if category == "소비기한설정" and section_filter:
            return self._extract_section_text(text, section_filter)

        # 알레르기는 키트 섹션 필터 적용
        if category == "알레르기" and section_filter:
            return self._extract_allergy_kit_section(text, section_filter)

        # 제목 제거 (Q로 시작하는 질문 제목 전체)
        # Q3.비건(Vegan) 검사의 종류와 시료량 같은 제목 전체 제거
        text = re.sub(r'Q\d+\.[^Q]*?(?=DN|검사|Kit|필요한|해당|개별|\*|-|$)', '', text, count=1)

        # "자세히 보기" 제거
        text = re.sub(r'자세히\s*보기', '', text)

        result = []

        # DN으로 시작하는 키트명 추출 (DNAnimal Screen, DNAnimal Ident 등)
        kit_pattern = r'(DN(?:Animal\s+(?:Screen|Ident)\s+[A-Za-z\s&]+(?:Kit)?)[^DN]*?)(?=DN|$|\*|필요한)'
        kits = re.findall(kit_pattern, text, re.IGNORECASE)

        if kits:
            result.append("🧪 검사 키트")
            result.append("")
            for kit in kits:
                kit = kit.strip()
                kit = re.sub(r'\s+', ' ', kit)
                if kit and len(kit) > 5:
                    result.append(f"• {kit}")

        # 필요한 시료량 추출
        sample_match = re.search(r'(필요한\s*시료량?[^*]*)', text)
        if sample_match:
            sample_info = sample_match.group(1).strip()
            sample_info = re.sub(r'\s+', ' ', sample_info)
            result.append(f"\n📦 시료량")
            result.append(f"• {sample_info}")

        # "-" 로 시작하는 항목 추출 (예: - 항생물질 28종)
        dash_items = re.findall(r'-\s*([^-*\n]+?)(?=\s*-|\s*\*|$)', text)
        if dash_items:
            result.append("\n📋 검사항목")
            result.append("")
            for item in dash_items:
                item = item.strip()
                item = re.sub(r'\s+', ' ', item)
                if item and len(item) > 2:
                    result.append(f"• {item}")

        # * 로 시작하는 참고 사항 추출
        notes = re.findall(r'\*\s*([^*]+)', text)
        if notes:
            result.append("\n⚠️ 참고사항")
            result.append("")
            for note in notes:
                note = note.strip()
                note = re.sub(r'\s+', ' ', note)
                if note and len(note) > 3:
                    result.append(f"• {note}")

        # 결과가 없으면 원본 텍스트 정리해서 반환
        if not result:
            items = re.split(r'[-•]\s*', text)
            for item in items:
                item = re.sub(r'\s+', ' ', item).strip()
                if item and len(item) > 3:
                    result.append(f"• {item}")

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
                            details = self._extract_items_from_text(raw_text, category, section_filter)
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
