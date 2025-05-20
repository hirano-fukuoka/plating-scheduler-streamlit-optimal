import plotly.express as px
import pandas as pd
from datetime import datetime, timedelta

def plot_gantt(schedule_df):
    if schedule_df.empty:
        return px.scatter()

    df = schedule_df.copy()

    # 各工程の時間を分割
    rows = []
    for _, row in df.iterrows():
        start = pd.to_datetime(row["StartTime"])
        soak = timedelta(minutes=row["SoakMin"])
        plate = timedelta(minutes=row["DurationMin"])
        rinse = timedelta(minutes=row["RinseMin"])

        rows.append({
            "JobID": row["JobID"],
            "TankID": row["TankID"],
            "Phase": "Soak",
            "Start": start,
            "Finish": start + soak
        })
        rows.append({
            "JobID": row["JobID"],
            "TankID": row["TankID"],
            "Phase": "Plating",
            "Start": start + soak,
            "Finish": start + soak + plate
        })
        rows.append({
            "JobID": row["JobID"],
            "TankID": row["TankID"],
            "Phase": "Rinse",
            "Start": start + soak + plate,
            "Finish": start + soak + plate + rinse
        })

    gdf = pd.DataFrame(rows)

    gdf["Label"] = gdf["JobID"].astype(str) + " (" + gdf["Phase"] + ")"

    fig = px.timeline(
        gdf,
        x_start="Start",
        x_end="Finish",
        y="TankID",
        color="Phase",
        hover_name="Label",
        title="めっき工程スケジュール：ガントチャート（Soak/Plating/Rinse）"
    )
    fig.update_yaxes(autorange="reversed")
    fig.update_layout(xaxis_title="時刻", yaxis_title="槽ID")

    return fig
