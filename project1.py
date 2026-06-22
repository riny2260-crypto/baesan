import streamlit as st
import fitz  # PyMuPDF
import io
import re
import pandas as pd
from datetime import datetime
import json

from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload


# =========================================================================
# 1. 학교 맞춤 설정
# =========================================================================

ALL_TEACHERS = ["김철수", "이영희", "박민수", "최수연", "정우성", "홍길동", "조서린"]

TRAINING_KEYWORDS = {
    "다문화이해교육": ["다문화", "상호문화", "다문화이해"],
    "성희롱예방교육": ["성희롱", "폭력예방", "양성평등", "4대폭력"],
    "안전보건교육": ["안전보건", "산업안전", "중대재해"],
    "학교폭력예방교육": ["학교폭력", "학폭예방"],
    "아동학대예방교육": ["아동학대", "학대신고"],
    "개인정보보호교육": ["개인정보", "정보보안"],
    "청렴교육": ["부패방지", "청렴", "이해충돌"],
    "긴급복지신고의무자교육": ["긴급복지", "긴급", "신고의무자"]
}

SCOPES = ['https://www.googleapis.com/auth/drive']


# =========================================================================
# 2. Streamlit Secrets 기반 Google OAuth
# =========================================================================

def get_gdrive_service():
    # Streamlit Cloud 또는 로컬 secrets.toml의 JSON 읽기
    client_secret_json = st.secrets["google"]["client_secret"]

    client_config = json.loads(client_secret_json)

    flow = InstalledAppFlow.from_client_config(client_config, SCOPES)
    creds = flow.run_local_server(port=0)

    return build('drive', 'v3', credentials=creds)


# =========================================================================
# 3. 구글 드라이브 폴더 조회/생성
# =========================================================================

def get_or_create_drive_folder(service, folder_name, parent_id=None):
    query = f"name = '{folder_name}' and mimeType = 'application/vnd.google-apps.folder' and trashed = false"
    if parent_id:
        query += f" and '{parent_id}' in parents"

    result = service.files().list(q=query, fields="files(id)").execute()
    items = result.get("files", [])

    if items:
        return items[0]["id"]

    folder_meta = {"name": folder_name, "mimeType": "application/vnd.google-apps.folder"}
    if parent_id:
        folder_meta["parents"] = [parent_id]

    folder = service.files().create(body=folder_meta, fields="id").execute()
    return folder["id"]


# =========================================================================
# 4. PDF 분석 함수
# =========================================================================

def analyze_pdf_details(file_bytes):
    doc = fitz.open(stream=file_bytes, filetype="pdf")
    text = "".join(page.get_text() for page in doc)

    detected_name = next((n for n in ALL_TEACHERS if n in text), "미확인이름")

    detected_courses = [
        name for name, keywords in TRAINING_KEYWORDS.items()
        if any(k in text for k in keywords)
    ]
    if not detected_courses:
        detected_courses = ["기타연수"]

    serial_match = re.search(r'(제\s*[\w\s-]+호)', text)
    serial = serial_match.group(1) if serial_match else "미확인(이수번호)"

    date_pattern = r'(\d{4}[.\s년-]\s*\d{1,2}[.\s월-]\s*\d{1,2}[일]?\s*[~\-]\s*\d{4}[.\s년-]?\s*\d{1,2}[.\s월-]\s*\d{1,2}[일]?)'
    date_match = re.search(date_pattern, text)
    period = date_match.group(1) if date_match else "미확인(연수기간)"

    time_match = re.search(r'(\d+\s*시간\s*\d*\s*분?|\d+\s*시간)', text)
    hours = time_match.group(1) if time_match else "미확인(이수시간)"

    return detected_name, detected_courses, serial, period, hours


# =========================================================================
# 5. CSV 장부 업데이트
# =========================================================================

