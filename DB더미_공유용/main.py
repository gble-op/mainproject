from datetime import datetime

import joblib
import pandas as pd
import psycopg2
from fastapi import FastAPI, HTTPException
from fastapi.responses import RedirectResponse
from pydantic import BaseModel

app = FastAPI()

# 서버가 켜질 때 모델을 한 번만 불러옴
model = joblib.load('침수예측_모델.pkl')
cols = joblib.load('침수예측_모델_입력컬럼순서.pkl')

# 지하차도 목록 (학습 데이터에서 이름·지역·높이·길이만 뽑아둠)
_목록 = (pd.read_excel('랜덤포레스트_학습용_더미_v3.xlsx', sheet_name='지하차도목록')
         .drop_duplicates('지하차도명'))
지하차도 = {r['지하차도명']: {'지역': r['지역'], '높이_m': float(r['높이_m']), '길이_m': int(r['길이_m'])}
            for _, r in _목록.iterrows()}

# 실제로 있었던 침수 기록 (비가 안 오는 날 시연할 때 씀)
_전체 = pd.read_excel('랜덤포레스트_학습용_더미_v3.xlsx', sheet_name='전체학습데이터')
_실제기록 = _전체[_전체['데이터구분'] == '실제'].reset_index(drop=True)

# 요청으로 받을 값 (v3: 월·시간·요일 없음)
class FloodInput(BaseModel):
    지하차도명: str
    강수량_mm: float
    직전1시간_mm: float
    최근3시간_누적_mm: float
    최근6시간_누적_mm: float
    최근12시간_누적_mm: float
    최근24시간_누적_mm: float
    높이_m: float
    길이_m: int

    # /docs 화면에 자동으로 채워지는 입력 예시
    model_config = {'json_schema_extra': {'examples': [{
        '지하차도명': '테스트지하차도',
        '강수량_mm': 25.0,
        '직전1시간_mm': 18.0,
        '최근3시간_누적_mm': 55.0,
        '최근6시간_누적_mm': 70.0,
        '최근12시간_누적_mm': 80.0,
        '최근24시간_누적_mm': 85.0,
        '높이_m': 4.0,
        '길이_m': 132,
    }]}}

labels = ['안전', '위험', '침수']


def 위험점수(X):
    """단계를 딱 잘라 정하지 않고, 각 단계일 확률을 섞어 0~100점 하나로 만듦
       (안전 0점, 위험 50점, 침수 100점으로 두고 확률만큼 가중평균)"""
    확률 = model.predict_proba(X)[0]
    점수 = sum(p * 배점 for p, 배점 in zip(확률, [0, 50, 100]))
    return round(float(점수), 1), {이름: round(float(p) * 100, 1) for 이름, p in zip(labels, 확률)}


# 기본 주소로 들어오면 테스트 화면(/docs)으로 이동
@app.get('/', include_in_schema=False)
def root():
    return RedirectResponse('/docs')

@app.post('/predict')
def predict(data: FloodInput):
    # 받은 값을 표 형태로 바꾸고, 학습 때와 같은 순서로 정렬
    X = pd.DataFrame([data.model_dump()])[cols]
    result = int(model.predict(X)[0])
    점수, 단계별확률 = 위험점수(X)

    # 예측 결과를 DB에 저장
    conn = psycopg2.connect(host='localhost', dbname='postgres',
                            user='postgres', password='1234')
    conn.autocommit = True
    cur = conn.cursor()
    cur.execute('insert into flood_predictions (underpass_name, risk_level, risk_score) values (%s, %s, %s)',
                (data.지하차도명, result, 점수))
    conn.close()

    return {'지하차도명': data.지하차도명, '위험점수': 점수, '단계별확률': 단계별확률,
            '위험단계': result, '상태': labels[result]}


