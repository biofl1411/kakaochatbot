"""
데이터베이스 모델 (SQLite)
- 크롤링한 검사항목/검사주기 데이터 저장
"""
import sqlite3
from datetime import datetime
from rapidfuzz import fuzz
from config import DATABASE_PATH, VISION_API_MONTHLY_LIMIT


def get_connection():
    """데이터베이스 연결 (동시 접근 지원)"""
    conn = sqlite3.connect(DATABASE_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    # WAL 모드: 읽기/쓰기 동시 접근 허용
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_database():
    """데이터베이스 초기화 - 테이블 생성"""
    conn = get_connection()
    cursor = conn.cursor()

    # 검사항목 테이블
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS inspection_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            category TEXT NOT NULL,
            food_type TEXT NOT NULL,
            items TEXT NOT NULL,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # 검사주기 테이블
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS inspection_cycles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            category TEXT NOT NULL,
            industry TEXT NOT NULL,
            food_group TEXT NOT NULL,
            food_type TEXT NOT NULL,
            cycle TEXT NOT NULL,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # 크롤링 로그 테이블
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS crawl_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            crawl_type TEXT NOT NULL,
            status TEXT NOT NULL,
            message TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # API 사용량 추적 테이블
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS api_usage (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            api_name TEXT NOT NULL,
            year_month TEXT NOT NULL,
            call_count INTEGER DEFAULT 0,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(api_name, year_month)
        )
    """)

    # 영양성분검사 정보 테이블
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS nutrition_info (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            category TEXT NOT NULL,
            test_type TEXT NOT NULL,
            details TEXT NOT NULL,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(category, test_type)
        )
    """)

    conn.commit()
    conn.close()


# ===== 검사항목 관련 함수 =====

def save_inspection_item(category: str, food_type: str, items: str):
    """검사항목 저장 (기존 데이터 업데이트 또는 새로 추가)"""
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT id FROM inspection_items
        WHERE category = ? AND food_type = ?
    """, (category, food_type))

    existing = cursor.fetchone()

    if existing:
        cursor.execute("""
            UPDATE inspection_items
            SET items = ?, updated_at = ?
            WHERE id = ?
        """, (items, datetime.now(), existing['id']))
    else:
        cursor.execute("""
            INSERT INTO inspection_items (category, food_type, items)
            VALUES (?, ?, ?)
        """, (category, food_type, items))

    conn.commit()
    conn.close()


def get_inspection_item(category: str, food_type: str) -> dict:
    """검사항목 조회 (우선순위: 정확일치 > 끝나는일치 > 포함일치)"""
    conn = get_connection()
    cursor = conn.cursor()

    # 띄어쓰기 제거한 검색어
    search_key = food_type.replace(" ", "")

    # 1. 정확히 일치하는 경우
    cursor.execute("""
        SELECT * FROM inspection_items
        WHERE category = ? AND REPLACE(food_type, ' ', '') = ?
    """, (category, search_key))
    result = cursor.fetchone()
    if result:
        conn.close()
        return dict(result)

    # 2. 검색어로 끝나는 경우 (예: "햄" → "생햄", "프레스햄")
    cursor.execute("""
        SELECT * FROM inspection_items
        WHERE category = ? AND REPLACE(food_type, ' ', '') LIKE ?
    """, (category, f"%{search_key}"))
    results = cursor.fetchall()

    # 검색어로 시작하는 항목 제외 (예: "햄버거류"는 "햄"으로 시작하므로 제외)
    filtered = [r for r in results if not dict(r)['food_type'].replace(" ", "").startswith(search_key) or dict(r)['food_type'].replace(" ", "") == search_key]

    if filtered:
        conn.close()
        return dict(filtered[0])

    conn.close()
    return None


def get_inspection_item_all_matches(category: str, food_type: str) -> list:
    """검사항목 조회 - 모든 매칭 결과 반환 (정확일치 > 끝나는일치 > 포함일치)"""
    conn = get_connection()
    cursor = conn.cursor()

    # 띄어쓰기 제거한 검색어
    search_key = food_type.replace(" ", "")

    # 1. 정확히 일치하는 경우
    cursor.execute("""
        SELECT * FROM inspection_items
        WHERE category = ? AND REPLACE(food_type, ' ', '') = ?
    """, (category, search_key))
    result = cursor.fetchone()
    if result:
        conn.close()
        return [dict(result)]  # 정확 일치는 1개만 반환

    # 2. 검색어로 끝나는 경우 (예: "음료" → "탄산음료", "과채음료")
    cursor.execute("""
        SELECT * FROM inspection_items
        WHERE category = ? AND REPLACE(food_type, ' ', '') LIKE ?
    """, (category, f"%{search_key}"))
    results = cursor.fetchall()

    # 검색어로 시작하는 항목 제외 (예: "햄버거류"는 "햄"으로 시작하므로 제외)
    endswith_filtered = [dict(r) for r in results if not dict(r)['food_type'].replace(" ", "").startswith(search_key) or dict(r)['food_type'].replace(" ", "") == search_key]

    if endswith_filtered:
        conn.close()
        return endswith_filtered

    # 3. 검색어가 포함된 경우 (예: "탄산" → "탄산음료", "유산균" → "유산균음료")
    cursor.execute("""
        SELECT * FROM inspection_items
        WHERE category = ? AND REPLACE(food_type, ' ', '') LIKE ?
    """, (category, f"%{search_key}%"))
    results = cursor.fetchall()

    contains_filtered = [dict(r) for r in results]

    conn.close()
    return contains_filtered


def search_inspection_items(category: str, keyword: str) -> list:
    """검사항목 검색 (유사 검색)"""
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT * FROM inspection_items
        WHERE category = ? AND food_type LIKE ?
        ORDER BY food_type
    """, (category, f"%{keyword}%"))

    results = cursor.fetchall()
    conn.close()

    return [dict(row) for row in results]


