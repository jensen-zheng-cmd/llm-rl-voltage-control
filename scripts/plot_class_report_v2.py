"""Classroom figures from frozen v2 results, with source tables and rendered QA.

No API calls, training or changes to the frozen control methods.
Paper means are transcribed from Table III; training curves use all three seeds.
"""
import argparse
import csv
import hashlib
import json
import os
from pathlib import Path
import sys

os.environ.setdefault("MPLCONFIGDIR", "D:/Codex/cache/matplotlib")
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D
import numpy as np
import pymupdf

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from run_seed_v2 import trained_sources

METHODS = ["original", "no_rl", "no_pt", "pure_rl", "no_llm", "no_reflexion", "proposed", "misocp"]
LABELS = ["Original", "No-RL", "No-PT", "Pure-RL", "No-LLM", "No-Reflexion", "Proposed", "MISOCP"]
# Table III means. Its SD is across 75 episodes, unlike our between-seed SD.
PAPER_MAD = np.array([.025, .0169, .0102, .0206, .0114, .0086, .00822, .00273])
PAPER_VIOL = np.array([10.7, 1.21, 1.26, 3.24, .0365, .00608, .0026, 0])
BLUE, GRAY, ORANGE, GREEN = "#2762C3", "#9CA6B4", "#D77D29", "#25836D"
INK = "#202C3A"
COLORS = {"Proposed": BLUE, "Pure-RL": "#7D8592", "No-Reflexion": GREEN, "No-PT": ORANGE}


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def table(path):
    with Path(path).open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def write_table(path, fields, rows):
    with Path(path).open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)


def trailing(x, window=25):
    x = np.asarray(x, float)
    return np.array([np.nanmean(x[max(0, i-window+1):i+1])
                     if np.isfinite(x[max(0, i-window+1):i+1]).any() else np.nan for i in range(len(x))])


def style():
    plt.rcParams.update({"font.family": "sans-serif", "font.sans-serif": ["Microsoft YaHei", "DejaVu Sans", "Arial"], "font.size": 14,
        "axes.unicode_minus": False, "axes.spines.top": False, "axes.spines.right": False,
        "axes.edgecolor": "#CAD0D8", "axes.labelcolor": INK, "text.color": INK,
        "xtick.color": "#526173", "ytick.color": INK, "legend.frameon": False,
        "pdf.fonttype": 42, "svg.fonttype": "none", "savefig.facecolor": "white"})


def canvas(title, subtitle, n=2):
    fig, axes = plt.subplots(1, n, figsize=(16, 8.7), squeeze=False)
    fig.subplots_adjust(left=.115, right=.955, bottom=.19, top=.78, wspace=.35)
    fig.text(.055, .94, title, fontsize=25, weight="bold", va="top")
    fig.text(.055, .865, subtitle, fontsize=14, color="#5D6A7B", va="top")
    return fig, list(axes[0])


def foot(fig, text):
    fig.text(.055, .055, text, fontsize=11.5, color="#5D6A7B", va="bottom")


def row_axis(ax, labels=LABELS):
    ax.set_yticks(range(len(labels)), labels)
    ax.set_ylim(len(labels)-.5, -.65)
    ax.set_axisbelow(True)
    ax.tick_params(axis="y", length=0, pad=12)
    if "Proposed" in labels:
        j = list(labels).index("Proposed")
        ax.axhspan(j-.47, j+.47, color="#EDF3FC", zorder=0)
        ax.get_yticklabels()[j].set_color(BLUE)
        ax.get_yticklabels()[j].set_weight("bold")


def save(fig, name, output, qa, alignment):
    alignment(fig, json_out=qa/f"{name}.alignment.json", strict=True)
    fig.savefig(output/f"{name}.png", dpi=300)
    fig.savefig(output/f"{name}.pdf")
    fig.savefig(output/f"{name}.svg")
    plt.close(fig)


