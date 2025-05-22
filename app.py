import streamlit as st
import pandas as pd
import plotly.express as px
from scheduler import optimize_schedule

st.title("めっき工程スケジューラ")

jobs_file = st.file_uploader("jobs.csv", type="csv")
sos_file = st.file_uploader("so_template.csv", type="csv")
workers_file = st.file_uploader("workers_template.csv", type="csv")
start_date = st.date_input("スケジュール開始日", value=pd.Timestamp.today())
weeks = st.number_input("スケジュール週数", min_value=1, max_value=4, value=1)

if jobs_file and sos_file and workers_file:
    jobs_df = pd.read_csv(jobs_file)
    sos_df = pd.read_csv(sos_file)
    workers_df = pd.read_csv(workers_file)
    df_result = optimize_schedule(jobs_df, workers_df, sos_df, pd.to_datetime(start_date), weeks=weeks)

    st.subheader("ガントチャート（工程・タンク）")
    gantt_data = []
    for _, row in df_result.iterrows():
        soak_start_dt = pd.to_datetime(row["SoakStart"])
        soak_end_dt = pd.to_datetime(row["SoakEnd"])
        plating_end_dt = pd.to_datetime(row["PlatingEnd"])
        rinse_end_dt = pd.to_datetime(row["RinseEnd"])
        plating_start_dt = soak_end_dt
        rinse_start_dt = plating_end_dt
        gantt_data += [
            dict(JobID=row["JobID"], 工程="Soak", TankID=row["TankID"], Start=soak_start_dt, End=soak_end_dt),
            dict(JobID=row["JobID"], 工程="Plating", TankID=row["TankID"], Start=plating_start_dt, End=plating_end_dt),
            dict(JobID=row["JobID"], 工程="Rinse", TankID=row["TankID"], Start=rinse_start_dt, End=rinse_end_dt)
        ]
    df_gantt = pd.DataFrame(gantt_data)
    fig = px.timeline(
        df_gantt,
        x_start="Start",
        x_end="End",
        y="TankID",
        color="工程",
        hover_data=["JobID"]
    )
    fig.update_yaxes(autorange="reversed")
    st.plotly_chart(fig, use_container_width=True)
else:
    st.info("CSVをすべてアップロードしてください。")
