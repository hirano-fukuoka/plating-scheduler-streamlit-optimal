import streamlit as st
import pandas as pd
from scheduler import optimize_schedule, show_worker_load
import plotly.express as px

st.title("🧪 めっき工程スケジューラ（完全版）")

# --- 入力 ---
jobs_file = st.file_uploader("📂 jobs.csv", type="csv")
sos_file = st.file_uploader("📂 so_template.csv", type="csv")
workers_file = st.file_uploader("📂 workers_template.csv", type="csv")
start_date = st.date_input("📅 スケジュール開始日", value=pd.Timestamp.today())
weeks = st.number_input("📆 スケジュール対象週数", min_value=1, max_value=4, value=1)

# --- 実行 ---
if st.button("🧠 スケジューリング実行"):
    if not (jobs_file and sos_file and workers_file):
        st.error("すべてのCSVファイルをアップロードしてください。")
    else:
        jobs_df = pd.read_csv(jobs_file)
        sos_df = pd.read_csv(sos_file)
        workers_df = pd.read_csv(workers_file)

        st.info("スケジューリングを実行中...")
        df_result, excluded_jobs, worker_slots, used_slots = optimize_schedule(
            jobs_df, workers_df, sos_df, pd.to_datetime(start_date), weeks=weeks
        )

        # --- スケジュール結果 ---
        st.subheader("📈 スケジュール結果")
        st.dataframe(df_result)

        # --- ガントチャート ---
        st.subheader("📊 工程ガントチャート（タンク別）")
        gantt_data = []
        for _, row in df_result.iterrows():
            job = row["JobID"]
            tank = row["TankID"]
            gantt_data += [
                dict(JobID=job, 工程="Soak", TankID=tank, Start=pd.to_datetime(row["SoakStart"]), End=pd.to_datetime(row["SoakEnd"])),
                dict(JobID=job, 工程="Plating", TankID=tank, Start=pd.to_datetime(row["SoakEnd"]), End=pd.to_datetime(row["PlatingEnd"])),
                dict(JobID=job, 工程="Rinse", TankID=tank, Start=pd.to_datetime(row["PlatingEnd"]), End=pd.to_datetime(row["RinseEnd"]))
            ]
        df_gantt = pd.DataFrame(gantt_data)
        fig = px.timeline(df_gantt, x_start="Start", x_end="End", y="TankID", color="工程", text="JobID")
        fig.update_yaxes(autorange="reversed")
        st.plotly_chart(fig, use_container_width=True)

        # --- 除外ジョブ ---
        if excluded_jobs:
            st.subheader("❌ 除外ジョブ一覧")
            df_ex = pd.DataFrame(excluded_jobs)
            st.dataframe(df_ex)
            st.download_button("⬇ 除外ジョブCSV", df_ex.to_csv(index=False), file_name="excluded_jobs.csv")

            st.subheader("📊 除外理由カテゴリ集計")
            cat_sum = df_ex['Category'].value_counts().reset_index()
            cat_sum.columns = ['理由', '件数']
            st.bar_chart(cat_sum.set_index("理由"))

        # --- 作業者負荷率 ---
        st.subheader("👷 作業者負荷率分析")
        show_worker_load(worker_slots, used_slots)