def test_comparison(s, miso, out, qa, alignment):
    ours_mad = np.array([s["across_seeds"][m]["mad_pu"]["mean"] for m in METHODS[:-1]]
                        + [miso["misocp"]["stage2_ac_mad_pu"]])
    ours_viol = np.array([s["across_seeds"][m]["violation_pct"]["mean"] for m in METHODS[:-1]]+[0.])
    fig, (a,b) = canvas("联合控制的优势保留下来，越限率仍高于论文", "最终测试结果与原论文表 III 对比")
    for ax, p, v, label in [(a, PAPER_MAD*1000, ours_mad*1000, r"平均绝对电压偏差 / $10^{-3}$ p.u."),
                             (b, PAPER_VIOL, ours_viol, "电压越限率 / %")]:
        row_axis(ax)
        for y, (pv, ov) in enumerate(zip(p,v)):
            ax.barh(y-.17, pv, height=.27, color=GRAY)
            ax.barh(y+.17, ov, height=.27, color=BLUE)
            fmt = (lambda x:f"{x:.2f}") if ax is a else (lambda x:f"{x:.4g}")
            for yy, value in [(y-.17,pv),(y+.17,ov)]:
                ax.annotate(fmt(value), (value,yy), xytext=(7,0), textcoords="offset points",
                            fontsize=11, va="center", ha="left")
        ax.set_xlabel(label, labelpad=12)
    a.set_xlim(0,30)
    b.set_xscale("symlog", linthresh=.01, linscale=.55)
    b.set_xlim(0,32)
    b.set_xticks([0,.01,.1,1,10], ["0","0.01","0.1","1","10"])
    a.set_title("a  偏差", loc="left", fontsize=17, pad=14)
    b.set_title("b  越限", loc="left", fontsize=17, pad=14)
    fig.legend(handles=[Line2D([0],[0],lw=8,color=GRAY,label="原论文"),
                        Line2D([0],[0],lw=8,color=BLUE,label="本复现")],
               loc="upper right", bbox_to_anchor=(.94,.88), ncol=2, fontsize=12)
    foot(fig,"原文：75 个测试回合均值；复现：48 天 × 3 种子均值。右图 0~0.01% 为线性，其余为对数刻度。\n"
             "来源：Yang et al., TSG 2026, Table III；本复现最终测试与 MISOCP 参照。")
    save(fig,"01_test_vs_paper",out,qa,alignment)
    # Single-metric versions keep the labels legible in a Markdown projection.
    for name,p,v,title,xlabel in [
        ("01a_voltage_deviation",PAPER_MAD*1000,ours_mad*1000,"最终测试：平均电压偏差",r"平均绝对电压偏差 / $10^{-3}$ p.u."),
        ("01b_voltage_violations",PAPER_VIOL,ours_viol,"最终测试：电压越限率","电压越限率 / %")]:
        f,ax=plt.subplots(figsize=(10,7.4))
        f.subplots_adjust(left=.22,right=.91,bottom=.19,top=.78)
        f.text(.07,.95,title,fontsize=22,weight="bold",va="top")
        row_axis(ax)
        for y,(pv,ov) in enumerate(zip(p,v)):
            for yy,value,c in [(y-.17,pv,GRAY),(y+.17,ov,BLUE)]:
                ax.barh(yy,value,height=.27,color=c)
                value_text=f"{value:.2f}" if name.startswith("01a") else f"{value:.4g}"
                ax.annotate(value_text,(value,yy),xytext=(6,0),textcoords="offset points",fontsize=11,va="center")
        if name.startswith("01a"):
            ax.set_xlim(0,29)
        else:
            ax.set_xscale("symlog",linthresh=.01,linscale=.55)
            ax.set_xlim(0,36);ax.set_xticks([0,.01,.1,1,10],["0","0.01","0.1","1","10"])
        ax.set_xlabel(xlabel,labelpad=10)
        f.legend(handles=[Line2D([0],[0],lw=7,color=GRAY,label="原论文"),Line2D([0],[0],lw=7,color=BLUE,label="本复现")],
                 loc="upper right",bbox_to_anchor=(.92,.90),ncol=2,fontsize=12)
        note="原文：75 回合均值；复现：48 天 × 3 种子均值。"
        if name.startswith("01b"):note+="\n0~0.01% 为线性，其余为对数刻度。"
        f.text(.07,.04,note,fontsize=10.5,color="#5D6A7B")
        save(f,name,out,qa,alignment)
    write_table(out/"01_test_vs_paper.csv", ["method","paper_mad_pu","reproduction_mad_pu","paper_violation_pct","reproduction_violation_pct"],
        [dict(zip(["method","paper_mad_pu","reproduction_mad_pu","paper_violation_pct","reproduction_violation_pct"],r))
         for r in zip(LABELS,PAPER_MAD,ours_mad,PAPER_VIOL,ours_viol)])
    return ours_mad


