from ortools.sat.python import cp_model
import pandas as pd
from datetime import timedelta

def optimize_schedule(jobs_df, workers_df, sos_df, start_date):
    model = cp_model.CpModel()

    SLOT_MIN = 30
    SLOTS_PER_DAY = 24 * 60 // SLOT_MIN
    TOTAL_SLOTS = SLOTS_PER_DAY * 7

    # 稼働中の槽のみ使用
    so_dict = {row['SoID']: row for _, row in sos_df.iterrows() if row['Status'] == '稼働中'}

    # 作業者スロット定義
    worker_slots = {}
    global_workable_slots = [False] * TOTAL_SLOTS
    for _, w in workers_df.iterrows():
        wid = w['WorkerID']
        slots = [False] * TOTAL_SLOTS
        for d in range(7):
            if w[f'Day{d+1}'] == '〇':
                s = int(float(w['StartHour']) * 2) + d * SLOTS_PER_DAY
                e = int(float(w['EndHour']) * 2) + d * SLOTS_PER_DAY
                for t in range(s, e):
                    slots[t] = True
                    global_workable_slots[t] = True
        worker_slots[wid] = slots

    assigned = []
    all_intervals = []
    job_results = []

    for i, job in jobs_df.iterrows():
        try:
            soak = int(float(job['入槽時間']) * 60) // SLOT_MIN
            duration = int(float(job['PlatingMin']) * 60) // SLOT_MIN
            rinse = int(float(job['出槽時間']) * 60) // SLOT_MIN
        except:
            continue  # 数値変換失敗時スキップ

        valid_sos = [soid for soid, row in so_dict.items() if row['PlatingType'] == job['PlatingType']]
        if not valid_sos:
            continue

        pres = model.NewBoolVar(f"assigned_{i}")
        start = model.NewIntVar(0, TOTAL_SLOTS - soak - duration - rinse, f"start_{i}")
        soak_end = model.NewIntVar(0, TOTAL_SLOTS, f"soak_end_{i}")
        plate_end = model.NewIntVar(0, TOTAL_SLOTS, f"plate_end_{i}")
        rinse_end = model.NewIntVar(0, TOTAL_SLOTS, f"rinse_end_{i}")

        soak_int = model.NewOptionalIntervalVar(start, soak, soak_end, pres, f"soak_{i}")
        plate_start = soak_end
        plate_int = model.NewOptionalIntervalVar(plate_start, duration, plate_end, pres, f"plate_{i}")
        rinse_start = plate_end
        rinse_int = model.NewOptionalIntervalVar(rinse_start, rinse, rinse_end, pres, f"rinse_{i}")

        # 勤務時間外に Soak/Rinse を配置できない制約
        for t in range(TOTAL_SLOTS - soak - duration - rinse):
            soak_range = list(range(t, t + soak))
            rinse_range = list(range(t + soak + duration, t + soak + duration + rinse))
            combined_range = soak_range + rinse_range
            if any(global_workable_slots[s] == False for s in combined_range):
                model.Add(start != t)

        all_intervals.append((plate_int, valid_sos))
        assigned.append(pres)
        job_results.append((i, start, soak, duration, rinse, pres, job['JobID'], job['PlatingType'], valid_sos[0]))

    # 各槽の NoOverlap 制約
    for soid in so_dict.keys():
        intervals = [iv for iv, soids in all_intervals if soid in soids]
        if intervals:
            model.AddNoOverlap(intervals)

    model.Maximize(sum(assigned))

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = 15.0
    status = solver.Solve(model)

    results = []
    if status in [cp_model.OPTIMAL, cp_model.FEASIBLE]:
        for i, start, soak, plate, rinse, pres, jid, pt, soid in job_results:
            if solver.Value(pres):
                base = solver.Value(start)
                start_dt = start_date + timedelta(minutes=base * SLOT_MIN)
                results.append({
                    "JobID": jid,
                    "PlatingType": pt,
                    "StartTime": start_dt.strftime("%Y-%m-%d %H:%M"),
                    "DurationMin": plate * SLOT_MIN,
                    "TankID": soid,
                    "SoakMin": soak * SLOT_MIN,
                    "RinseMin": rinse * SLOT_MIN
                })

    return pd.DataFrame(results)
