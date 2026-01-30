"""
웹페이지 HTML 구조 확인용 테스트 스크립트
"""
import time
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By

def test_page_structure():
    """페이지 HTML 구조 출력"""
    options = Options()
    options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")

    driver = webdriver.Chrome(options=options)

    try:
        # 표시기준 페이지 테스트
        url = "https://www.biofl.co.kr/sub.jsp?code=EJ2GKW3"
        driver.get(url)
        time.sleep(2)

        print("=" * 60)
        print("페이지 제목:", driver.title)
        print("=" * 60)

        # question_ 로 시작하는 모든 요소 찾기
        elements = driver.find_elements(By.XPATH, "//*[contains(@id, 'question_')]")
        print(f"\n'question_' 포함 요소 수: {len(elements)}")

        for i, elem in enumerate(elements[:5]):  # 처음 5개만
            print(f"\n--- 요소 {i+1} ---")
            print(f"ID: {elem.get_attribute('id')}")
            print(f"태그: {elem.tag_name}")
            print(f"클래스: {elem.get_attribute('class')}")
            print(f"텍스트 (앞 200자): {elem.text[:200] if elem.text else '(없음)'}...")

        # 아코디언/FAQ 관련 클래스 찾기
        print("\n" + "=" * 60)
        print("아코디언/FAQ 관련 요소 검색")
        print("=" * 60)

        selectors_to_try = [
            ".accordion", ".faq", ".qa", ".question", ".answer",
            ".panel", ".collapse", ".expandable",
            "[data-toggle]", ".toggle-content"
        ]

        for selector in selectors_to_try:
            try:
                found = driver.find_elements(By.CSS_SELECTOR, selector)
                if found:
                    print(f"\n'{selector}' 발견: {len(found)}개")
                    if found[0].text:
                        print(f"  첫번째 텍스트: {found[0].text[:100]}...")
            except:
                pass

        # 특정 question 클릭 시도
        print("\n" + "=" * 60)
        print("question_161 클릭 테스트")
        print("=" * 60)

        # URL 파라미터로 직접 접근
        url_with_q = "https://www.biofl.co.kr/sub.jsp?code=EJ2GKW3&question_161"
        driver.get(url_with_q)
        time.sleep(2)

        # 클릭 가능한 요소 찾기
        clickable = driver.find_elements(By.CSS_SELECTOR, "a[href*='question_161'], [onclick*='question_161'], #question_161")
        print(f"question_161 관련 클릭 요소: {len(clickable)}개")

        for elem in clickable:
            print(f"  - 태그: {elem.tag_name}, href: {elem.get_attribute('href')}, onclick: {elem.get_attribute('onclick')}")

        # 전체 body HTML 일부 저장
        body = driver.find_element(By.TAG_NAME, "body")
        html_content = body.get_attribute('innerHTML')

        # HTML 파일로 저장
        with open('/home/user/kakaochatbot/page_structure.html', 'w', encoding='utf-8') as f:
            f.write(html_content)
        print("\n전체 HTML이 page_structure.html에 저장되었습니다.")

    finally:
        driver.quit()

if __name__ == "__main__":
    test_page_structure()