def seed_and_pairs(s, out,qa,alignment):
    days=table(ROOT/"results/test-v2/day_means.csv")
    fig,(a,b)=canvas("Proposed 在三个种子中均最优，多数测试日保持收益", "种子间波动与逐日配对")
    labels=LABELS[:-1]
    row_axis(a,labels)
    for j,m in enumerate(METHODS[:-1]):
        values=np.array([s["per_seed"][str(seed)][m]["mad_pu"] for seed in [42,43,44]])*1000
        a.errorbar(values.mean(),j,xerr=values.std(ddof=1),fmt="D",color=BLUE,ms=7,capsize=4,lw=1.6,zorder=3)
        for offset,v in zip([-.17,0,.17],values):a.scatter(v,j+offset,s=28,color=GRAY,zorder=4)
    a.set_xlim(6.5,23.5);a.set_xlabel(r"平均偏差 / $10^{-3}$ p.u.",labelpad=12)
    a.set_title("a  三个种子的日均偏差",loc="left",fontsize=17,pad=14)
    controls=["no_reflexion","no_llm","pure_rl","no_pt","no_rl","original"]
    b_labels=["No-Reflexion","No-LLM","Pure-RL","No-PT","No-RL","Original"]
    row_axis(b,b_labels)
    exports=[]
    for j,(m,lab) in enumerate(zip(controls,b_labels)):
        d=np.array([float(r["proposed_mad_pu"])-float(r[f"{m}_mad_pu"]) for r in days])*1000
        # Deterministic vertical offsets separate the 48 real daily values; x is unchanged.
        offsets=((np.arange(len(d))*17)%len(d))/(len(d)-1)*.38-.19
        b.scatter(d,j+offsets,s=22,alpha=.5,color=GRAY,edgecolors="none")
        b.scatter(d.mean(),j,s=65,color=BLUE,marker="D",zorder=4)
        for r,value in zip(days,d):exports.append({"date":r["date"],"control":lab,"proposed_minus_control_mad_pu":value/1000})
    b.axvline(0,color="#D57D72",ls="--",lw=1.2)
    b.set_xlim(-21,5.5);b.set_xlabel(r"Proposed − 对照的偏差 / $10^{-3}$ p.u.",labelpad=12)
    b.set_title("b  48 个测试日的配对差",loc="left",fontsize=17,pad=14)
    foot(fig,"左：灰点为种子 42/43/44，蓝色为均值 ± 样本标准差（n=3）。\n"
             "右：每个灰点为一个测试日（先对 3 种子取均值），蓝色为日均差；负值代表 Proposed 更好。")
    save(fig,"02_seeds_and_daily_pairs",out,qa,alignment)
    write_table(out/"02_daily_pairs.csv",list(exports[0]),exports)


