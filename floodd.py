import pandas as pd
import joblib
from sklearn.ensemble import RandomForestClassifier

# 1. 엑셀에서 'RF_최종학습데이터' 시트 불러오기 (v3: 강수량·누적강수량·높이·길이 8칸)
df = pd.read_excel('랜덤포레스트_학습용_더미_v3.xlsx', sheet_name='RF_최종학습데이터')

# 2. 정답 칸만 따로 떼어내고, 나머지는 전부 입력값으로 씀
feature_cols = [c for c in df.columns if c != '정답_위험단계']
X = df[feature_cols]
y = df['정답_위험단계']

# 3. 랜덤포레스트 모델 준비
model = RandomForestClassifier(n_estimators=200, class_weight='balanced', random_state=42)

# 4. 학습!
model.fit(X, y)

# 5. 모델 저장
joblib.dump(model, '침수예측_모델.pkl')
joblib.dump(feature_cols, '침수예측_모델_입력컬럼순서.pkl')

print("학습 완료!")
print("입력 칸:", feature_cols)
