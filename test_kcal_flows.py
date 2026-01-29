import json
from app import app

def send(client, text, uid):
    p = {'userRequest': {'utterance': text, 'user': {'id': uid}}}
    resp = client.post('/chatbot', json=p)
    return json.loads(resp.data)

print("=" * 60)
print("열량 특수 flow 검증")
print("=" * 60)

# Test 1: 영유아 열량 일반 산출 (탄수화물×4 + 단백질×4 + 지방×9)
print("\n### 1. 영유아 열량 일반 산출 ###")
with app.test_client() as c:
    uid = 'test_kcal_1'
    send(c, '영양성분검사', uid)
    send(c, '영양표시 도우미', uid)
    send(c, '영유아(만1~2세)', uid)
    send(c, '표시값 변환', uid)
    send(c, '열량', uid)
    send(c, '표시값으로 산출', uid)
    d = send(c, '일반 산출 ①', uid)
    print("일반 산출 선택:", d['template']['outputs'][0]['simpleText']['text'][:80])
    
    send(c, '30', uid)  # 탄수화물
    send(c, '10', uid)  # 단백질
    d = send(c, '5', uid)  # 지방
    text = d['template']['outputs'][0]['simpleText']['text']
    print("\n결과 (탄수30 + 단백10 + 지방5):")
    print(text)
    # Expected: (30×4 + 10×4 + 5×9) = 120+40+45 = 205 kcal -> 표시값 205 kcal
    if '오류' in text:
        print("*** ERROR ***")

# Test 2: 일반 열량 일반 산출
print("\n### 2. 일반 열량 일반 산출 ###")
with app.test_client() as c:
    uid = 'test_kcal_2'
    send(c, '영양성분검사', uid)
    send(c, '영양표시 도우미', uid)
    send(c, '일반(3세 이상)', uid)
    send(c, '표시값 변환', uid)
    send(c, '열량', uid)
    send(c, '표시값으로 산출', uid)
    send(c, '일반 산출 ①', uid)
    send(c, '50', uid)  # 탄수화물
    send(c, '20', uid)  # 단백질
    d = send(c, '10', uid)  # 지방
    text = d['template']['outputs'][0]['simpleText']['text']
    print("\n결과 (탄수50 + 단백20 + 지방10):")
    print(text)
    # Expected: (50×4 + 20×4 + 10×9) = 200+80+90 = 370 kcal -> 표시값 370 kcal
    if '오류' in text:
        print("*** ERROR ***")

# Test 3: 영유아 열량 당알콜등 별도표시 ②
print("\n### 3. 영유아 열량 당알콜등 별도표시 ###")
with app.test_client() as c:
    uid = 'test_kcal_3'
    send(c, '영양성분검사', uid)
    send(c, '영양표시 도우미', uid)
    send(c, '영유아(만1~2세)', uid)
    send(c, '표시값 변환', uid)
    send(c, '열량', uid)
    send(c, '표시값으로 산출', uid)
    d = send(c, '당알콜등 별도표시 ②', uid)
    print("당알콜등 별도표시 선택:", d['template']['outputs'][0]['simpleText']['text'][:80])
    
    send(c, '5', uid)    # 당알콜(에리스리톨 제외)
    send(c, '2', uid)    # 에리스리톨
    send(c, '3', uid)    # 식이섬유
    send(c, '1', uid)    # 타가토스
    send(c, '0.5', uid)  # 알룰로오스
    send(c, '20', uid)   # 기타탄수화물
    send(c, '15', uid)   # 단백질
    d = send(c, '8', uid)  # 지방
    text = d['template']['outputs'][0]['simpleText']['text']
    print("\n결과:")
    print(text)
    # Expected: 5×2.4 + 2×0 + 3×2 + 1×1.5 + 0.5×0 + 20×4 + 15×4 + 8×9
    # = 12 + 0 + 6 + 1.5 + 0 + 80 + 60 + 72 = 231.5 kcal -> 표시값 230 kcal
    if '오류' in text:
        print("*** ERROR ***")

# Test 4: 일반 열량 알콜/유기산 추가
print("\n### 4. 일반 열량 일반산출 + 알콜/유기산 추가 ###")
with app.test_client() as c:
    uid = 'test_kcal_4'
    send(c, '영양성분검사', uid)
    send(c, '영양표시 도우미', uid)
    send(c, '일반(3세 이상)', uid)
    send(c, '표시값 변환', uid)
    send(c, '열량', uid)
    send(c, '표시값으로 산출', uid)
    send(c, '일반 산출 ①', uid)
    send(c, '40', uid)   # 탄수화물
    send(c, '15', uid)   # 단백질
    d = send(c, '8', uid)  # 지방
    text = d['template']['outputs'][0]['simpleText']['text']
    print("일반산출 결과:", text[:60])
    
    # Check if 알콜/유기산 추가 button appears
    buttons = [qr.get('label','') for qr in json.loads(app.test_client().post('/chatbot', json={'userRequest':{'utterance':'x','user':{'id':'dummy'}}}).data)['template'].get('quickReplies',[])]
    
    d = send(c, '알콜/유기산 추가', uid)
    text = d['template']['outputs'][0]['simpleText']['text']
    print("알콜/유기산 입력 안내:", text[:80])
    
    send(c, '5', uid)  # 알콜
    d = send(c, '3', uid)  # 유기산
    text = d['template']['outputs'][0]['simpleText']['text']
    print("\n결과 (탄수40+단백15+지방8+알콜5+유기산3):")
    print(text)
    # Expected: 40×4 + 15×4 + 8×9 + 5×7 + 3×3 = 160+60+72+35+9 = 336 kcal -> 335 kcal
    if '오류' in text:
        print("*** ERROR ***")

print("\n=== DONE ===")
