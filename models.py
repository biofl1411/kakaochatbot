"""
데이터베이스 모델 및 함수
"""
import sqlite3
from datetime import datetime
from config import DB_PATH, VISION_API_MONTHLY_LIMIT


def get_db_connection():
    """데이터베이스 연결"""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_database():
    """데이터베이스 초기화"""
    conn = get_db_connection()
    cursor = conn.cursor()

    # 검사항목 테이블
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS inspection_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            category TEXT NOT NULL,
            food_type TEXT NOT NULL,
            items TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    # 검사주기 테이블
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS inspection_cycles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            category TEXT NOT NULL,
            business_type TEXT NOT NULL,
            food_group TEXT,
            food_type TEXT NOT NULL,
            cycle TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    # 크롤링 기록 테이블
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS crawl_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            crawl_type TEXT NOT NULL,
            crawl_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            status TEXT DEFAULT 'success'
        )
    ''')

    # API 사용량 테이블
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS api_usage (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            api_type TEXT NOT NULL,
            usage_date DATE NOT NULL,
            count INTEGER DEFAULT 0,
            UNIQUE(api_type, usage_date)
        )
    ''')

    conn.commit()
    conn.close()


def get_inspection_item(category: str, food_type: str) -> dict:
    """검사항목 조회 (단일)"""
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute('''
        SELECT food_type, items FROM inspection_items
        WHERE category = ? AND food_type LIKE ?
        LIMIT 1
    ''', (category, f'%{food_type}%'))

    row = cursor.fetchone()
    conn.close()

    if row:
        return {'food_type': row['food_type'], 'items': row['items']}
    return None


def get_inspection_item_all_matches(category: str, food_type: str) -> list:
    """검사항목 조회 (모든 매칭)"""
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute('''
        SELECT food_type, items FROM inspection_items
        WHERE category = ? AND food_type LIKE ?
    ''', (category, f'%{food_type}%'))

    rows = cursor.fetchall()
    conn.close()

    return [{'food_type': row['food_type'], 'items': row['items']} for row in rows]


def get_inspection_cycle(category: str, business_type: str, food_type: str) -> dict:
    """검사주기 조회 (단일)"""
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute('''
        SELECT food_group, food_type, cycle FROM inspection_cycles
        WHERE category = ? AND business_type = ? AND food_type LIKE ?
        LIMIT 1
    ''', (category, business_type, f'%{food_type}%'))

    row = cursor.fetchone()
    conn.close()

    if row:
        return {
            'food_group': row['food_group'],
            'food_type': row['food_type'],
            'cycle': row['cycle']
        }
    return None


def get_inspection_cycle_all_matches(category: str, business_type: str, food_type: str) -> list:
    """검사주기 조회 (모든 매칭)"""
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute('''
        SELECT food_group, food_type, cycle FROM inspection_cycles
        WHERE category = ? AND business_type = ? AND food_type LIKE ?
    ''', (category, business_type, f'%{food_type}%'))

    rows = cursor.fetchall()
    conn.close()

    return [{
        'food_group': row['food_group'],
        'food_type': row['food_type'],
        'cycle': row['cycle']
    } for row in rows]


def search_inspection_items(category: str, keyword: str) -> list:
    """검사항목 검색"""
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute('''
        SELECT food_type, items FROM inspection_items
        WHERE category = ? AND food_type LIKE ?
    ''', (category, f'%{keyword}%'))

    rows = cursor.fetchall()
    conn.close()

    return [{'food_type': row['food_type'], 'items': row['items']} for row in rows]


def search_inspection_cycles(category: str, business_type: str, keyword: str) -> list:
    """검사주기 검색"""
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute('''
        SELECT food_group, food_type, cycle FROM inspection_cycles
        WHERE category = ? AND business_type = ? AND food_type LIKE ?
    ''', (category, business_type, f'%{keyword}%'))

    rows = cursor.fetchall()
    conn.close()

    return [{
        'food_group': row['food_group'],
        'food_type': row['food_type'],
        'cycle': row['cycle']
    } for row in rows]


def find_similar_items(category: str, food_type: str, limit: int = 5) -> list:
    """유사한 검사항목 찾기"""
    conn = get_db_connection()
    cursor = conn.cursor()

    # 2글자 이상 공통인 항목 찾기
    similar = []
    if len(food_type) >= 2:
        for i in range(len(food_type) - 1):
            substring = food_type[i:i+2]
            cursor.execute('''
                SELECT DISTINCT food_type FROM inspection_items
                WHERE category = ? AND food_type LIKE ?
                LIMIT ?
            ''', (category, f'%{substring}%', limit))

            for row in cursor.fetchall():
                if row['food_type'] not in similar and row['food_type'] != food_type:
                    similar.append(row['food_type'])
                    if len(similar) >= limit:
                        break

            if len(similar) >= limit:
                break

    conn.close()
    return similar[:limit]


def find_similar_cycles(category: str, business_type: str, food_type: str, limit: int = 5) -> list:
    """유사한 검사주기 찾기"""
    conn = get_db_connection()
    cursor = conn.cursor()

    similar = []
    if len(food_type) >= 2:
        for i in range(len(food_type) - 1):
            substring = food_type[i:i+2]
            cursor.execute('''
                SELECT DISTINCT food_type FROM inspection_cycles
                WHERE category = ? AND business_type = ? AND food_type LIKE ?
                LIMIT ?
            ''', (category, business_type, f'%{substring}%', limit))

            for row in cursor.fetchall():
                if row['food_type'] not in similar and row['food_type'] != food_type:
                    similar.append(row['food_type'])
                    if len(similar) >= limit:
                        break

            if len(similar) >= limit:
                break

    conn.close()
    return similar[:limit]


def get_last_crawl_time() -> datetime:
    """마지막 크롤링 시간 조회"""
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute('''
        SELECT crawl_time FROM crawl_history
        ORDER BY crawl_time DESC
        LIMIT 1
    ''')

    row = cursor.fetchone()
    conn.close()

    if row:
        return datetime.fromisoformat(row['crawl_time'])
    return None


def can_use_vision_api() -> bool:
    """Vision API 사용 가능 여부"""
    conn = get_db_connection()
    cursor = conn.cursor()

    today = datetime.now().strftime('%Y-%m-01')  # 이번 달 1일

    cursor.execute('''
        SELECT SUM(count) as total FROM api_usage
        WHERE api_type = 'vision' AND usage_date >= ?
    ''', (today,))

    row = cursor.fetchone()
    conn.close()

    total = row['total'] if row and row['total'] else 0
    return total < VISION_API_MONTHLY_LIMIT


def get_vision_api_remaining() -> int:
    """Vision API 남은 횟수"""
    conn = get_db_connection()
    cursor = conn.cursor()

    today = datetime.now().strftime('%Y-%m-01')

    cursor.execute('''
        SELECT SUM(count) as total FROM api_usage
        WHERE api_type = 'vision' AND usage_date >= ?
    ''', (today,))

    row = cursor.fetchone()
    conn.close()

    total = row['total'] if row and row['total'] else 0
    return max(0, VISION_API_MONTHLY_LIMIT - total)


def increment_api_usage(api_type: str = 'vision'):
    """API 사용량 증가"""
    conn = get_db_connection()
    cursor = conn.cursor()

    today = datetime.now().strftime('%Y-%m-%d')

    cursor.execute('''
        INSERT INTO api_usage (api_type, usage_date, count)
        VALUES (?, ?, 1)
        ON CONFLICT(api_type, usage_date) DO UPDATE SET count = count + 1
    ''', (api_type, today))

    conn.commit()
    conn.close()
