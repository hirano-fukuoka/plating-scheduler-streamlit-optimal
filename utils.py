import plotly.express as px
import pandas as pd
from datetime import datetime, timedelta

def plot_gantt(schedule_df, day_filter=None, worker_filter=None):
    if schedule_df.empty:
        return px.scatter()

    df = schedule_df.copy()
    df['Start'] = pd.to_datetime(df['StartTime'])
    df['Finish'] = df['Start'] + pd.to_timedelta(df['DurationMin'], unit='m')
    df['DateStr'] = df['Start'].dt.strftime('%m/%d')
    df['Hour'] = df['Start'].dt.strftime('%H:%M')
    df['Phase'] = 'Plating'
    df['Label'] = df['JobID'].astype(str) + " (" + df['Phase'] + ")"

    # Soak/Rinse対応（工程を3分割）
    if 'SoakMin' in df.columns and 'RinseMin' in df.columns:
        expanded = []
        for _, row in df.iterrows():
            base = row['Start']
            soak = timedelta(minutes=row['SoakMin'])
            plate = timedelta(minutes=row['DurationMin'])
            rinse = timedelta(minutes=row['RinseMin'])

            expanded += [
                {
                    "JobID": row["JobID"],
                    "TankID": row["TankID"],
                    "Phase": "Soak",
                    "Start": base,
                    "Finish": base + soak,
                    "Worker": row.get("WorkerID", "N/A")
                },
                {
                    "JobID": row["JobID"],
                    "TankID": row["TankID"],
                    "Phase": "Plating",
                    "Start": base + soak,
                    "Finish": base + soak + plate,
                    "Worker": row.get("WorkerID", "N/A")
                },
                {
                    "JobID": row["JobID"],
                    "TankID": row["TankID"],
                    "Phase": "Rinse",
                    "Start": base + soak + plate,
                    "Finish": base + soak + plate + rinse,
                    "Worker": row.get("WorkerID", "N/A")
                }
            ]

        df = pd.DataFrame(expanded)
        df['DateStr'] = df['Start'].dt.strftime('%m/%d')
        df['Label'] = df['JobID'] + " (" + df['Phase'] + ")"

    # 日付フィルタ
    if day_filter:
        df = df[df['DateStr'] == day_filter]

    # 作業者フィルタ（将来的に使用）
    if worker_filter:
        df = df[df['Worker'] == worker_filter]

    fig = px.timeline(
        df,
        x_start="Start",
        x_end="Finish",
        y="TankID",
        color="Phase",
        hover_name="Label",
        title=f"めっき工程スケジュール（{day_filter if day_filter else '全体'}）"
    )
    fig.update_yaxes(autorange="reversed")
    fig.update_layout(
        xaxis_title="時刻",
        yaxis_title="槽（TankID）",
        xaxis_tickformat="%m/%d %H:%M"
    )
    return fig
