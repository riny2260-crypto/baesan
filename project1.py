import streamlit as st
import fitz  # PyMuPDF
import io
import os
import re
import pandas as pd
from datetime import datetime
import json

# Google API 관련 모듈
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload
from streamlit.runtime.secrets import secrets
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials


# =========================================================================
# 1. 학교 환경 맞춤 설정
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
# 2. Google OAuth (Streamlit Secrets 기반)
# =========================================================================

def get_gdrive_service():
    """
    Streamlit Secrets에 저장된 client_secret(JSON 전체)을 이용하여 OAuth 인증 수행
    token.json 없이 동작
    """
    client_secret_json = secrets["google"]["client_secret"]
    client_config = json.loads(client_secret_json)

    flow = InstalledAppFlow.from_client_config(client_config, SCOPES)
    creds = flow.run_local_server(port=0)

    return build('drive', 'v3', credentials=creds)


# =========================================================================
# 3. 구글 드라이브 폴더 생성/조회 함수
# =========================================================================

def get_or_create_drive_folder(service, folder_name, parent_id=None):
    query = f"name = '{folder_name}' and mimeType = 'application/vnd.google-apps.folder' and trashed = false"
    if parent_id:
        query += f" and '{parent_id}' in parents"

    results = service.files().list(q=query, fields="files(id)").execute()
    items = results.get('files', [])

    if items:
        return items[0]['id']
    else:
        file_metadata = {
            'name': folder_name,
            'mimeType': 'application/vnd.google-apps.folder'
        }
        if parent_id:
            file_metadata['parents'] = [parent_id]

        folder = service.files().create(body=file_metadata, fields='id').execute()
        return folder.get('id')


# =========================================================================
# 4. PDF 분석 함수
# =========================================================================

def analyze_pdf_details(file_bytes):
    doc = fitz.open(stream=file_bytes, filetype="pdf")
    full_text = "".join([page.get_text() for page in doc])

    detected_name = "미확인이름"
    for name in ALL_TEACHERS:
        if name in full_text:
            detected_name = name
            break

    detected_courses = []
    for course_name, keywords in TRAINING_KEYWORDS.items():
        if any(keyword in full_text for keyword in keywords):
            detected_courses.append(course_name)
    if not detected_courses:
        detected_courses.append("기타연수")

    serial_match = re.search(r'(제\s*[\w\s-]+(?:호|호\b))', full_text)
    detected_serial = serial_match.group(1).strip() if serial_match else "미확인(이수번호)"

    date_pattern = r'(\d{4}[.\s년-]\s*\d{1,2}[.\s월-]\s*\d{1,2}[일]?\.?\s*(?:~|-)\s*\(?\d{4}[.\s년-]\s*\d{1,2}[.\s월-]\s*\d{1,2}[일]?\.?)'
    date_match = re.search(date_pattern, full_text)
    detected_period = date_match.group(1).strip() if date_match else "미확인(연수기간)"

    time_match = re.search(r'(\d+\s*시간\s*\d*\s*분?|\d+\s*시간)', full_text)
    detected_time = time_match.group(1).strip() if time_match else "미확인(이수시간)"

    return detected_name, detected_courses, detected_serial, detected_period, detected_time


# =========================================================================
# 5. CSV 장부 업데이트 함수
# =========================================================================

