from ortools.sat.python import cp_model
import pandas as pd
from datetime import timedelta
import streamlit as st
import plotly.express as px

def optimize_schedule(jobs_df, workers_df, sos_df, start_date):
    model = cp_model.CpModel()

    SLOT_MIN = 30
    SLOTS_PER_DAY = 24 * 60 // SLOT_MIN
    TOTAL_SLOTS = SLOTS_PER_DAY * 7

    # 稼働中の槽のみ対象
    so_dict = {
        str(row['SoID']).strip(): row for _, row in sos_df.iterrows()
        if str(row.get('Status', '')).strip() == '稼働中'
    }
    all_so_ids = set(so_dict.keys())

    # 作業者スロットと稼働可能時間数
    worker_slots = {}
    worker_total_slots = {}
    for _, w in workers_df.iterrows():
        wid = w['WorkerID']
        slots = [False] * TOTAL_SLOTS
        total = 0
        for d in range(7):
            if str(w.get(f'Day{d+1}', '')).strip() == '〇':
                s = int(float(w['StartHour']) * 2) + d * SLOTS_PER_DAY
                e = int(float(w['EndHour']) * 2) + d * SLOTS_PER_DAY
                total += (e - s)
                for t in range(s, e):
                    slots[t] = True
        worker_slots[wid] = slots
        worker_total_slots[wid] = total

    global_workable_slots = [any(worker_slots[w][t] for w in worker_slots) for t in range(TOTAL_SLOTS)]
    worker_usage = {wid: 0 for wid in worker_total_slots}

    assigned = []
    all_tank_intervals = []
    job_results = []
    excluded_jobs = []

    # スロットごとの作業者需要
    slot_worker_demand = [0] * TOTAL_SLOTS
    slot_worker_capacity = [sum(worker_slots[wid][t] for wid in worker_slots) for t in range(TOTAL_SLOTS)]

    for i, job in jobs_df.iterrows():
        job_id = str(job.get('JobID', f"job_{i}")).strip()

        try:
            soak = int(float(job['入槽時間'])) // SLOT_MIN
            duration = int(float(job['PlatingMin']) * 60) // SLOT_MIN
            rinse = int(float(job['出槽時間'])) // SLOT_MIN
        except Exception as e:
            excluded_jobs.append({
                "JobID": job_id,
                "Category": "time_conversion_error",
                "Reason": f"{job_id}: ❌ 時間変換エラー（{e}）"
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
                "Category": "type_unmatched",
                "Reason": f"{job_id}: ❌ 対応槽なし → PlatingType='{job_type}', RequiredSoType='{required_type}' に一致する槽がありません"
            })
            continue

        soid = valid_sos[0]
        row = so_dict[soid]
        soak_workers = int(row.get('SoakWorker', 1))
        rinse_workers = int(row.get('RinseWorker', 1))

        pres = model.NewBoolVar(f"assigned_{i}")
        start = model.NewIntVar(0, TOTAL_SLOTS - soak - duration - rinse, f"start_{i}")
        soak_end = model.NewIntVar(0, TOTAL_SLOTS, f"soak_end_{i}")
        plate_end = model.NewIntVar(0, TOTAL_SLOTS, f"plate_end_{i}")
        rinse_end = model.NewIntVar(0, TOTAL_SLOTS, f"rinse_end_{i}")

        soak_worker_int = model.NewOptionalIntervalVar(start, soak, soak_end, pres, f"soak_worker_{i}")
        rinse_start = plate_end
        rinse_worker_int = model.NewOptionalIntervalVar(rinse_start, rinse, rinse_end, pres, f"rinse_worker_{i}")

        soak_tank_int = model.NewOptionalIntervalVar(start, soak, soak_end, pres, f"soak_tank_{i}")
        plate_start = soak_end
        plate_int = model.NewOptionalIntervalVar(plate_start, duration, plate_end, pres, f"plate_{i}")
        rinse_tank_int = model.NewOptionalIntervalVar(rinse_start, rinse, rinse_end, pres, f"rinse_tank_{i}")

        # Soak/Rinse の勤務帯チェック（人）
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
            excluded_jobs.append({
                "JobID": job_id,
                "Category": "out_of_shift",
                "Reason": f"{job_id}: ❌ 勤務帯外 → Soak+Rinse が出勤時間に収まりません"
            })
            continue

        job_results.append({
            'index': i, 'start': start, 'soak': soak, 'duration': duration, 'rinse': rinse,
            'pres': pres, 'JobID': job_id, 'PlatingType': job_type, 'TankID': soid,
            'SoakWorker': soak_workers, 'RinseWorker': rinse_workers,
            'soak_worker_int': soak_worker_int,
            'rinse_worker_int': rinse_worker_int,
            'soak_tank_int': soak_tank_int,
            'plate_int': plate_int,
            'rinse_tank_int': rinse_tank_int
        })

        assigned.append(pres)

    # 槽NoOverlap制約（Soak/Plating/Rinse重複不可）
    for soid in so_dict:
        intervals = []
        for job in job_results:
            if job['TankID'] == soid:
                intervals += [
                    job['soak_tank_int'],
                    job['plate_int'],
                    job['rinse_tank_int']
                ]
        if intervals:
            model.AddNoOverlap(intervals)

    # 作業者リソース制約（Soak/Rinse工程の各スロットごと人数制約）
    for t in range(TOTAL_SLOTS):
        demand_expr = []
        for job in job_results:
            i = job['index']
            s = job['start']
            pres = job['pres']
            soak = job['soak']
            rinse = job['rinse']
            soak_w = job['SoakWorker']
            rinse_w = job['RinseWorker']
            is_in_soak = model.NewBoolVar(f"soak_active_{i}_{t}")
            model.Add(t >= s).OnlyEnforceIf(is_in_soak)
            model.Add(t < s + soak).OnlyEnforceIf(is_in_soak)
            model.Add(is_in_soak == 1).OnlyEnforceIf(pres)
            demand_expr.append(is_in_soak * soak_w)
            rinse_start = s + soak + job['duration']
            is_in_rinse = model.NewBoolVar(f"rinse_active_{i}_{t}")
            model.Add(t >= rinse_start).OnlyEnforceIf(is_in_rinse)
            model.Add(t < rinse_start + rinse).OnlyEnforceIf(is_in_rinse)
            model.Add(is_in_rinse == 1).OnlyEnforceIf(pres)
            demand_expr.append(is_in_rinse * rinse_w)
        if demand_expr:
            model.Add(sum(demand_expr) <= slot_worker_capacity[t])

    # 目的関数
    model.Maximize(sum(assigned))

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = 15.0
    status = solver.Solve(model)

    results = []
    used_so_ids = set()
    slot_usage_map = [0] * TOTAL_SLOTS

    if status in [cp_model.OPTIMAL, cp_model.FEASIBLE]:
        for job in job_results:
            i = job['index']
            pres = job['pres']
            if solver.Value(pres) == 0:
                excluded_jobs.append({
                    "JobID": job['JobID'],
                    "Category": "tank_or_worker_conflict",
                    "Reason": f"{job['JobID']}: ⚠ 候補にはなったが最適化で未採用 → タンクや人数競合の可能性"
                })
                continue

            start_val = solver.Value(job['start'])
            used_so_ids.add(job['TankID'])
            for t in range(start_val, start_val + job['soak']):
                slot_usage_map[t] += 1
            for t in range(start_val + job['soak'] + job['duration'], start_val + job['soak'] + job['duration'] + job['rinse']):
                slot_usage_map[t] += 1

            start_dt = start_date + timedelta(minutes=start_val * SLOT_MIN)
            results.append({
                "JobID": job['JobID'],
                "PlatingType": job['PlatingType'],
                "StartTime": start_dt.strftime("%Y-%m-%d %H:%M"),
                "DurationMin": job['duration'] * SLOT_MIN,
                "TankID": job['TankID'],
                "SoakMin": job['soak'] * SLOT_MIN,
                "RinseMin": job['rinse'] * SLOT_MIN
            })

    df_result = pd.DataFrame(results)

    # 除外ジョブの表示・分析
    if excluded_jobs:
        st.subheader("🛑 除外ジョブ一覧（理由つき）")
        for entry in excluded_jobs:
            reason = entry["Reason"]
            if "❌" in reason:
                st.error(reason)
            elif "⚠" in reason:
                st.warning(reason)
            else:
                st.write("🔹", reason)

        # DataFrame化しカテゴリごとに集計
        df_excl = pd.DataFrame(excluded_jobs)
        reason_summary = df_excl['Category'].value_counts().reset_index()
        reason_summary.columns = ['除外理由カテゴリ', '件数']
        st.subheader("📝 除外理由ごとの集計")
        st.dataframe(reason_summary)
        # 円グラフ
        fig = px.pie(reason_summary, names='除外理由カテゴリ', values='件数', title="除外ジョブ理由の割合")
        st.plotly_chart(fig, use_container_width=True)

        # CSVダウンロード
        df_excl = pd.DataFrame(excluded_jobs)
        csv_log = df_excl.to_csv(index=False, encoding="utf-8-sig")
        st.download_button(
            label="📥 除外ジョブCSVダウンロード",
            data=csv_log,
            file_name="excluded_jobs.csv",
            mime="text/csv"
        )

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
        for wid in worker_slots:
            total = worker_total_slots[wid]
            used = sum(1 for t in range(TOTAL_SLOTS) if worker_slots[wid][t] and slot_usage_map[t] > 0)
            rate = 100 * used / total if total else 0
            st.write(f"👷 {wid}: {used} / {total} スロット → {rate:.1f} %")
    return df_result
