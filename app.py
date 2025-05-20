import streamlit as st
import pandas as pd
from datetime import date
from scheduler import optimize_schedule
from utils import plot_gantt

st.set_page_config(page_title="Plating Scheduler", layout="wide")
st.title("🔧 めっき工程スケジューラ（Streamlit + OR-Tools 最適化）")

st.markdown("""
本アプリは、**品物リスト、作業者設定、槽（タンク）設定**に基づいて、  
めっき工程の Soak → Plating → Rinse の3工程を自動で最適スケジュール化します。
""")

# 日付選択
start_date = st.date_input("📅 スケジュール開始日", value=date.today())

# ファイルアップロード
st.subheader("📤 入力CSVのアップロード")
uploaded_jobs = st.file_uploader("📦 品物リストCSV（JobID, PlatingType, PlatingMin, 入槽時間, 出槽時間）", type="csv")
uploaded_workers = st.file_uploader("👷‍♂️ 作業者リストCSV（勤務帯・出勤・担当槽など）", type="csv")
uploaded_sos = st.file_uploader("🛢 槽リストCSV（SoID, 種類, PlatingType, 稼働状態）", type="csv")

# 実行ボタン
if st.button("🚀 スケジュール最適化を実行") and uploaded_jobs and uploaded_workers and uploaded_sos:
    try:
        jobs_df = pd.read_csv(uploaded_jobs)
        workers_df = pd.read_csv(uploaded_workers)
        sos_df = pd.read_csv(uploaded_sos)

        schedule_df = optimize_schedule(jobs_df, workers_df, sos_df, start_date)
        st.success("✅ 最適スケジュールが作成されました")

        st.subheader("📋 スケジュール一覧")
        st.dataframe(schedule_df)

        st.subheader("🗂 ガントチャート表示")
        fig = plot_gantt(schedule_df)
        st.plotly_chart(fig, use_container_width=True)

        csv = schedule_df.to_csv(index=False).encode("utf-8")
        st.download_button("📥 スケジュールCSVダウンロード", csv, "schedule.csv", mime="text/csv")

    except Exception as e:
        st.error(f"❌ スケジューリング中にエラーが発生しました: {e}")

else:
    st.info("⬆️ 上の3つのCSVファイルをすべてアップロードしてください")
