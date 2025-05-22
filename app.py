import streamlit as st
import pandas as pd
from datetime import timedelta
import plotly.express as px
from scheduler import optimize_schedule  # 別ファイルならimport（または同一ファイルに関数をコピペ）

st.title("めっき工程スケジューラ【全タンクガントチャート可視化】")

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
        # =============================
        # ▼全タンク使用状況ガントチャート
        # =============================
        # 工程ごとにバーを作成
        gantt_data = []
        for _, row in df_result.iterrows():
            soak_start_dt = pd.to_datetime(row["SoakStart"])
            soak_end_dt = soak_start_dt + pd.Timedelta(minutes=row["SoakMin"])
            plating_start_dt = soak_end_dt
            plating_end_dt = pd.to_datetime(row["PlatingEnd"])
            rinse_start_dt = pd.to_datetime(row["RinseStart"])
            rinse_end_dt = rinse_start_dt + pd.Timedelta(minutes=row["RinseMin"])
            gantt_data += [
                dict(JobID=row["JobID"], 工程="Soak", TankID=row["TankID"], Start=soak_start_dt, End=soak_end_dt),
                dict(JobID=row["JobID"], 工程="Plating", TankID=row["TankID"], Start=plating_start_dt, End=plating_end_dt),
                dict(JobID=row["JobID"], 工程="Rinse", TankID=row["TankID"], Start=rinse_start_dt, End=rinse_end_dt)
            ]

        # 未使用タンクにも空バー
        all_tanks = sos_df['SoID'].astype(str).unique()
        used_tanks = set(df_result['TankID'].astype(str).unique())
        # ガントチャート全体の開始・終了時刻
        if gantt_data:
            min_start = min([g["Start"] for g in gantt_data])
            max_end = max([g["End"] for g in gantt_data])
        else:
            min_start = pd.to_datetime(start_date)
            max_end = min_start + pd.Timedelta(days=7*weeks)

        for tank in set(all_tanks) - used_tanks:
            gantt_data.append(dict(JobID="(未使用)", 工程="未使用", TankID=tank, Start=min_start, End=max_end))

        df_gantt = pd.DataFrame(gantt_data)

        st.subheader("🗓 **全タンクの使用状況ガントチャート**")
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

        # ========================
        # タンクごとの空き時間帯も表示
        # ========================
        st.subheader("● タンクごとの空き時間帯")
        SLOT_MIN = 30
        SLOTS_PER_DAY = 24 * 60 // SLOT_MIN
        SLOTS_PER_WEEK = SLOTS_PER_DAY * 7
        TOTAL_SLOTS = SLOTS_PER_WEEK * weeks
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

        for tank in all_tanks:
            tank_slots = [False] * TOTAL_SLOTS
            for _, row in df_result[df_result["TankID"] == tank].iterrows():
                soak_start = int(((pd.to_datetime(row["SoakStart"]) - pd.to_datetime(start_date)).total_seconds() // 60) // SLOT_MIN)
                soak_end = soak_start + int(row["SoakMin"]) // SLOT_MIN
                plating_end = int(((pd.to_datetime(row["PlatingEnd"]) - pd.to_datetime(start_date)).total_seconds() // 60) // SLOT_MIN)
                rinse_start = int(((pd.to_datetime(row["RinseStart"]) - pd.to_datetime(start_date)).total_seconds() // 60) // SLOT_MIN)
                rinse_end = rinse_start + int(row["RinseMin"]) // SLOT_MIN

                for t in range(soak_start, soak_end):
                    tank_slots[t] = True
                for t in range(soak_end, plating_end):
                    tank_slots[t] = True
                for t in range(rinse_start, rinse_end):
                    tank_slots[t] = True

            free_ranges = find_free_ranges(tank_slots)
            if free_ranges:
                st.write(f"◆ タンク {tank} の空き時間帯:")
                for start, end in free_ranges:
                    st.write(f"{(pd.to_datetime(start_date) + timedelta(minutes=start*SLOT_MIN)).strftime('%Y-%m-%d %H:%M')} ～ {(pd.to_datetime(start_date) + timedelta(minutes=end*SLOT_MIN)).strftime('%Y-%m-%d %H:%M')}")
            else:
                st.write(f"◆ タンク {tank} は空き枠なし！")
    else:
        st.warning("スケジュールが空です。診断情報をご確認ください。")
else:
    st.info("入力ファイルを3つすべてアップロードしてください。")
