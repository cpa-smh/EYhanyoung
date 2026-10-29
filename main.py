"""Windows/Python 3.12 실행 시작점. GPT/OpenAI API를 사용하지 않는다."""
import argparse
from datetime import datetime
from decimal import Decimal, InvalidOperation
from importlib.metadata import version
import json
import os
from pathlib import Path
import platform
import sys

from dotenv import load_dotenv
from credit_risk import load_credit_notes
from dart_client import DartClient, DartError, resolve_company
from pipeline import collect, analyze, load_snapshot, save_snapshot, file_hash

ROOT = Path(__file__).resolve().parent


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="OpenDART 동종기업 비교 · IQR · 신용손실 검토")
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--snapshot", type=Path, help="저장한 원본으로 재현; API 호출 없음")
    source.add_argument("--demo", action="store_true", help="명확히 표시한 합성 테스트 자료로 시연")
    parser.add_argument("--target", help="대상기업명. 동명 기업: 기업명@고유번호")
    parser.add_argument("--peers", nargs="+", help="사용자가 선택한 비교기업명 1~10개 (권장 5~10개)")
    parser.add_argument("--start-year", type=int, help="시작연도. 생략 시 종료연도-2")
    parser.add_argument("--end-year", type=int, default=2025, help="마지막 사업연도, 기본 2025")
    parser.add_argument("--credit-notes", type=Path, help="출처 있는 매출채권/손실충당금 주석 JSON")
    parser.add_argument("--receivables-scope", choices=("total_trade", "current_trade"), default="total_trade")
    parser.add_argument("--turnover-change", default="0.30", help="회전율 전년대비 변화율 기준, 기본 0.30=30%%")
    parser.add_argument("--margin-change-pp", default="5", help="매출총이익률 변동 기준, 기본 5%%p")
    parser.add_argument("--output-dir", type=Path, help="새 결과 폴더. 기존 파일이 있는 폴더는 사용하지 않음")
    parser.add_argument("--font", type=Path, help="한글 글꼴 .ttf/.otf 경로. Windows 기본 맑은 고딕")
    return parser.parse_args(argv)


def resolve_spec(companies, spec):
    if "@" in spec:
        name, code = spec.rsplit("@", 1)
        return resolve_company(companies, name, code)
    return resolve_company(companies, spec)