def training(out,qa,alignment,runs,paper):
    curves={k:[] for k in COLORS};exports=[]
    for seed in [42,43,44]:
        src=trained_sources(seed,"20261111",runs)
        if seed==42:
            ps=runs/"llm_v2_c2_seed_20261109";ns=runs/"llm_v2_norefl_seed_20261110"
        else:
            ps=runs/f"llm_v2_seed_s{seed}_20261111";ns=runs/f"llm_v2_norefl_seed_s{seed}_20261111"
        p=table(ps/"days.csv")+table(src["pool"]/"days.csv")
        nr=table(ns/"days.csv")+table(src["nr"]/"days.csv")
        pure=table(src["purerl"]/"episode_rewards.csv")
        assert len(p)==len(nr)==206 and len(pure)==200
        assert [r["date"] for r in p]==[r["date"] for r in nr]
        assert [r["date"] for r in pure]==[r["date"] for r in p[6:]]
        tune={k:table(src[v]/"episode_rewards.csv") for k,v in [("Proposed","proposed"),("Pure-RL","purerl_ft"),("No-PT","nopt")]}
        assert all(len(t)==150 for t in tune.values())
        assert all([r["date"] for r in t]==[r["date"] for r in tune["Proposed"]] for t in tune.values())
        series={"Proposed":np.r_[[float(r["selected_reward"]) for r in p],[float(r["reward"]) for r in tune["Proposed"]]],
                "Pure-RL":np.r_[np.full(6,np.nan),[float(r["reward"]) for r in pure],[float(r["reward"]) for r in tune["Pure-RL"]]],
                "No-Reflexion":np.r_[[float(r["selected_reward"]) for r in nr],np.full(150,np.nan)],
                "No-PT":np.r_[[float(r["selected_reward"]) for r in p],[float(r["reward"]) for r in tune["No-PT"]]]}
        dates=[r["date"] for r in p]+[r["date"] for r in tune["Proposed"]]
        for name,v in series.items():
            sm=trailing(v*33)
            if name=="No-PT":sm[:206]=np.nan
            if name=="No-Reflexion":sm[206:]=np.nan
            curves[name].append(sm)
            for i in np.flatnonzero(np.isfinite(sm)):
                exports.append({"seed":seed,"method":name,"episode":i+1,"date":dates[i],"raw_paper_scale":v[i]*33,"moving_mean_25":sm[i]})
    fig,(a,b)=canvas("训练过程：建库改善日前计划，预训练 PPO 带来主要跃升", "原论文图 7 与本复现三种子曲线")
    doc=pymupdf.open(paper);pix=doc[8].get_pixmap(matrix=pymupdf.Matrix(3,3),clip=pymupdf.Rect(311,61,593,237),alpha=False)
    a.imshow(np.frombuffer(pix.samples,dtype=np.uint8).reshape(pix.height,pix.width,pix.n),aspect="equal")
    b.set_box_aspect(pix.height/pix.width)
    a.axis("off");a.set_title("a  原论文 Fig. 7",loc="left",fontsize=17,pad=14)
    x=np.arange(1,357)
    for name,ss in curves.items():
        mat=np.array(ss);valid=np.isfinite(mat).all(axis=0)
        yy=mat[:,valid];mean=yy.mean(axis=0);sd=yy.std(axis=0,ddof=1)
        b.plot(x[valid],mean,label=name,color=COLORS[name],lw=2.1)
        b.fill_between(x[valid],mean-sd,mean+sd,color=COLORS[name],alpha=.12,linewidth=0)
    b.axvline(206.5,color="#B4BDC9",ls="--",lw=1)
    b.set_xlim(0,356);b.set_ylim(-110,-15)
    b.set_xticks([0,100,200,300,350]);b.set_xlabel("回合",labelpad=12)
    b.set_ylabel("日总奖励（原文图 7 量纲）",labelpad=8)
    b.set_title("b  本复现：均值 ± 种子标准差",loc="left",fontsize=17,pad=14)
    fig.legend(*b.get_legend_handles_labels(),fontsize=10.5,loc="upper right",bbox_to_anchor=(.96,.88),ncol=4)
    foot(fig,"本复现：前 6 回合起步建库，再正式建库 200 回合，随后日内微调 150 回合；每条曲线包含 3 个种子。\n"
             "日奖励 × 33，25 回合滑动平均跨阶段连续计算；1,500 回合 PPO 预训练不在这段横轴内。原图：Yang et al., TSG 2026。")
    save(fig,"03_training_vs_fig7",out,qa,alignment)
    write_table(out/"03_training_all_seeds.csv",list(exports[0]),exports)


