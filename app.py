from ortools.sat.python import cp_model
import pandas as pd
from datetime import timedelta
import streamlit as st
import plotly.express as px

# ...（ここに optimize_schedule 関数を入れてください。既存のままでもOK）...

def find_free_ranges(slot_array):
    free_ranges = []
    in_free = False
    for t, used in enumerate(slot_array):
        if not used and not in_free:
            start = t
            in_free = True
        elif used and in_free:
            end = t
            free_ranges.append((start, end))
            in_free = False
    if in_free:
        free_ranges.append((start, len(slot_array)))
    return free_ranges

# ---- Streamlitメイン処理 ----
st.title("めっき工程スケジューラ（ガント・空き時間可視化つき）")

# ファイルアップロードUI
jobs_file = st.file_uploader("jobs.csvをアップロード", type="csv")
sos_file = st.file_uploader("so_template.csvをアップロード", type="csv")
workers_file = st.file_uploader("workers_template.csvをアップロード", type="csv")
start_date = st.date_input("スケジュール開始日", value=pd.Timestamp.today())
weeks = st.number_input("スケジュール対象週数", min_value=1, max_value=4, value=1)

if jobs_file and sos_file and workers_file:
    jobs_df = pd.read_csv(jobs_file)
    sos_df = pd.read_csv(sos_file)
    workers_df = pd.read_csv(workers_file)
    df_result = optimize_schedule(jobs_df, workers_df, sos_df, pd.to_datetime(start_date), weeks=weeks)
    if df_result.shape[0] > 0:
        st.subheader("🗓 ガントチャート（タンク別/工程別）")
        # ====== ガントチャート用データ作成 ======
        gantt_data = []
        for _, row in df_result.iterrows():
            # Soak
            soak_start_dt = pd.to_datetime(row["SoakStart"])
            soak_end_dt = soak_start_dt + pd.Timedelta(minutes=row["SoakMin"])
            # Plating
            plating_start_dt = soak_end_dt
            plating_end_dt = pd.to_datetime(row["PlatingEnd"])
            # Rinse
            rinse_start_dt = pd.to_datetime(row["RinseStart"])
            rinse_end_dt = rinse_start_dt + pd.Timedelta(minutes=row["RinseMin"])
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

        # ====== 空き時間帯の可視化 ======
        st.subheader("🕳 タンクごとの空き時間帯")
        SLOT_MIN = 30
        SLOTS_PER_DAY = 24 * 60 // SLOT_MIN
        SLOTS_PER_WEEK = SLOTS_PER_DAY * 7
        MAX_WEEKS = 4
        TOTAL_SLOTS = SLOTS_PER_WEEK * weeks

        all_so_ids = df_result['TankID'].unique()
        tank_slots = {tank: [False] * TOTAL_SLOTS for tank in all_so_ids}
        for _, row in df_result.iterrows():
            soak_start = int(((pd.to_datetime(row["SoakStart"]) - pd.to_datetime(start_date)).total_seconds() // 60) // SLOT_MIN)
            soak_end = soak_start + int(row["SoakMin"]) // SLOT_MIN
            plating_end = int(((pd.to_datetime(row["PlatingEnd"]) - pd.to_datetime(start_date)).total_seconds() // 60) // SLOT_MIN)
            rinse_start = int(((pd.to_datetime(row["RinseStart"]) - pd.to_datetime(start_date)).total_seconds() // 60) // SLOT_MIN)
            rinse_end = rinse_start + int(row["RinseMin"]) // SLOT_MIN

            for t in range(soak_start, soak_end):
                tank_slots[row["TankID"]][t] = True
            for t in range(soak_end, plating_end):
                tank_slots[row["TankID"]][t] = True
            for t in range(rinse_start, rinse_end):
                tank_slots[row["TankID"]][t] = True

        for tank in all_so_ids:
            free_ranges = find_free_ranges(tank_slots[tank])
            if free_ranges:
                st.write(f"🔹 タンク {tank} の空き時間帯:")
                for start, end in free_ranges:
                    st.write(f"　{(pd.to_datetime(start_date) + timedelta(minutes=start*SLOT_MIN)).strftime('%Y-%m-%d %H:%M')} ～ {(pd.to_datetime(start_date) + timedelta(minutes=end*SLOT_MIN)).strftime('%Y-%m-%d %H:%M')}")
            else:
                st.write(f"🔹 タンク {tank} は空き枠なし！")

    else:
        st.warning("スケジュールが空です。診断情報をご確認ください。")
else:
    st.info("入力ファイルを3つすべてアップロードしてください。")