# ===== 검사주기 관련 함수 =====

def save_inspection_cycle(category: str, industry: str, food_group: str, food_type: str, cycle: str):
    """검사주기 저장"""
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT id FROM inspection_cycles
        WHERE category = ? AND industry = ? AND food_type = ?
    """, (category, industry, food_type))

    existing = cursor.fetchone()

    if existing:
        cursor.execute("""
            UPDATE inspection_cycles
            SET food_group = ?, cycle = ?, updated_at = ?
            WHERE id = ?
        """, (food_group, cycle, datetime.now(), existing['id']))
    else:
        cursor.execute("""
            INSERT INTO inspection_cycles (category, industry, food_group, food_type, cycle)
            VALUES (?, ?, ?, ?, ?)
        """, (category, industry, food_group, food_type, cycle))

    conn.commit()
    conn.close()


def get_inspection_cycle(category: str, industry: str, food_type: str) -> dict:
    """검사주기 조회 (우선순위: 정확일치 > 끝나는일치 > 포함일치)"""
    conn = get_connection()
    cursor = conn.cursor()

    # 띄어쓰기 제거한 검색어
    search_key = food_type.replace(" ", "")

    # 1. 정확히 일치하는 경우
    cursor.execute("""
        SELECT * FROM inspection_cycles
        WHERE category = ? AND industry = ? AND REPLACE(food_type, ' ', '') = ?
    """, (category, industry, search_key))
    result = cursor.fetchone()
    if result:
        conn.close()
        return dict(result)

    # 2. 검색어로 끝나는 경우 (예: "햄" → "생햄", "프레스햄")
    cursor.execute("""
        SELECT * FROM inspection_cycles
        WHERE category = ? AND industry = ? AND REPLACE(food_type, ' ', '') LIKE ?
    """, (category, industry, f"%{search_key}"))
    results = cursor.fetchall()

    # 검색어로 시작하는 항목 제외 (예: "햄버거류"는 "햄"으로 시작하므로 제외)
    filtered = [r for r in results if not dict(r)['food_type'].replace(" ", "").startswith(search_key) or dict(r)['food_type'].replace(" ", "") == search_key]

    if filtered:
        conn.close()
        return dict(filtered[0])

    conn.close()
    return None


def get_inspection_cycle_all_matches(category: str, industry: str, food_type: str) -> list:
    """검사주기 조회 - 모든 매칭 결과 반환 (정확일치 > 끝나는일치 > 포함일치)"""
    conn = get_connection()
    cursor = conn.cursor()

    # 띄어쓰기 제거한 검색어
    search_key = food_type.replace(" ", "")

    # 1. 정확히 일치하는 경우
    cursor.execute("""
        SELECT * FROM inspection_cycles
        WHERE category = ? AND industry = ? AND REPLACE(food_type, ' ', '') = ?
    """, (category, industry, search_key))
    result = cursor.fetchone()
    if result:
        conn.close()
        return [dict(result)]  # 정확 일치는 1개만 반환

    # 2. 검색어로 끝나는 경우 (예: "음료" → "탄산음료", "과채음료")
    cursor.execute("""
        SELECT * FROM inspection_cycles
        WHERE category = ? AND industry = ? AND REPLACE(food_type, ' ', '') LIKE ?
    """, (category, industry, f"%{search_key}"))
    results = cursor.fetchall()

    # 검색어로 시작하는 항목 제외 (예: "햄버거류"는 "햄"으로 시작하므로 제외)
    endswith_filtered = [dict(r) for r in results if not dict(r)['food_type'].replace(" ", "").startswith(search_key) or dict(r)['food_type'].replace(" ", "") == search_key]

    if endswith_filtered:
        conn.close()
        return endswith_filtered

    # 3. 검색어가 포함된 경우 (예: "탄산" → "탄산음료", "유산균" → "유산균음료")
    cursor.execute("""
        SELECT * FROM inspection_cycles
        WHERE category = ? AND industry = ? AND REPLACE(food_type, ' ', '') LIKE ?
    """, (category, industry, f"%{search_key}%"))
    results = cursor.fetchall()

    contains_filtered = [dict(r) for r in results]

    conn.close()
    return contains_filtered


def search_inspection_cycles(category: str, industry: str, keyword: str) -> list:
    """검사주기 검색"""
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT * FROM inspection_cycles
        WHERE category = ? AND industry = ? AND food_type LIKE ?
        ORDER BY food_type
    """, (category, industry, f"%{keyword}%"))

    results = cursor.fetchall()
    conn.close()

    return [dict(row) for row in results]