def gap(ours,out,qa,alignment):
    fig,(ax,)=canvas("Proposed 缩小了约 74% 的优化参照差距", "Original 为 0%，MISOCP 参照为 100%",n=1)
    fig.subplots_adjust(left=.18,right=.90)
    row_axis(ax)
    results=[]
    for y,(m,lab,p,o) in enumerate(zip(METHODS,LABELS,PAPER_MAD,ours)):
        pv=100*(PAPER_MAD[0]-p)/(PAPER_MAD[0]-PAPER_MAD[-1])
        ov=100*(ours[0]-o)/(ours[0]-ours[-1])
        for yy,v,c in [(y-.17,pv,GRAY),(y+.17,ov,BLUE)]:
            ax.barh(yy,v,height=.27,color=c)
            ax.annotate(f"{v:.1f}%",(v,yy),xytext=(7,0),textcoords="offset points",va="center",fontsize=11)
        results.append({"method":lab,"paper_gap_closed_pct":pv,"reproduction_gap_closed_pct":ov})
    ax.set_xlim(0,113);ax.set_xticks([0,25,50,75,100]);ax.set_xlabel("缩小 Original 至 MISOCP 差距的比例 / %",labelpad=12)
    fig.legend(handles=[Line2D([0],[0],lw=8,color=GRAY,label="原论文"),Line2D([0],[0],lw=8,color=BLUE,label="本复现")],
               loc="upper right",bbox_to_anchor=(.94,.88),ncol=2,fontsize=12)
    foot(fig,"比例 = (Original 偏差 − 方法偏差) / (Original 偏差 − MISOCP 偏差)。\n"
             "MISOCP 使用真实未来信息；本复现取两步求解后 AC 潮流评分，48 个测试日零越限。")
    save(fig,"04_gap_to_misocp",out,qa,alignment)
    write_table(out/"04_gap_to_misocp.csv",list(results[0]),results)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--output",type=Path,required=True)
    p.add_argument("--qa-output",type=Path,required=True)
    p.add_argument("--runs",type=Path,default=Path("D:/Codex/outputs/llm-rl-voltage/runs"))
    p.add_argument("--qa-scripts",type=Path,default=Path.home()/".codex/skills/nature-figure/scripts")
    p.add_argument("--paper",type=Path,default=ROOT/"references/目标论文.pdf")
    args=p.parse_args();args.output.mkdir(parents=True,exist_ok=True);args.qa_output.mkdir(parents=True,exist_ok=True)
    sys.path.insert(0,str(args.qa_scripts))
    from audit_panel_alignment import require_matplotlib_panel_alignment
    style();s=read(ROOT/"results/test-v2/summary.json");m=read(ROOT/"results/misocp-v2/summary.json")
    vals=test_comparison(s,m,args.output,args.qa_output,require_matplotlib_panel_alignment)
    seed_and_pairs(s,args.output,args.qa_output,require_matplotlib_panel_alignment)
    training(args.output,args.qa_output,require_matplotlib_panel_alignment,args.runs,args.paper)
    gap(vals,args.output,args.qa_output,require_matplotlib_panel_alignment)
    sources=[ROOT/"results/test-v2/summary.json",ROOT/"results/test-v2/day_means.csv",ROOT/"results/misocp-v2/summary.json",args.paper]
    manifest={"test_seeds":[42,43,44],"test_dates":48,"paper_table":"III", "exclusions":[],
        "training_smoothing":"per-seed trailing 25-episode mean, then mean and sample SD over all 3 seeds",
        "source_sha256":{str(f):hashlib.sha256(f.read_bytes()).hexdigest() for f in sources},
        "figures":[p.name for p in args.output.glob('*.png')]}
    (args.output/"source_manifest.json").write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps({"figures":manifest["figures"]},ensure_ascii=False))


if __name__=="__main__":
    main()