# ============================================================
# /predict/underpass  (2026-09-24 추가)
# ============================================================
#
# [왜 추가했나]
#   기존 /predict 는 예측 결과를 flood_predictions 표에 저장한다.
#   이 표는 예전에 시험용으로 만든 것이라 팀 DB 설계서에는 없다.
#   그래서 웹 화면이 보는 표(risk_score, underpass)와 예측 서버가 따로 놀고 있었다.
#   예측을 아무리 해도 웹 화면의 위험단계는 바뀌지 않는 상태였다.
#
#   팀 설계서의 underpass 표에는 이런 주석이 있다.
#       current_risk_level -- 현재 위험도 단계(FastAPI 수신 시마다 갱신, 오르내림 둘 다 반영)
#   즉 이 FastAPI 가 예측할 때마다 DB 를 갱신하는 것이 원래 설계 의도다.
#   그 연결을 실제로 구현한 것이 이 주소다.
#
# [무엇을 하나]
#   1. 지하차도 번호(underpass_id)로 예측한다.
#      기존 /predict 는 지하차도명을 글자로 받았는데, 팀 DB 는 번호로 연결되어 있다.
#      모델이 쓰는 높이·길이는 DB 에 없어서 학습 데이터 엑셀에서 가져온다.
#   2. 예측 결과를 risk_score 표에 저장한다.
#   3. 단계 판정 기준을 threshold 표에서 읽어온다.
#      코드에 40점/70점을 박아두지 않으므로, 관리자가 화면에서 기준을 바꾸면 바로 반영된다.
#   4. 단계가 바뀌면 underpass.current_risk_level 을 갱신하고
#      underpass_event 에 RISK_LEVEL_UP / RISK_LEVEL_DOWN 을 남긴다.
#
# [하지 않는 것 - 의도적임]
#   지하차도 차단(BLOCK)과 해제(RELEASE)는 이 주소에서 하지 않는다.
#   설계서의 CHECK 조건이 BLOCK/RELEASE 에 operator_id(조작한 관리자)를 필수로 요구한다.
#   즉 차단은 사람이 판단하고 그 책임을 기록에 남기라는 설계다.
#   모델 점수만 보고 자동으로 도로를 막으면 오작동 한 번이 사고로 이어질 수 있다.
#   이 서버는 위험을 알리는 데까지만 하고, 차단 결정은 관리자 화면에서 한다.
#
# [기존 /predict 는 그대로 둠]
#   이미 쓰고 있는 곳이 있을 수 있어 건드리지 않았다.
#   정리하려면 팀과 상의한 뒤 flood_predictions 표와 함께 없애면 된다.
# ============================================================

class 예측요청(BaseModel):
    """/predict/underpass 가 받는 값.
       기존 FloodInput 과 달리 지하차도명 대신 underpass_id 를 받고,
       높이·길이는 서버가 알아서 채운다."""
    underpass_id: int
    강수량_mm: float
    직전1시간_mm: float
    최근3시간_누적_mm: float
    최근6시간_누적_mm: float
    최근12시간_누적_mm: float
    최근24시간_누적_mm: float
    예측강수량_mm: float | None = None      # 앞으로 내릴 것으로 보는 비 (없으면 안 넣음)

    model_config = {'json_schema_extra': {'examples': [{
        'underpass_id': 1,
        '강수량_mm': 25.0,
        '직전1시간_mm': 18.0,
        '최근3시간_누적_mm': 55.0,
        '최근6시간_누적_mm': 70.0,
        '최근12시간_누적_mm': 80.0,
        '최근24시간_누적_mm': 85.0,
        '예측강수량_mm': 30.0,
    }]}}


def 연결():
    """DB 접속. 예측 한 번에 여러 표를 건드려서 함수로 빼둠."""
    conn = psycopg2.connect(host='localhost', dbname='postgres',
                            user='postgres', password='1234')
    conn.autocommit = True
    return conn