def update_csv_ledger(service, course_folder_id, course_name, row):
    filename = f"{course_name}_취합장부.csv"
    query = f"name = '{filename}' and '{course_folder_id}' in parents and trashed = false"

    result = service.files().list(q=query, fields="files(id)").execute()
    items = result.get("files", [])

    new_df = pd.DataFrame([row])

    if items:
        file_id = items[0]["id"]
        content = service.files().get_media(fileId=file_id).execute()

        existing_df = pd.read_csv(io.BytesIO(content))
        existing_df = existing_df[existing_df["선생님 성함"] != row["선생님 성함"]]

        combined = pd.concat([existing_df, new_df], ignore_index=True)

        buf = io.BytesIO()
        combined.to_csv(buf, index=False, encoding="utf-8-sig")
        buf.seek(0)

        media = MediaIoBaseUpload(buf, mimetype="text/csv", resumable=True)
        service.files().update(fileId=file_id, media_body=media).execute()

    else:
        buf = io.BytesIO()
        new_df.to_csv(buf, index=False, encoding="utf-8-sig")
        buf.seek(0)

        meta = {"name": filename, "parents": [course_folder_id]}
        media = MediaIoBaseUpload(buf, mimetype="text/csv", resumable=True)
        service.files().create(body=meta, media_body=media, fields="id").execute()


# =========================================================================
# 6. Streamlit UI
# =========================================================================

st.set_page_config(page_title="연수 이수증 자동 분류기", layout="wide")
st.title("📄 연수 이수증 자동 분류 & 장부 자동 생성 프로그램")
st.markdown("---")

if "course_submissions" not in st.session_state:
    st.session_state.course_submissions = {c: set() for c in TRAINING_KEYWORDS}
    st.session_state.course_submissions["기타연수"] = set()

menu = st.sidebar.radio("메뉴 선택", ["이수증 업로드", "미제출자 확인"])


# =========================================================================
# 메뉴 1: 업로드
# =========================================================================

if menu == "이수증 업로드":
    st.header("📥 이수증 업로드")

    uploaded_files = st.file_uploader("PDF 선택", type="pdf", accept_multiple_files=True)

    if uploaded_files and st.button("업로드 시작"):
        try:
            service = get_gdrive_service()
            root_id = get_or_create_drive_folder(service, "연수이수증_취합소")

            success = 0

            for uf in uploaded_files:
                bytes_data = uf.read()

                name, courses, serial, period, hours = analyze_pdf_details(bytes_data)

                if name == "미확인이름":
                    st.warning(f"⚠️ '{uf.name}' 에서 이름을 찾지 못했습니다.")
                    continue

                record = {
                    "선생님 성함": name,
                    "이수번호": serial,
                    "연수 기간": period,
                    "이수 시간": hours,
                    "비고": "통합 연수" if len(courses) >= 2 else "-",
                    "제출 일시": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                }

                folders = []
                for course in courses:
                    course_id = get_or_create_drive_folder(service, course, root_id)

                    new_name = f"({course})_{name}.pdf"
                    meta = {"name": new_name, "parents": [course_id]}
                    media = MediaIoBaseUpload(io.BytesIO(bytes_data), mimetype="application/pdf", resumable=True)

                    service.files().create(body=meta, media_body=media, fields="id").execute()

                    update_csv_ledger(service, course_id, course, record)

                    st.session_state.course_submissions[course].add(name)
                    folders.append(course)

                with st.expander(f"📌 {name} 처리 내역"):
                    st.write(f"저장 폴더: {', '.join(folders)}")
                    st.write(f"이수번호: {serial}")
                    st.write(f"기간: {period}")
                    st.write(f"시간: {hours}")

                success += 1

            if success > 0:
                st.balloons()
                st.success(f"{success}건 업로드 완료!")

        except Exception as e:
            st.error(f"오류 발생: {e}")


# =========================================================================
# 메뉴 2: 미제출자 확인
# =========================================================================

else:
    st.header("🔍 미제출자 확인")

    course = st.selectbox("연수 과정 선택", list(TRAINING_KEYWORDS.keys()) + ["기타연수"])

    submitted = st.session_state.course_submissions[course]
    unsubmitted = [t for t in ALL_TEACHERS if t not in submitted]

    col1, col2 = st.columns(2)

    with col1:
        st.subheader("🟢 제출 완료")
        for t in sorted(submitted):
            st.write(f"- {t}")

    with col2:
        st.subheader("🔴 미제출")
        for t in sorted(unsubmitted):
            st.write(f"- {t}")