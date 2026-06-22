import streamlit as st
import fitz
import os
import re
import pandas as pd
from datetime import datetime
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload
import io

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


def check_gdrive_auth():
    creds = None
    if os.path.exists('token.json'):
        try:
            creds = Credentials.from_authorized_user_file('token.json', SCOPES)
        except Exception:
            pass

    if creds and creds.valid:
        return build('drive', 'v3', credentials=creds)

    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            with open('token.json', 'w') as token:
                token.write(creds.to_json())
            return build('drive', 'v3', credentials=creds)
        except Exception:
            pass

    if 'gdrive_secrets' in st.secrets:
        client_config = {
            "web": {
                "client_id": st.secrets["gdrive_secrets"]["client_id"],
                "client_secret": st.secrets["gdrive_secrets"]["client_secret"],
                "project_id": st.secrets["gdrive_secrets"]["project_id"],
                "auth_uri": st.secrets["gdrive_secrets"]["auth_uri"],
                "token_uri": st.secrets["gdrive_secrets"]["token_uri"]
            }
        }
        redirect_uri = st.secrets["gdrive_secrets"]["redirect_uri"]

        flow = Flow.from_client_config(client_config, scopes=SCOPES, redirect_uri=redirect_uri)
        auth_url, _ = flow.authorization_url(prompt='consent', access_type='offline')

        st.sidebar.markdown(f"[🔗 1단계: 여기를 클릭하여 구글 로그인 진행]({auth_url})")

        code_input = st.sidebar.text_input("🔑 2단계: 로그인 완료 후 주소창의 code= 뒤에 나오는 문구를 입력해 주세요:")
        if code_input:
            try:
                flow.fetch_token(code=code_input)
                creds = flow.credentials
                with open('token.json', 'w') as token:
                    token.write(creds.to_json())
                st.sidebar.success("🎉 인증 열쇠 생성 성공! 새로고침합니다.")
                st.rerun()
            except Exception as e:
                st.sidebar.error(f"인증 실패: