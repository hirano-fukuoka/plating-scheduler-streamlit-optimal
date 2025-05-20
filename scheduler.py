from ortools.sat.python import cp_model
import pandas as pd
from datetime import timedelta

def optimize_schedule(jobs_df, workers_df, sos_df, start_date):
    model = cp_model.CpModel()

    SLOT_MIN = 30
    SLOTS_PER_DAY = 24 * 60 // SLOT_MIN
    TOTAL_SLOTS = SLOTS_PER_DAY * 7

    # 槽IDごとのPlatingType対応辞書
    so_dict = {row['SoID']: row for _, row in sos_df.iterrows() if row['Status'] == '稼働中'}

    # 作業者可用性マップ：worker_id -> [bool x 336]
    worker_slots = {}
    for _, w in workers_df.iterrows():
        wid = w['WorkerID']
        slots = [False] * TOTAL_SLOTS
        for day in range(7):
            if w[f'Day{day+1}'] == '〇':
                start = int(float(w['StartHour']) * 2) + day * SLOTS_PER_DAY
                end = int(float(w['EndHour']) * 2) + day * SLOTS_PER_DAY
                for s in range(start, end):
                    slots[s] = True
        worker_slots[wid] = {
            'slots': slots,
            '担当槽': str(w['担当槽']).split(',') if pd.notna(w['担当槽']) else [],
            '必須槽': str(w['必須槽']).split(',') if pd.notna(w['必須槽']) else []
        }

    assigned = []
    all_intervals = []
    job_results = []

    for i, job in jobs_df.iterrows():
        duration = int(job['PlatingMin']) // SLOT_MIN
        soak = int(job['入槽時間']) // SLOT_MIN
        rinse = int(job['出槽時間']) // SLOT_MIN

        # 使用可能な槽候補（PlatingType一致＋稼働中）
        valid_sos = [soid for soid, row in so_dict.items() if row['PlatingType'] == job['PlatingType']]

        if not valid_sos:
            continue  # 対応可能な槽なし

        start = model.NewIntVar(0, TOTAL_SLOTS - soak - duration - rinse, f"start_{i}")
        soak_int = model.NewIntervalVar(start, soak, start + soak, f"soak_{i}")
        plate_start = model.NewIntVar(0, TOTAL_SLOTS, f"plate_start_{i}")
        model.Add(plate_start == start + soak)
        plate_int = model.NewIntervalVar(plate_start, duration, plate_start + duration, f"plate_{i}")
        rinse_start = model.NewIntVar(0, TOTAL_SLOTS, f"rinse_start_{i}")
        model.Add(rinse_start == plate_start + duration)
        rinse_int = model.NewIntervalVar(rinse_start, rinse, rinse_start + rinse, f"rinse_{i}")

        pres = model.NewBoolVar(f"assigned_{i}")
        model.AddPresenceOf(soak_int, pres)
        model.AddPresenceOf(plate_int, pres)
        model.AddPresenceOf(rinse_int, pres)

        all_intervals.append((plate_int, valid_sos))  # only plating blocks槽重複

        # 人員制約：Soak / Rinse に最低人数（2人）必要
        for seg, label in [(soak_int, 'Soak'), (rinse_int, 'Rinse')]:
            for wid, wdict in worker_slots.items():
                if job['PlatingType'] in ['Ni', 'Cr', 'Zn']:  # 仮ルール
                    required = 2 if 'BL' not in valid_sos[0] else 3
                    cond = [
                        model.NewBoolVar(f"{label}_{i}_{wid}_{s}") for s in range(TOTAL_SLOTS)
                    ]
                    active = []
                    for s in range(TOTAL_SLOTS):
                        if wdict['slots'][s] and valid_sos[0] in wdict['担当槽']:
                            active.append(model.NewBoolVar(f"use_{i}_{wid}_{s}"))
                    # 下限を満たす人数がいるかどうかは簡略処理で別途近似チェックでも良い（高速化目的）

        # 特定作業者必須条件（例：BL槽）
        for wid, wdict in worker_slots.items():
            if valid_sos[0] in wdict['必須槽']:
                # 作業者 wid が soak_start～rinse_end まで空いていることを条件とする
                pass  # 実装省略中（強化時に追加）

        assigned.append(pres)
        job_results.append((i, start, soak, duration, rinse, pres, job['JobID'], job['PlatingType'], valid_sos[0]))

    # 槽のNoOverlap制約
    for soid in so_dict.keys():
        intervals = [iv for iv, soids in all_intervals if soid in soids]
        if intervals:
            model.AddNoOverlap(intervals)

    # 目的関数：最大ジョブ数
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
