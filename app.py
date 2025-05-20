import streamlit as st
import pandas as pd
from datetime import date
from scheduler import optimize_schedule
from utils import plot_gantt
import traceback

st.set_page_config(page_title="めっき工程スケジューラ", layout="wide")

st.title("🧪 めっき工程スケジューラ（Streamlit + OR-Tools）")

st.sidebar.header("📅 スケジュール条件")

start_date = st.sidebar.date_input("スケジュール開始日", value=date(2025, 5, 20))

uploaded_job = st.sidebar.file_uploader("📎 品物リストCSV", type=["csv"])
uploaded_so = st.sidebar.file_uploader("📎 槽リストCSV", type=["csv"])
uploaded_worker = st.sidebar.file_uploader("📎 作業者リストCSV", type=["csv"])

if uploaded_job and uploaded_so and uploaded_worker:
    try:
        jobs_df = pd.read_csv(uploaded_job)
        sos_df = pd.read_csv(uploaded_so)
        workers_df = pd.read_csv(uploaded_worker)

        st.subheader("🧾 品物一覧")
        st.dataframe(jobs_df)

        st.subheader("🛢 槽一覧")
        st.dataframe(sos_df)

        st.subheader("👷 作業者一覧")
        st.dataframe(workers_df)

        if st.button("📌 スケジュール作成"):
            try:
                schedule_df = optimize_schedule(jobs_df, workers_df, sos_df, pd.to_datetime(start_date))

                if 'StartTime' not in schedule_df.columns or schedule_df.empty:
                    st.warning("⚠ スケジュールが空です。診断情報をご確認ください。")
                else:
                    schedule_df["StartTime"] = pd.to_datetime(schedule_df["StartTime"])
                    schedule_df["EndTime"] = schedule_df["StartTime"] + pd.to_timedelta(schedule_df["DurationMin"], unit="m")

                    st.subheader("📋 スケジュール一覧")
                    st.dataframe(schedule_df)

                    st.subheader("📊 ガントチャート")
                    fig = plot_gantt(schedule_df)
                    st.plotly_chart(fig, use_container_width=True)

                    csv = schedule_df.to_csv(index=False).encode("utf-8")
                    st.download_button("📥 スケジュールCSVダウンロード", csv, "schedule.csv", mime="text/csv")

            except Exception as e:
                st.error(f"❌ スケジュール処理中にエラーが発生しました: {e}")
                st.code(traceback.format_exc())
    except Exception as e:
        st.error(f"❌ データ読み込み時にエラーが発生しました: {e}")
        st.code(traceback.format_exc())
else:
    st.info("左側から3つのCSVファイル（品物・槽・作業者）をすべてアップロードしてください。")
