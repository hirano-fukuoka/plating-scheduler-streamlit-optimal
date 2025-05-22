from ortools.sat.python import cp_model
import pandas as pd
from datetime import timedelta
import streamlit as st
import plotly.express as px

def find_first_workable_rinse_start(plating_end, rinse, workable_slots):
    for s in range(plating_end, len(workable_slots) - rinse + 1):
        if all(workable_slots[ss] for ss in range(s, s + rinse)):
            return s
    return None

def optimize_schedule(jobs_df, workers_df, sos_df, start_date, weeks=1):
    model = cp_model.CpModel()

    SLOT_MIN = 30
    SLOTS_PER_DAY = 24 * 60 // SLOT_MIN
    SLOTS_PER_WEEK = SLOTS_PER_DAY * 7
    MAX_WEEKS = 4
    TOTAL_SLOTS = SLOTS_PER_WEEK * MAX_WEEKS

    VALID_START_MIN = 0
    VALID_START_MAX = SLOTS_PER_WEEK * weeks - 1

    so_dict = {
        str(row['SoID']).strip(): row for _, row in sos_df.iterrows()
        if str(row.get('Status', '')).strip() == '稼働中'
    }
    all_so_ids = set(so_dict.keys())

    worker_slots = {}
    worker_total_slots = {}
    for _, w in workers_df.iterrows():
        wid = w['WorkerID']
        slots = [False] * TOTAL_SLOTS
        total = 0
        for d in range(7):
            if str(w.get(f'Day{d+1}', '')).strip() == '〇':
                for week in range(MAX_WEEKS):
                    s = int(float(w['StartHour']) * 2) + d * SLOTS_PER_DAY + week * SLOTS_PER_WEEK
                    e = int(float(w['EndHour']) * 2) + d * SLOTS_PER_DAY + week * SLOTS_PER_WEEK
                    total += (e - s)
                    for t in range(s, e):
                        slots[t] = True
        worker_slots[wid] = slots
        worker_total_slots[wid] = total

    global_workable_slots = [any(worker_slots[w][t] for w in worker_slots) for t in range(TOTAL_SLOTS)]
    slot_worker_capacity = [sum(worker_slots[wid][t] for wid in worker_slots) for t in range(TOTAL_SLOTS)]
    assigned = []
    job_results = []
    excluded_jobs = []

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

        # Soak工程の開始スロット（勤務帯内で可能なものを探す）
        soak_found = False
        for t in range(VALID_START_MIN, VALID_START_MAX + 1):
            soak_range = list(range(t, t + soak))
            if all(0 <= s < TOTAL_SLOTS and global_workable_slots[s] for s in soak_range):
                soak_start = t
                soak_found = True
                break
        if not soak_found:
            excluded_jobs.append({
                "JobID": job_id,
                "Category": "out_of_shift",
                "Reason": f"{job_id}: ❌ Soak工程が勤務帯内で開始できません"
            })
            continue

        plating_start = soak_start + soak
        plating_end = plating_start + duration

        # RinseはPlating終了後、勤務帯が再開するまで待機
        rinse_start = find_first_workable_rinse_start(plating_end, rinse, global_workable_slots)
        if rinse_start is None:
            excluded_jobs.append({
                "JobID": job_id,
                "Category": "out_of_shift_rinse",
                "Reason": f"{job_id}: ❌ Rinse工程が勤務帯内で配置できません"
            })
            continue

        # OR-Tools変数で工程配置
        start = model.NewIntVar(soak_start, soak_start, f"start_{i}")
        pres = model.NewBoolVar(f"assigned_{i}")

        soak_worker_int = model.NewOptionalIntervalVar(soak_start, soak, soak_start + soak, pres, f"soak_worker_{i}")
        plating_worker_int = model.NewOptionalIntervalVar(plating_start, duration, plating_end, pres, f"plating_worker_{i}")
        rinse_worker_int = model.NewOptionalIntervalVar(rinse_start, rinse, rinse_start + rinse, pres, f"rinse_worker_{i}")

        soak_tank_int = model.NewOptionalIntervalVar(soak_start, soak, soak_start + soak, pres, f"soak_tank_{i}")
        plating_tank_int = model.NewOptionalIntervalVar(plating_start, duration, plating_end, pres, f"plating_tank_{i}")
        rinse_tank_int = model.NewOptionalIntervalVar(rinse_start, rinse, rinse_start + rinse, pres, f"rinse_tank_{i}")

        job_results.append({
            'index': i, 'start': soak_start, 'soak': soak, 'duration': duration, 'rinse': rinse,
            'pres': pres, 'JobID': job_id, 'PlatingType': job_type, 'TankID': soid,
            'SoakWorker': soak_workers, 'RinseWorker': rinse_workers,
            'soak_worker_int': soak_worker_int,
            'plating_worker_int': plating_worker_int,
            'rinse_worker_int': rinse_worker_int,
            'soak_tank_int': soak_tank_int,
            'plating_tank_int': plating_tank_int,
            'rinse_tank_int': rinse_tank_int,
            'rinse_start': rinse_start
        })
        assigned.append(pres)

    # OR-Toolsモデルが空だとエラーになるので、必ずSolve前にモデル構築
    solver = cp_model.CpSolver()
    status = cp_model.UNKNOWN  # 初期値

    # NoOverlapなどの制約追加
    for soid in so_dict:
        intervals = []
        for job in job_results:
            if job['TankID'] == soid:
                intervals += [
                    job['soak_tank_int'],
                    job['plating_tank_int'],
                    job['rinse_tank_int']
                ]
        if intervals:
            model.AddNoOverlap(intervals)

    # 作業者リソース制約（Soak/Rinseのみ）
    for t in range(TOTAL_SLOTS):
        demand_expr = []
        for job in job_results:
            soak_start = job['start']
            soak = job['soak']
            rinse_start = job['rinse_start']
            rinse = job['rinse']
            pres = job['pres']
            # Soak
            if t >= soak_start and t < soak_start + soak:
                demand_expr.append(job['SoakWorker'])
            # Rinse
            if t >= rinse_start and t < rinse_start + rinse:
                demand_expr.append(job['RinseWorker'])
        if demand_expr:
            model.Add(sum(demand_expr) <= slot_worker_capacity[t])

    # 優先度付き最大化
    priority_weights = [len(assigned) - i for i in range(len(assigned))]
    model.Maximize(sum(priority_weights[i] * assigned[i] for i in range(len(assigned))))

    # ここで必ずSolveし、statusに値をセット
    if job_results:
        status = solver.Solve(model)

    results = []
    used_so_ids = set()
    slot_usage_map = [0] * TOTAL_SLOTS

    if status in [cp_model.OPTIMAL, cp_model.FEASIBLE]:
        for job in job_results:
            pres = job['pres']
            if solver.Value(pres) == 0:
                excluded_jobs.append({
                    "JobID": job['JobID'],
                    "Category": "tank_or_worker_conflict",
                    "Reason": f"{job['JobID']}: ⚠ 候補にはなったが最適化で未採用 → タンクや人数競合の可能性"
                })
                continue

            soak_start = job['start']
            plating_start = soak_start + job['soak']
            plating_end = plating_start + job['duration']
            rinse_start = job['rinse_start']

            used_so_ids.add(job['TankID'])
            for t in range(soak_start, soak_start + job['soak']):
                slot_usage_map[t] += 1
            for t in range(rinse_start, rinse_start + job['rinse']):
                slot_usage_map[t] += 1

            start_dt = start_date + timedelta(minutes=soak_start * SLOT_MIN)
            plating_end_dt = start_date + timedelta(minutes=plating_end * SLOT_MIN)
            rinse_start_dt = start_date + timedelta(minutes=rinse_start * SLOT_MIN)

            results.append({
                "JobID": job['JobID'],
                "PlatingType": job['PlatingType'],
                "SoakStart": start_dt.strftime("%Y-%m-%d %H:%M"),
                "PlatingEnd": plating_end_dt.strftime("%Y-%m-%d %H:%M"),
                "RinseStart": rinse_start_dt.strftime("%Y-%m-%d %H:%M"),
                "TankID": job['TankID'],
                "SoakMin": job['soak'] * SLOT_MIN,
                "PlatingMin": job['duration'] * SLOT_MIN,
                "RinseMin": job['rinse'] * SLOT_MIN,
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

        df_excl = pd.DataFrame(excluded_jobs)
        reason_summary = df_excl['Category'].value_counts().reset_index()
        reason_summary.columns = ['除外理由カテゴリ', '件数']
        st.subheader("📝 除外理由ごとの集計")
        st.dataframe(reason_summary)
        fig = px.pie(reason_summary, names='除外理由カテゴリ', values='件数', title="除外ジョブ理由の割合")
        st.plotly_chart(fig, use_container_width=True)
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