@app.post('/predict/underpass')
def predict_underpass(data: 예측요청):
    """지하차도 번호로 예측하고 팀 DB(risk_score)에 저장.
       위험단계가 바뀌면 underpass 의 현재 단계도 갱신하고 underpass_event 에 기록함."""
    conn = 연결()
    cur = conn.cursor()

    # 1) 지하차도 확인 (모델이 쓰는 높이·길이는 학습 데이터에서 가져옴)
    cur.execute('select name, current_risk_level from underpass where id=%s and is_deleted=false',
                (data.underpass_id,))
    찾음 = cur.fetchone()
    if 찾음 is None:
        conn.close()
        raise HTTPException(404, f'{data.underpass_id}번 지하차도가 없어요.')
    이름, 이전단계 = 찾음
    정보 = 지하차도.get(이름)
    if 정보 is None:
        conn.close()
        raise HTTPException(400, f"'{이름}' 의 높이·길이 정보를 찾을 수 없어요.")

    # 2) 예측
    입력 = {'강수량_mm': data.강수량_mm, '직전1시간_mm': data.직전1시간_mm,
            '최근3시간_누적_mm': data.최근3시간_누적_mm, '최근6시간_누적_mm': data.최근6시간_누적_mm,
            '최근12시간_누적_mm': data.최근12시간_누적_mm, '최근24시간_누적_mm': data.최근24시간_누적_mm,
            '높이_m': 정보['높이_m'], '길이_m': 정보['길이_m']}
    X = pd.DataFrame([입력])[cols]
    점수, 단계별확률 = 위험점수(X)

    # 3) 이 지하차도의 임계치를 DB에서 읽어 단계 판정
    #    코드에 40점/70점을 박아두지 않고 threshold 표에서 읽는 이유:
    #    관리자가 화면에서 기준을 바꾸면 서버를 다시 켜지 않아도 바로 반영되게 하려고.
    #    높은 기준부터 내려오며 처음 넘는 단계를 고름 (위험 -> 경고 -> 안전)
    cur.execute('''select level, risk_score_min from threshold
                   where underpass_id=%s order by risk_score_min desc''', (data.underpass_id,))
    기준표 = cur.fetchall()
    새단계 = '안전'
    for 레벨, 하한 in 기준표:
        if 점수 >= float(하한):
            새단계 = 레벨
            break

    # 4) 예측 결과 저장
    지금 = datetime.now()
    cur.execute('''insert into risk_score (underpass_id, risk_score, rainfall_recent_mm,
                                           rainfall_forecast_mm, predicted_at)
                   values (%s, %s, %s, %s, %s) returning id''',
                (data.underpass_id, 점수, data.최근6시간_누적_mm, data.예측강수량_mm, 지금))
    기록번호 = cur.fetchone()[0]

    # 5) 단계가 바뀌었으면 현재 상태를 갱신하고 이벤트를 남김
    #    설계서: underpass.current_risk_level 은 FastAPI 수신 시마다 갱신(오르내림 둘 다)
    #    차단(BLOCK)은 여기서 하지 않음. 관리자가 화면에서 직접 판단하는 몫.
    이벤트 = None
    if 새단계 != 이전단계:
        순서 = ['안전', '경고', '위험']
        올라감 = 순서.index(새단계) > 순서.index(이전단계)
        이벤트 = 'RISK_LEVEL_UP' if 올라감 else 'RISK_LEVEL_DOWN'
        cur.execute('''insert into underpass_event (underpass_id, event_type, risk_level,
                                                    risk_score_at_event, created_at)
                       values (%s, %s, %s, %s, %s)''',
                    (data.underpass_id, 이벤트, 새단계, 점수, 지금))
        cur.execute('update underpass set current_risk_level=%s where id=%s',
                    (새단계, data.underpass_id))

    cur.close()
    conn.close()

    return {
        '지하차도명': 이름,
        'underpass_id': data.underpass_id,
        '위험점수': 점수,
        '단계별확률': 단계별확률,
        '위험단계': 새단계,
        '이전단계': 이전단계,
        '단계변화': 이벤트 or '변화 없음',
        '예측시각': 지금.strftime('%Y-%m-%d %H:%M:%S'),
        'risk_score_기록번호': 기록번호,
    }


@app.get('/underpasses')
def underpasses():
    """예측할 수 있는 지하차도 목록"""
    return [{'지하차도명': 이름, '지역': 정보['지역'],
             '높이_m': 정보['높이_m'], '길이_m': 정보['길이_m']}
            for 이름, 정보 in 지하차도.items()]


@app.get('/past_cases')
def past_cases(위험단계: int = 2):
    """실제로 있었던 침수·위험 사례 목록 (시연용). 위험단계 2=침수, 1=위험"""
    사례 = _실제기록[_실제기록['정답_위험단계'] == 위험단계]
    return [{'번호': i, '지하차도명': r['지하차도명'],
             '일시': str(r['일시']), '강수량_mm': r['강수량_mm'],
             '최근3시간_누적_mm': r['최근3시간_누적_mm']}
            for i, (_, r) in enumerate(사례.iterrows(), 1)]


@app.get('/predict_case/{번호}')
def predict_case(번호: int, 위험단계: int = 2):
    """실제 사례의 그날 강수량을 모델에 넣어 예측 (비 안 오는 날 시연용)"""
    사례 = _실제기록[_실제기록['정답_위험단계'] == 위험단계].reset_index(drop=True)
    if not 1 <= 번호 <= len(사례):
        raise HTTPException(404, f'1~{len(사례)} 사이의 번호를 넣어 주세요. 목록은 /past_cases 에서 볼 수 있어요.')
    행 = 사례.iloc[번호 - 1]

    입력 = {칸: (float(행[칸]) if 칸 != '길이_m' else int(행[칸])) for 칸 in cols}
    X = pd.DataFrame([입력])[cols]
    result = int(model.predict(X)[0])
    점수, 단계별확률 = 위험점수(X)

    return {
        '지하차도명': 행['지하차도명'],
        '실제일시': str(행['일시']),
        '위험점수': 점수,
        '단계별확률': 단계별확률,
        '모델예측': f'{result} ({labels[result]})',
        '실제정답': f"{int(행['정답_위험단계'])} ({labels[int(행['정답_위험단계'])]})",
        '맞았는지': '맞음' if result == int(행['정답_위험단계']) else '틀림',
        '그날강수량': 입력,
    }
