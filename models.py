"""
데이터베이스 모델 (SQLite)
- 크롤링한 검사항목/검사주기 데이터 저장
"""
import sqlite3
from datetime import datetime
from config import DATABASE_PATH


def get_connection():
    """데이터베이스 연결"""
    conn = sqlite3.connect(DATABASE_PATH)
    conn.row_factory = sqlite3.Row
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
    """검사항목 조회"""
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT * FROM inspection_items
        WHERE category = ? AND food_type LIKE ?
    """, (category, f"%{food_type}%"))

    result = cursor.fetchone()
    conn.close()

    return dict(result) if result else None


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
    """검사주기 조회"""
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT * FROM inspection_cycles
        WHERE category = ? AND industry = ? AND food_type LIKE ?
    """, (category, industry, f"%{food_type}%"))

    result = cursor.fetchone()
    conn.close()

    return dict(result) if result else None


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


# 데이터베이스 초기화 실행
if __name__ == "__main__":
    init_database()
    print("데이터베이스 초기화 완료!")