def update_csv_ledger(service, course_folder_id, course_name, data_row):
    filename = f"{course_name}_취합장부.csv"
    query = f"name = '{filename}' and '{course_folder_id}' in parents and trashed = false"
    results = service.files().list(q=query, fields="files(id)").execute()
    items = results.get('files', [])

    new_df = pd.DataFrame([data_row])

    if items:
        file_id = items[0]['id']
        file_content = service.files().get_media(fileId=file_id).execute()
        existing_df = pd.read_csv(io.BytesIO(file_content))

        if data_row["선생님 성함"] in existing_df["선생님 성함"].values:
            existing_df = existing_df[existing_df["선생님 성함"] != data_row["선생님 성함"]]

        combined_df = pd.concat([existing_df, new_df], ignore_index=True)

        csv_buffer = io.BytesIO()
        combined_df.to_csv(csv_buffer, index=False, encoding='utf-8-sig')
        csv_buffer.seek(0)

        media = MediaIoBaseUpload(csv_buffer, mimetype='text/csv', resumable=True)
        service.files().update(fileId=file_id, media_body=media).execute()

    else:
        csv_buffer = io.BytesIO()
        new_df.to_csv(csv_buffer, index=False, encoding='utf-8-sig')
        csv_buffer.seek(0)

        file_metadata = {'name': filename, 'parents': [course_folder_id]}
        media = MediaIoBaseUpload(csv_buffer, mimetype='text/csv', resumable=True)
        service.files().create(body=file_metadata, media_body=media, fields='id').execute()


# =========================================================================
# 6. 웹 UI 구성
# =========================================================================

st.set_page_config(page_title="연수 이수증 자동 분류기", layout="wide")
st.title("📄 연수 이수증 자동 분류 & 장부 자동 생성 프로그램")
st.markdown("---")

if "course_submissions" not in st.session_state:
    st.session_state.course_submissions = {course: set() for course in TRAINING_KEYWORDS.keys()}
    st.session_state.course_submissions["기타연수"] = set()

menu = st.sidebar.radio("메뉴 선택", ["이수증 업로드", "미제출자 확인"])


# =========================================================================
# 메뉴 1: 파일 업로드
# =========================================================================

if menu == "이수증 업로드":
    st.header("📥 이수증 업로드 및 정보 추출")

    uploaded_files = st.file_uploader(
        "PDF 파일을 선택하세요.",
        type=["pdf"],
        accept_multiple_files=True
    )

    if uploaded_files and st.button("분석 시작"):
        try:
            drive_service = get_gdrive_service()
            root_folder_id = get_or_create_drive_folder(drive_service, "연수이수증_취합소")
            success_count = 0

            for uploaded_file in uploaded_files:
                file_bytes = uploaded_file.read()
                name, courses, serial, period, itime = analyze_pdf_details(file_bytes)

                if name == "미확인이름":
                    st.warning(f"⚠️ '{uploaded_file.name}'에서 선생님 이름을 찾을 수 없습니다.")
                    continue

                record = {
                    "선생님 성함": name,
                    "이수번호": serial,
                    "연수 기간": period,
                    "이수 시간": itime,
                    "비고": "통합 연수" if len(courses) >= 2 else "-",
                    "제출 일시": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                }

                saved_folders = []
                for course in courses:
                    folder_id = get_or_create_drive_folder(drive_service, course, root_folder_id)

                    new_filename = f"({course})_{name}.pdf"
                    metadata = {'name': new_filename, 'parents': [folder_id]}
                    media = MediaIoBaseUpload(io.BytesIO(file_bytes), mimetype='application/pdf', resumable=True)

                    drive_service.files().create(body=metadata, media_body=media, fields='id').execute()

                    update_csv_ledger(drive_service, folder_id, course, record)

                    st.session_state.course_submissions[course].add(name)
                    saved_folders.append(course)

                with st.expander(f"📌 {name} 선생님 처리 결과"):
                    st.write(f"저장된 폴더: {', '.join(saved_folders)}")
                    st.write(f"이수번호: {serial}")
                    st.write(f"연수기간: {period}")
                    st.write(f"이수시간: {itime}")

                success_count += 1

            if success_count > 0:
                st.balloons()
                st.success(f"총 {success_count}건 업로드 완료!")

        except Exception as e:
            st.error(f"오류 발생: {e}")


# =========================================================================
# 메뉴 2: 미제출자 확인
# =========================================================================

elif menu == "미제출자 확인":
    st.header("🔍 미제출자 확인")

    course = st.selectbox("연수 과정 선택", list(TRAINING_KEYWORDS.keys()) + ["기타연수"])

    submitted = st.session_state.course_submissions.get(course, set())
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