def main(argv=None):
    args = parse_args(argv)
    try:
        turnover, margin = Decimal(args.turnover_change), Decimal(args.margin_change_pp)
        if any(not n.is_finite() or n < 0 for n in (turnover, margin)):
            raise ValueError("변동 기준은 0 이상의 유한한 숫자여야 합니다.")
        start, end = args.start_year if args.start_year is not None else args.end_year - 2, args.end_year
        if args.snapshot and (args.target or args.peers or args.start_year is not None or args.end_year != 2025):
            raise ValueError("스냅샷 재현 시 기업·기간은 스냅샷에 저장된 값을 사용하세요.")
        if args.demo and (args.target or args.peers or args.credit_notes):
            raise ValueError("합성 데모에 실제 기업/주석을 혼합하지 마세요.")
        if not args.snapshot and (end - start != 2 or start < 2016 or end > datetime.now().year):
            raise ValueError("2016년 이후의 연속 3개년을 지정하세요. 종료연도는 현재 연도 이하여야 합니다.")
        out = args.output_dir or ROOT / "reports" / datetime.now().strftime("run_%Y%m%d_%H%M%S_%f")
        if out.exists() and any(out.iterdir()):
            raise ValueError("출력 폴더가 비어 있지 않습니다. 새 폴더를 지정하세요.")
        out.mkdir(parents=True, exist_ok=True)
        # matplotlib의 글꼴 캐시는 프로젝트 결과 폴더에 한정한다.
        os.environ.setdefault("MPLCONFIGDIR", str(out / ".matplotlib"))
        from chart_generator import configure_font, generate_charts
        from excel_report import write_report
        configure_font(args.font)  # 긴 API 수집 전에 환경 오류 확인
        note_path = args.credit_notes
        if args.demo:
            from sample_data import synthetic_inputs
            snapshot, synthetic_notes = synthetic_inputs(start, end)
            note_path = out / "credit_notes.json"
            note_path.write_text(json.dumps(synthetic_notes, ensure_ascii=False, indent=2), encoding="utf-8")
            print("합성 테스트 데이터입니다. 실제 기업/공시 수치가 아닙니다.")
        elif args.snapshot:
            snapshot = load_snapshot(args.snapshot)
            start, end = snapshot["start_year"], snapshot["end_year"]
            if snapshot["synthetic"]:
                print("합성 테스트 스냅샷 재현입니다. 실제 공시가 아닙니다.")
        else:
            target = args.target or input("대상기업 정확한 이름: ").strip()
            peers = args.peers or [p.strip() for p in input("비교기업 이름(쉼표 구분, 권장 5~10개): ").split(",") if p.strip()]
            if not 1 <= len(peers) <= 10:
                raise ValueError("비교기업은 1~10개를 입력하세요. IQR에는 지표별 유효 비교기업 5개 이상이 필요합니다.")
            load_dotenv(ROOT / ".env", override=False)
            client = DartClient(os.getenv("OPENDART_API_KEY", ""))
            try:
                index = client.companies()
                companies = [resolve_spec(index, spec) for spec in (target, *peers)]
                if len({c.code for c in companies}) != len(companies):
                    raise ValueError("대상기업과 비교기업은 중복 없이 입력하세요.")
                snapshot = collect(client, companies, start, end)
            finally:
                client.close()
        snapshot_path = out / "raw_snapshot.json"
        save_snapshot(snapshot, snapshot_path)
        # 실제로 사용한 주석도 결과 폴더에 보존한다. 자동 추출 자료로 표시하지 않는다.
        if note_path and note_path.resolve() != (out / "credit_notes.json").resolve():
            (out / "credit_notes.json").write_bytes(note_path.read_bytes())
            note_path = out / "credit_notes.json"
        notes = load_credit_notes(note_path)
        valid_note_keys = {(c["code"], y) for c in snapshot["companies"] for y in range(start - 1, end + 1)}
        if set(notes) - valid_note_keys:
            raise ValueError("주석 파일에 분석 범위 밖 기업/연도가 있습니다. 입력 오류를 확인하세요.")
        analysis = analyze(snapshot, notes, args.receivables_scope, turnover, margin)
        charts, font = generate_charts(analysis, snapshot, out / "charts", args.font)
        manifest = {"schema_version": 1, "created_at": datetime.now().astimezone().isoformat(),
                    "source": "synthetic" if snapshot["synthetic"] else "snapshot replay" if args.snapshot else "OpenDART live",
                    "python": platform.python_version(), "platform": platform.platform(),
                    "packages": {name: version(name) for name in ("requests", "python-dotenv", "defusedxml", "matplotlib", "numpy", "openpyxl", "Pillow")},
                    "snapshot_sha256": file_hash(snapshot_path), "notes_sha256": file_hash(note_path) if note_path else None,
                    "receivables_scope": args.receivables_scope, "turnover_change": str(turnover), "margin_change_pp": str(margin),
                    "font": font, "start_year": start, "end_year": end,
                    "peer_selection": "사용자가 선택한 비교기업; 업종 전체 표본 아님"}
        manifest_path = out / "manifest.json"
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        workbook = write_report(out / f"peer_financial_ratio_analysis_{end}.xlsx", snapshot, analysis, charts, manifest)
        excluded = sum(not r["eligible"] for r in analysis["records"])
        pending = sum(r["ratios"]["ar_turnover"] is None for r in analysis["records"])
        print(f"완료: {workbook.resolve()}")
        print(f"PNG {len(charts)}개 / 기업-연도 제외 {excluded}건 / 매출채권회전율 계산 유보 {pending}건")
        print("Excel의 기업 기본정보·계정매핑·신용손실 검토·분석 방법 및 한계를 확인하세요.")
        return 0
    except (DartError, ValueError, InvalidOperation, EOFError) as error:
        message = str(error) if isinstance(error, (DartError, ValueError)) else "입력값을 확인하세요."
        print(f"오류: {message}", file=sys.stderr)
        return 2
    except OSError:
        print("오류: 파일/글꼴을 읽거나 저장할 수 없습니다. 경로·권한·Excel 파일 열림 상태를 확인하세요.", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("사용자가 실행을 취소했습니다.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
