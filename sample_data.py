"""명확하게 표시한 합성 테스트 전용 자료. 실제 기업명이나 실제 공시 수치가 아니다."""
from account_mapper import ACCOUNTS


def synthetic_inputs(start=2023, end=2025):
    snapshot = {"schema_version": 1, "synthetic": True, "retrieved_at": "SYNTHETIC TEST — NOT FETCHED",
                "start_year": start, "end_year": end, "companies": []}
    notes = {"schema_version": 1, "records": []}
    for index in range(7):
        code, stock = f"{index + 1:08d}", f"{900001 + index:06d}"
        company = {"code": code, "name": "합성 대상기업" if index == 0 else f"합성 비교기업 {index}", "stock_code": stock,
                   "role": "대상기업" if index == 0 else "비교기업",
                   "profile": {"corp_cls": "Y", "acc_mt": "12", "induty_code": "26110", "stock_code": stock}, "years": {}}
        for offset, year in enumerate(range(start - 1, end + 1)):
            receipt = f"{year + 1}0301{index + 1:06d}"
            revenue = (1000 + index * 100 + offset * 100) * 1_000_000
            margin = .20 + index * .025
            cost = int(revenue * (1 - margin))
            gross = (100 + index * 4 + offset * 4) * 1_000_000
            allowance = (2 + index) * 1_000_000
            if index == 6:
                allowance = [8, 12, 18, 30][offset] * 1_000_000
            inventory = (70 + index * 4 + offset * 5) * 1_000_000
            if index == 0:
                revenue = [1000, 1100, 1150, 1200][offset] * 1_000_000
                cost = [700, 820, 950, 1050][offset] * 1_000_000
                gross = [160, 240, 400, 600][offset] * 1_000_000
                allowance = [8, 20, 60, 150][offset] * 1_000_000
                inventory = [100, 180, 300, 500][offset] * 1_000_000
            amounts = {"revenue": revenue, "cost": cost, "receivables": gross - allowance,
                       "inventory": inventory, "gross_profit": revenue - cost,
                       "assets": 3_000_000_000, "liabilities": 1_000_000_000, "equity": 2_000_000_000}
            full = []
            labels = {"revenue": "매출액", "cost": "매출원가", "receivables": "매출채권", "inventory": "재고자산",
                      "gross_profit": "매출총이익", "assets": "자산총계", "liabilities": "부채총계", "equity": "자본총계"}
            for item, specs in ACCOUNTS.items():
                full.append({"bsns_year": str(year), "corp_code": code, "reprt_code": "11011", "rcept_no": receipt,
                             "sj_div": specs[2][0], "account_id": specs[0][0], "account_nm": labels[item],
                             "thstrm_amount": str(amounts[item]), "currency": "KRW"})
            period = [{"bsns_year": str(year), "stock_code": stock, "reprt_code": "11011", "rcept_no": receipt,
                       "fs_div": "CFS", "sj_div": statement, "thstrm_dt": date_text, "currency": "KRW"}
                      for statement, date_text in (("BS", f"{year}.12.31 현재"), ("IS", f"{year}.01.01 ~ {year}.12.31"))]
            company["years"][str(year)] = {"full": full, "period": period, "notes": []}
            previous_allowance = (notes["records"][-1]["allowance"] if offset else None)
            writeoffs = (offset + index) * 1_000_000 if offset else None
            ecl_expense = allowance - int(previous_allowance) + writeoffs if offset else None
            notes["records"].append({"corp_code": code, "year": year, "fs_div": "CFS", "currency": "KRW",
                                     "scope": "total_trade", "reported_basis": "net", "balance_date": f"{year}-12-31",
                                     "rcept_no": receipt, "gross": str(gross), "allowance": str(allowance), "net": str(gross - allowance),
                                     "ecl_expense": str(ecl_expense) if ecl_expense is not None else None,
                                     "writeoffs": str(writeoffs) if writeoffs is not None else None,
                                     "recoveries": "0" if offset else None, "other_movements": "0" if offset else None,
                                     "source_url": "https://example.invalid/synthetic-test-only",
                                     "source_ref": "합성 테스트 주석 — 실제 공시 없음"})
        snapshot["companies"].append(company)
    return snapshot, notes
