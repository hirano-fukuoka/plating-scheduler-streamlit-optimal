import streamlit as st
import pandas as pd
from datetime import date
from scheduler import optimize_schedule
from utils import plot_gantt
import plotly.express as px
import numpy as np

st.set_page_config(page_title="Plating Scheduler", layout="wide")
st.title("🔧 めっき工程スケジューラ（Streamlit + OR-Tools）")

start_date = st.date_input("📅 スケジュール開始日", value=date.today())

st.subheader("📤 入力CSVアップロード")
uploaded_jobs = st.file_uploader("📦 品物リスト（JobID, PlatingType, PlatingMin[hr], 入槽時間[hr], 出槽時間[hr]）", type="csv")
uploaded_workers = st.file_uploader("👷 作業者設定", type="csv")
uploaded_sos = st.file_uploader("🛢 槽リスト", type="csv")

if st.button("🚀 スケジュール最適化を実行") and uploaded_jobs and uploaded_workers and uploaded_sos:
    try:
        jobs_df = pd.read_csv(uploaded_jobs)
        workers_df = pd.read_csv(uploaded_workers)
        sos_df = pd.read_csv(uploaded_sos)

        schedule_df = optimize_schedule(jobs_df, workers_df, sos_df, start_date)
        st.success("✅ 最適スケジュールが作成されました")

        st.subheader("📋 スケジュール一覧")
        st.dataframe(schedule_df)

        # 日付抽出
        schedule_df["StartTime"] = pd.to_datetime(schedule_df["StartTime"])
        unique_days = sorted(schedule_df['StartTime'].dt.strftime('%m/%d').unique())
        selected_day = st.selectbox("📆 表示する日付（MM/DD）", unique_days)

        # 作業者選択（任意）
        selected_worker = None
        if "WorkerID" in schedule_df.columns:
            worker_list = sorted(schedule_df["WorkerID"].dropna().unique())
            selected_worker = st.selectbox("👤 作業者フィルタ（任意）", ["（すべて）"] + list(worker_list))
            if selected_worker == "（すべて）":
                selected_worker = None

        # ガントチャート
        st.subheader("🗂 ガントチャート（日別表示）")
        fig = plot_gantt(schedule_df, day_filter=selected_day, worker_filter=selected_worker)
        st.plotly_chart(fig, use_container_width=True)

        # ダッシュボード：合計時間
        st.subheader("📊 工程時間ダッシュボード")

        df = schedule_df.copy()
        df["TotalTimeMin"] = df["SoakMin"] + df["DurationMin"] + df["RinseMin"]

        # Job別
        st.metric("📦 スケジュール済ジョブ数", len(df["JobID"].unique()))
        job_summary = df.groupby("JobID")[["TotalTimeMin"]].sum().reset_index()
        st.dataframe(job_summary)

        # 作業者別
        if "WorkerID" in df.columns:
            st.markdown("👷 **作業者別処理時間**")
            worker_summary = df.groupby("WorkerID")["TotalTimeMin"].sum().reset_index()
            st.bar_chart(worker_summary.set_index("WorkerID"))

            # 📈 作業者×日ヒートマップ
            st.markdown("📅 **作業者 × 日別ヒートマップ**")
            df["Date"] = df["StartTime"].dt.strftime("%m/%d")
            heatmap = df.groupby(["WorkerID", "Date"])["TotalTimeMin"].sum().unstack(fill_value=0)
            st.dataframe(heatmap.style.background_gradient(cmap="Blues", axis=None))

        # タンク別
        st.markdown("🛢 **タンク別合計処理時間（分）**")
        tank_summary = df.groupby("TankID")["TotalTimeMin"].sum().reset_index()
        st.bar_chart(tank_summary.set_index("TankID"))

        # タンク稼働率
        st.markdown("📈 **タンク稼働率（336スロット基準）**")
        TOTAL_SLOTS = 336
        tank_summary["稼働率（％）"] = (tank_summary["TotalTimeMin"] / (TOTAL_SLOTS * 30)) * 100
        fig2 = px.pie(tank_summary, names="TankID", values="稼働率（％）", title="タンク稼働率シェア")
        st.plotly_chart(fig2)

        # 🕓 時間軸 × 槽 の稼働分布（折れ線）
        st.markdown("⏱️ **時間帯別 タンク稼働量（累積）**")
        df["HourBlock"] = df["StartTime"].dt.floor("H")
        tank_time = df.groupby(["HourBlock", "TankID"])["TotalTimeMin"].sum().reset_index()
        fig3 = px.line(tank_time, x="HourBlock", y="TotalTimeMin", color="TankID", title="時間帯別 タンク稼働推移")
        st.plotly_chart(fig3, use_container_width=True)

        # 未処理ジョブの可視化
        st.subheader("❗ 未処理ジョブ一覧")
        assigned_jobs = set(schedule_df["JobID"].astype(str).unique())
        all_jobs = set(jobs_df["JobID"].astype(str).unique())
        unassigned_jobs = sorted(list(all_jobs - assigned_jobs))

        if unassigned_jobs:
            st.warning(f"未スケジュールジョブ数：{len(unassigned_jobs)}")
            st.write(jobs_df[jobs_df["JobID"].astype(str).isin(unassigned_jobs)])
        else:
            st.success("すべてのジョブがスケジュールされました ✅")

        # CSV出力
        csv = schedule_df.to_csv(index=False).encode("utf-8")
        st.download_button("📥 スケジュールCSVダウンロード", csv, "schedule.csv", mime="text/csv")

    except Exception as e:
        st.error(f"❌ エラー発生: {e}")
else:
    st.info("⬆️ 上の3つのCSVファイルをすべてアップロードし、ボタンを押してください")
