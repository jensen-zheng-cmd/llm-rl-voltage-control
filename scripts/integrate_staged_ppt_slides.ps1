param([string]$Source='', [string]$BuildDir='D:\Codex\work\llm-rl-ppt-integrate-20261113')
$ErrorActionPreference='Stop'
if(!$Source){$Source=Join-Path $PSScriptRoot '..\LLM-RL.pptx'}
$Source=(Resolve-Path -LiteralPath $Source).Path
New-Item -ItemType Directory -Path $BuildDir -Force|Out-Null
$backup=Join-Path $BuildDir 'before-integration.pptx'
if(!(Test-Path -LiteralPath $backup)){Copy-Item -LiteralPath $Source -Destination $backup}
$draft=Join-Path $BuildDir 'integrated-draft.pptx';Copy-Item -LiteralPath $backup -Destination $draft -Force
$purple=104+256*11+65536*122;$ink=2500134;$rule=215+256*200+65536*222
function Text($slide,$name,$value,[double]$x,[double]$y,[double]$w,[double]$h,[double]$size=17,[bool]$bold=$false,[int]$color=$ink){
    $s=$slide.Shapes.AddTextbox(1,$x,$y,$w,$h);$s.Name=$name
    $s.TextFrame.MarginLeft=0;$s.TextFrame.MarginRight=0;$s.TextFrame.MarginTop=0;$s.TextFrame.MarginBottom=0
    $s.TextFrame.WordWrap=-1;$s.TextFrame.AutoSize=0
    $r=$s.TextFrame.TextRange;$r.Text=$value;$r.Font.Name='Microsoft YaHei';$r.Font.NameFarEast='Microsoft YaHei'
    $r.Font.Size=$size;$r.Font.Bold=0;$r.Font.Color.RGB=$color
    if($bold){$r.Font.Bold=-1}
    $r.ParagraphFormat.SpaceBefore=0;$r.ParagraphFormat.SpaceAfter=0
    $s.TextFrame2.AutoSize=0;$s.Width=$w;$s.Height=$h
    return $s
}
$app=New-Object -ComObject PowerPoint.Application
$d=$app.Presentations.Open($draft,0,0,0)
try{
    if($d.Slides.Count -ne 25){throw 'Inspect current page order before using the original page references.'}
    $originalIds=@();foreach($s in $d.Slides){$originalIds+=$s.SlideID}
    $promptOld=$d.Slides.Item(12);$reflex=$d.Slides.Item(13);$team=$d.Slides.Item(21);$teamOld=$d.Slides.Item(22)
    if($reflex.Shapes.Item('调度方向标题').TextFrame.TextRange.Text -notmatch '日前LLM'){throw 'Original slide 13 no longer matches.'}

    # Original slide 13: preserve its right-hand source flowchart and top band.
    foreach($sh in @($reflex.Shapes)){
        if($sh.HasTextFrame -eq -1 -and $sh.TextFrame.HasText -eq -1 -and $sh.Top -gt 53){
            if($sh.TextFrame.TextRange.Text -match '^3\. 日前LLM'){
                $sh.TextFrame.TextRange.Text='3. 日前LLM智能体-Reflexion策略改进';$sh.Width=850
            }
        }
    }
    $headings=@('1. 相似历史日检索','2. 环境反馈与 Reflexion','3. 知识库更新','4. 离线改进、在线冻结')
    $bodies=@(
        "趋势相似度（余弦）× 幅值相似度；`r检索最相似历史日，作为少样本示例。",
        "根据总奖励与逐时电压反思并调整动作；`r多轮迭代后，选取奖励最高的候选计划。",
        "低于相似度阈值：追加新样本；`r达到阈值：奖励更高则替换，否则保留。",
        "离线执行反思与知识库更新；`r在线冻结知识库，仅做检索与推理。"
    )
    for($i=0;$i -lt 4;$i++){
        $y=132+95*$i
        $null=Text $reflex ('Reflexion标题'+($i+1)) $headings[$i] 56.25 $y 415 29 19 $true $purple
        $null=Text $reflex ('Reflexion说明'+($i+1)) $bodies[$i] 56.25 ($y+37) 415 51 16.5
    }
    $reflex.NotesPage.Shapes.Placeholders.Item(2).TextFrame.TextRange.Text='相似度为趋势余弦相似度与幅值相似度乘积。执行初始计划后反思n_LLM轮，从n_LLM+1个候选中选最高奖励R_i。相似度低于阈值则追加新场景，达到阈值时仅在R_i>R_j时替换历史。Reflexion及经验库更新仅离线进行，在线冻结。来源：原论文III.C，rc-000116–rc-000137。'

    # Original slides 21 and 22: merge the declared roles and replace copied placeholders.
    for($i=$team.Shapes.Count;$i -ge 1;$i--){if($team.Shapes.Item($i).Top -gt 53){$team.Shapes.Item($i).Delete()}}
    $null=Text $team '团队分工标题' '团队分工与具体工作' 40 82 880 42 26 $true $purple
    $people=@('吕闻达','王韬涵','郑义骅')
    $roles=@('LLM 智能体设计与实现','RL 智能体设计与实现','整体架构设计、联调实验与 RL 微调')
    $tasks=@(
        @('提示词与结构化输出设计、运行规则校验','相似度检索、少样本示例与经验库维护','Reflexion 反馈迭代与日前计划生成'),
        @('状态、动作与奖励函数设计','PPO 网络结构、超参数与无功动作映射','随机机械计划下的策略预训练与评估'),
        @('两阶段接口与交流潮流仿真环境联调','固定 LLM 计划下的 PPO 微调与协同验证','对照与消融实验、性能评估及结果整理')
    )
    for($i=0;$i -lt 3;$i++){
        $y=143+125*$i
        $null=Text $team ('成员'+($i+1)) $people[$i] 56 $y 133 37 25 $true $purple
        $null=Text $team ('职责'+($i+1)) $roles[$i] 214 $y 709 32 20 $true
        for($j=0;$j -lt 3;$j++){$null=Text $team ('工作'+($i+1)+'-'+($j+1)) ('· '+$tasks[$i][$j]) 214 ($y+39+23*$j) 709 26 17}
        if($i -lt 2){$line=$team.Shapes.AddLine(56,$y+111,920,$y+111);$line.Name='成员分隔线'+$i;$line.Line.ForeColor.RGB=$rule;$line.Line.Weight=0.8}
    }
    $team.NotesPage.Shapes.Placeholders.Item(2).TextFrame.TextRange.Text='吕闻达负责日前LLM智能体，包括提示词、结构化输出、运行约束校验、检索经验库与Reflexion反馈。王韬涵负责日内RL智能体，包括MDP定义、PPO策略网络、参数配置及预训练。郑义骅负责整体架构、环境与两阶段接口联调、PPO微调、对照实验和结果整理。方法模块见论文III.A–C。'
    $teamOld.Delete()

    # Replace, rather than duplicate, the original prompt-design page.
    $promptFile=Join-Path (Split-Path $Source) '第12页-提示词构建-待插入.pptx'
    $count=$d.Slides.InsertFromFile($promptFile,$promptOld.SlideIndex-1,1,1)
    if($count -ne 1){throw 'Prompt slide import failed.'}
    $promptOld.Delete()
    $prompt=$d.Slides.Item(12)
    $prompt.NotesPage.Shapes.Placeholders.Item(2).TextFrame.TextRange.Text='提示词由四部分组成：环境与任务信息、标准化输出格式、CoT引导、少样本历史知识。格式校验和运行约束检查支持重试；检索到的预测、设备动作、奖励和电压曲线为新场景决策提供参考。来源：原论文III.A，rc-000089–rc-000095。'

    $rlFile=Join-Path (Split-Path $Source) 'RL文献综述-待插入.pptx'
    $count=$d.Slides.InsertFromFile($rlFile,7,1,1)
    if($count -ne 1){throw 'RL literature import failed.'}
    $rl=$d.Slides.Item(8)
    foreach($sh in $rl.Shapes){
        if($sh.HasTextFrame -eq -1 -and $sh.TextFrame.HasText -eq -1){
            $r=$sh.TextFrame.TextRange
            if($r.Text -match '^\[(\d+)\] '){$n=[int]$Matches[1];$null=$r.Replace(('['+$n+']'),('['+($n+2)+']'));$sh.Width=890;$sh.TextFrame.MarginRight=12}
        }
    }
    $rl.NotesPage.Shapes.Placeholders.Item(2).TextFrame.TextRange.Text='按目标论文引言分三类列示原文[5]–[14]。当前主PPT的LLM文献已由用户编号为[1]–[6]，本页顺延编号为[7]–[16]；作者、标题及出版信息保持原样。'
    $d.Save()
    $preview=Join-Path $BuildDir 'after';New-Item -ItemType Directory -Path $preview -Force|Out-Null
    $audit=@()
    foreach($s in $d.Slides){
        $s.Export((Join-Path $preview ($s.SlideIndex.ToString()+'.png')),'PNG',1600,900)
        $objects=@()
        foreach($sh in $s.Shapes){
            if($sh.HasTextFrame -eq -1 -and $sh.TextFrame.HasText -eq -1){$objects+=@{name=$sh.Name;text=$sh.TextFrame.TextRange.Text;left=$sh.Left;top=$sh.Top;width=$sh.Width;height=$sh.Height;boundWidth=$sh.TextFrame.TextRange.BoundWidth;boundHeight=$sh.TextFrame.TextRange.BoundHeight}}
        }
        $audit+=@{index=$s.SlideIndex;id=$s.SlideID;objects=$objects;shapeCount=$s.Shapes.Count}
    }
    @{slides=$d.Slides.Count;originalIds=$originalIds;pages=$audit;newPages=@{RL=$rl.SlideIndex;Prompt=$prompt.SlideIndex;Reflexion=$reflex.SlideIndex;Teamwork=$team.SlideIndex};modifiedOriginalIds=@($reflex.SlideID,$team.SlideID)}|ConvertTo-Json -Depth 8|Set-Content -LiteralPath (Join-Path $BuildDir 'verification.json') -Encoding UTF8
    Write-Output ('Draft: '+$draft+'; slides='+$d.Slides.Count+'; RL='+$rl.SlideIndex+' prompt='+$prompt.SlideIndex+' reflexion='+$reflex.SlideIndex+' team='+$team.SlideIndex)
}finally{$d.Close()}
