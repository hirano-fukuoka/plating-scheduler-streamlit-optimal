from ortools.sat.python import cp_model
import pandas as pd
from datetime import timedelta
import streamlit as st

def optimize_schedule(jobs_df, workers_df, sos_df, start_date):
    model = cp_model.CpModel()

    SLOT_MIN = 30
    SLOTS_PER_DAY = 24 * 60 // SLOT_MIN
    TOTAL_SLOTS = SLOTS_PER_DAY * 7

    so_dict = {
        str(row['SoID']).strip(): row for _, row in sos_df.iterrows()
        if str(row.get('Status', '')).strip() == '稼働中'
    }
    all_so_ids = set(so_dict.keys())

    global_workable_slots = [False] * TOTAL_SLOTS
    worker_total_slots = {}
    for _, w in workers_df.iterrows():
        wid = w['WorkerID']
        total = 0
        for d in range(7):
            if str(w.get(f'Day{d+1}', '')).strip() == '〇':
                s = int(float(w['StartHour']) * 2) + d * SLOTS_PER_DAY
                e = int(float(w['EndHour']) * 2) + d * SLOTS_PER_DAY
                total += e - s
                for t in range(s, e):
                    global_workable_slots[t] = True
        worker_total_slots[wid] = total
    worker_usage = {wid: 0 for wid in worker_total_slots}

    assigned = []
    all_intervals = []
    job_results = []
    excluded_jobs = []

    for i, job in jobs_df.iterrows():
        job_id = str(job.get('JobID', f"job_{i}")).strip()

        try:
            soak = int(float(job['入槽時間'])) // SLOT_MIN
            duration = int(float(job['PlatingMin']) * 60) // SLOT_MIN
            rinse = int(float(job['出槽時間'])) // SLOT_MIN
        except Exception as e:
            excluded_jobs.append(f"{job_id}: 時間変換エラー（{e}）")
            continue

        job_type = str(job.get('PlatingType', '')).strip()
        required_type = str(job.get('RequiredSoType', '')).strip()

        valid_sos = [
            soid for soid, row in so_dict.items()
            if job_type == str(row.get('PlatingType', '')).strip()
            and (required_type == '' or required_type == str(row.get('SoType', row.get('種類', ''))).strip())
        ]

        if not valid_sos:
            excluded_jobs.append(f"{job_id}: PlatingType='{job_type}' + RequiredSoType='{required_type}' に一致する槽なし")
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

        restricted = True
        for t in range(TOTAL_SLOTS - soak - duration - rinse):
            soak_range = list(range(t, t + soak))
            rinse_range = list(range(t + soak + duration, t + soak + duration + rinse))
            combined = soak_range + rinse_range
            if all(0 <= s < TOTAL_SLOTS and global_workable_slots[s] for s in combined):
                restricted = False
            else:
                model.Add(start != t)

        if restricted:
            excluded_jobs.append(f"{job_id}: 勤務時間外により処理スロットが確保できません")
            continue

        all_intervals.append((plate_int, valid_sos))
        assigned.append(pres)
        job_results.append((i, start, soak, duration, rinse, pres, job_id, job_type, valid_sos[0]))

    for soid in so_dict.keys():
        intervals = [iv for iv, soids in all_intervals if soid in soids]
        if intervals:
            model.AddNoOverlap(intervals)

    model.Maximize(sum(assigned))

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = 15.0
    status = solver.Solve(model)

    results = []
    used_so_ids = set()
    slot_usage_map = [0] * TOTAL_SLOTS

    if status in [cp_model.OPTIMAL, cp_model.FEASIBLE]:
        for i, start, soak, plate, rinse, pres, jid, pt, soid in job_results:
            if solver.Value(pres):
                base = solver.Value(start)
                used_so_ids.add(soid)
                # Soak + Rinse スロット加算（全体に対して）
                for t in range(base, base + soak):
                    slot_usage_map[t] += 1
                for t in range(base + soak + plate, base + soak + plate + rinse):
                    slot_usage_map[t] += 1

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

    df_result = pd.DataFrame(results)

    if excluded_jobs:
        st.subheader("🛑 除外されたジョブと理由")
        for msg in excluded_jobs:
            st.write("🔸", msg)

    if df_result.shape[0] > 0:
        st.subheader("📊 槽使用状況")

        used_count = df_result['TankID'].value_counts()
        st.write("✅ 使用された槽:")
        st.dataframe(used_count.rename_axis("TankID").reset_index(name="UsageCount"))

        unused = all_so_ids - used_so_ids
        if unused:
            st.warning("⚠ 使用されなかった槽とその理由")
            for soid in sorted(unused):
                row = so_dict[soid]
                pt = str(row.get("PlatingType", "")).strip()
                stype = str(row.get("SoType", row.get("種類", ""))).strip()

                match_found = any(
                    str(job.get("PlatingType", "")).strip() == pt and
                    (str(job.get("RequiredSoType", "")).strip() in ["", stype])
                    for _, job in jobs_df.iterrows()
                )
                if not match_found:
                    reason = f"PlatingType='{pt}' / SoType='{stype}' に一致するジョブなし"
                else:
                    reason = "対応可能ジョブはあるが、別の槽に割当された可能性"

                st.write(f"🔸 {soid}: {reason}")
        else:
            st.success("🎉 すべての槽が使用されました")

        st.subheader("👷 作業者ごとの負荷率（Soak/Rinse）")

        for _, w in workers_df.iterrows():
            wid = w['WorkerID']
            total = worker_total_slots[wid]
            used = sum(slot_usage_map[t] for t in range(TOTAL_SLOTS) if global_workable_slots[t])
            load_pct = 100 * used / max(1, total)
            st.write(f"👷 {wid}: {used} / {total} スロット → {load_pct:.1f} %")

    return df_result