# ===== 유사 단어 검색 =====

def get_all_food_types_items(category: str) -> list:
    """검사항목의 모든 식품 유형 조회"""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT DISTINCT food_type FROM inspection_items
        WHERE category = ?
    """, (category,))
    results = cursor.fetchall()
    conn.close()
    return [row['food_type'] for row in results]


def get_all_food_types_cycles(category: str, industry: str) -> list:
    """검사주기의 모든 식품 유형 조회"""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT DISTINCT food_type FROM inspection_cycles
        WHERE category = ? AND industry = ?
    """, (category, industry))
    results = cursor.fetchall()
    conn.close()
    return [row['food_type'] for row in results]


def find_similar_items(category: str, keyword: str, min_score: int = 40) -> list:
    """검사항목에서 유사한 식품 유형 찾기 (검색어로 시작/끝나는 단어 제외)"""
    all_types = get_all_food_types_items(category)
    similar = []

    # 띄어쓰기 제거
    keyword_normalized = keyword.replace(" ", "")

    for food_type in all_types:
        food_type_normalized = food_type.replace(" ", "")

        # 검색어로 시작하는 단어 제외 (예: "햄" 검색 시 "햄버거류" 제외)
        if food_type_normalized.startswith(keyword_normalized) and food_type_normalized != keyword_normalized:
            continue

        # 검색어로 끝나는 단어 제외 (endswith 매칭에서 처리됨)
        if food_type_normalized.endswith(keyword_normalized) and food_type_normalized != keyword_normalized:
            continue

        # 공통 글자 수 체크
        common_chars = set(keyword_normalized) & set(food_type_normalized)
        if len(common_chars) >= 2:
            # 짧은 검색어는 ratio만 사용
            if len(keyword_normalized) <= 2:
                score = fuzz.ratio(keyword_normalized, food_type_normalized)
            else:
                score = fuzz.partial_ratio(keyword_normalized, food_type_normalized)
            if score >= min_score:
                similar.append((food_type, score))

    # 점수순 정렬
    similar.sort(key=lambda x: x[1], reverse=True)
    return [item[0] for item in similar[:5]]


def find_similar_cycles(category: str, industry: str, keyword: str, min_score: int = 40) -> list:
    """검사주기에서 유사한 식품 유형 찾기 (검색어로 시작/끝나는 단어 제외)"""
    all_types = get_all_food_types_cycles(category, industry)
    similar = []

    # 띄어쓰기 제거
    keyword_normalized = keyword.replace(" ", "")

    for food_type in all_types:
        food_type_normalized = food_type.replace(" ", "")

        # 검색어로 시작하는 단어 제외 (예: "햄" 검색 시 "햄버거류" 제외)
        if food_type_normalized.startswith(keyword_normalized) and food_type_normalized != keyword_normalized:
            continue

        # 검색어로 끝나는 단어 제외 (endswith 매칭에서 처리됨)
        if food_type_normalized.endswith(keyword_normalized) and food_type_normalized != keyword_normalized:
            continue

        # 공통 글자 수 체크
        common_chars = set(keyword_normalized) & set(food_type_normalized)
        if len(common_chars) >= 2:
            # 짧은 검색어는 ratio만 사용
            if len(keyword_normalized) <= 2:
                score = fuzz.ratio(keyword_normalized, food_type_normalized)
            else:
                score = fuzz.partial_ratio(keyword_normalized, food_type_normalized)
            if score >= min_score:
                similar.append((food_type, score))

    # 점수순 정렬
    similar.sort(key=lambda x: x[1], reverse=True)
    return [item[0] for item in similar[:5]]


