from ortools.sat.python import cp_model
import pandas as pd
from datetime import timedelta

def optimize_schedule(jobs_df, workers_df, sos_df, start_date):
    model = cp_model.CpModel()

    SLOT_MIN = 30
    SLOTS_PER_DAY = 24 * 60 // SLOT_MIN
    TOTAL_SLOTS = SLOTS_PER_DAY * 7

    job_vars = []
    intervals = []
    tank_slots = {row['SoID']: [] for _, row in sos_df.iterrows() if row['Status'] == '稼働中'}

    for i, job in jobs_df.iterrows():
        duration = int(job['DurationMin']) // SLOT_MIN
        start = model.NewIntVar(0, TOTAL_SLOTS - duration, f'start_{i}')
        end = model.NewIntVar(0, TOTAL_SLOTS, f'end_{i}')
        interval = model.NewIntervalVar(start, duration, end, f'interval_{i}')
        job_vars.append((start, end, interval, job['PlatingType'], job['JobID']))
        intervals.append(interval)

    # 槽別のNoOverlap
    for soID in tank_slots:
        type_mask = jobs_df['PlatingType'] == sos_df[sos_df['SoID'] == soID]['PlatingType'].values[0]
        intervals_for_so = [job_vars[i][2] for i in range(len(jobs_df)) if type_mask.iloc[i]]
        if intervals_for_so:
            model.AddNoOverlap(intervals_for_so)

    # 目的関数：最大ジョブ数
    presences = [model.NewBoolVar(f'assigned_{i}') for i in range(len(job_vars))]
    for i, (start, end, interval, _, _) in enumerate(job_vars):
        model.AddPresenceOf(interval, presences[i])
    model.Maximize(sum(presences))

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = 15.0
    status = solver.Solve(model)

    results = []
    if status in [cp_model.OPTIMAL, cp_model.FEASIBLE]:
        for i, (start, end, interval, pt, jobid) in enumerate(job_vars):
            if solver.Value(presences[i]):
                slot = solver.Value(start)
                dmin = int(jobs_df.iloc[i]['DurationMin'])
                day = start_date + timedelta(minutes=slot * SLOT_MIN)
                results.append({
                    'JobID': jobid,
                    'PlatingType': pt,
                    'StartSlot': slot,
                    'StartTime': day.strftime('%Y-%m-%d %H:%M'),
                    'DurationMin': dmin,
                    'TankID': sos_df[sos_df['PlatingType'] == pt]['SoID'].values[0]
                })

    return pd.DataFrame(results)
