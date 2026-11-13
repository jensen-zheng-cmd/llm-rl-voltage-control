"""Reproduce the presentation of Fig. 7, Table III and Fig. 9 using frozen v2 data.

Training and table statistics are read from existing runs. One deterministically
chosen test day is replayed with saved plans and frozen PPOs; no LLM or training.
"""
import argparse
import csv
import hashlib
import json
import os
from pathlib import Path
import sys

os.environ.setdefault('MPLCONFIGDIR', 'D:/Codex/cache/matplotlib')
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
sys.path.insert(0, str(ROOT / 'scripts'))
from run_seed_v2 import trained_sources
from summarize_seeds_v2 import METHODS

ORDER = ['original', 'no_llm', 'no_rl', 'proposed', 'pure_rl', 'no_pt', 'no_reflexion', 'misocp']
NAMES = dict(zip(ORDER, ['Original', 'No-LLM', 'No-RL', 'Proposed', 'Pure-RL', 'No-PT', 'No-Reflexion', 'MISOCP']))
COLORS = {'Proposed': '#4977B7', 'Pure-RL': '#888888', 'No-PT': '#E77839', 'No-Reflexion': '#48A767'}


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def table(path):
    with Path(path).open(encoding='utf-8', newline='') as f:
        return list(csv.DictReader(f))


