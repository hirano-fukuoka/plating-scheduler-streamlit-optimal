import streamlit as st
import pandas as pd
from datetime import date
from scheduler import optimize_schedule
from utils import plot_gantt

st.set_page_config(page_title="Plating Scheduler", layout="wide")
st.title("🔧 めっき工程スケジューラ（Streamlit + OR-Tools）")

start_date = st.date_input("スケジュール開始日", value=date.today())

uploaded_jobs = st.file_uploader("📦 品物リストCSV", type="csv")
uploaded_workers = st.file_uploader("👷‍♂️ 作業者リストCSV", type="csv")
uploaded_sos = st.file_uploader("🛢 槽リストCSV", type="csv")

if st.button("📅 スケジュール最適化を実行") and uploaded_jobs and uploaded_workers and uploaded_sos:
    jobs_df = pd.read_csv(uploaded_jobs)
    workers_df = pd.read_csv(uploaded_workers)
    sos_df = pd.read_csv(uploaded_sos)

    schedule_df = optimize_schedule(jobs_df, workers_df, sos_df, start_date)
    st.success("✅ 最適スケジュール計算完了！")

    st.subheader("📋 スケジュール一覧")
    st.dataframe(schedule_df)

    st.subheader("🗂 ガントチャート")
    fig = plot_gantt(schedule_df)
    st.plotly_chart(fig, use_container_width=True)

    csv = schedule_df.to_csv(index=False).encode('utf-8')
    st.download_button("📥 スケジュールCSVダウンロード", data=csv, file_name="schedule.csv")