# ===== 영양성분검사 관련 함수 =====

def save_nutrition_info(category: str, test_type: str, details: str):
    """영양성분검사 정보 저장 (기존 데이터 업데이트 또는 새로 추가)"""
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        INSERT INTO nutrition_info (category, test_type, details, updated_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(category, test_type)
        DO UPDATE SET details = ?, updated_at = ?
    """, (category, test_type, details, datetime.now(), details, datetime.now()))

    conn.commit()
    conn.close()


def get_nutrition_info(category: str, test_type: str) -> dict:
    """영양성분검사 정보 조회"""
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT * FROM nutrition_info
        WHERE category = ? AND test_type = ?
    """, (category, test_type))

    result = cursor.fetchone()
    conn.close()

    return dict(result) if result else None


def get_all_nutrition_info(category: str) -> list:
    """특정 카테고리의 모든 영양성분검사 정보 조회"""
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT * FROM nutrition_info
        WHERE category = ?
        ORDER BY test_type
    """, (category,))

    results = cursor.fetchall()
    conn.close()

    return [dict(row) for row in results]


# ===== 크롤링 로그 =====

def save_crawl_log(crawl_type: str, status: str, message: str = None):
    """크롤링 로그 저장"""
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        INSERT INTO crawl_logs (crawl_type, status, message)
        VALUES (?, ?, ?)
    """, (crawl_type, status, message))

    conn.commit()
    conn.close()


def get_last_crawl_time() -> datetime:
    """마지막 크롤링 시간 조회"""
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT created_at FROM crawl_logs
        WHERE status = 'success'
        ORDER BY created_at DESC LIMIT 1
    """)

    result = cursor.fetchone()
    conn.close()

    if result:
        return datetime.fromisoformat(result['created_at'])
    return None


# ===== API 사용량 추적 =====

def get_current_year_month() -> str:
    """현재 년월 문자열 반환 (YYYY-MM)"""
    return datetime.now().strftime("%Y-%m")


def get_api_usage(api_name: str) -> int:
    """현재 월의 API 호출 횟수 조회"""
    conn = get_connection()
    cursor = conn.cursor()

    year_month = get_current_year_month()

    cursor.execute("""
        SELECT call_count FROM api_usage
        WHERE api_name = ? AND year_month = ?
    """, (api_name, year_month))

    result = cursor.fetchone()
    conn.close()

    return result['call_count'] if result else 0


def increment_api_usage(api_name: str) -> int:
    """API 호출 횟수 증가 및 현재 횟수 반환"""
    conn = get_connection()
    cursor = conn.cursor()

    year_month = get_current_year_month()

    # UPSERT: 있으면 증가, 없으면 새로 생성
    cursor.execute("""
        INSERT INTO api_usage (api_name, year_month, call_count, updated_at)
        VALUES (?, ?, 1, ?)
        ON CONFLICT(api_name, year_month)
        DO UPDATE SET call_count = call_count + 1, updated_at = ?
    """, (api_name, year_month, datetime.now(), datetime.now()))

    conn.commit()

    # 현재 횟수 조회
    cursor.execute("""
        SELECT call_count FROM api_usage
        WHERE api_name = ? AND year_month = ?
    """, (api_name, year_month))

    result = cursor.fetchone()
    conn.close()

    return result['call_count'] if result else 0


def can_use_vision_api() -> bool:
    """Vision API 사용 가능 여부 (월별 제한 체크)"""
    current_usage = get_api_usage("google_vision")
    return current_usage < VISION_API_MONTHLY_LIMIT


def get_vision_api_remaining() -> int:
    """Vision API 남은 호출 횟수"""
    current_usage = get_api_usage("google_vision")
    return max(0, VISION_API_MONTHLY_LIMIT - current_usage)


# 데이터베이스 초기화 실행
if __name__ == "__main__":
    init_database()
    print("데이터베이스 초기화 완료!")