def write_csv(path, rows):
    with Path(path).open('w', encoding='utf-8-sig', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def save(fig, stem, args, alignment, groups=None):
    fig.canvas.draw()
    kwargs = {'comparable_groups': groups} if groups is not None else {}
    require_matplotlib_panel_alignment = alignment
    require_matplotlib_panel_alignment(fig, json_out=str(args.qa_output / f'{stem}.alignment.json'), strict=True, **kwargs)
    fig.savefig(args.output / f'{stem}.png', dpi=300, facecolor='white')
    fig.savefig(args.output / f'{stem}.pdf', facecolor='white')
    fig.savefig(args.output / f'{stem}.svg', facecolor='white')
    plt.close(fig)


def trailing(values):
    values = np.asarray(values)
    result = np.full(len(values), np.nan)
    for i in range(len(values)):
        block = values[max(0, i-24):i+1]
        if np.isfinite(block).any():
            result[i] = block[np.isfinite(block)].mean()
    return result


def training(args, alignment, manifest):
    by_method = {name: [] for name in COLORS}
    records = []
    for seed in (42, 43, 44):
        src = trained_sources(seed, '20261111', args.runs)
        pseed = args.runs / ('llm_v2_c2_seed_20261109' if seed == 42 else f'llm_v2_seed_s{seed}_20261111')
        nseed = args.runs / ('llm_v2_norefl_seed_20261110' if seed == 42 else f'llm_v2_norefl_seed_s{seed}_20261111')
        files = [pseed/'days.csv', src['pool']/'days.csv', nseed/'days.csv', src['nr']/'days.csv', src['purerl']/'episode_rewards.csv']
        files += [src[k]/'episode_rewards.csv' for k in ('proposed', 'purerl_ft', 'nopt')]
        manifest['training_sources'].update({str(p): digest(p) for p in files})
        p = table(files[0]) + table(files[1])
        nr = table(files[2]) + table(files[3])
        pure = table(files[4])
        tune = {k: table(src[v]/'episode_rewards.csv') for k,v in [('Proposed','proposed'),('Pure-RL','purerl_ft'),('No-PT','nopt')]}
        assert len(p) == len(nr) == 206 and len(pure) == 200
        assert [r['date'] for r in p] == [r['date'] for r in nr]
        assert [r['date'] for r in pure] == [r['date'] for r in p[6:]]
        assert all(len(r) == 150 for r in tune.values())
        assert all([d['date'] for d in r] == [d['date'] for d in tune['Proposed']] for r in tune.values())
        dates = [r['date'] for r in p[6:]] + [r['date'] for r in tune['Proposed']]
        phase1 = np.array([float(r['selected_reward'])*33 for r in p[6:]])
        values = {
            'Proposed': np.r_[phase1, [float(r['reward'])*33 for r in tune['Proposed']]],
            'Pure-RL': np.r_[[float(r['reward'])*33 for r in pure], [float(r['reward'])*33 for r in tune['Pure-RL']]],
            'No-Reflexion': np.r_[[float(r['selected_reward'])*33 for r in nr[6:]], np.full(150, np.nan)],
            'No-PT': np.r_[phase1, [float(r['reward'])*33 for r in tune['No-PT']]],
        }
        for name, y in values.items():
            sm = trailing(y)
            shown = np.isfinite(y)
            if name == 'No-PT': shown[:200] = False
            sm[~shown] = np.nan
            by_method[name].append(sm)
            for i in range(350):
                if np.isfinite(y[i]):
                    records.append({'seed': seed, 'method': name, 'episode': i+1, 'date': dates[i],
                                    'total_reward': y[i], 'moving_mean_25': sm[i] if shown[i] else '', 'displayed': bool(shown[i])})
    fig, ax = plt.subplots(figsize=(8.6, 5.45))
    fig.subplots_adjust(left=.12, right=.98, bottom=.14, top=.89)
    exports = []
    for name in ['Proposed', 'Pure-RL', 'No-PT', 'No-Reflexion']:
        mat = np.array(by_method[name]); valid = np.isfinite(mat).all(axis=0)
        mean = mat[:,valid].mean(axis=0); sd = mat[:,valid].std(axis=0, ddof=1)
        x = np.arange(1,351)[valid]
        ax.plot(x, mean, color=COLORS[name], lw=1.9, label=name)
        ax.fill_between(x, mean-sd, mean+sd, color=COLORS[name], alpha=.14, linewidth=0)
        exports += [{'method':name,'episode':int(xx),'mean':float(m),'seed_sd':float(s)} for xx,m,s in zip(x,mean,sd)]
    all_lows = [r['mean']-r['seed_sd'] for r in exports]
    all_highs = [r['mean']+r['seed_sd'] for r in exports]
    ymin = np.floor(min(all_lows)/10)*10
    ymax = np.ceil(max(all_highs)/10)*10
    ax.set(xlim=(0,350), ylim=(ymin,ymax), xlabel='Episodes', ylabel='Total Rewards')
    ax.set_xticks(np.arange(0,351,50))
    ax.axvline(200, color='#CF4D52', lw=1.5, ls='--')
    ax.text(100/350, 1.025, 'LLM Improvement', ha='center', va='bottom', transform=ax.transAxes, fontweight='bold')
    ax.text(275/350, 1.025, 'RL Finetuning', ha='center', va='bottom', transform=ax.transAxes, fontweight='bold')
    handles, labels = ax.get_legend_handles_labels()
    # Legend occupies its own strip to keep all data and uncertainty visible.
    ax.legend(handles, labels, loc='upper center', bbox_to_anchor=(.5,1.22), ncol=4, frameon=False, fontsize=11, handlelength=1.7, columnspacing=1.2)
    fig.subplots_adjust(top=.79)
    save(fig, 'fig7_training', args, alignment)
    write_csv(args.output/'fig7_training_raw.csv', records)
    write_csv(args.output/'fig7_training_mean_sd.csv', exports)
    manifest['training'] = {'formal_planning_episodes':200,'finetuning_episodes':150,'seeds':[42,43,44],
        'smoothing':'per-seed trailing 25 episodes, continuous across phase boundary; then mean +/- sample SD',
        'reward':'daily reward multiplied by 33, negative sum of all node-time absolute voltage deviations',
        'not_on_axis':'6 LLM seed days; 1500 PPO pretraining episodes; value-network warmup',
        'No-PT_phase1':'shared Proposed phase-1 history is used only to initialize smoothing; not plotted as separate observations',
        'No-Reflexion_display':'first 200 formal planning episodes only, matching paper Fig.7 stage definition'}


def execution(args, alignment, manifest):
    summary = read(ROOT/'results/test-v2/summary.json')
    pooled = {m: [] for m in ORDER[:-1]}
    daily = []
    all_tables = {}
    for seed in ('42','43','44'):
        tabs = [table(Path(p)/'pairs.csv') for p in summary['sources'][seed]]
        all_tables[seed] = tabs
        assert all([r['date'] for r in t] == [r['date'] for r in tabs[0]] for t in tabs)
        for m,(index,prefix) in METHODS.items():
            for row in tabs[index]:
                item={'seed':seed,'date':row['date'],'method':m,'mad_pu':float(row[f'{prefix}_mad_pu']),
                      'violation_pct':float(row[f'{prefix}_violation_pct'])}
                daily.append(item);pooled[m].append(item)
    miso = table(ROOT/'results/misocp-v2/days.csv')
    rows=[]
    means_ref=read(ROOT/'results/misocp-v2/summary.json')['methods_seed_mean']
    for method in ORDER:
        vals = np.array([[float(r['misocp_mad_pu']),float(r['misocp_violation_pct'])] for r in miso]) if method=='misocp' else np.array([[r['mad_pu'],r['violation_pct']] for r in pooled[method]])
        mean=vals.mean(axis=0);sd=vals.std(axis=0,ddof=1)
        assert vals.shape[0] == (48 if method=='misocp' else 144)
        if method!='misocp':
            assert np.isclose(mean[0],means_ref[method]['mad_pu'],rtol=0,atol=1e-12)
            assert np.isclose(mean[1],means_ref[method]['violation_pct'],rtol=0,atol=1e-12)
        rows.append({'method':NAMES[method], 'voltage_deviation_mean':mean[0], 'voltage_deviation_std':sd[0],
                     'violation_rate_mean_pct':mean[1], 'violation_rate_std_pct':sd[1], 'daily_episodes':len(vals)})
    fig,ax=plt.subplots(figsize=(9.3,3.65));fig.subplots_adjust(left=.02,right=.98,bottom=.02,top=.98)
    ax.set_xlim(0,1);ax.set_ylim(0,1);ax.axis('off')
    x=[.13,.36,.55,.74,.92]
    ax.text(x[0],.87,'Method',ha='center',va='center',fontsize=18)
    ax.text((x[1]+x[2])/2,.93,'Voltage Deviation (p.u.)',ha='center',va='center',fontsize=17)
    ax.text((x[3]+x[4])/2,.93,'Violation Rate (%)',ha='center',va='center',fontsize=17)
    for xx,title in zip(x[1:],['Mean','Std.','Mean','Std.']):ax.text(xx,.825,title,ha='center',va='center',fontsize=17)
    ax.plot([0,1],[.995,.995],color='black',lw=1.8)
    ax.plot([.245,.64],[.87,.87],color='black',lw=.8)
    ax.plot([.65,1],[.87,.87],color='black',lw=.8)
    ax.plot([0,1],[.77,.77],color='black',lw=.8)
    for i,row in enumerate(rows):
        y=.705-i*.087
        ax.text(x[0],y,row['method'],ha='center',va='center',fontsize=17)
        vals=[row[k] for k in ['voltage_deviation_mean','voltage_deviation_std','violation_rate_mean_pct','violation_rate_std_pct']]
        for j,(xx,val) in enumerate(zip(x[1:],vals)):
            text=f'{val:.2e}' if j<2 or 0<val<.1 else f'{val:.2f}'
            best=min(r[['voltage_deviation_mean','voltage_deviation_std','violation_rate_mean_pct','violation_rate_std_pct'][j]] for r in rows[:-1])
            ax.text(xx,y,text,ha='center',va='center',fontsize=17,fontweight='bold' if i<7 and np.isclose(val,best,rtol=1e-9) else 'normal')
    ax.plot([0,1],[.045,.045],color='black',lw=1.8)
    save(fig,'table3_execution',args,alignment)
    write_csv(args.output/'table3_execution.csv',rows)
    write_csv(args.output/'table3_daily_episodes.csv',daily)
    manifest['execution']={'RL_daily_episodes':144,'unique_test_dates':48,'seeds':[42,43,44], 'MISOCP_days':48,
        'SD':'sample standard deviation of daily episode metrics, not standard deviation of three seed means',
        'Original':'same deterministic date metric evaluated for each seed; no independence or confidence interval is implied',
        'voltage_metric':'mean |V-1| over 96 times and 33 nodes; violations are node-time percentage'}
    return summary,all_tables


def replay(args, summary, all_tables, manifest):
    import torch
    from stable_baselines3 import PPO
    from finetune_ppo_v2 import v2_dataset, final_record
    from evaluate_ppo_checkpoints import model_file
    from run_llm_planning import NEUTRAL, parse_plan
    from train_ppo import flatten
    from voltage_control.environment import VoltageControlEnv
    torch.set_num_threads(1)
    rows=all_tables[str(args.seed)][0]
    median=float(np.median([float(r['original_mad_pu']) for r in rows]))
    date=min(rows,key=lambda r:(abs(float(r['original_mad_pu'])-median),r['date']))['date']
    sources=[Path(p) for p in summary['sources'][str(args.seed)]]
    cfgs=[read(p/'config.json') for p in sources]
    plans=[];hashes={}
    for cfg in cfgs:
        path=final_record(Path(cfg['c2_source'])/date,cfg['c2_arm'])
        assert digest(path)==cfg['plan_record_sha256'][date]
        plans.append(read(path)['plan']);hashes[str(path)]=digest(path)
    dataset=v2_dataset();assert 29 in dataset.system['paper_devices']['pv_buses']
    arrays={};checks=[];models={}
    for method in ORDER[:-1]:
        index,prefix=METHODS[method];cfg=cfgs[index]
        spec=NEUTRAL if method in ('original','no_llm') else plans[index]
        model=None;norm=None
        if method not in ('original','no_rl'):
            arm='proposed' if method=='no_llm' else prefix.rsplit('_ep',1)[0]
            path=model_file(Path(cfg['runs'][arm]),150)
            assert digest(path)==cfg['models_sha256'][prefix]
            npth=Path(cfg['pretrained'])/'normalization.npz'
            assert digest(npth)==cfg['normalization_sha256']
            hashes[str(path)]=digest(path);hashes[str(npth)]=digest(npth)
            if str(path) not in models:models[str(path)]=PPO.load(path,device='cpu')
            model=models[str(path)]
            with np.load(npth,allow_pickle=False) as n:norm=(n['mean'],n['scale'])
        env=VoltageControlEnv(dataset);obs,_=env.reset(date,parse_plan(spec))
        voltage=[];reactive=[]
        for step in range(96):
            action=np.zeros(6) if model is None else model.predict((flatten(obs)-norm[0])/norm[1],deterministic=True)[0]
            obs,_,done,trunc,info=env.step(action)
            assert not trunc and done==(step==95)
            voltage.append(info['snapshot'].voltage_pu.copy());reactive.append(info['snapshot'].pv_q_mvar.copy())
        v=np.stack(voltage);arrays[method]=v;arrays[method+'_pv_q']=np.stack(reactive)
        metrics={'mad_pu':float(np.abs(v-1).mean()),'violation_pct':float(100*((v<.95)|(v>1.05)).mean()),'min_pu':float(v.min()),'max_pu':float(v.max())}
        saved=next(r for r in all_tables[str(args.seed)][index] if r['date']==date)
        errors={k:abs(val-float(saved[f'{prefix}_{k}'])) for k,val in metrics.items()}
        assert max(errors.values())<1e-9,(method,errors)
        checks.append({'method':method,'date':date,**metrics,'max_metric_replay_error':max(errors.values())})
        print(f'Replay matched: {method} {date}',flush=True)
    mrun=Path(read(ROOT/'results/misocp-v2/summary.json')['misocp_run'])
    path=mrun/date/'stage2.npz';hashes[str(path)]=digest(path)
    with np.load(path,allow_pickle=False) as n:arrays['misocp']=n['v_ac'].copy();arrays['misocp_pv_q']=n['pv_q_mvar'].copy()
    assert arrays['misocp'].shape==(96,33)
    saved=next(r for r in table(ROOT/'results/misocp-v2/days.csv') if r['date']==date)
    assert np.isclose(np.abs(arrays['misocp']-1).mean(),float(saved['misocp_mad_pu']),rtol=0,atol=1e-12)
    args.replay_output.mkdir(parents=True,exist_ok=False)
    np.savez_compressed(args.replay_output/'all_bus_traces.npz',**arrays)
    write_csv(args.replay_output/'replay_metrics.csv',checks)
    manifest['voltage']={'date':date,'seed':args.seed,'bus':29,'array_index':28,'time':'96 intervals starting 00:00, every 15 minutes',
        'day_selection':'Original whole-network daily MAD nearest median of 48 test dates, date as tie-breaker; fixed before replay',
        'Original_test_median_mad':median,'policy':'frozen final weights, deterministic prediction, saved test day-ahead plans',
        'replay_metric_tolerance':1e-9,'max_replay_metric_error':max(r['max_metric_replay_error'] for r in checks),
        'source_hashes':hashes,'all_bus_traces':str(args.replay_output/'all_bus_traces.npz')}
    write_csv(args.output/'fig9_bus29.csv',[{'date':date,'seed':args.seed,'hour':i/4,**{NAMES[m]:arrays[m][i,28] for m in ORDER}} for i in range(96)])
    return date,arrays


def voltage_figure(args,alignment,date,arrays):
    fig,axes=plt.subplots(4,2,figsize=(8.6,9.8),sharex=True,sharey=True)
    fig.subplots_adjust(left=.105,right=.985,bottom=.075,top=.98,hspace=.12,wspace=.055)
    values=np.concatenate([arrays[m][:,28] for m in ORDER])
    low=min(.94,np.floor((values.min()-.003)*100)/100)
    high=max(1.06,np.ceil((values.max()+.003)*100)/100)
    for i,(ax,method) in enumerate(zip(axes.flat,ORDER)):
        ax.plot(np.arange(96)/4,arrays[method][:,28],color='#0072BD',lw=1.55,label=NAMES[method])
        ax.axhline(1,color='#BC252A',ls='-.',lw=1.15)
        ax.set(xlim=(0,24),ylim=(low,high),xticks=[0,6,12,18,24],yticks=[.95,.975,1,1.025,1.05])
        ax.grid(color='#D6D6D6',alpha=.75,lw=.8)
        legend=ax.legend(loc='upper right',fontsize=10,frameon=True,facecolor='white',edgecolor='#CBCBCB',framealpha=1)
        if i%2==0:ax.set_ylabel('Voltage (p.u.)',fontsize=12)
        if i>=6:ax.set_xlabel('Time (h)',fontsize=12)
        ax.tick_params(labelsize=11)
        # Stop grid paths at the opaque legend; no hidden grid crosses its text.
        fig.canvas.draw()
        box=legend.get_window_extent(fig.canvas.get_renderer()).transformed(ax.transAxes.inverted())
        for grid in ax.get_ygridlines():
            y=(grid.get_ydata()[0]-low)/(high-low)
            if box.y0-.01 < y < box.y1+.01:
                grid.set_xdata([0,box.x0-.01])
        for grid in ax.get_xgridlines():
            x=grid.get_xdata()[0]/24
            if box.x0-.01 < x < box.x1+.01:
                grid.set_ydata([0,box.y0-.01])
    # Legend placement and 1.0 reference intentionally follow the source figure.
    save(fig,'fig9_bus29',args,alignment)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runs',type=Path,default=Path('D:/Codex/outputs/llm-rl-voltage/runs'))
    parser.add_argument('--output',type=Path,default=ROOT/'results/paper-style-v2')
    parser.add_argument('--replay-output',type=Path,required=True)
    parser.add_argument('--qa-output',type=Path,required=True)
    parser.add_argument('--seed',type=int,default=42,choices=[42,43,44])
    parser.add_argument('--reuse-replay',action='store_true',help='Redraw from verified saved replay arrays; recheck source hashes')
    args=parser.parse_args();args.output.mkdir(parents=True,exist_ok=True);args.qa_output.mkdir(parents=True,exist_ok=True)
    sys.path.insert(0,str(Path.home()/'.codex/skills/nature-figure/scripts'))
    from audit_panel_alignment import require_matplotlib_panel_alignment
    plt.rcParams.update({'font.family':'serif','font.serif':['Times New Roman','DejaVu Serif'], 'font.size':13,
        'mathtext.fontset':'stix','svg.fonttype':'none','pdf.fonttype':42,'axes.linewidth':1,'xtick.direction':'in','ytick.direction':'in',
        'axes.unicode_minus':True,'savefig.dpi':300})
    manifest={'summary_sources':{str(p.relative_to(ROOT)):digest(p) for p in [ROOT/'results/test-v2/summary.json',ROOT/'results/misocp-v2/summary.json',ROOT/'results/misocp-v2/days.csv']},
              'training_sources':{},'figure_style':'paper Fig.7, Table III and Fig.9; original data not digitized or invented'}
    training(args,require_matplotlib_panel_alignment,manifest)
    summary,tables=execution(args,require_matplotlib_panel_alignment,manifest)
    if args.reuse_replay:
        cached=read(args.output/'source_manifest.json')['voltage']
        assert cached['seed']==args.seed
        assert all(digest(p)==h for p,h in cached['source_hashes'].items())
        with np.load(args.replay_output/'all_bus_traces.npz',allow_pickle=False) as saved:
            arrays={key:saved[key].copy() for key in saved.files}
        date=cached['date'];manifest['voltage']=cached
    else:
        date,arrays=replay(args,summary,tables,manifest)
    voltage_figure(args,require_matplotlib_panel_alignment,date,arrays)
    (args.output/'source_manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
    print('Exported figures, date='+date,flush=True)


if __name__=='__main__':
    main()
