# 카카오 챗봇

바이오에프엘 검사 안내 카카오톡 챗봇 서버

## 구조

```
kakaochatbot/
├── app.py              # Flask 서버 (카카오 챗봇 API)
├── config.py           # 설정 파일
├── models.py           # 데이터베이스 모델 (SQLite)
├── crawler.py          # 크롤러 모듈
├── scheduler.py        # 주기적 크롤링 스케줄러
├── requirements.txt    # 의존성
├── data/               # DB 저장
└── logs/               # 로그 저장
```

## 설치

```bash
pip install -r requirements.txt
```

## 실행

### 1. 초기 크롤링 (최초 1회)
```bash
python scheduler.py --once
```

### 2. 서버 실행
```bash
python app.py
```

### 3. 스케줄러 실행 (별도 터미널)
```bash
python scheduler.py
```

## 카카오 오픈빌더 연동

1. [카카오 i 오픈빌더](https://i.kakao.com/) 접속
2. 스킬 생성 → URL: `https://your-domain.com/chatbot`
3. POST 메서드 설정
4. 시나리오에서 스킬 연결

## 설정

`config.py`에서 설정 변경:

- `SERVER_PORT`: 서버 포트 (기본: 5000)
- `CRAWL_INTERVAL_HOURS`: 크롤링 주기 (기본: 1시간)

## API 엔드포인트

| 엔드포인트 | 메서드 | 설명 |
|-----------|--------|------|
| `/chatbot` | POST | 카카오 챗봇 스킬 |
| `/health` | GET | 헬스체크 |
