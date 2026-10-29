"""matplotlib PNG: 결측값을 0으로 그리지 않으며 한글 글꼴을 명시한다."""
from pathlib import Path
import warnings
import matplotlib
matplotlib.use("Agg")  # GUI 없는 CI와 Windows 터미널에서도 저장 가능
import matplotlib.pyplot as plt
from matplotlib import font_manager
from matplotlib.patches import Patch
from matplotlib.lines import Line2D
from ratio_calculator import CORE, METRICS


def configure_font(font_path=None):
    if font_path:
        font_manager.fontManager.addfont(str(font_path))
        chosen = font_manager.FontProperties(fname=str(font_path)).get_name()
    else:
        available = {f.name for f in font_manager.fontManager.ttflist}
        chosen = next((name for name in ("Malgun Gothic", "맑은 고딕", "Noto Sans CJK KR", "Noto Sans KR", "NanumGothic", "AppleGothic") if name in available), None)
        if chosen is None:
            raise ValueError("한글 글꼴이 없습니다. --font로 한글 .ttf/.otf 경로를 지정하세요.")
    plt.rcParams.update({"font.family": chosen, "axes.unicode_minus": False,
                         "font.size": 10, "axes.spines.top": False, "axes.spines.right": False})
    return chosen


def generate_charts(analysis, snapshot, output_dir, font_path=None):
    font_name = configure_font(font_path)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    year = snapshot["end_year"]
    prefix = "[합성 TEST — 실제 공시 아님] " if snapshot["synthetic"] else ""
    for metric in CORE:
        label, unit = METRICS[metric]
        factor = 100 if unit == "%" else 1
        subset = [r for r in analysis["cross_outliers"] if r["metric"] == metric]
        fig, ax = plt.subplots(figsize=(12, 5.8), layout="constrained")
        for index, row in enumerate(subset):
            value = row["value"]
            color = "#D66A37" if "후보" in row["flag"] else "#173F5F" if row["role"] == "대상기업" else "#5A9AAB"
            if value is not None:
                ax.bar(index, float(value) * factor, color=color, width=.62)
            else:
                ax.text(index, .03, "계산 불가", transform=ax.get_xaxis_transform(), ha="center", rotation=90, color="#777777")
        stats = analysis["statistics"][metric]
        if stats["median"] is not None:
            ax.axhline(float(stats["median"]) * factor, color="#A33C57", linestyle="--", linewidth=1.5)
        ax.set_xticks(range(len(subset)), [r["name"] for r in subset], rotation=25, ha="right")
        ax.set_title(f"{prefix}{year}년 {label} 비교", loc="left", fontweight="bold", pad=16)
        ax.set_ylabel(f"{label} ({unit})")
        ax.set_xlabel(f"사용자 선택 기업 | 유효 비교기업 n={stats['n']} | IQR 판정은 지표별 n≥5 및 IQR>0")
        ax.grid(axis="y", alpha=.15)
        ax.set_axisbelow(True)
        ax.legend(handles=[Patch(color="#173F5F", label="대상기업"), Patch(color="#5A9AAB", label="비교기업"),
                           Patch(color="#D66A37", label="이상치 후보"), Line2D([0], [0], color="#A33C57", linestyle="--", label="비교기업 중앙값")],
                  loc="upper center", bbox_to_anchor=(.5, 1.01), ncol=4, fontsize=9)
        path = output_dir / f"comparison_{metric}_{year}.png"
        fig.savefig(path, dpi=150, facecolor="white")
        plt.close(fig)
        paths.append(path)

    # 비율마다 별도 선그래프: 5~10개 비교기업의 범례를 읽을 수 있게 한다.
    for metric in CORE:
        label, unit = METRICS[metric]
        factor = 100 if unit == "%" else 1
        fig, ax = plt.subplots(figsize=(12, 6), layout="constrained")
        for company in snapshot["companies"]:
            records = sorted((r for r in analysis["records"] if r["code"] == company["code"]), key=lambda r: r["year"])
            y = [float(r["ratios"][metric]) * factor if r["ratios"][metric] is not None else float("nan") for r in records]
            ax.plot([r["year"] for r in records], y, marker="o", label=company["name"],
                    linewidth=2.8 if company["role"] == "대상기업" else 1.4)
        ax.set_title(f"{prefix}{snapshot['start_year']}–{year}년 {label} 추세", loc="left", fontweight="bold", pad=16)
        ax.set_xticks(range(snapshot["start_year"], year + 1))
        ax.set_ylabel(f"{label} ({unit})")
        ax.set_xlabel("사업연도 | 결측은 선을 연결하지 않음")
        ax.grid(alpha=.15)
        ax.legend(loc="upper left", bbox_to_anchor=(1.01, 1), fontsize=9)
        if not any(r["ratios"][metric] is not None for r in analysis["records"]):
            ax.text(.5, .5, "계산 가능한 데이터 없음 — 주석·매핑·기간 확인", transform=ax.transAxes, ha="center")
        path = output_dir / f"trend_{metric}_{year}.png"
        fig.savefig(path, dpi=150, facecolor="white")
        plt.close(fig)
        paths.append(path)
    return paths, font_name
