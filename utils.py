import plotly.express as px
import pandas as pd

def plot_gantt(df):
    # 表示用ラベルを作成
    df = df.copy()
    df["Label"] = df["JobID"] + " (" + df["TankID"] + ")"
    df["Start"] = pd.to_datetime(df["StartTime"])
    df["End"] = df["Start"] + pd.to_timedelta(df["DurationMin"], unit="m")
    df["Resource"] = df["TankID"]

    fig = px.timeline(
        df,
        x_start="Start",
        x_end="End",
        y="Resource",
        color="PlatingType",
        text="JobID",
        hover_data=["JobID", "PlatingType", "TankID", "SoakMin", "RinseMin", "DurationMin"]
    )

    fig.update_yaxes(title="TankID", autorange="reversed")
    fig.update_xaxes(title="日時", tickformat="%m/%d %H:%M")
    fig.update_layout(
        title="めっきスケジュール ガントチャート",
        height=600,
        margin=dict(l=20, r=20, t=50, b=20)
    )

    return fig
