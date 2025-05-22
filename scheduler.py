from ortools.sat.python import cp_model
import pandas as pd
from datetime import timedelta

def optimize_schedule(jobs_df, workers_df, sos_df, start_date, weeks=1):
    model = cp_model.CpModel()

    SLOT_MIN = 30
    SLOTS_PER_DAY = 24 * 60 // SLOT_MIN
    SLOTS_PER_WEEK = SLOTS_PER_DAY * 7
    MAX_WEEKS = 4
    TOTAL_SLOTS = SLOTS_PER_WEEK * MAX_WEEKS

    VALID_START_MIN = 0
    VALID_START_MAX = SLOTS_PER_WEEK * weeks - 1

    # 槽辞書
    so_dict = {
        str(row['SoID']).strip(): row for _, row in sos_df.iterrows()
        if str(row.get('Status', '')).strip() == '稼働中'
    }
    all_so_ids = sorted(so_dict.keys())
    tank_id_to_idx = {soid: idx for idx, soid in enumerate(all_so_ids)}
    num_tanks = len(all_so_ids)

    # 作業者可用性
    worker_slots = {}
    for _, w in workers_df.iterrows():
        wid = w['WorkerID']
        slots = [False] * TOTAL_SLOTS
        for d in range(7):
            if str(w.get(f'Day{d+1}', '')).strip() == '〇':
                for week in range(MAX_WEEKS):
                    s = int(float(w['StartHour']) * 2) + d * SLOTS_PER_DAY + week * SLOTS_PER_WEEK
                    e = int(float(w['EndHour']) * 2) + d * SLOTS_PER_DAY + week * SLOTS_PER_WEEK
                    for t in range(s, e):
                        slots[t] = True
        worker_slots[wid] = slots

    global_workable_slots = [any(worker_slots[w][t] for w in worker_slots) for t in range(TOTAL_SLOTS)]
    slot_worker_capacity = [sum(worker_slots[wid][t] for wid in worker_slots) for t in range(TOTAL_SLOTS)]

    jobs = []
    for i, job in jobs_df.iterrows():
        job_id = str(job.get('JobID', f"job_{i}")).strip()
        try:
            soak = int(float(job['入槽時間'])) // SLOT_MIN
            duration = int(float(job['PlatingMin']) * 60) // SLOT_MIN
            rinse = int(float(job['出槽時間'])) // SLOT_MIN
        except Exception as e:
            continue

        job_type = str(job.get('PlatingType', '')).strip()
        required_type = str(job.get('RequiredSoType', '')).strip()

        valid_sos = [
            soid for soid, row in so_dict.items()
            if job_type == str(row.get('PlatingType', '')).strip()
            and (required_type == '' or required_type == str(row.get('SoType', row.get('種類', ''))).strip())
        ]
        if not valid_sos:
            continue

        jobs.append(dict(
            index=i,
            JobID=job_id,
            soak=soak, duration=duration, rinse=rinse,
            valid_sos=valid_sos,
            PlatingType=job_type
        ))

    assigned = []
    job_vars = []
    tank_intervals = {soid: [] for soid in all_so_ids}
    job_to_var = {}

    for job in jobs:
        n_start = VALID_START_MAX - (job["soak"] + job["duration"] + job["rinse"]) + 1
        if n_start < VALID_START_MIN:  # too large job
            continue

        pres = model.NewBoolVar(f"assigned_{job['JobID']}")
        assigned.append(pres)

        # 開始スロット
        start = model.NewIntVar(VALID_START_MIN, n_start, f"start_{job['JobID']}")
        # タンク選択（有効なタンクのインデックスから選択）
        tank_choices = [tank_id_to_idx[soid] for soid in job["valid_sos"]]
        tank = model.NewIntVarFromDomain(cp_model.Domain.FromValues(tank_choices), f"tank_{job['JobID']}")

        soak_end   = model.NewIntVar(0, TOTAL_SLOTS, f"soak_end_{job['JobID']}")
        plating_end= model.NewIntVar(0, TOTAL_SLOTS, f"plating_end_{job['JobID']}")
        rinse_end  = model.NewIntVar(0, TOTAL_SLOTS, f"rinse_end_{job['JobID']}")

        # 工程区間
        soak_int    = model.NewOptionalIntervalVar(start, job['soak'], soak_end, pres, f"soak_{job['JobID']}")
        plating_int = model.NewOptionalIntervalVar(soak_end, job['duration'], plating_end, pres, f"plating_{job['JobID']}")
        rinse_int   = model.NewOptionalIntervalVar(plating_end, job['rinse'], rinse_end, pres, f"rinse_{job['JobID']}")

        # 工程区間のつなぎ
        model.Add(soak_end    == start + job['soak']).OnlyEnforceIf(pres)
        model.Add(plating_end == soak_end + job['duration']).OnlyEnforceIf(pres)
        model.Add(rinse_end   == plating_end + job['rinse']).OnlyEnforceIf(pres)

        # ジョブごとの「タンク」割当によってIntervalを対応するタンクグループに割り当て
        for soid, t_idx in tank_id_to_idx.items():
            sel = model.NewBoolVar(f"sel_{job['JobID']}_{soid}")
            model.Add(tank == t_idx).OnlyEnforceIf(sel)
            model.Add(tank != t_idx).OnlyEnforceIf(sel.Not())
            # 使う場合のみ、interval追加（NoOverlapのため）
            tank_intervals[soid].append((sel, soak_int, plating_int, rinse_int, pres))

        # 勤務帯内配置（Soak/Rinseとも全枠勤務帯で）
        for phase, length, offset in [("soak", job['soak'], 0), ("rinse", job['rinse'], job['soak'] + job['duration'])]:
            for t in range(VALID_START_MIN, n_start + 1):
                idxs = [t + offset + k for k in range(length)]
                ok = all(0 <= idx < TOTAL_SLOTS and global_workable_slots[idx] for idx in idxs)
                model.Add(start != t).OnlyEnforceIf(pres.Not()).OnlyEnforceIf(model.NewBoolVar(f"forbid_{phase}_{job['JobID']}_{t}")) if not ok else None

        # 保存
        job_vars.append(dict(
            JobID=job['JobID'],
            PlatingType=job['PlatingType'],
            start=start,
            soak=job['soak'],
            duration=job['duration'],
            rinse=job['rinse'],
            tank=tank,
            pres=pres,
            soak_int=soak_int, plating_int=plating_int, rinse_int=rinse_int,
            soak_end=soak_end, plating_end=plating_end, rinse_end=rinse_end
        ))
        job_to_var[job['JobID']] = job_vars[-1]

    # 各タンクで工程区間重複禁止
    for soid, interval_list in tank_intervals.items():
        soak_ints = [v[1] for v in interval_list]
        plating_ints = [v[2] for v in interval_list]
        rinse_ints = [v[3] for v in interval_list]
        pres_list = [v[4] for v in interval_list]
        all_ints = []
        for v in interval_list:
            # presでONの場合のみ
            all_ints += [v[1], v[2], v[3]]
        if all_ints:
            model.AddNoOverlap(all_ints)

    # 作業者リソース制約（勤務枠合計）
    for t in range(TOTAL_SLOTS):
        demand = []
        for j in job_vars:
            # Soak
            model.Add(j['pres'] == 0).OnlyEnforceIf(model.NewBoolVar(f"skip_{j['JobID']}_{t}")).OnlyEnforceIf(
                model.NewBoolVar(f"out_{j['JobID']}_{t}")) if not (t >= j['start'] and t < j['start'] + j['soak']) else None
            if_expr = j['pres'] * (1 if (t >= j['start'] and t < j['start'] + j['soak']) else 0)
            demand.append(if_expr)
            # Rinse
            demand.append(j['pres'] * (1 if (t >= j['plating_end'] and t < j['plating_end'] + j['rinse']) else 0))
        if demand:
            model.Add(sum(demand) <= slot_worker_capacity[t])

    # 最大化：ジョブ数
    model.Maximize(sum(assigned))

    # ソルバー
    solver = cp_model.CpSolver()
    status = solver.Solve(model)

    # 結果
    results = []
    if status in [cp_model.OPTIMAL, cp_model.FEASIBLE]:
        for j in job_vars:
            if solver.Value(j['pres']) == 1:
                tank_idx = solver.Value(j['tank'])
                tank_id = all_so_ids[tank_idx]
                soak_start = solver.Value(j['start'])
                soak_end = soak_start + j['soak']
                plating_end = soak_end + j['duration']
                rinse_end = plating_end + j['rinse']
                results.append({
                    "JobID": j['JobID'],
                    "PlatingType": j['PlatingType'],
                    "SoakStart": (start_date + timedelta(minutes=soak_start * SLOT_MIN)).strftime("%Y-%m-%d %H:%M"),
                    "SoakEnd": (start_date + timedelta(minutes=soak_end * SLOT_MIN)).strftime("%Y-%m-%d %H:%M"),
                    "PlatingEnd": (start_date + timedelta(minutes=plating_end * SLOT_MIN)).strftime("%Y-%m-%d %H:%M"),
                    "RinseEnd": (start_date + timedelta(minutes=rinse_end * SLOT_MIN)).strftime("%Y-%m-%d %H:%M"),
                    "TankID": tank_id,
                    "SoakMin": j['soak'] * SLOT_MIN,
                    "PlatingMin": j['duration'] * SLOT_MIN,
                    "RinseMin": j['rinse'] * SLOT_MIN,
                })
    df_result = pd.DataFrame(results)
    return df_result
