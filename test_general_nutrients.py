import json
from app import app

def test_nutrient(client, nutrient, value, user_id):
    p = {'userRequest': {'utterance': '영양성분검사', 'user': {'id': user_id}}}
    client.post('/chatbot', json=p)
    p['userRequest']['utterance'] = '영양표시 도우미'
    client.post('/chatbot', json=p)
    p['userRequest']['utterance'] = '일반(3세 이상)'
    client.post('/chatbot', json=p)
    p['userRequest']['utterance'] = '표시값 변환'
    client.post('/chatbot', json=p)
    
    p['userRequest']['utterance'] = nutrient
    client.post('/chatbot', json=p)
    
    if nutrient in ['열량', '탄수화물']:
        p['userRequest']['utterance'] = '실측값 입력'
        client.post('/chatbot', json=p)
    
    p['userRequest']['utterance'] = str(value)
    resp = client.post('/chatbot', json=p)
    data = json.loads(resp.data)
    return data['template']['outputs'][0]['simpleText']['text']

# 일반 기준치: 탄수화물 324g, 당류 100g, 식이섬유 25g, 단백질 55g, 지방 54g, 포화지방 15g, 콜레스테롤 300mg, 나트륨 2000mg
test_cases = [
    ("열량", 250),
    ("탄수화물", 160),
    ("당류", 55.3),
    ("식이섬유", 12.7),
    ("단백질", 18.5),
    ("지방", 15.2),
    ("포화지방", 4.8),
    ("트랜스지방", 0.3),
    ("콜레스테롤", 127.3),
    ("나트륨", 523.7),
]

print("=" * 60)
print("일반(3세 이상) 주요 10개 영양소 검증")
print("=" * 60)

for i, (nutrient, value) in enumerate(test_cases):
    with app.test_client() as client:
        result = test_nutrient(client, nutrient, value, f'test_general_{i}')
        print(f"\n--- {nutrient} (입력: {value}) ---")
        print(result)
        if '오류' in result:
            print(f"*** ERROR DETECTED for {nutrient} ***")
