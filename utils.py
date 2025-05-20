import plotly.express as px
import pandas as pd
from datetime import datetime, timedelta

def plot_gantt(schedule_df):
    if schedule_df.empty:
        return px.scatter()

    df = schedule_df.copy()
    df['Start'] = pd.to_datetime(df['StartTime'])
    df['Finish'] = df['Start'] + pd.to_timedelta(df['DurationMin'], unit='m')
    df['Resource'] = df['TankID']

    fig = px.timeline(df, x_start="Start", x_end="Finish", y="Resource", color="JobID", title="ガントチャート")
    fig.update_yaxes(autorange="reversed")
    return fig
