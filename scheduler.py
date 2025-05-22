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

    # 槽（稼働中のみ）
    so_dict = {
        str(row['SoID']).strip(): row for _, row in sos_df.iterrows()
        if str(row.get('Status', '')).strip() == '稼働中'
    }
    all_so_ids = sorted(so_dict.keys())
    tank_id_to_idx = {soid: idx for idx, soid in enumerate(all_so_ids)}

    # 作業者勤務可否スロット
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
    slot_worker_capacity = [sum(worker_slots[w][t] for w in worker_slots) for t in range(TOTAL_SLOTS)]

    # ジョブ整形
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

        soak_workers = int(so_dict[valid_sos[0]].get('SoakWorker', 1))
        rinse_workers = int(so_dict[valid_sos[0]].get('RinseWorker', 1))
        jobs.append(dict(
            index=i,
            JobID=job_id,
            soak=soak, duration=duration, rinse=rinse,
            valid_sos=valid_sos,
            PlatingType=job_type,
            SoakWorker=soak_workers,
            RinseWorker=rinse_workers
        ))

    assigned = []
    job_vars = []
    tank_intervals = {soid: [] for soid in all_so_ids}

    for job in jobs:
        total_len = job["soak"] + job["duration"] + job["rinse"]
        latest_start = TOTAL_SLOTS - total_len
        pres = model.NewBoolVar(f"assigned_{job['JobID']}")
        assigned.append(pres)

        start = model.NewIntVar(0, latest_start, f"start_{job['JobID']}")
        tank_choices = [tank_id_to_idx[soid] for soid in job["valid_sos"]]
        tank = model.NewIntVarFromDomain(cp_model.Domain.FromValues(tank_choices), f"tank_{job['JobID']}")

        soak_end    = model.NewIntVar(0, TOTAL_SLOTS, f"soak_end_{job['JobID']}")
        plating_end = model.NewIntVar(0, TOTAL_SLOTS, f"plating_end_{job['JobID']}")
        rinse_end   = model.NewIntVar(0, TOTAL_SLOTS, f"rinse_end_{job['JobID']}")

        soak_int    = model.NewOptionalIntervalVar(start, job['soak'], soak_end, pres, f"soak_{job['JobID']}")
        plating_int = model.NewOptionalIntervalVar(soak_end, job['duration'], plating_end, pres, f"plating_{job['JobID']}")
        rinse_int   = model.NewOptionalIntervalVar(plating_end, job['rinse'], rinse_end, pres, f"rinse_{job['JobID']}")

        model.Add(soak_end    == start + job['soak']).OnlyEnforceIf(pres)
        model.Add(plating_end == soak_end + job['duration']).OnlyEnforceIf(pres)
        model.Add(rinse_end   == plating_end + job['rinse']).OnlyEnforceIf(pres)

        for soid, t_idx in tank_id_to_idx.items():
            sel = model.NewBoolVar(f"sel_{job['JobID']}_{soid}")
            model.Add(tank == t_idx).OnlyEnforceIf(sel)
            model.Add(tank != t_idx).OnlyEnforceIf(sel.Not())
            tank_intervals[soid].append((sel, soak_int, plating_int, rinse_int, pres, job))

        # Soak/Rinse が勤務帯内に収まるかチェック
        for phase, length, offset in [("soak", job['soak'], 0), ("rinse", job['rinse'], job['soak'] + job['duration'])]:
            for t in range(VALID_START_MIN, latest_start + 1):
                time_slots = [t + offset + k for k in range(length)]
                if not all(0 <= s < TOTAL_SLOTS and global_workable_slots[s] for s in time_slots):
                    model.Add(start != t).OnlyEnforceIf(pres)

        job_vars.append(dict(
            JobID=job['JobID'],
            PlatingType=job['PlatingType'],
            start=start,
            soak=job['soak'],
            duration=job['duration'],
            rinse=job['rinse'],
            tank=tank,
            pres=pres,
            soak_int=soak_int,
            plating_int=plating_int,
            rinse_int=rinse_int,
            soak_end=soak_end,
            plating_end=plating_end,
            rinse_end=rinse_end,
            SoakWorker=job['SoakWorker'],
            RinseWorker=job['RinseWorker']
        ))

    # 各タンク内での工程重複を防ぐ（NoOverlap）
    for soid, interval_list in tank_intervals.items():
        all_ints = []
        for sel, soak_i, plate_i, rinse_i, pres, job in interval_list:
            all_ints += [soak_i, plate_i, rinse_i]
        if all_ints:
            model.AddNoOverlap(all_ints)

    # 作業者リソース制約（Soak/Rinse工程ごとの必要人数を反映）
    for t in range(TOTAL_SLOTS):
        demand = []
        for j in job_vars:
            # Soak: t ∈ [start, start + soak)
            is_soak = model.NewBoolVar(f"is_soak_{j['JobID']}_{t}")
            model.Add(t >= j['start']).OnlyEnforceIf(is_soak)
            model.Add(t < j['start'] + j['soak']).OnlyEnforceIf(is_soak)
            model.AddBoolOr([t < j['start'], t >= j['start'] + j['soak']]).OnlyEnforceIf(is_soak.Not())

            # Rinse: t ∈ [plating_end, plating_end + rinse)
            is_rinse = model.NewBoolVar(f"is_rinse_{j['JobID']}_{t}")
            model.Add(t >= j['plating_end']).OnlyEnforceIf(is_rinse)
            model.Add(t < j['plating_end'] + j['rinse']).OnlyEnforceIf(is_rinse)
            model.AddBoolOr([t < j['plating_end'], t >= j['plating_end'] + j['rinse']]).OnlyEnforceIf(is_rinse.Not())

            # 人数分の作業スロットにカウント
            for _ in range(j['SoakWorker']):
                active = model.NewBoolVar(f"active_soak_{j['JobID']}_{t}")
                model.AddBoolAnd([is_soak, j['pres']]).OnlyEnforceIf(active)
                model.AddBoolOr([is_soak.Not(), j['pres'].Not()]).OnlyEnforceIf(active.Not())
                demand.append(active)

            for _ in range(j['RinseWorker']):
                active_r = model.NewBoolVar(f"active_rinse_{j['JobID']}_{t}")
                model.AddBoolAnd([is_rinse, j['pres']]).OnlyEnforceIf(active_r)
                model.AddBoolOr([is_rinse.Not(), j['pres'].Not()]).OnlyEnforceIf(active_r.Not())
                demand.append(active_r)

        if demand:
            model.Add(sum(demand) <= slot_worker_capacity[t])

    # 🎯 目的：最大ジョブ数
    model.Maximize(sum([j['pres'] for j in job_vars]))

    # ソルバ
    solver = cp_model.CpSolver()
    status = solver.Solve(model)

    # 結果の整形
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
                    "TankID": tank_id,
                    "SoakStart": (start_date + timedelta(minutes=soak_start * SLOT_MIN)).strftime("%Y-%m-%d %H:%M"),
                    "SoakEnd": (start_date + timedelta(minutes=soak_end * SLOT_MIN)).strftime("%Y-%m-%d %H:%M"),
                    "PlatingEnd": (start_date + timedelta(minutes=plating_end * SLOT_MIN)).strftime("%Y-%m-%d %H:%M"),
                    "RinseEnd": (start_date + timedelta(minutes=rinse_end * SLOT_MIN)).strftime("%Y-%m-%d %H:%M"),
                    "SoakMin": j['soak'] * SLOT_MIN,
                    "PlatingMin": j['duration'] * SLOT_MIN,
                    "RinseMin": j['rinse'] * SLOT_MIN
                })

    return pd.DataFrame(results)


