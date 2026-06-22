import streamlit as st
import pandas as pd
import fitz  # PyMuPDF
import os
import tempfile
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from google_auth_oauthlib.flow import InstalledAppFlow

# Google Drive API 스코프
SCOPES = ["https://www.googleapis.com/auth/drive.file"]

st.title("📄 PDF 업로드 → 정보 추출 → Google Drive 저장 자동화")

# --------------------------------------------------------------------------------------
# 🔐 Google OAuth 인증 함수 (Streamlit Cloud 지원 버전)
# --------------------------------------------------------------------------------------
def get_google_credentials():
    client_secret = st.secrets["google"]["client_secret"]

    # 임시 client_secret.json 파일 생성
    with tempfile.NamedTemporaryFile(delete=False, suffix=".json") as temp_file:
        temp_file.write(client_secret.encode())
        temp_path = temp_file.name

    flow = InstalledAppFlow.from_client_secrets_file(temp_path, SCOPES)

    # Cloud 환경에서는 브라우저 자동 실행이 불가능하므로 open_browser=False
    creds = flow.run_local_server(port=0, open_browser=False)

    return creds


# --------------------------------------------------------------------------------------
# 📤 Google Drive 업로드 함수
# --------------------------------------------------------------------------------------
def upload_to_drive(creds, file_path, file_name):
    drive_service = build("drive", "v3", credentials=creds)

    file_metadata = {
        "name": file_name,
        "parents": ["root"]  # My Drive 최상단
    }
    media = MediaFileUpload(file_path, mimetype="application/pdf", resumable=True)

    uploaded = drive_service.files().create(
        body=file_metadata,
        media_body=media,
        fields="id"
    ).execute()

    return uploaded.get("id")


# --------------------------------------------------------------------------------------
# 📄 PDF 분석 함수
# --------------------------------------------------------------------------------------
def extract_pdf_text(pdf_file):
    with fitz.open(stream=pdf_file.read(), filetype="pdf") as doc:
        text = ""
        for page in doc:
            text += page.get_text()
    return text


# --------------------------------------------------------------------------------------
# 🟦 메인 로직
# --------------------------------------------------------------------------------------
uploaded_file = st.file_uploader("PDF 파일을 업로드하세요", type=["pdf"])

if uploaded_file:
    st.success("PDF 업로드 완료!")

    # PDF 텍스트 추출
    text = extract_pdf_text(uploaded_file)

    st.write("📄 추출된 텍스트 미리보기:")
    st.text(text[:500] + "\n\n... (생략)")

    # CSV 생성
    extracted_data = {"텍스트": [text]}
    df = pd.DataFrame(extracted_data)

    csv_path = "result.csv"
    df.to_csv(csv_path, index=False)

    st.success("CSV 파일 생성 완료!")
    st.download_button("CSV 다운로드", data=df.to_csv(index=False), file_name="result.csv")

    st.divider()

    # Google Drive 업로드
    st.subheader("📤 Google Drive 업로드")

    if st.button("Google Drive에 업로드"):
        with st.spinner("Google 인증 중..."):
            creds = get_google_credentials()

        with st.spinner("파일 업로드 중..."):
            file_id = upload_to_drive(creds, csv_path, "result.csv")

        st.success("Google Drive 업로드 완료!")
        st.write(f"파일 ID: {file_id}")