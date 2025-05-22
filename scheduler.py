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

    # 槽リスト（稼働中のみ）
    so_dict = {
        str(row['SoID']).strip(): row for _, row in sos_df.iterrows()
        if str(row.get('Status', '')).strip() == '稼働中'
    }
    all_so_ids = sorted(so_dict.keys())
    tank_id_to_idx = {soid: idx for idx, soid in enumerate(all_so_ids)}

    # 作業者可用スロット
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

    # ジョブ定義
    jobs = []
    excluded_jobs = []
    for i, job in jobs_df.iterrows():
        job_id = str(job.get('JobID', f"job_{i}")).strip()
        try:
            plating_min_raw = job['PlatingMin']
            soak_time_raw = job['入槽時間']
            rinse_time_raw = job['出槽時間']
        
            if pd.isna(plating_min_raw) or str(plating_min_raw).strip() == "":
                raise ValueError("PlatingMin is empty")
            if pd.isna(soak_time_raw) or str(soak_time_raw).strip() == "":
                raise ValueError("入槽時間 is empty")
            if pd.isna(rinse_time_raw) or str(rinse_time_raw).strip() == "":
                raise ValueError("出槽時間 is empty")
        
            soak = int(float(str(soak_time_raw).strip())) // SLOT_MIN
            duration = int(float(str(plating_min_raw).strip()) * 60) // SLOT_MIN
            rinse = int(float(str(rinse_time_raw).strip())) // SLOT_MIN
        
        except Exception as e:
            excluded_jobs.append({
                "JobID": job_id,
                "Category": "time_parse_error",
                "Reason": f"{job_id}: ❌ 時間変換エラー - {e}"
            })
            continue

        job_type = str(job.get('PlatingType', '')).strip()
        required_type = str(job.get('RequiredSoType', '')).strip()
        valid_sos = [
            soid for soid, row in so_dict.items()
            if job_type == str(row.get('PlatingType', '')).strip()
            and (required_type == '' or required_type == str(row.get('SoType', row.get('種類', ''))).strip())
        ]
        if not valid_sos:
            excluded_jobs.append({
                "JobID": job_id,
                "Category": "no_valid_tank",
                "Reason": f"{job_id}: 条件に合うタンクが存在しません"
            })
            continue

        soak_workers = int(so_dict[valid_sos[0]].get('SoakWorker', 1))
        rinse_workers = int(so_dict[valid_sos[0]].get('RinseWorker', 1))

        jobs.append(dict(
            index=i,
            JobID=job_id,
            soak=soak,
            duration=duration,
            rinse=rinse,
            valid_sos=valid_sos,
            PlatingType=job_type,
            SoakWorker=soak_workers,
            RinseWorker=rinse_workers
        ))
    assigned = []
    job_vars = []
    tank_intervals = {soid: [] for soid in all_so_ids}
    used_slots = [0] * TOTAL_SLOTS  # 負荷率算出用

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
    # NoOverlap: 各タンク内の工程は重ならない
    for soid, interval_list in tank_intervals.items():
        all_intervals = []
        for sel, soak_i, plate_i, rinse_i, pres, job in interval_list:
            all_intervals += [soak_i, plate_i, rinse_i]
        if all_intervals:
            model.AddNoOverlap(all_intervals)

    # 作業者スロット制約
    for t in range(TOTAL_SLOTS):
    demand = []
    for j in job_vars:
        is_soak = model.NewBoolVar(f"is_soak_{j['JobID']}_{t}")

        # ① 比較用のブール変数を用意
        cond_before_soak = model.NewBoolVar(f"before_soak_{j['JobID']}_{t}")
        cond_after_soak  = model.NewBoolVar(f"after_soak_{j['JobID']}_{t}")

        # ② 比較条件をそれぞれのBoolVarに関連付け
        model.Add(t < j['start']).OnlyEnforceIf(cond_before_soak)
        model.Add(t >= j['start']).OnlyEnforceIf(cond_before_soak.Not())

        model.Add(t >= j['start'] + j['soak']).OnlyEnforceIf(cond_after_soak)
        model.Add(t < j['start'] + j['soak']).OnlyEnforceIf(cond_after_soak.Not())

        # ③ AddBoolOr に BoolVar を渡す（is_soakがTrueでない場合＝Soak時間外なら）
        model.AddBoolOr([cond_before_soak, cond_after_soak]).OnlyEnforceIf(is_soak.Not())

        # Soak時間中であれば、demand追加（※これが負荷の要素）
        model.Add(is_soak == 1).OnlyEnforceIf(j['pres'])
        demand.append(is_soak * j['SoakWorker'])

    if demand:
        model.Add(sum(demand) <= slot_worker_capacity[t])


            is_rinse = model.NewBoolVar(f"is_rinse_{j['JobID']}_{t}")
            model.Add(t >= j['plating_end']).OnlyEnforceIf(is_rinse)
            model.Add(t < j['plating_end'] + j['rinse']).OnlyEnforceIf(is_rinse)

            temp_rinse_end = model.NewIntVar(0, TOTAL_SLOTS, f"rinse_end_{j['JobID']}_{t}")
            model.Add(temp_rinse_end == j['plating_end'] + j['rinse'])
            model.Add(t >= temp_rinse_end).OnlyEnforceIf(is_rinse.Not())
            model.Add(t < j['plating_end']).OnlyEnforceIf(is_rinse.Not())

            # 人数分だけ需要に追加
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

    # 🎯 最大ジョブ数を目的関数に
    model.Maximize(sum(j['pres'] for j in job_vars))

    # ソルバ実行
    solver = cp_model.CpSolver()
    status = solver.Solve(model)
    # 実行結果の整形
    results = []
    used_slot_map = [0] * TOTAL_SLOTS  # スロット使用状況（作業者負荷算出用）

    if status in [cp_model.OPTIMAL, cp_model.FEASIBLE]:
        for j in job_vars:
            if solver.Value(j['pres']) == 1:
                tank_idx = solver.Value(j['tank'])
                tank_id = all_so_ids[tank_idx]
                soak_start = solver.Value(j['start'])
                soak_end = soak_start + j['soak']
                plate_end = soak_end + j['duration']
                rinse_end = plate_end + j['rinse']

                for t in range(soak_start, soak_end):
                    used_slot_map[t] += j['SoakWorker']
                for t in range(plate_end, rinse_end):
                    used_slot_map[t] += j['RinseWorker']

                results.append({
                    "JobID": j['JobID'],
                    "PlatingType": j['PlatingType'],
                    "TankID": tank_id,
                    "SoakStart": (start_date + timedelta(minutes=soak_start * SLOT_MIN)).strftime("%Y-%m-%d %H:%M"),
                    "SoakEnd": (start_date + timedelta(minutes=soak_end * SLOT_MIN)).strftime("%Y-%m-%d %H:%M"),
                    "PlatingEnd": (start_date + timedelta(minutes=plate_end * SLOT_MIN)).strftime("%Y-%m-%d %H:%M"),
                    "RinseEnd": (start_date + timedelta(minutes=rinse_end * SLOT_MIN)).strftime("%Y-%m-%d %H:%M"),
                    "SoakMin": j['soak'] * SLOT_MIN,
                    "PlatingMin": j['duration'] * SLOT_MIN,
                    "RinseMin": j['rinse'] * SLOT_MIN
                })
            else:
                excluded_jobs.append({
                    "JobID": j['JobID'],
                    "Category": "not_selected",
                    "Reason": f"{j['JobID']}: 最適化で除外（リソース競合）"
                })

    return pd.DataFrame(results), excluded_jobs, worker_slots, used_slot_map


# 👷 作業者ごとの負荷率を表示
def show_worker_load(worker_slots, used_slot_map):
    import streamlit as st
    import pandas as pd

    st.subheader("👷 作業者別 負荷率")
    data = []
    for wid, slots in worker_slots.items():
        total = sum(slots)
        used = sum(1 for i, f in enumerate(slots) if f and used_slot_map[i] > 0)
        load = (used / total * 100) if total else 0
        data.append(dict(WorkerID=wid, 出勤枠=total, 使用枠=used, 負荷率=f"{load:.1f}%"))
    st.dataframe(pd.DataFrame(data))
